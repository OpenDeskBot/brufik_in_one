#include "display_text.h"

#include <U8g2_for_Adafruit_GFX.h>
#include <u8g2_fonts.h>

#include <string.h>

/* 文泉驿 12px，覆盖 gb2312b 子集（约 4400+ 字形）；U8g2 按 Unicode 查表，输入须为 UTF-8。 */
#ifndef DESKBOT_DISPLAY_CJK_FONT
#define DESKBOT_DISPLAY_CJK_FONT u8g2_font_wqy12_t_gb2312b
#endif

static U8G2_FOR_ADAFRUIT_GFX s_u8g2;
static Adafruit_GFX*         s_bound_gfx = nullptr;

static bool utf8_has_non_ascii(const char* s) {
  if (!s) {
    return false;
  }
  for (const uint8_t* p = reinterpret_cast<const uint8_t*>(s); *p; p++) {
    if (*p >= 0x80u) {
      return true;
    }
  }
  return false;
}

static void bind_gfx(Adafruit_GFX* gfx) {
  if (!gfx) {
    return;
  }
  if (s_bound_gfx != gfx) {
    s_u8g2.begin(*gfx);
    s_bound_gfx = gfx;
  }
}

void display_text_draw(Adafruit_GFX* gfx, int16_t x, int16_t y, const char* utf8, uint8_t text_size,
                         uint16_t rgb565) {
  if (!gfx || !utf8 || utf8[0] == '\0') {
    return;
  }
  uint8_t sz = text_size ? text_size : 1;
  if (sz > 3) {
    sz = 3;
  }

  if (!utf8_has_non_ascii(utf8)) {
    gfx->setTextSize(sz);
    gfx->setTextColor(rgb565);
    gfx->setCursor(x, y);
    gfx->print(utf8);
    gfx->setTextSize(1);
    return;
  }

  bind_gfx(gfx);
  s_u8g2.setFont(DESKBOT_DISPLAY_CJK_FONT);
  s_u8g2.setForegroundColor(rgb565);
  /* drawUTF8 的 y 为基线；GFX cursor 为顶部 → 加上字模高度对齐。 */
  const int16_t box_h = static_cast<int16_t>(s_u8g2.u8g2.font_info.max_char_height);
  const int16_t baseline_y = y + box_h;
  s_u8g2.drawUTF8(x, baseline_y, utf8);
}

int16_t display_text_line_height(uint8_t text_size) {
  uint8_t sz = text_size ? text_size : 1;
  if (sz > 3) {
    sz = 3;
  }
  /* wqy12 字模盒高度约 12px；size>1 时仍用同一 CJK 字库，行距按倍数估算。 */
  return static_cast<int16_t>(12 * sz);
}
