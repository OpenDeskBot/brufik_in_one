#include "ws_uplink.h"

#include "asr_chat_client.h"
#include "camera_uplink_client.h"
#include "deskbot_uplink_state.h"
#include "logger.h"

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <string.h>

namespace {

static uint32_t s_ws_session = 0;
static bool s_session_has_connected = false;

struct WsRxItem {
  WStype_t type = WStype_ERROR;
  uint8_t* data = nullptr;
  size_t len = 0;
  uint32_t session = 0;
};

struct WsTxItem {
  char* json = nullptr;
};

static WebSocketsClient* s_ws = nullptr;
static AsrChatClient* s_client = nullptr;
static QueueHandle_t s_rx_q = nullptr;
static QueueHandle_t s_tx_q = nullptr;
static bool s_tx_active = false;
static WsTxItem s_tx_active_item{};

static constexpr UBaseType_t kRxDepth = 128;
static constexpr UBaseType_t kTxDepth = 3;
static constexpr size_t kMaxRxCopy = 256 * 1024;
static constexpr size_t kMaxTxJson = 16 * 1024;

static void ws_tx_free_item(WsTxItem* item) {
  if (item && item->json) {
    free(item->json);
    item->json = nullptr;
  }
}

static void ws_rx_enqueue(WStype_t type, uint8_t* payload, size_t length) {
  if (!s_rx_q) {
    return;
  }
  WsRxItem item{};
  item.type = type;
  item.session = s_ws_session;
  if (payload != nullptr && length > 0) {
    if (length > kMaxRxCopy) {
      log_warn("[WS_UPLINK] RX drop oversized type=%u len=%u", (unsigned)type, (unsigned)length);
      return;
    }
    item.data = (uint8_t*)malloc(length + 1);
    if (!item.data) {
      log_warn("[WS_UPLINK] RX alloc fail len=%u", (unsigned)length);
      return;
    }
    memcpy(item.data, payload, length);
    item.data[length] = '\0';
    item.len = length;
  }
  if (xQueueSend(s_rx_q, &item, 0) != pdTRUE) {
    log_warn("[WS_UPLINK] RX queue full type=%u", (unsigned)type);
    free(item.data);
  }
}

static void ws_event_shim(WStype_t type, uint8_t* payload, size_t length) {
  if (type == WStype_CONNECTED) {
    deskbot_uplink_set_ws_ready(false);
  }
  ws_rx_enqueue(type, payload, length);
}

}  // namespace

bool ws_uplink_init(WebSocketsClient* ws, AsrChatClient* client) {
  if (!ws || !client) {
    return false;
  }
  s_ws = ws;
  s_client = client;
  if (!s_rx_q) {
    s_rx_q = xQueueCreate(kRxDepth, sizeof(WsRxItem));
  }
  if (!s_tx_q) {
    s_tx_q = xQueueCreate(kTxDepth, sizeof(WsTxItem));
  }
  if (!s_rx_q || !s_tx_q) {
    return false;
  }
  ws->onEvent([](WStype_t type, uint8_t* payload, size_t length) { ws_event_shim(type, payload, length); });
  log_info("[WS_UPLINK] init (session-tagged RX, rx_depth=%u tx_depth=%u)",
           (unsigned)kRxDepth, (unsigned)kTxDepth);
  return true;
}

void ws_uplink_new_session(void) {
  s_ws_session++;
  s_session_has_connected = false;
  log_info("[WS_UPLINK] new session=%u (old RX events will be dropped)", (unsigned)s_ws_session);
}

void ws_uplink_pump(void) {
  if (s_ws) {
    s_ws->loop();
  }
}

bool ws_uplink_send_json(const char* json) {
  return ws_uplink_send(json, nullptr, 0);
}

bool ws_uplink_send(const char* json, const uint8_t* bin, size_t bin_len,
                    const WsSendProfile* profile, bool* out_stream_ok) {
  if (out_stream_ok) {
    *out_stream_ok = true;
  }
  if (!s_ws || !json || !s_ws->isConnected()) {
    return false;
  }
  WsSendProfile def{};
  const WsSendProfile& p = profile ? *profile : def;
  const unsigned long deadline_ms = millis() + (unsigned long)p.max_wall_ms;
  const uint8_t attempts = p.max_attempts > 0 ? p.max_attempts : 1;
  for (uint8_t attempt = 0; attempt < attempts && (long)(millis() - deadline_ms) < 0; ++attempt) {
    ws_uplink_pump();
    if (s_client) {
      ws_uplink_drain_rx(s_client);
    }
    camera_uplink_pump_only();
    if (!s_ws->sendTXT(json)) {
      vTaskDelay(pdMS_TO_TICKS(2));
      taskYIELD();
      continue;
    }
    if (bin == nullptr || bin_len == 0) {
      return true;
    }
    if ((long)(millis() - deadline_ms) >= 0) {
      break;
    }
    ws_uplink_pump();
    if (s_client) {
      ws_uplink_drain_rx(s_client);
    }
    camera_uplink_pump_only();
    if (s_ws->sendBIN(bin, bin_len)) {
      return true;
    }
    log_warn("[WS_UPLINK] binary send failed after JSON (stream corrupted) bin_len=%u", (unsigned)bin_len);
    if (out_stream_ok) {
      *out_stream_ok = false;
    }
    return false;
  }
  log_warn("[WS_UPLINK] send failed after retries bin_len=%u max_ms=%u",
           (unsigned)bin_len, (unsigned)p.max_wall_ms);
  return false;
}

bool ws_uplink_enqueue_text(const char* json) {
  if (!json || !s_tx_q) {
    return false;
  }
  const size_t n = strlen(json);
  if (n == 0 || n > kMaxTxJson) {
    return false;
  }
  WsTxItem item{};
  item.json = (char*)heap_caps_malloc(n + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!item.json) {
    item.json = (char*)malloc(n + 1);
  }
  if (!item.json) {
    return false;
  }
  memcpy(item.json, json, n + 1);
  if (xQueueSend(s_tx_q, &item, 0) != pdTRUE) {
    WsTxItem drop{};
    if (xQueueReceive(s_tx_q, &drop, 0) == pdTRUE) {
      ws_tx_free_item(&drop);
      if (xQueueSend(s_tx_q, &item, 0) == pdTRUE) {
        log_warn("[WS_UPLINK] TX queue full, dropped oldest");
        return true;
      }
    }
    ws_tx_free_item(&item);
    log_warn("[WS_UPLINK] TX queue full, drop new json len=%u", (unsigned)n);
    return false;
  }
  return true;
}

bool ws_uplink_drain_tx(AsrChatClient* client) {
  if (!s_ws || !s_tx_q || !s_ws->isConnected()) {
    return false;
  }
  if (!s_tx_active) {
    WsTxItem item{};
    if (xQueueReceive(s_tx_q, &item, 0) != pdTRUE) {
      return false;
    }
    s_tx_active_item = item;
    s_tx_active = true;
  }
  WsSendProfile profile{};
  profile.max_wall_ms = 2500;
  profile.max_attempts = 1;
  const bool ok = ws_uplink_send(s_tx_active_item.json, nullptr, 0, &profile, nullptr);
  (void)ok;
  (void)client;
  ws_tx_free_item(&s_tx_active_item);
  s_tx_active = false;
  return true;
}

void ws_uplink_discard_tx_queue(void) {
  if (!s_tx_q) {
    return;
  }
  WsTxItem item{};
  while (xQueueReceive(s_tx_q, &item, 0) == pdTRUE) {
    ws_tx_free_item(&item);
  }
  if (s_tx_active) {
    ws_tx_free_item(&s_tx_active_item);
    s_tx_active = false;
  }
}

uint32_t ws_uplink_tx_slots_free(void) {
  if (!s_tx_q) {
    return 0;
  }
  return (uint32_t)uxQueueSpacesAvailable(s_tx_q);
}

bool ws_uplink_camera_tx_busy(void) {
  if (!s_tx_q) {
    return false;
  }
  return s_tx_active || uxQueueMessagesWaiting(s_tx_q) > 0;
}

void ws_uplink_drain_rx(AsrChatClient* client) {
  if (!client || !s_rx_q) {
    return;
  }
  WsRxItem item{};
  while (xQueueReceive(s_rx_q, &item, 0) == pdTRUE) {
    if (item.session == s_ws_session) {
      if (item.type == WStype_CONNECTED) {
        s_session_has_connected = true;
      }
      if (item.type == WStype_DISCONNECTED) {
        if (!s_session_has_connected) {
          log_info("[WS_UPLINK] drop pre-connect DISCONNECTED session=%u (no CONNECTED yet)",
                   (unsigned)s_ws_session);
          free(item.data);
          continue;
        }
        deskbot_uplink_bump_ws_generation();
      }
      client->dispatchWebSocketEvent(item.type, item.data, item.len);
    } else {
      log_info("[WS_UPLINK] drop stale RX type=%u session=%u (cur=%u)",
               (unsigned)item.type, (unsigned)item.session, (unsigned)s_ws_session);
    }
    free(item.data);
  }
}

bool ws_uplink_wait_connected(WebSocketsClient* ws, AsrChatClient* client, unsigned long timeout_ms) {
  if (!ws) {
    return false;
  }
  const unsigned long deadline = millis() + timeout_ms;
  unsigned long last_begin_ms = 0;
  while ((long)(millis() - deadline) < 0) {
    ws_uplink_pump();
    if (client) {
      ws_uplink_drain_rx(client);
    }
    if (ws->isConnected()) {
      return true;
    }
    taskYIELD();
  }
  return ws->isConnected();
}

void ws_uplink_write_pump_impl(void) {
  if (s_ws) {
    s_ws->loop();
  }
}

extern "C" void deskbot_ws_uplink_write_pump(void) {
  ws_uplink_write_pump_impl();
  camera_uplink_write_pump();
}
