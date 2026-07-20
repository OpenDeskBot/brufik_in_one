#pragma once

#include <stdint.h>

/** 初始化 OV2640（esp_camera）。失败返回 false，此时勿调用 task_setup_camera。 */
bool setup_camera();

/**
 * 启动相机上传任务：按配置频率抓 JPEG，经独立 /camera_uplink WebSocket 发送。
 * 须在 setup_camera 成功之后调用；网络错误则跳过本周期，等下一轮。
 * 若 DESKBOT_CAMERA_UPLINK_ENABLED=0 则直接返回。
 */
void task_setup_camera();

/** 动态调整上传帧率（服务端 pb cam_fps）；fps==0 忽略。 */
void camera_set_fps(uint32_t fps);
