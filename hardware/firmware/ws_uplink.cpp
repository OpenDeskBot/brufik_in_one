#include "ws_uplink.h"

#include "asr_chat_client.h"
#include "deskbot_uplink_state.h"
#include "logger.h"

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <string.h>

namespace {

struct WsRxItem {
  WStype_t type = WStype_ERROR;
  uint8_t* data = nullptr;
  size_t len = 0;
};

static WebSocketsClient* s_ws = nullptr;
static AsrChatClient* s_client = nullptr;
static QueueHandle_t s_rx_q = nullptr;

static constexpr UBaseType_t kRxDepth = 32;
static constexpr size_t kMaxRxCopy = 256 * 1024;

static void ws_rx_enqueue(WStype_t type, uint8_t* payload, size_t length) {
  if (!s_rx_q) {
    return;
  }
  WsRxItem item{};
  item.type = type;
  if (payload != nullptr && length > 0) {
    if (length > kMaxRxCopy) {
      log_warn("[WS_UPLINK] RX drop oversized type=%u len=%u", (unsigned)type, (unsigned)length);
      return;
    }
    item.data = (uint8_t*)malloc(length);
    if (!item.data) {
      log_warn("[WS_UPLINK] RX alloc fail len=%u", (unsigned)length);
      return;
    }
    memcpy(item.data, payload, length);
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
  } else if (type == WStype_DISCONNECTED) {
    deskbot_uplink_bump_ws_generation();
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
  if (!s_rx_q) {
    return false;
  }
  ws->onEvent([](WStype_t type, uint8_t* payload, size_t length) { ws_event_shim(type, payload, length); });
  log_info("[WS_UPLINK] init (main-thread pump+send, rx_depth=%u)", (unsigned)kRxDepth);
  return true;
}

void ws_uplink_pump(void) {
  if (s_ws) {
    s_ws->loop();
  }
}

bool ws_uplink_send_json(const char* json) {
  return ws_uplink_send(json, nullptr, 0);
}

bool ws_uplink_send(const char* json, const uint8_t* bin, size_t bin_len) {
  if (!s_ws || !json || !s_ws->isConnected()) {
    return false;
  }
  for (int attempt = 0; attempt < 40; ++attempt) {
    s_ws->loop();
    if (!s_ws->sendTXT(json)) {
      vTaskDelay(pdMS_TO_TICKS(2));
      continue;
    }
    if (bin == nullptr || bin_len == 0) {
      return true;
    }
    s_ws->loop();
    if (s_ws->sendBIN(bin, bin_len)) {
      return true;
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
  log_warn("[WS_UPLINK] send failed after retries bin_len=%u", (unsigned)bin_len);
  return false;
}

void ws_uplink_discard_tx_queue(void) {
  /* 无 TX 队列。 */
}

uint32_t ws_uplink_tx_slots_free(void) {
  return 999;
}

void ws_uplink_drain_rx(AsrChatClient* client) {
  if (!client || !s_rx_q) {
    return;
  }
  WsRxItem item{};
  while (xQueueReceive(s_rx_q, &item, 0) == pdTRUE) {
    client->dispatchWebSocketEvent(item.type, item.data, item.len);
    free(item.data);
  }
}

bool ws_uplink_wait_connected(WebSocketsClient* ws, AsrChatClient* client, unsigned long timeout_ms) {
  if (!ws) {
    return false;
  }
  const unsigned long start = millis();
  while (!ws->isConnected() && (millis() - start) < timeout_ms) {
    ws_uplink_pump();
    if (client) {
      ws_uplink_drain_rx(client);
    }
    taskYIELD();
  }
  return ws->isConnected();
}
