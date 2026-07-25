#pragma once

#include <stdint.h>

/** 抓帧任务通知：Stop=暂停截图；Go=允许再截一张（先按 fps delay）。 */
enum CamNotify : int8_t {
  kCamStop = 0,
  kCamGo = 1,
};

/** 初始化 OV2640（esp_camera）。失败返回 false，此时勿调用 task_setup_camera。 */
bool setup_camera();

/**
 * 启动抓帧任务：仅通过通知队列决定是否截图发图，不直接碰 WiFi/WebSocket。
 * 实际发送由 camera_ws + ws_transport owner 完成。
 */
void task_setup_camera();

/** 动态调整上传帧率（服务端 pb cam_fps）；fps==0 忽略。 */
void camera_set_fps(uint32_t fps);

/** 通知抓帧任务：先清空单槽队列再放入 n。 */
void camera_notify_capture(CamNotify n);
