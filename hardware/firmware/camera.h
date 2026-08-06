#pragma once

#include <stddef.h>
#include <stdint.h>

/** 初始化 OV2640（esp_camera）。失败返回 false，此时勿调用 task_setup_camera。 */
bool setup_camera();

/** 释放相机驱动（幂等）；用于启动诊断后的二次 init。 */
void camera_deinit();

/** 创建 camera_task：按间隔抓帧并入队 ws_transport TX。 */
void task_setup_camera();

/** 动态调整上传帧率（服务端 pb cam_fps）；fps==0 忽略。 */
void camera_set_fps(uint32_t fps);

/**
 * 条件满足时抓一帧 JPEG（含舵机/音量），打包为 u32be+json+bin。
 * @return true 时 *packed 由调用方 free。
 */
bool camera_try_capture_packed(uint8_t** packed, size_t* packed_len);
