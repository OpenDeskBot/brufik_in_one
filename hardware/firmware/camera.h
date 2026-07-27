#pragma once

#include <stdint.h>

/** 抓帧门控：Stop=暂停；Go=允许按 fps 间隔在 ws_transport 循环里拍一张。 */
enum CamNotify : int8_t {
  kCamStop = 0,
  kCamGo = 1,
};

/** 初始化 OV2640（esp_camera）。失败返回 false，此时勿调用 task_setup_camera。 */
bool setup_camera();

/**
 * 标记相机上行就绪（无独立任务；实际抓帧在 ws_transport_task 空档调用
 * camera_try_capture_and_enqueue）。
 */
void task_setup_camera();

/** 动态调整上传帧率（服务端 pb cam_fps）；fps==0 忽略。 */
void camera_set_fps(uint32_t fps);

/** 开/停抓帧门控（atomic；替代旧 notify xQueue）。 */
void camera_notify_capture(CamNotify n);

/**
 * 仅 ws_transport_task 调用：条件满足时抓一帧 JPEG（含舵机/音量）并入 TX。
 * @return 是否成功入队一张。
 */
bool camera_try_capture_and_enqueue(void);
