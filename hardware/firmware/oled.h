#ifndef OLED_H
#define OLED_H

#include "common.h"
#include "deskbot_config.h"
#include "oled_display.h"

#define SCREEN_WIDTH  DESKBOT_LCD_WIDTH
#define SCREEN_HEIGHT DESKBOT_LCD_HEIGHT

extern DeskbotLcd oled;

void setup_oled();

void oled_clear_display_timed();
void oled_display_timed();

void oled_print(String text, int delay_time = 100);
void oled_println(String text, int delay_time = 100);
/** 在服务端 280×240 坐标 (sx,sy) 处输出一行（随 ROTATION 映射到物理屏） */
void oled_println_server(int16_t sx, int16_t sy, String text, int delay_time = 100);
void oled_text_layout_reset(int16_t sx = 8, int16_t sy = 8);

#define DESKBOT_OLED_BOOT_SX 8
#define DESKBOT_OLED_BOOT_SY0 8
#define DESKBOT_OLED_BOOT_TEXT_SIZE 1
#define DESKBOT_OLED_BOOT_LINE_DY 14

/** 清空 boot 叠字 canvas 背景标记，下次 oled_println_server 会整屏刷黑后重绘 */
void oled_boot_screen_reset();

/** 写入第 1/2 行：应用名+版本、设备 ID。 */
void oled_boot_header_lines(char* line1, size_t line1_len, char* line2, size_t line2_len);

/** 开机屏：第 1/2 行固定为版本、设备 ID；第 3/4 行可选状态（nullptr 省略）。 */
void oled_boot_show(const char* status_line3 = nullptr, const char* status_line4 = nullptr);
/** 开机屏三行自定义文案（空指针行跳过）。 */
void oled_boot_show3(const char* line1, const char* line2, const char* line3);
/** 开机屏四行自定义文案（空指针行跳过）。 */
void oled_boot_show4(const char* line1, const char* line2, const char* line3, const char* line4);
/** WiFi 已连接且系统就绪：版本、设备 ID、WiFi 名称+IP、对话提示。 */
void oled_boot_show_ready();

enum OledScene : uint8_t {
  OLED_SCENE_PB_VECTOR_JSON = 0,
  OLED_SCENE_RESET,
};

void display_task_setup();

/** 提交 pb anim[] JSON；播放时长由数组内各段 ms 之和决定（与 chunk_ms 无关）。
 *  anim[k].bg、c 为 RGB565（十进制或 0x 语义同 uint16）；缺省 bg=黑、图元 c=65535（白）。
 *  asset_bufs/asset_lens：本片 assets[] 对应 JPEG 二进制（所有权转移给渲染任务）。 */
void oled_render_submit_pb_vector_json(const char* json, size_t json_len, bool wait_done = false);
void oled_render_submit_pb_vector_json(const char* json, size_t json_len, uint8_t* const* asset_bufs,
                                       const size_t* asset_lens, uint8_t asset_count,
                                       bool wait_done = false);
/** 与上相同，但 json 堆缓冲所有权转移给渲染任务（失败时内部 free）。 */
void oled_render_submit_pb_vector_json_owned(char* json, size_t json_len, uint8_t* const* asset_bufs,
                                             const size_t* asset_lens, uint8_t asset_count,
                                             bool wait_done = false);

void oled_render_reset();

unsigned oled_render_input_queue_depth();

#endif
