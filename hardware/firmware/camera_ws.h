#pragma once

#include <stdint.h>
#include <stddef.h>

bool deskbot_vision_uplink_paused(void);

/* 摄像头 JPEG 缓存；帧经 /asr_chat WebSocket 发送（next_bin_len）。
 *
 *   if (camera_ws_take_frame(&buf, &len, &seq)) { … send … camera_ws_release_frame(); }
 */

/** 启动 camera_cap 任务（JPEG 抓帧缓存）；须在 setup_camera 成功之后。 */
void task_setup_camera_capture(void);
bool camera_ws_take_frame(const uint8_t** out_buf, size_t* out_len, uint32_t* out_seq);
void camera_ws_release_frame(void);
/** 丢弃尚未发送的 JPEG（语音 PCM 上行开始时调用，避免错开发射）。 */
void camera_ws_discard_pending(void);

/** 调整上行帧率（fps>0）；默认 10fps，服务端 pb 字段 cam_fps 可动态覆盖。 */
void camera_ws_set_fps(uint32_t fps);
