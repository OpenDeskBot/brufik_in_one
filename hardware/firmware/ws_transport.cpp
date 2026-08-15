#include "ws_transport.h"

#include "deskbot_config.h"
#include "logger.h"
#include "mic.h"
#include "pb_runtime.h"
#include "utils/nvs_config_utils.h"
#include "utils/opus_codec.h"
#include "utils/utils.h"

#include <Arduino.h>
#include <WiFi.h>
#include <atomic>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <string.h>

WsProto server_ws_proto = {};
String server_ws_path;

namespace {

WebSocketsClient ws_client;
std::atomic<int> ws_state{static_cast<int>(WsState::kDisconnected)};
std::atomic<bool> s_app_ready{false};

/** 入队时已打包：u32be(json_len)+json+media，发送只 sendBIN。 */
struct WsTxItem {
  WsTxType type = WsTxType::kState;
  uint8_t* packed = nullptr;
  size_t packed_len = 0;
};

static uint32_t s_ws_session = 0;
static bool s_setup_ok = false;
static QueueHandle_t s_tx_q = nullptr;
static TaskHandle_t s_task = nullptr;
static bool s_boot_connect_sent = false;
static unsigned long s_connect_attempt_ms = 0;
static unsigned long s_last_reconnect_ms = 0;
static uint8_t s_connect_fail_count = 0;
static constexpr unsigned long RECONNECT_MIN_MS = 500;
static constexpr unsigned long RECONNECT_MAX_MS = 60000;

static constexpr UBaseType_t kTxDepth = 64;
/* WebSockets 收发、ArduinoJson 解析都在 ws_transport 任务。 */
static constexpr uint32_t kTaskStack = 32 * 1024;
/* 与 mic(6) 同级：持续上行时 drain 不能被 mic encode 饿死。 */
static constexpr UBaseType_t kTaskPrio = 6;
static constexpr size_t kMaxRxCopy = 256 * 1024;
static constexpr size_t kMaxTxJson = 16 * 1024;
static constexpr size_t kMaxTxAudioBin = 4 * 1024;

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

static void clear_tx_queue(void) {
  WsTxItem item{};
  while (xQueueReceive(s_tx_q, &item, 0) == pdTRUE) {
    ws_tx_free_item(&item);
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
  log_warn("[WS_TRANSPORT] TX queue full type=%u len=%u", (unsigned)item->type, (unsigned)item->packed_len);
  ws_tx_free_item(item);
  return false;
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

static void task_loop_ws_transport(void* /*arg*/) {
  for (;;) {
    ws_transport_ensure_connected();
    ws_client.loop();
    const bool sent = ws_transport_drain_tx();
    /* 空闲让出 CPU，避免同核饿死 WiFi/其它任务；有 TX 或建连中则只 yield。 */
    if (sent || ws_transport_state() == WsState::kConnecting) {
      taskYIELD();
    } else {
      vTaskDelay(pdMS_TO_TICKS(2));
    }
  }
}

}  // namespace

bool setup_ws_transport(void) {
  if (s_setup_ok) {
    log_info("[WS_TRANSPORT] setup already ok");
    return true;
  }

  server_ws_proto = {};
  server_ws_path = "";
  char base_url[128];
  base_url[0] = '\0';
  const char* active = nvs_ws_get_active_id();
  if (strcmp(active, "builtin") == 0) {
    if (DESKBOT_WS_HOST[0] != '\0') {
      snprintf(base_url, sizeof(base_url), "ws://%s:%u", DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT);
    }
  } else {
    (void)nvs_ws_get_custom_url(active, base_url, sizeof(base_url));
  }
  if (base_url[0] != '\0' && parse_ws_proto(base_url, server_ws_proto)) {
    if (server_ws_proto.path[0] == '\0') {
      server_ws_path = String("/asr_chat?device_id=") + get_device_id() + "&version=" + VERSION;
    } else {
      server_ws_path = String(server_ws_proto.path) + "/asr_chat?device_id=" + get_device_id() +
                       "&version=" + VERSION;
    }
    log_info("[WS_TRANSPORT] server %s://%s:%u path=%s", server_ws_proto.is_wss ? "wss" : "ws",
             server_ws_proto.host, (unsigned)server_ws_proto.port, server_ws_path.c_str());
  } else {
    server_ws_proto = {};
    server_ws_path = "";
    log_warn("[WS_TRANSPORT] no configured server url");
  }

  s_tx_q = xQueueCreate(kTxDepth, sizeof(WsTxItem));
  ws_client.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
    if (type == WStype_CONNECTED) {
      ws_state.store(static_cast<int>(WsState::kConnected), std::memory_order_release);
      s_connect_fail_count = 0;
      s_last_reconnect_ms = 0;
      s_app_ready.store(true, std::memory_order_release);
      s_connect_attempt_ms = 0;
      mic_set_ws_state(kMicWsOk);
      (void)opus_codec_decode_init();
      if (!s_boot_connect_sent) {
        if (ws_transport_enqueue_state("{\"type\":\"boot_connect\"}")) {
          s_boot_connect_sent = true;
          log_info("[WS_TRANSPORT] connected → boot_connect enqueued (first power-on)");
        } else {
          log_warn("[WS_TRANSPORT] connected → boot_connect enqueue failed");
        }
      }
      log_info("[WS_TRANSPORT] connected");
      return;
    }
    if (type == WStype_DISCONNECTED) {
      ws_state.store(static_cast<int>(WsState::kDisconnected), std::memory_order_release);
      s_app_ready.store(false, std::memory_order_release);
      s_connect_attempt_ms = 0;
      mic_set_ws_state(kMicWsError);
      log_warn("[WS_TRANSPORT] disconnected");
      return;
    }
    if (type == WStype_BIN) {
      if (payload == nullptr || length == 0) {
        return;
      }
      if (length > kMaxRxCopy) {
        log_warn("[WS_TRANSPORT] RX drop oversized len=%u", (unsigned)length);
        return;
      }
      uint8_t* data = (uint8_t*)rx_alloc(length + 1);
      if (!data) {
        log_warn("[WS_TRANSPORT] RX alloc fail len=%u", (unsigned)length);
        return;
      }
      memcpy(data, payload, length);
      data[length] = '\0';
      static uint32_t s_last_rx_hand_ms = 0;
      const uint32_t now = millis();
      const uint32_t gap = (s_last_rx_hand_ms == 0) ? 0 : (now - s_last_rx_hand_ms);
      s_last_rx_hand_ms = now;
      log_warn("[PB_LAT] rx_to_pb len=%u gap_ms=%u", (unsigned)length, (unsigned)gap);
      if (!pb_runtime_enqueue_frame(data, length)) {
        free(data);
        log_warn("[PB_LAT] rx_to_pb DROP (pb not ready or frame_q full) len=%u",
                 (unsigned)length);
      }
      return;
    }
    log_warn("[WS_TRANSPORT] ignore non-BIN type=%d len=%u", (int)type, (unsigned)length);
  });
  s_setup_ok = true;
  log_info("[WS_TRANSPORT] setup ok TX depth=%u", (unsigned)kTxDepth);
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
  BaseType_t rc = utils_task_create_pinned(task_loop_ws_transport, "ws_transport", kTaskStack, nullptr,
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

WsState ws_transport_state(void) {
  return static_cast<WsState>(ws_state.load(std::memory_order_acquire));
}

bool ws_transport_ok(void) {
  return ws_transport_state() == WsState::kConnected;
}

bool ws_transport_ready(void) {
  return s_app_ready.load(std::memory_order_acquire);
}

void ws_transport_ensure_connected(void) {
  if (WiFi.status() != WL_CONNECTED) {
    ws_state.store(static_cast<int>(WsState::kDisconnected), std::memory_order_release);
    s_connect_attempt_ms = 0;
    return;
  }
  const WsState st = ws_transport_state();
  if (st == WsState::kConnected) {
    return;
  }
  if (server_ws_proto.host[0] == '\0' || server_ws_path.length() == 0) {
    s_connect_attempt_ms = 0;
    return;
  }
  /* connecting：只泵 loop，避免每轮 disconnect+begin 打断连接。 */
  if (st == WsState::kConnecting) {
    if (s_connect_attempt_ms != 0 &&
        (millis() - s_connect_attempt_ms) < (unsigned long)DESKBOT_WS_CONNECT_TIMEOUT_MS) {
      return;
    }
    /* 超时：回到 disconnected，下一轮重新 begin。 */
    ws_state.store(static_cast<int>(WsState::kDisconnected), std::memory_order_release);
    s_connect_attempt_ms = 0;
  }

  /* 指数退避：500ms → 1s → 2s → 4s → 8s → 16s → 32s → 60s */
  unsigned long interval = RECONNECT_MIN_MS << s_connect_fail_count;
  if (interval > RECONNECT_MAX_MS) {
    interval = RECONNECT_MAX_MS;
  }
  const unsigned long now = millis();
  if (s_last_reconnect_ms != 0 && (now - s_last_reconnect_ms) < interval) {
    return; /* 还在退避窗口内，等下一轮 */
  }
  s_last_reconnect_ms = now;
  if (s_connect_fail_count < 8) {
    s_connect_fail_count++;
  }

  ws_client.disconnect();
  ws_transport_new_session();
  log_warn("[WS_TRANSPORT] connect %s://%s:%u%s (retry #%u, backoff %lus)",
           server_ws_proto.is_wss ? "wss" : "ws",
           server_ws_proto.host, (unsigned)server_ws_proto.port, server_ws_path.c_str(),
           (unsigned)s_connect_fail_count, (unsigned)(interval / 1000));
  /*
   * 必须在 begin 前设好 interval。库 loop() 里用
   * (millis() - _lastConnectionFail) < _reconnectInterval 抑制重连；
   * begin() 会把 _lastConnectionFail 置 0，若 interval 设成「几天」则
   * (millis()-0) 一直小于 interval，TCP 永远不会发起。
   */
  ws_client.setReconnectInterval(interval);
  if (server_ws_proto.is_wss) {
    ws_client.beginSSL(server_ws_proto.host, server_ws_proto.port, server_ws_path.c_str());
  } else {
    ws_client.begin(server_ws_proto.host, server_ws_proto.port, server_ws_path.c_str());
  }
  ws_state.store(static_cast<int>(WsState::kConnecting), std::memory_order_release);
  s_connect_attempt_ms = millis();
}

bool ws_transport_send(const uint8_t* data, size_t len) {
  if (ws_transport_state() != WsState::kConnected) {
    return false;
  }
  if (!data || len == 0) {
    return false;
  }
  if (ws_client.sendBIN(data, len)) {
    return true;
  }
  ws_state.store(static_cast<int>(WsState::kDisconnected), std::memory_order_release);
  log_warn("[WS_TRANSPORT] sendBIN fail → disconnected len=%u", (unsigned)len);
  return false;
}

void ws_transport_on_link_down(const char* why) {
  log_warn("[WS_TRANSPORT] wifi down (%s)", why ? why : "?");
  ws_client.disconnect();
  ws_state.store(static_cast<int>(WsState::kDisconnected), std::memory_order_release);
  s_app_ready.store(false, std::memory_order_release);
  s_connect_attempt_ms = 0;
  mic_set_ws_state(kMicWsError);
}

void ws_transport_on_link_up(void) {
  log_info("[WS_TRANSPORT] wifi up");
  ws_state.store(static_cast<int>(WsState::kDisconnected), std::memory_order_release);
  s_connect_attempt_ms = 0;
}

void ws_transport_new_session(void) {
  s_ws_session++;
  s_app_ready.store(false, std::memory_order_release);
  mic_set_ws_state(kMicWsError);
  pb_runtime_discard_rx_queue();
  log_info("[WS_TRANSPORT] new session=%u (PB rx queue cleared)", (unsigned)s_ws_session);
}

bool ws_transport_enqueue_state(const char* json) {
  WsTxItem item{};
  if (!build_tx_item(WsTxType::kState, json, nullptr, 0, 0, &item)) {
    return false;
  }
  return enqueue_tx(&item);
}

bool ws_transport_enqueue_audio(const char* json, const uint8_t* bin, size_t bin_len) {
  if (!ws_transport_ok() || !ws_transport_ready()) {
    return false;
  }
  WsTxItem item{};
  if (!build_tx_item(WsTxType::kAudio, json, bin, bin_len, kMaxTxAudioBin, &item)) {
    return false;
  }
  return enqueue_tx(&item);
}

bool ws_transport_enqueue_camera(uint8_t* packed, size_t packed_len) {
  if (!packed || packed_len == 0) {
    free(packed);
    return false;
  }
  if (!ws_transport_ok() || !ws_transport_ready()) {
    free(packed);
    return false;
  }
  WsTxItem item{};
  item.type = WsTxType::kCamera;
  item.packed = packed;
  item.packed_len = packed_len;
  return enqueue_tx(&item);
}

bool ws_transport_drain_tx(void) {
  if (!ws_transport_ok()) {
    clear_tx_queue();
    return false;
  }

  bool sent_any = false;
  WsTxItem item{};
  while (xQueueReceive(s_tx_q, &item, 0) == pdTRUE) {
    const uint32_t t0 = millis();
    const size_t plen = item.packed_len;
    const bool ok = ws_transport_send(item.packed, plen);
    const uint32_t send_ms = millis() - t0;
    if (!ok) {
      log_warn("[WS_TRANSPORT] sendBIN fail type=%u len=%u send_ms=%u", (unsigned)item.type,
               (unsigned)plen, (unsigned)send_ms);
      if (xQueueSendToFront(s_tx_q, &item, 0) != pdTRUE) {
        ws_tx_free_item(&item);
      }
      return sent_any;
    }
    // log_warn("[PB_LAT] tx_send type=%u len=%u send_ms=%u", (unsigned)item.type, (unsigned)plen,
    //          (unsigned)send_ms);
    ws_tx_free_item(&item);
    sent_any = true;
  }
  return sent_any;
}
