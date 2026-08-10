#pragma once

#include <Arduino.h>
#include <Adafruit_GFX.h>
#include "deskbot_config.h"
#include "display_panel.h"
#include "pb_model.h"

#define SCREEN_WIDTH  DESKBOT_DISPLAY_WIDTH
#define SCREEN_HEIGHT DESKBOT_DISPLAY_HEIGHT

extern DeskbotDisplay g_display;

void setup_display();

void display_clear_timed();
void display_flush_timed();

void display_print(String text, int delay_time = 100);
void display_println(String text, int delay_time = 100);
/** 在服务端 280×240 坐标 (sx,sy) 处输出一行（随 ROTATION 映射到物理屏） */
void display_println_server(int16_t sx, int16_t sy, String text, int delay_time = 100);
void display_text_layout_reset(int16_t sx = 8, int16_t sy = 8);

#define DESKBOT_DISPLAY_BOOT_SX 8
#define DESKBOT_DISPLAY_BOOT_SY0 8
#define DESKBOT_DISPLAY_BOOT_TEXT_SIZE 1
#define DESKBOT_DISPLAY_BOOT_LINE_DY 14

/** 清空 boot 叠字 canvas 背景标记，下次 display_println_server 会整屏刷黑后重绘 */
void display_boot_screen_reset();

/** 开机引导绘制：返回 canvas 或面板；clear_black 时整屏刷黑。 */
Adafruit_GFX* display_guide_target_begin(bool clear_black);
void display_guide_target_end();

/** 执行器任务元素 type；cancel 走 xQueue 队尾，作旧/新任务分界。 */
enum DisplayJobType : uint8_t {
  DISPLAY_JOB_CANCEL = 0,
  DISPLAY_JOB_PB_ANIM_FRAMES = 1,
};

void task_setup_display();

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

/** 麦克风上行是否有效：屏顶麦克风图标，绿=开麦，红=关麦（跟 mic_capture_allowed）。 */
bool deskbot_mic_uplink_active(void);

