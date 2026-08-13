#pragma once

#include <Arduino.h>
#include <Adafruit_GFX.h>
#include "deskbot_config.h"
#include "display_panel.h"
#include "pb_model.h"

#define SCREEN_WIDTH  DESKBOT_DISPLAY_WIDTH
#define SCREEN_HEIGHT DESKBOT_DISPLAY_HEIGHT

#define DESKBOT_DISPLAY_BOOT_SX 8
#define DESKBOT_DISPLAY_BOOT_SY0 8
#define DESKBOT_DISPLAY_BOOT_TEXT_SIZE 1
#define DESKBOT_DISPLAY_BOOT_LINE_DY 14

extern DeskbotDisplay g_display;

/** 初始化 ST7789 面板 & PSRAM canvas。 */
void setup_display();

/** 创建渲染任务及其队列/信号量。 */
void task_setup_display();

/** 开机引导绘制：返回 canvas 或面板；clear_black 时整屏刷黑。 */
Adafruit_GFX* display_guide_target_begin(bool clear_black);
void display_guide_target_end();

/** 执行器任务元素 type；cancel 走 xQueue 队尾，作旧/新任务分界。 */
enum DisplayJobType : uint8_t {
  DISPLAY_JOB_CANCEL = 0,
  DISPLAY_JOB_PB_ANIM_FRAMES = 1,
};

/** 提交 pb_anim_frame[]；frames 所有权转移给渲染任务。 */
void display_render_submit_pb_anim_frames_owned(pb_anim_frame* frames, size_t frame_count,
                                                bool wait_done = false);

/**
 * 打断：置 need_cancel，再队尾入队 type=cancel。
 * 正在渲染的插值循环在下一短 delay 经 poll_cancel 退出。
 */
void display_abort();
/** 同 display_abort（兼容旧名）。 */
void display_render_reset();

/** xQueue 缓冲深度（供 pb 回压）。 */
unsigned display_render_input_queue_depth();
