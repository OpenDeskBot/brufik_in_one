#ifndef WS_UPLINK_H
#define WS_UPLINK_H

#include <WebSocketsClient.h>
#include <stddef.h>
#include <stdint.h>

class AsrChatClient;

/** 注册 WS 事件 → RX 队列（回调内不跑 pb 逻辑）。 */
bool ws_uplink_init(WebSocketsClient* ws, AsrChatClient* client);

/** 主线程泵 TCP/WS（须与 send 同线程，避免库重入）。 */
void ws_uplink_pump(void);

/** 递增 session 计数：调用后，队列中所有旧事件在 drain_rx 时被丢弃。
 *  应在每次 ws_.begin() 之前调用，防止旧连接 TCP cleanup 覆盖新连接状态。 */
void ws_uplink_new_session(void);

/** 同步发送 JSON；可选紧跟 BIN。profile 为空时用默认（音频等关键上行）。
 *
 *  out_stream_ok（可选输出）：
 *    true  = 未发出任何数据 / 发送前失败，WebSocket 流仍完整，可安全重试。
 *    false = JSON 已发出但 binary 未完整发出，WebSocket 协议流已污染，
 *            调用方必须立即重置 WebSocket 连接，否则服务端协议状态混乱。
 */
struct WsSendProfile {
  unsigned max_wall_ms = 800;
  uint8_t max_attempts = 8;
};

bool ws_uplink_send(const char* json, const uint8_t* bin, size_t bin_len,
                    const WsSendProfile* profile = nullptr,
                    bool* out_stream_ok = nullptr);
bool ws_uplink_send_json(const char* json);

void ws_uplink_drain_rx(AsrChatClient* client);

/** 兼容旧接口：无 TX 队列时为 no-op。 */
void ws_uplink_discard_tx_queue(void);
uint32_t ws_uplink_tx_slots_free(void);

bool ws_uplink_wait_connected(WebSocketsClient* ws, AsrChatClient* client, unsigned long timeout_ms);

#endif
