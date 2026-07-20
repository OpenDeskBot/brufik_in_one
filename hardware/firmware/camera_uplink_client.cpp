#include "camera_uplink_client.h"

#include "asr_chat_client.h"
#include "camera_ws.h"
#include "common.h"
#include "deskbot_config.h"
#include "deskbot_uplink_state.h"
#include "audio_player.h"
#include "logger.h"

#include <WebSocketsClient.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

CameraUplinkClient cameraUplinkClient;

namespace {

WebSocketsClient s_cam_ws;
static unsigned long s_connected_at_ms = 0;

struct CamTxItem {
  uint8_t* data = nullptr;
  size_t len = 0;
  uint32_t seq = 0;
};

static QueueHandle_t s_cam_tx_q = nullptr;
static bool s_cam_tx_active = false;
static CamTxItem s_cam_tx_active_item{};
static constexpr size_t kMaxJpegTx = 32 * 1024;
static constexpr UBaseType_t kCamTxDepth = 4;

static void pumpWsStackMs(WebSocketsClient* ws, unsigned long ms) {
  const unsigned long deadline = millis() + ms;
  while ((long)(millis() - deadline) < 0) {
    ws->loop();
    taskYIELD();
  }
}

static void cam_tx_free_item(CamTxItem* item) {
  if (item && item->data) {
    heap_caps_free(item->data);
    item->data = nullptr;
    item->len = 0;
  }
}

static bool cam_tx_queue_init(void) {
  if (s_cam_tx_q) {
    return true;
  }
  s_cam_tx_q = xQueueCreate(kCamTxDepth, sizeof(CamTxItem));
  return s_cam_tx_q != nullptr;
}

static bool cam_tx_enqueue_copy(const uint8_t* jpeg, size_t jpeg_len, uint32_t seq) {
  if (!cam_tx_queue_init() || !jpeg || jpeg_len == 0 || jpeg_len > kMaxJpegTx) {
    return false;
  }
  CamTxItem item{};
  item.data = (uint8_t*)heap_caps_malloc(jpeg_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!item.data) {
    item.data = (uint8_t*)malloc(jpeg_len);
  }
  if (!item.data) {
    return false;
  }
  memcpy(item.data, jpeg, jpeg_len);
  item.len = jpeg_len;
  item.seq = seq;
  if (xQueueSend(s_cam_tx_q, &item, 0) != pdTRUE) {
    CamTxItem drop{};
    if (xQueueReceive(s_cam_tx_q, &drop, 0) == pdTRUE) {
      cam_tx_free_item(&drop);
      if (xQueueSend(s_cam_tx_q, &item, 0) == pdTRUE) {
        log_warn("[CAM_WS] TX queue full, dropped oldest");
        return true;
      }
    }
    cam_tx_free_item(&item);
    log_warn("[CAM_WS] TX queue full, drop seq=%u", (unsigned)seq);
    return false;
  }
  return true;
}

static bool send_jpeg_frame(const uint8_t* data, size_t len) {
  if (!data || len == 0) {
    return false;
  }
  /* QVGA JPEG ~7KB：整帧一次 sendBIN，避免多段 WS 帧在等 ACK 时 write 超时。 */
  s_cam_ws.loop();
  asr_chat_cooperative_pump();
  if (!s_cam_ws.sendBIN(data, len)) {
    return false;
  }
  s_cam_ws.loop();
  asr_chat_cooperative_pump();
  return true;
}

static bool cam_tx_queue_busy(void) {
  if (!s_cam_tx_q) {
    return false;
  }
  return s_cam_tx_active || uxQueueMessagesWaiting(s_cam_tx_q) > 0;
}

static bool defer_camera_network(void) {
  return asr_chat_voice_uplink_busy();
}

}  // namespace

void camera_uplink_write_pump(void) {
  s_cam_ws.loop();
  cameraUplinkClient.drainTx();
}

void camera_uplink_pump_only(void) {
  s_cam_ws.loop();
}

bool CameraUplinkClient::isCapturePaused() const {
  return defer_camera_network() || deskbot_uplink_speaker_audible() ||
         audio_play_speaker_busy() || tx_active_ || cam_tx_queue_busy();
}

void CameraUplinkClient::registerHandlers() {
  if (handlers_registered_) {
    return;
  }
  handlers_registered_ = true;
  s_cam_ws.onEvent([this](WStype_t type, uint8_t* payload, size_t length) {
    if (type == WStype_CONNECTED) {
      ready_ = false;
      s_connected_at_ms = millis();
      return;
    }
    if (type == WStype_DISCONNECTED) {
      ready_ = false;
      needs_reconnect_ = true;
      connect_in_progress_ = false;
      connect_started_ms_ = 0;
      s_connected_at_ms = 0;
      discardTxQueue();
      return;
    }
    if (type == WStype_TEXT && payload && length > 0) {
      const char* txt = reinterpret_cast<const char*>(payload);
      if (strstr(txt, "\"type\":\"ready\"") != nullptr ||
          strstr(txt, "\"type\": \"ready\"") != nullptr) {
        ready_ = true;
        needs_reconnect_ = false;
        log_warn("[CAM_WS] ready received");
      }
    }
  });
}

void CameraUplinkClient::discardTxQueue() {
  if (!s_cam_tx_q) {
    return;
  }
  CamTxItem item{};
  while (xQueueReceive(s_cam_tx_q, &item, 0) == pdTRUE) {
    cam_tx_free_item(&item);
  }
  if (s_cam_tx_active) {
    cam_tx_free_item(&s_cam_tx_active_item);
    s_cam_tx_active = false;
  }
  tx_active_ = false;
}

void CameraUplinkClient::drainTx() {
  if (defer_camera_network()) {
    return;
  }
  if (!s_cam_ws.isConnected() || !ready_ || needs_reconnect_) {
    return;
  }
  if (s_cam_tx_active) {
    return;
  }
  if (!s_cam_tx_q) {
    return;
  }
  CamTxItem item{};
  if (xQueueReceive(s_cam_tx_q, &item, 0) != pdTRUE) {
    return;
  }
  s_cam_tx_active_item = item;
  s_cam_tx_active = true;
  tx_active_ = true;

  static uint32_t s_last_log_seq = 0;
  if (item.seq <= 1u || item.seq % 30u == 0u || item.seq != s_last_log_seq) {
    s_last_log_seq = item.seq;
    log_warn("[CAM_WS] sendBIN seq=%u jpeg=%uB", (unsigned)item.seq, (unsigned)item.len);
  }

  const unsigned long t0 = millis();
  const bool ok = send_jpeg_frame(s_cam_tx_active_item.data, s_cam_tx_active_item.len);
  const unsigned long send_ms = millis() - t0;
  bool success = ok;
  if (!ok) {
    pump();
    if (s_cam_ws.isConnected()) {
      log_warn("[CAM_WS] sendBIN fail ms=%lu bytes=%u, reconnect",
               (unsigned long)send_ms, (unsigned)s_cam_tx_active_item.len);
      s_cam_ws.disconnect();
      ready_ = false;
      needs_reconnect_ = true;
      connect_in_progress_ = false;
      connect_started_ms_ = 0;
    }
  } else if (send_ms > 800UL) {
    log_warn("[CAM_WS] sendBIN slow ms=%lu bytes=%u",
             (unsigned long)send_ms, (unsigned)s_cam_tx_active_item.len);
  }

  cam_tx_free_item(&s_cam_tx_active_item);
  s_cam_tx_active = false;
  tx_active_ = cam_tx_queue_busy();

  if (!success) {
    backoff_until_ms_ = millis() + 1000UL;
    return;
  }
  backoff_until_ms_ = 0;
  needs_reconnect_ = false;
  last_uplink_ms_ = millis();
}

void CameraUplinkClient::pump() {
  s_cam_ws.loop();
}

void CameraUplinkClient::onLinkDown(const char* why) {
  (void)why;
  ready_ = false;
  needs_reconnect_ = true;
  connect_in_progress_ = false;
  connect_started_ms_ = 0;
  discardTxQueue();
  s_cam_ws.disconnect();
}

void CameraUplinkClient::onLinkUp() {
  reconnect_backoff_ms_ = 2000;
  last_reconnect_attempt_ms_ = 0;
  needs_reconnect_ = true;
  connect_in_progress_ = false;
  connect_started_ms_ = 0;
}

bool CameraUplinkClient::connect() {
  if (WiFi.status() != WL_CONNECTED || !deskbot_camera_uplink_enabled()) {
    connect_in_progress_ = false;
    connect_started_ms_ = 0;
    return false;
  }
  if (s_cam_ws.isConnected() && ready_ && !needs_reconnect_) {
    connect_in_progress_ = false;
    return true;
  }
  if (!deskbot_api_key_configured() || DESKBOT_WS_HOST[0] == '\0') {
    return false;
  }

  registerHandlers();
  cam_tx_queue_init();

  if (!connect_in_progress_) {
    s_cam_ws.disconnect();
    pumpWsStackMs(&s_cam_ws, 100);
    ready_ = false;
    s_connected_at_ms = 0;
    discardTxQueue();

    char path[80];
    snprintf(path, sizeof(path), "%s?device_id=%s", DESKBOT_CAMERA_WS_PATH, get_device_id());
    char auth_header[96];
    snprintf(auth_header, sizeof(auth_header), "X-API-Key: %s", DESKBOT_API_KEY);
    s_cam_ws.setExtraHeaders(auth_header);
    log_warn("[CAM_WS] connecting ws://%s:%u%s", DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT, path);

    s_cam_ws.setReconnectInterval(500);
    s_cam_ws.begin(DESKBOT_WS_HOST, DESKBOT_WS_PORT, path);
    connect_in_progress_ = true;
    connect_started_ms_ = millis();
    return false;
  }

  pump();
  const unsigned long elapsed = millis() - connect_started_ms_;
  if (s_cam_ws.isConnected()) {
    if (!ready_ && s_connected_at_ms != 0 && (millis() - s_connected_at_ms) > 3000UL) {
      log_warn("[CAM_WS] no ready JSON, continue anyway");
      ready_ = true;
    }
    if (ready_) {
      s_cam_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
      needs_reconnect_ = false;
      connect_in_progress_ = false;
      log_warn("[CAM_WS] connected ws://%s:%u%s", DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT,
               DESKBOT_CAMERA_WS_PATH);
      return true;
    }
    if (elapsed > (unsigned long)DESKBOT_WS_CONNECT_TIMEOUT_MS) {
      log_warn("[CAM_WS] ready timeout after connect");
      ready_ = true;
      s_cam_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
      needs_reconnect_ = false;
      connect_in_progress_ = false;
      return true;
    }
    return false;
  }

  if (elapsed > (unsigned long)DESKBOT_WS_CONNECT_TIMEOUT_MS) {
    log_warn("[CAM_WS] connect timeout (TCP/WS handshake)");
    s_cam_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
    s_cam_ws.disconnect();
    needs_reconnect_ = true;
    connect_in_progress_ = false;
    connect_started_ms_ = 0;
    return false;
  }
  return false;
}

void CameraUplinkClient::maintainConnection() {
  if (WiFi.status() != WL_CONNECTED || !deskbot_camera_uplink_enabled()) {
    return;
  }
  if (defer_camera_network()) {
    return;
  }
  if (s_cam_ws.isConnected()) {
    if (ready_) {
      return;
    }
    pump();
    if (s_connected_at_ms != 0 && (millis() - s_connected_at_ms) > 3000UL) {
      log_warn("[CAM_WS] connected pending ready timeout, proceed");
      ready_ = true;
      needs_reconnect_ = false;
      connect_in_progress_ = false;
    }
    return;
  }
  const unsigned long now = millis();
  if (connect_in_progress_) {
    (void)connect();
    return;
  }
  if (last_reconnect_attempt_ms_ != 0 &&
      (now - last_reconnect_attempt_ms_) < reconnect_backoff_ms_) {
    return;
  }
  last_reconnect_attempt_ms_ = now;
  if (connect()) {
    reconnect_backoff_ms_ = 2000;
  } else if (!connect_in_progress_) {
    if (reconnect_backoff_ms_ < 30000UL) {
      reconnect_backoff_ms_ *= 2;
      if (reconnect_backoff_ms_ > 30000UL) {
        reconnect_backoff_ms_ = 30000UL;
      }
    }
  }
}

bool CameraUplinkClient::canUpload() const {
  if (!deskbot_camera_uplink_enabled()) {
    return false;
  }
  if (!s_cam_ws.isConnected() || !ready_ || needs_reconnect_) {
    return false;
  }
  if (backoff_until_ms_ != 0 && millis() < backoff_until_ms_) {
    return false;
  }
  if (!s_cam_tx_q || uxQueueSpacesAvailable(s_cam_tx_q) == 0) {
    return false;
  }
  return true;
}

bool CameraUplinkClient::tryUploadFrame() {
  if (!canUpload()) {
    return false;
  }
  const uint8_t* jpeg_buf = nullptr;
  size_t jpeg_len = 0;
  uint32_t jpeg_seq = 0;
  if (!camera_ws_take_frame(&jpeg_buf, &jpeg_len, &jpeg_seq)) {
    return false;
  }
  const bool enq = cam_tx_enqueue_copy(jpeg_buf, jpeg_len, jpeg_seq);
  camera_ws_release_frame();
  if (enq) {
    last_capture_ms_ = millis();
  }
  return enq;
}

bool CameraUplinkClient::tryUploadFrameIfDue() {
  unsigned long interval = (unsigned long)DESKBOT_CAMERA_UPLINK_INTERVAL_MS;
  if (deskbot_uplink_capture_allowed()) {
    interval = (unsigned long)DESKBOT_CAMERA_UPLINK_INTERVAL_DURING_LISTEN_MS;
  }
  if (last_capture_ms_ != 0 && (millis() - last_capture_ms_) < interval) {
    return false;
  }
  return tryUploadFrame();
}

void CameraUplinkClient::serviceLoop() {
  if (!deskbot_camera_uplink_enabled()) {
    return;
  }
  registerHandlers();
  cam_tx_queue_init();
  pump();
  if (defer_camera_network()) {
    return;
  }
  maintainConnection();
  drainTx();
  if (canUpload()) {
    tryUploadFrameIfDue();
  }
}
