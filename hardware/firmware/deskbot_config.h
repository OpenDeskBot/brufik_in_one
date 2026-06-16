#pragma once

#include <stddef.h>
#include <driver/gpio.h>

/* ========== 烧录前可改：网络 ==========
 * 默认连 deskbot_wifi；也可改成你的路由器 SSID/密码。
 * 若 SSID 留空 → 热点 Deskbot_Rom，http://192.168.4.1/ 配网；NVS 已存凭证优先。
 * WS host 留空 → 禁用 WebSocket 上行。
 */
#define WIFI_DEFAULT_SSID "deskbot_wifi"
#define WIFI_DEFAULT_PASSWORD "hello2026"

#define DESKBOT_WS_HOST "39.107.38.241"
#define DESKBOT_WS_PORT 9000

/* 服务端 WebSocket 鉴权 Key（odk_... 或 odk_free_...）。留空则无法连接 /asr_chat。 */
#define DESKBOT_API_KEY "odk_free_k60xNgMyI6A-09nP1Tc6gQ67tnHPcMgF"

#define ASR_CHAT_HOST DESKBOT_WS_HOST
#define ASR_CHAT_PORT DESKBOT_WS_PORT

static inline bool deskbot_ws_configured(void) {
  return DESKBOT_WS_HOST[0] != '\0';
}

static inline bool deskbot_api_key_configured(void) {
  return DESKBOT_API_KEY[0] != '\0';
}

/* ========== 硬件接线（Seeed XIAO ESP32S3 Sense）==========
 * 焊盘: D0=1 D1=2 D2=3 D3=4 D4=5 D5=6 D6=43 D7=44 D8=7 D9=8 D10=9
 * 图纸 IO8/IO3 = GPIO 编号，非丝印 D8/D3。
 * LCD：微雪 1.83" ST7789P 240×284，RST/BL 接 3.3V（不经 MCU GPIO）
 */

#define DESKBOT_LCD_MOSI 9
#define DESKBOT_LCD_SCK  7
#define DESKBOT_LCD_CS   2
#define DESKBOT_LCD_DC   3

#define DESKBOT_LCD_WIDTH 240
#ifndef DESKBOT_LCD_HEIGHT
#define DESKBOT_LCD_HEIGHT 284
#endif
#ifndef DESKBOT_LCD_ROW_OFFSET
#define DESKBOT_LCD_ROW_OFFSET 36
#endif
#ifndef DESKBOT_LCD_COL_OFFSET
#define DESKBOT_LCD_COL_OFFSET 0
#endif

#ifndef DESKBOT_LCD_TOP_SAFE_PX
#define DESKBOT_LCD_TOP_SAFE_PX 4
#endif

#define DESKBOT_PB_COORD_W DESKBOT_LCD_HEIGHT
#define DESKBOT_PB_COORD_H 240
#ifndef DESKBOT_LCD_CANVAS_X0
#define DESKBOT_LCD_CANVAS_X0 ((DESKBOT_LCD_HEIGHT - DESKBOT_PB_COORD_W) / 2)
#endif
#ifndef DESKBOT_LCD_ROT3_XSTART_ADJ
#define DESKBOT_LCD_ROT3_XSTART_ADJ (-18)
#endif

#define DESKBOT_DRAW_W DESKBOT_PB_COORD_W
#define DESKBOT_DRAW_H DESKBOT_PB_COORD_H

/* 舵机 PWM：丝印 D6=GPIO43，D7=GPIO44（芯片默认 UART0，Bootloader 日志易致误动作）
 * 左右(X) → D7/44 小舵机；上下(Y) → D6/43 大舵机
 * 保护：custom bootloader + servo_early_init constructor + setup claim LOW */
#ifndef DESKBOT_ROM_X_PIN
#define DESKBOT_ROM_X_PIN 44
#endif
#ifndef DESKBOT_ROM_Y_PIN
#define DESKBOT_ROM_Y_PIN 43
#endif

#ifndef DESKBOT_AUDIO_PLAY_VOLUME
#define DESKBOT_AUDIO_PLAY_VOLUME 0.85f
#endif

#define DESKBOT_ROM_MAX98357_DIN  GPIO_NUM_1
#define DESKBOT_ROM_MAX98357_BCLK GPIO_NUM_6
#define DESKBOT_ROM_MAX98357_LRC  GPIO_NUM_5
#define DESKBOT_ROM_MAX98357_SD   GPIO_NUM_NC
#define DESKBOT_ROM_MAX98357_GAIN GPIO_NUM_NC

#define DESKBOT_PDM_MIC_CLK  GPIO_NUM_42
#define DESKBOT_PDM_MIC_DATA GPIO_NUM_41
#define DESKBOT_PDM_VOICE_MARGIN             220
#define DESKBOT_PDM_VOICE_HANGOVER_MARGIN    180
#define DESKBOT_PDM_VOICE_TRIGGER_RATIO_NUM    105
#define DESKBOT_PDM_VOICE_TRIGGER_RATIO_DEN  100

static inline size_t deskbot_pdm_voice_trigger_thr(size_t ema) {
  const size_t t_delta = ema + (size_t)DESKBOT_PDM_VOICE_MARGIN;
  const size_t t_ratio =
      (ema * (size_t)DESKBOT_PDM_VOICE_TRIGGER_RATIO_NUM) / (size_t)DESKBOT_PDM_VOICE_TRIGGER_RATIO_DEN;
  return (t_delta < t_ratio) ? t_delta : t_ratio;
}

static inline size_t deskbot_pdm_voice_hangover_thr(size_t ema) {
  return ema + (size_t)DESKBOT_PDM_VOICE_HANGOVER_MARGIN;
}
#define DESKBOT_PDM_EMA_QUIET_RATIO_NUM      102
#define DESKBOT_PDM_EMA_QUIET_RATIO_DEN      100
#define DESKBOT_PDM_VOICE_TRIGGER_FRAMES     1
#define DESKBOT_PDM_VOICE_THRESHOLD_MAX      24000
#define DESKBOT_PDM_PRE_VOICE_FRAMES         50
#define DESKBOT_PDM_SILENCE_END_MS           2000

