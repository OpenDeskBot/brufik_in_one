#include "ws_transport.h"

#include "asr_ws.h"
#include "camera.h"
#include "camera_ws.h"
#include "logger.h"
#include "mic.h"
#include "pb_runtime.h"
#include "utils/opus_codec.h"
#include "utils/utils.h"

#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <string.h>

namespace {

struct WsRxItem {
  WStype_t type = WStype_ERROR;
  uint8_t* data = nullptr;
  size_t len = 0;
  uint32_t session = 0;
};

/** 入队时已打包：u32be(json_len)+json+media，发送只 sendBIN。 */
struct WsTxItem {
  WsTxType type = WsTxType::kState;
  uint8_t* packed = nullptr;
  size_t packed_len = 0;
};

static uint32_t s_ws_session = 0;
static bool s_session_has_connected = false;
static WebSocketsClient* s_asr_ws = nullptr;
static WebSocketsClient* s_cam_ws = nullptr;
static bool s_setup_ok = false;
static QueueHandle_t s_rx_q = nullptr;
static QueueHandle_t s_tx_q = nullptr;
static TaskHandle_t s_task = nullptr;
static SemaphoreHandle_t s_owner_mu = nullptr;
static bool s_tx_active = false;
static WsTxItem s_tx_active_item{};
static volatile bool s_in_write_pump = false;
static uint32_t s_tx_drop_audio = 0;
static unsigned long s_tx_drop_audio_log_ms = 0;
static bool s_boot_connect_sent = false;

static constexpr UBaseType_t kRxDepth = 128;
static constexpr UBaseType_t kTxDepth = 40;
/* WebSockets 收发、ArduinoJson 解析与 write-pump 都在这个任务调用。
 * 16KB 会在握手/ready 阶段留下过窄余量；loopTask 缩栈后恢复为 32KB。 */
static constexpr uint32_t kTaskStack = 32 * 1024;
static constexpr UBaseType_t kTaskPrio = 5;
static constexpr size_t kMaxRxCopy = 256 * 1024;
static constexpr size_t kMaxTxJson = 16 * 1024;
static constexpr size_t kMaxTxAudioBin = 4 * 1024;
static constexpr size_t kMaxTxImageBin = 32 * 1024;

static void owner_lock(void) {
  if (s_owner_mu) {
    xSemaphoreTakeRecursive(s_owner_mu, portMAX_DELAY);
  }
}

static void owner_unlock(void) {
  if (s_owner_mu) {
    xSemaphoreGiveRecursive(s_owner_mu);
  }
}

static void* rx_alloc(size_t n) {
  return heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
}

static void ws_tx_free_item(WsTxItem* item) {
  if (!item) {
    return;
  }
  if (item->packed) {
    free(item->packed);
    item->packed = nullptr;
  }
  item->packed_len = 0;
}

static uint32_t queue_waiting(QueueHandle_t q) {
  return (uint32_t)uxQueueMessagesWaiting(q);
}

static bool tx_busy(void) {
  return s_tx_active || queue_waiting(s_tx_q) > 0;
}

static void discard_queue(QueueHandle_t q) {
  WsTxItem item{};
  while (xQueueReceive(q, &item, 0) == pdTRUE) {
    ws_tx_free_item(&item);
  }
}

/** 丢掉指定 type；其余按原相对顺序转到队尾（O(1) 栈，不倒整表）。 */
static void discard_tx_of_type(WsTxType type) {
  if (!s_tx_q) {
    return;
  }
  const UBaseType_t n = uxQueueMessagesWaiting(s_tx_q);
  for (UBaseType_t i = 0; i < n; ++i) {
    WsTxItem item{};
    if (xQueueReceive(s_tx_q, &item, 0) != pdTRUE) {
      break;
    }
    if (item.type == type) {
      ws_tx_free_item(&item);
    } else if (xQueueSend(s_tx_q, &item, 0) != pdTRUE) {
      ws_tx_free_item(&item);
    }
  }
  if (s_tx_active && s_tx_active_item.type == type) {
    ws_tx_free_item(&s_tx_active_item);
    s_tx_active = false;
  }
}

static bool enqueue_tx(WsTxItem* item) {
  if (!item || !item->packed || item->packed_len == 0) {
    ws_tx_free_item(item);
    return false;
  }
  if (xQueueSend(s_tx_q, item, 0) == pdTRUE) {
    return true;
  }
  /*
   * 队列满：
   * - state 可丢音频腾位（ack/boot 优先）
   * - image 不得丢音频（双 WS 时相机会把 mic 饿死，进而拖垮 /asr_chat）
   * - audio 满则丢本包
   */
  if (item->type == WsTxType::kState) {
    discard_tx_of_type(WsTxType::kAudio);
    if (xQueueSend(s_tx_q, item, 0) == pdTRUE) {
      log_warn("[WS_TRANSPORT] TX full: dropped audio to enqueue state");
      return true;
    }
  }
  if (item->type == WsTxType::kAudio) {
    ++s_tx_drop_audio;
    const unsigned long now = millis();
    if (s_tx_drop_audio_log_ms == 0 || (now - s_tx_drop_audio_log_ms) >= 1000UL) {
      log_warn("[WS_TRANSPORT] TX drop audio x%u packed_len=%u q=%u/%u",
               (unsigned)s_tx_drop_audio, (unsigned)item->packed_len,
               (unsigned)queue_waiting(s_tx_q), (unsigned)kTxDepth);
      s_tx_drop_audio = 0;
      s_tx_drop_audio_log_ms = now;
    }
  } else {
    log_warn("[WS_TRANSPORT] TX drop type=%u packed_len=%u", (unsigned)item->type,
             (unsigned)item->packed_len);
  }
  ws_tx_free_item(item);
  return false;
}

/** 取队头：纯 FIFO。 */
static bool take_next_tx_item(WsTxItem* out) {
  if (!out || !s_tx_q) {
    return false;
  }
  return xQueueReceive(s_tx_q, out, 0) == pdTRUE;
}

static bool build_tx_item(WsTxType type, const char* json, const uint8_t* bin, size_t bin_len,
                          size_t max_bin, WsTxItem* out) {
  if (!json || !out) {
    return false;
  }
  const size_t n = strlen(json);
  if (n == 0 || n > kMaxTxJson) {
    return false;
  }
  if (bin_len > max_bin || (bin_len > 0 && bin == nullptr)) {
    if (bin_len > max_bin) {
      log_warn("[WS_TRANSPORT] TX bin too large type=%u len=%u max=%u", (unsigned)type,
               (unsigned)bin_len, (unsigned)max_bin);
    }
    return false;
  }
  *out = {};
  out->type = type;
  out->packed = new_packed_bin(json, bin, bin_len, &out->packed_len);
  return out->packed != nullptr;
}

static void ws_rx_enqueue_impl(WStype_t type, uint8_t* payload, size_t length) {
  WsRxItem item{};
  item.type = type;
  item.session = s_ws_session;
  if (payload != nullptr && length > 0) {
    if (length > kMaxRxCopy) {
      log_warn("[WS_TRANSPORT] RX drop oversized type=%u len=%u", (unsigned)type, (unsigned)length);
      return;
    }
    item.data = (uint8_t*)rx_alloc(length + 1);
    if (!item.data) {
      log_warn("[WS_TRANSPORT] RX alloc fail len=%u", (unsigned)length);
      return;
    }
    memcpy(item.data, payload, length);
    item.data[length] = '\0';
    item.len = length;
  }
  if (xQueueSend(s_rx_q, &item, 0) == pdTRUE) {
    return;
  }
  WsRxItem drop{};
  if (xQueueReceive(s_rx_q, &drop, 0) == pdTRUE) {
    free(drop.data);
    if (xQueueSend(s_rx_q, &item, 0) == pdTRUE) {
      log_warn("[WS_TRANSPORT] RX full, dropped oldest type=%u", (unsigned)drop.type);
      return;
    }
  }
  log_warn("[WS_TRANSPORT] RX queue full type=%u", (unsigned)type);
  free(item.data);
}

static void drain_tx_finish(bool is_image, bool send_ok) {
  if (is_image) {
    if (send_ok) {
      camera_ws_end_send_ok();
    } else {
      camera_ws_mark_disconnected();
      camera_ws_on_image_finished();
    }
  }
  ws_tx_free_item(&s_tx_active_item);
  s_tx_active = false;
}

static void write_pump_impl(void) {
  if (s_in_write_pump) {
    taskYIELD();
    return;
  }
  s_in_write_pump = true;
  if (s_asr_ws) {
    s_asr_ws->loop();
  }
  if (s_cam_ws) {
    s_cam_ws->loop();
  }
  s_in_write_pump = false;
  taskYIELD();
}

static void ws_transport_task(void* /*arg*/) {
  for (;;) {
    owner_lock();
    /* auto_reconnect 内部已 loop；此处不再重复 pump。 */
    ws_asr_auto_reconnect();
    ws_camera_auto_reconnect();
    const bool did_rx = ws_transport_drain_rx();
    const bool did_tx = ws_transport_drain_tx();
    /* 相机抓帧并入本任务：省掉 camera_cap 栈 + notify xQueue。 */
    const bool did_cam = camera_try_capture_and_enqueue();
    owner_unlock();

    if (did_rx || did_tx || did_cam || tx_busy() || queue_waiting(s_rx_q) > 0) {
      taskYIELD();
    } else {
      vTaskDelay(pdMS_TO_TICKS(2));
    }
  }
}

}  // namespace

bool setup_ws_transport(void) {
  if (!s_owner_mu) {
    /* recursive：drain_tx → asr_ws_force_reconnect → discard_tx / new_session 可重入。 */
    s_owner_mu = xSemaphoreCreateRecursiveMutex();
    if (!s_owner_mu) {
      s_setup_ok = false;
      log_error("[WS_TRANSPORT] setup mutex create failed");
      return false;
    }
  }
  if (!s_rx_q) {
    s_rx_q = xQueueCreate(kRxDepth, sizeof(WsRxItem));
  }
  if (!s_tx_q) {
    s_tx_q = xQueueCreate(kTxDepth, sizeof(WsTxItem));
  }
  if (!s_rx_q || !s_tx_q) {
    s_setup_ok = false;
    log_error("[WS_TRANSPORT] setup queue create failed");
    return false;
  }
  asr_ws_bind_transport();
  s_setup_ok = true;
  log_info("[WS_TRANSPORT] setup ok TX depth=%u rx=%u", (unsigned)kTxDepth, (unsigned)kRxDepth);
  return true;
}

bool task_setup_ws_transport(void) {
  if (!s_setup_ok) {
    log_error("[WS_TRANSPORT] task_setup skipped (setup not ok)");
    return false;
  }
  if (s_task) {
    return true;
  }
  BaseType_t rc = xTaskCreatePinnedToCore(ws_transport_task, "ws_transport", kTaskStack, nullptr,
                                           kTaskPrio, &s_task, APP_CPU_NUM);
  if (rc != pdPASS) {
    log_error("[WS_TRANSPORT] task create failed rc=%d (internal free=%u)", (int)rc,
              (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    s_task = nullptr;
    return false;
  }
  log_info("[WS_TRANSPORT] task OK stack=%u prio=%u", (unsigned)kTaskStack, (unsigned)kTaskPrio);
  return true;
}

void ws_transport_set_asr_client(WebSocketsClient* ws) {
  s_asr_ws = ws;
  log_info("[WS_TRANSPORT] asr client registered");
}

void ws_transport_set_camera_client(WebSocketsClient* cam_ws) {
  s_cam_ws = cam_ws;
  log_info("[WS_TRANSPORT] camera client registered");
}

void ws_transport_enqueue_rx(WStype_t type, uint8_t* payload, size_t length) {
  ws_rx_enqueue_impl(type, payload, length);
}

void ws_transport_new_session(void) {
  owner_lock();
  s_ws_session++;
  s_session_has_connected = false;
  owner_unlock();
  /* force reconnect 会让旧 DISCONNECTED 事件因 session 过期被丢弃；
   * 仍须由 PB 任务中止旧播放与状态，不能只清 RX 队列。 */
  pb_runtime_notify_link_down();
  log_info("[WS_TRANSPORT] new session=%u (old RX events and PB state will be dropped)",
           (unsigned)s_ws_session);
}

bool ws_transport_enqueue_state(const char* json) {
  WsTxItem item{};
  if (!build_tx_item(WsTxType::kState, json, nullptr, 0, 0, &item)) {
    return false;
  }
  return enqueue_tx(&item);
}

bool ws_transport_enqueue_text(const char* json) {
  return ws_transport_enqueue_state(json);
}

bool ws_transport_enqueue_audio(const char* json, const uint8_t* bin, size_t bin_len) {
  WsTxItem item{};
  if (!build_tx_item(WsTxType::kAudio, json, bin, bin_len, kMaxTxAudioBin, &item)) {
    return false;
  }
  return enqueue_tx(&item);
}

bool ws_transport_enqueue_image_borrow(const char* json, uint8_t* bin, size_t bin_len,
                                       void (*releaser)(void* ctx), void* release_ctx) {
  if (!json || !bin || bin_len == 0 || !releaser || bin_len > kMaxTxImageBin) {
    return false;
  }
  if (!s_cam_ws || !s_cam_ws->isConnected() || camera_ws_state() != 0) {
    return false;
  }
  WsTxItem item{};
  if (!build_tx_item(WsTxType::kImage, json, bin, bin_len, kMaxTxImageBin, &item)) {
    return false;
  }
  /* 已拷入 packed，立刻归还借用缓冲。 */
  releaser(release_ctx);
  if (!enqueue_tx(&item)) {
    return false;
  }
  return true;
}

bool ws_transport_drain_tx(void) {
  if (!s_tx_active) {
    if (!take_next_tx_item(&s_tx_active_item)) {
      return false;
    }
    s_tx_active = true;
  }

  const bool is_image = (s_tx_active_item.type == WsTxType::kImage);
  WebSocketsClient* target = is_image ? s_cam_ws : s_asr_ws;

  /*
   * 未就绪：保留队头，不计 send fail。
   * 必须在 camera_ws_try_begin_send 之前判断，否则 state 会卡在 1。
   */
  if (!is_image) {
    if (!target || !target->isConnected() || asr_ws_state() != 0) {
      static unsigned long s_hold_log_ms = 0;
      const unsigned long now = millis();
      if (s_hold_log_ms == 0 || (now - s_hold_log_ms) >= 2000UL) {
        log_warn("[WS_TRANSPORT] drain_tx hold type=%u (asr not ready)",
                 (unsigned)s_tx_active_item.type);
        s_hold_log_ms = now;
      }
      return false;
    }
  } else {
    if (!target || !target->isConnected()) {
      static unsigned long s_cam_hold_log_ms = 0;
      const unsigned long now = millis();
      if (s_cam_hold_log_ms == 0 || (now - s_cam_hold_log_ms) >= 2000UL) {
        log_warn("[WS_TRANSPORT] drain_tx hold image (camera not connected)");
        s_cam_hold_log_ms = now;
      }
      return false;
    }
    if (!camera_ws_try_begin_send()) {
      log_warn("[WS_TRANSPORT] skip image (camera_ws.state!=0)");
      ws_tx_free_item(&s_tx_active_item);
      s_tx_active = false;
      camera_ws_on_image_finished();
      return true;
    }
  }

  const bool ok = target->sendBIN(s_tx_active_item.packed, s_tx_active_item.packed_len);
  if (!ok) {
    log_warn("[WS_TRANSPORT] sendBIN fail type=%u packed_len=%u", (unsigned)s_tx_active_item.type,
             (unsigned)s_tx_active_item.packed_len);
    if (!is_image) {
      asr_ws_note_send_fail("sendBIN");
    }
  } else if (!is_image) {
    asr_ws_note_send_ok();
  }
  drain_tx_finish(is_image, ok);
  return true;
}

void ws_transport_discard_audio_tx(void) {
  owner_lock();
  discard_tx_of_type(WsTxType::kAudio);
  owner_unlock();
}

void ws_transport_discard_asr_tx(void) {
  owner_lock();
  discard_tx_of_type(WsTxType::kAudio);
  discard_tx_of_type(WsTxType::kState);
  owner_unlock();
}

void ws_transport_discard_tx_queue(void) {
  owner_lock();
  discard_queue(s_tx_q);
  if (s_tx_active) {
    ws_tx_free_item(&s_tx_active_item);
    s_tx_active = false;
  }
  owner_unlock();
}

uint32_t ws_transport_tx_slots_free(void) {
  return (uint32_t)uxQueueSpacesAvailable(s_tx_q);
}

bool ws_transport_drain_rx(void) {
  WsRxItem item{};
  if (xQueueReceive(s_rx_q, &item, 0) != pdTRUE) {
    return false;
  }
  if (item.session != s_ws_session) {
    log_info("[WS_TRANSPORT] drop stale RX type=%u session=%u (cur=%u)", (unsigned)item.type,
             (unsigned)item.session, (unsigned)s_ws_session);
    free(item.data);
    return true;
  }
  if (item.type == WStype_CONNECTED) {
    s_session_has_connected = true;
    log_info("[WS_TRANSPORT] connected (await ready)");
    free(item.data);
    return true;
  }
  if (item.type == WStype_DISCONNECTED) {
    if (!s_session_has_connected) {
      log_info("[WS_TRANSPORT] drop pre-connect DISCONNECTED session=%u", (unsigned)s_ws_session);
      free(item.data);
      return true;
    }
    mic_set_ws_state(kMicWsError);
    pb_runtime_notify_link_down();
    free(item.data);
    return true;
  }
  if (item.type == WStype_BIN) {
    /* 仅在未 ready 时窥探 ready；已 ready 后直接交 pb，避免每帧二次 deserialize。 */
    if (asr_ws_state() != 0 && item.data && item.len >= 5) {
      PackedFrame peek;
      if (parse_packed_frame(item.data, item.len, peek)) {
        const String t = peek.doc["type"].is<String>() ? peek.doc["type"].as<String>() : String("");
        if (t == "ready") {
          asr_ws_note_ready();
          (void)opus_codec_decode_init();
          if (!s_boot_connect_sent) {
            if (ws_transport_enqueue_state("{\"type\":\"boot_connect\"}")) {
              s_boot_connect_sent = true;
              log_info("[WS_TRANSPORT] ready → boot_connect enqueued (first power-on)");
            } else {
              log_warn("[WS_TRANSPORT] ready → boot_connect enqueue failed");
            }
          } else {
            log_info("[WS_TRANSPORT] ready (reconnect, skip boot_connect)");
          }
          free(item.data);
          return true;
        }
      }
    }
    if (!pb_runtime_enqueue_frame(item.data, item.len)) {
      free(item.data);
    }
    return true;
  }
  if (item.type == WStype_TEXT) {
    log_warn("[WS_TRANSPORT] ignore legacy TEXT len=%u (expect packed BIN)", (unsigned)item.len);
    free(item.data);
    return true;
  }
  if (item.type == WStype_FRAGMENT_BIN_START || item.type == WStype_FRAGMENT ||
      item.type == WStype_FRAGMENT_FIN) {
    log_warn("[WS_TRANSPORT] FRAGMENT type=%d chunk_len=%u — firmware only handles single "
             "WStype_BIN",
             (int)item.type, (unsigned)item.len);
    free(item.data);
    return true;
  }
  free(item.data);
  return true;
}

extern "C" void deskbot_ws_transport_write_pump(void) {
  write_pump_impl();
}
