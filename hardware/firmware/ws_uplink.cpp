#include "ws_uplink.h"

#include "asr_chat_client.h"
#include "deskbot_uplink_state.h"
#include "logger.h"

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <string.h>

namespace {

/* session_id：每次 ws_uplink_new_session() 递增。
 * 队列中属于旧 session 的事件（forceWsReconnect 后积压的 DISCONNECTED）在 drain 时丢弃，
 * 避免旧连接清理事件覆盖新连接刚建立的 ready 状态。 */
static uint32_t s_ws_session = 0;

/* 当前 session 是否已收到过 CONNECTED 事件。
 * 旧连接 TCP cleanup 的 DISCONNECTED 事件往往在 ws_uplink_new_session() 调用后才到达 lwIP，
 * 被 ws_event_shim 以新 session_id 入队，但其实属于旧连接遗留。
 * 仅当本 session 已见到 CONNECTED 后才派发 DISCONNECTED，避免虚假断线风暴。 */
static bool s_session_has_connected = false;

struct WsRxItem {
  WStype_t type = WStype_ERROR;
  uint8_t* data = nullptr;
  size_t len = 0;
  uint32_t session = 0;
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
  item.session = s_ws_session;
  if (payload != nullptr && length > 0) {
    if (length > kMaxRxCopy) {
      log_warn("[WS_UPLINK] RX drop oversized type=%u len=%u", (unsigned)type, (unsigned)length);
      return;
    }
    item.data = (uint8_t*)malloc(length + 1);  /* +1 for safe %s print */
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
    /* CONNECTED 同步通知 uplink_state，让 wsCanSend() 禁止上行直到 ready TEXT 到来。
     * 注意：generation bump 已移至 drain_rx，仅对当前 session 有效。 */
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
  if (!s_rx_q) {
    return false;
  }
  ws->onEvent([](WStype_t type, uint8_t* payload, size_t length) { ws_event_shim(type, payload, length); });
  log_info("[WS_UPLINK] init (session-tagged RX, rx_depth=%u)", (unsigned)kRxDepth);
  return true;
}

void ws_uplink_new_session(void) {
  s_ws_session++;
  s_session_has_connected = false;
  /* 不清空队列：旧 session 的条目会在 drain 时因 session 不匹配被丢弃，
   * 避免 xQueueReset 与正在入队的 ws_event_shim 出现竞态。 */
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
    *out_stream_ok = true;  /* 默认：流完整 */
  }
  if (!s_ws || !json || !s_ws->isConnected()) {
    return false;
  }
  WsSendProfile def{};
  const WsSendProfile& p = profile ? *profile : def;
  const unsigned long deadline_ms = millis() + (unsigned long)p.max_wall_ms;
  const uint8_t attempts = p.max_attempts > 0 ? p.max_attempts : 1;
  for (uint8_t attempt = 0; attempt < attempts && (long)(millis() - deadline_ms) < 0; ++attempt) {
    s_ws->loop();
    if (!s_ws->sendTXT(json)) {
      /* JSON 帧未发出，流仍完整，可重试。 */
      vTaskDelay(pdMS_TO_TICKS(2));
      taskYIELD();
      continue;
    }
    if (bin == nullptr || bin_len == 0) {
      return true;  /* 纯 JSON 发送成功。 */
    }
    if ((long)(millis() - deadline_ms) >= 0) {
      /* 截止时间已到，JSON 已发出但 binary 尚未开始 → 流已污染。 */
      break;
    }
    s_ws->loop();
    if (s_ws->sendBIN(bin, bin_len)) {
      return true;  /* JSON + binary 均成功。 */
    }
    /* JSON 已发出但 binary 失败：WebSocket 协议流已污染（服务端仍在等待剩余 binary）。
     * 不得重试（再发 JSON 会被服务端误判为 binary 数据），必须立即重置连接。 */
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
    if (item.session == s_ws_session) {
      /* 仅派发当前 session 的事件；generation bump 也在此处做，确保不受旧连接影响。 */
      if (item.type == WStype_CONNECTED) {
        s_session_has_connected = true;
      }
      if (item.type == WStype_DISCONNECTED) {
        if (!s_session_has_connected) {
          /* 本 session 尚未见到 CONNECTED：这是旧连接 TCP cleanup 事件在 session 切换后
           * 延迟到达，以新 session_id 入队，属于虚假断线，直接丢弃。 */
          log_info("[WS_UPLINK] drop pre-connect DISCONNECTED session=%u (no CONNECTED yet)",
                   (unsigned)s_ws_session);
          free(item.data);
          continue;
        }
        deskbot_uplink_bump_ws_generation();
      }
      client->dispatchWebSocketEvent(item.type, item.data, item.len);
    } else {
      /* 丢弃旧 session 的事件（forceWsReconnect 后遗留的 TCP cleanup 等）。 */
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
