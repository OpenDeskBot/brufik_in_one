#pragma once

#include <Adafruit_GFX.h>
#include <stdint.h>

/** 在 GFX/canvas 上绘制 UTF-8 文本（含 GB2312 汉字）。
 *  x,y：与 Adafruit setCursor 一致，为字形框左上角。
 *  text_size：1–3；ASCII 走 GFX setTextSize，CJK 为文泉驿 12px 最近邻放大。 */
void display_text_draw(Adafruit_GFX* gfx, int16_t x, int16_t y, const char* utf8, uint8_t text_size,
                         uint16_t rgb565);

/** 当前字库下单行高度（像素）= 12 * text_size，供 boot / println 行距。 */
int16_t display_text_line_height(uint8_t text_size);
