#pragma once

#include <WebSocketsClient.h>
#include <stdint.h>

/* 单帧 WS 入站上限：platformio.ini WEBSOCKETS_MAX_DATA_SIZE（默认 1MiB）；须大于 PB PCM chunk。 */
#if !defined(WEBSOCKETS_MAX_DATA_SIZE) || WEBSOCKETS_MAX_DATA_SIZE < (200 * 1024)
#error WEBSOCKETS_MAX_DATA_SIZE must be >= 200KiB; set -DWEBSOCKETS_MAX_DATA_SIZE in platformio.ini
#endif

/**
 * asr_ws 连接状态（原子）：
 *  -1 = 未连接 / 错误 / 等待 ready
 *   0 = ready，可经 TX 队列发送
 */
int asr_ws_state(void);

/** state==0 且 uplink 允许。 */
bool asr_ws_can_send(void);

WebSocketsClient* asr_ws_client(void);

/** WiFi 断线：立即标记需重连。 */
void asr_ws_on_link_down(const char* why = nullptr);
/** WiFi 恢复：重置 backoff，尽快重连。 */
void asr_ws_on_link_up(void);

/** 强制断开并标记需重连（发送失败 streak / 半开连接）。 */
void asr_ws_force_reconnect(const char* why);

/** TX 发送失败计数；达阈值则 force_reconnect。 */
void asr_ws_note_send_fail(const char* what);
void asr_ws_note_send_ok(void);

/** 收到服务端 ready（打包 BIN 内 JSON）；由 ws_transport 解析后调用。 */
void asr_ws_note_ready(void);

/**
 * setup_ws_transport 时调用：注册 onEvent→RX 队列、向 transport 登记 socket。
 * begin/重连由 ws_asr_auto_reconnect 完成。
 */
void asr_ws_bind_transport(void);

/** 仅 ws_transport_task 调用：自动重连 + loop。 */
void ws_asr_auto_reconnect(void);
