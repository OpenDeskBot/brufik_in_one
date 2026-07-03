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

/** 同步发送 JSON；可选紧跟 BIN。 */
bool ws_uplink_send(const char* json, const uint8_t* bin, size_t bin_len);
bool ws_uplink_send_json(const char* json);

void ws_uplink_drain_rx(AsrChatClient* client);

/** 兼容旧接口：无 TX 队列时为 no-op。 */
void ws_uplink_discard_tx_queue(void);
uint32_t ws_uplink_tx_slots_free(void);

bool ws_uplink_wait_connected(WebSocketsClient* ws, AsrChatClient* client, unsigned long timeout_ms);

#endif
