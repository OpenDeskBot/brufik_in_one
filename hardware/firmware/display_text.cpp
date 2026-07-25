#include "display_text.h"

#include <Adafruit_GFX.h>
#include <U8g2_for_Adafruit_GFX.h>
#include <u8g2_fonts.h>

#include <esp_heap_caps.h>
#include <stdlib.h>
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

/** size>1：先 1× 画到临时缓冲，再最近邻放大（文泉驿点阵无内建缩放）。 */
static void draw_cjk_scaled(Adafruit_GFX* gfx, int16_t x, int16_t y, const char* utf8, uint8_t sz,
                            uint16_t rgb565) {
  bind_gfx(gfx);
  s_u8g2.setFont(DESKBOT_DISPLAY_CJK_FONT);
  s_u8g2.setForegroundColor(rgb565);
  const int16_t box_h = static_cast<int16_t>(s_u8g2.u8g2.font_info.max_char_height);
  if (sz <= 1) {
    s_u8g2.drawUTF8(x, y + box_h, utf8);
    return;
  }

  const int16_t tw = static_cast<int16_t>(s_u8g2.getUTF8Width(utf8));
  if (tw <= 0 || box_h <= 0) {
    return;
  }
  const size_t px = static_cast<size_t>(tw) * static_cast<size_t>(box_h);
  uint16_t* tmp =
      static_cast<uint16_t*>(heap_caps_malloc(px * sizeof(uint16_t), MALLOC_CAP_SPIRAM));
  if (!tmp) {
    /* OOM：退回 1×，总比不画好 */
    s_u8g2.drawUTF8(x, y + box_h, utf8);
    return;
  }
  memset(tmp, 0, px * sizeof(uint16_t));

  class ScratchCanvas : public GFXcanvas16 {
  public:
    ScratchCanvas(uint16_t w, uint16_t h, uint16_t* buf) : GFXcanvas16(w, h, /*alloc=*/false) {
      buffer = buf;
    }
  };

  ScratchCanvas scratch(static_cast<uint16_t>(tw), static_cast<uint16_t>(box_h), tmp);
  s_u8g2.begin(scratch);
  s_bound_gfx = &scratch;
  s_u8g2.setFont(DESKBOT_DISPLAY_CJK_FONT);
  s_u8g2.setForegroundColor(0xFFFFu);
  s_u8g2.drawUTF8(0, box_h, utf8);

  for (int16_t py = 0; py < box_h; py++) {
    for (int16_t px_i = 0; px_i < tw; px_i++) {
      if (tmp[static_cast<size_t>(py) * static_cast<size_t>(tw) + static_cast<size_t>(px_i)] == 0) {
        continue;
      }
      gfx->fillRect(x + px_i * static_cast<int16_t>(sz), y + py * static_cast<int16_t>(sz), sz, sz,
                    rgb565);
    }
  }

  free(tmp);
  s_bound_gfx = nullptr;
  bind_gfx(gfx);
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

  draw_cjk_scaled(gfx, x, y, utf8, sz, rgb565);
}

int16_t display_text_line_height(uint8_t text_size) {
  uint8_t sz = text_size ? text_size : 1;
  if (sz > 3) {
    sz = 3;
  }
  /* wqy12 字模盒高度约 12px；size>1 时最近邻放大，行距同步。 */
  return static_cast<int16_t>(12 * sz);
}
