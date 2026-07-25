#pragma once

#include <WebSocketsClient.h>
#include <stdint.h>

/**
 * camera_ws 连接状态（原子）：
 *  -1 = 未连接 / 错误
 *   0 = 空闲，可发
 *   1 = 正在发送
 */
int camera_ws_state(void);

bool camera_ws_try_begin_send(void);
void camera_ws_end_send_ok(void);
/** 断线或发送失败：state=-1，并 camera_notify_capture(kCamStop)。 */
void camera_ws_mark_disconnected(void);

/**
 * 图片已处理完（成功发送，或队列里扔掉/跳过）：camera_notify_capture(kCamGo)。
 * 不改变 state=-1 的错误态（错误请走 mark_disconnected）。
 */
void camera_ws_on_image_finished(void);

WebSocketsClient* camera_ws_client(void);

/** 仅 ws_transport_task 调用：注册 camera WS + 自动重连 + loop。 */
void ws_camera_auto_reconnect(void);
