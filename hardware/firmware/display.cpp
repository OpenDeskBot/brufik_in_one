#include "display.h"
#include "display_text.h"
#include "pb_model.h"

#include "logger.h"

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include <atomic>
#include <cstring>
#include "esp_heap_caps.h"
#include "mic.h"

DeskbotDisplay g_display(DESKBOT_DISPLAY_CS, DESKBOT_DISPLAY_DC, -1);

/* ── PSRAM 帧缓冲 ──
 * PB 矢量动画先在 PSRAM 里绘制（速度快，0 SPI 流量），完成后整帧 DMA 推送。
 * 效果：消除逐像素撕裂，动画平滑；若 ps_malloc 失败则回退直写模式。
 */
class PsramCanvas16 : public GFXcanvas16 {
public:
  PsramCanvas16(uint16_t w, uint16_t h) : GFXcanvas16(w, h, /*alloc=*/false) {
    buffer = (uint16_t*)heap_caps_malloc((uint32_t)w * h * 2u, MALLOC_CAP_SPIRAM);
    if (buffer) memset(buffer, 0, (uint32_t)w * h * 2u);
  }
  ~PsramCanvas16() { if (buffer) { free(buffer); buffer = nullptr; } }
};

static PsramCanvas16* s_canvas   = nullptr;
static Adafruit_GFX*  s_draw_gfx = &g_display;   /* 渲染目标：canvas 或直写面板 */

bool deskbot_mic_uplink_active(void) { return mic_capture_allowed(); }

/** 屏顶麦克风状态点：绿=开麦，红=关麦（画在当前 s_draw_gfx 上）。 */
static void display_draw_mic_indicator() {
  if (!s_draw_gfx) {
    return;
  }
  const bool on = mic_capture_allowed();
  const uint16_t col = on ? DESKBOT_DISPLAY_COLOR_GREEN : DESKBOT_DISPLAY_COLOR_RED;
  const int16_t cx = static_cast<int16_t>(DESKBOT_DRAW_W - 10);
  const int16_t cy = static_cast<int16_t>(DESKBOT_DISPLAY_TOP_SAFE_PX + 6);
  s_draw_gfx->fillCircle(cx, cy - 2, 3, col);
  s_draw_gfx->fillRect(cx - 2, cy, 5, 4, col);
  s_draw_gfx->drawFastHLine(cx - 4, cy + 5, 9, col);
}

/** canvas 整帧推送；CANVAS_X0 在 COORD_W==HEIGHT 时为 0。 */
static inline void pb_canvas_push() {
  if (s_canvas && s_canvas->getBuffer()) {
    g_display.drawRGBBitmap(DESKBOT_DISPLAY_CANVAS_X0, 0, s_canvas->getBuffer(),
                       DESKBOT_PB_COORD_W, DESKBOT_PB_COORD_H);
  }
}

/* canvas 文字叠写：首次调用先刷黑背景，后续调用直接在上面叠加 */
static bool s_canvas_text_bg = false;

static void display_canvas_text_reset() {
  s_canvas_text_bg = false;
}

void display_boot_screen_reset() { display_canvas_text_reset(); }

static constexpr uint8_t kDisplayBootTextSize = DESKBOT_DISPLAY_BOOT_TEXT_SIZE;

Adafruit_GFX* display_guide_target_begin(bool clear_black) {
  display_boot_screen_reset();
  Adafruit_GFX* target = (s_canvas && s_canvas->getBuffer()) ? static_cast<Adafruit_GFX*>(s_canvas)
                                                             : static_cast<Adafruit_GFX*>(&g_display);
  if (clear_black) {
    if (s_canvas && s_canvas->getBuffer()) {
      s_canvas->fillScreen(DESKBOT_DISPLAY_COLOR_BLACK);
      s_canvas_text_bg = true;
    } else {
      g_display.fillScreen(DESKBOT_DISPLAY_COLOR_BLACK);
    }
  } else if (s_canvas && s_canvas->getBuffer()) {
    s_canvas_text_bg = true;
  }
  return target;
}

void display_guide_target_end() {
  if (s_canvas && s_canvas->getBuffer()) {
    pb_canvas_push();
  }
  display_flush_timed();
}

static void display_canvas_ensure_black() {
  if (s_canvas && s_canvas->getBuffer() && !s_canvas_text_bg) {
    s_canvas->fillScreen(DESKBOT_DISPLAY_COLOR_BLACK);
    s_canvas_text_bg = true;
  }
}

void display_clear_timed() { g_display.fillScreen(DESKBOT_DISPLAY_COLOR_BLACK); }

void display_flush_timed() { /* TFT 直写显存，无需 display() */ }

static int16_t s_display_text_sx = 8;
static int16_t s_display_text_sy = 8;
static constexpr uint8_t kDisplayTextServerSize = 2;

void setup_display() {
  g_display.setupPanel();
  if (g_display.width() <= 0 || g_display.height() <= 0) {
    log_error("[DISPLAY] setup_display panel size invalid w=%d h=%d", (int)g_display.width(),
              (int)g_display.height());
  }

  s_canvas = new PsramCanvas16(DESKBOT_DRAW_W, DESKBOT_DRAW_H);
  if (s_canvas && s_canvas->getBuffer()) {
    s_draw_gfx = s_canvas;
    log_info("[DISPLAY] PSRAM canvas %dx%d ok (%.0f KB)",
             DESKBOT_DRAW_W, DESKBOT_DRAW_H,
             (float)(DESKBOT_DRAW_W * DESKBOT_DRAW_H * 2) / 1024.f);
  } else {
    log_error("[DISPLAY] PSRAM canvas alloc failed, fallback direct-write (anim may tear)");
    delete s_canvas;
    s_canvas = nullptr;
    s_draw_gfx = &g_display;
  }

  g_display.fillScreen(DESKBOT_DISPLAY_COLOR_BLACK);
  display_canvas_text_reset();
  log_info("[DISPLAY] ready ST7789 %dx%d off=%d,%d SPI mosi=%d sck=%d cs=%d dc=%d",
           (int)g_display.width(), (int)g_display.height(), DESKBOT_DISPLAY_COL_OFFSET,
           DESKBOT_DISPLAY_ROW_OFFSET, DESKBOT_DISPLAY_MOSI, DESKBOT_DISPLAY_SCK, DESKBOT_DISPLAY_CS,
           DESKBOT_DISPLAY_DC);
  g_display.setTextSize(kDisplayBootTextSize);
  g_display.setTextColor(DESKBOT_DISPLAY_COLOR_WHITE, DESKBOT_DISPLAY_COLOR_BLACK);
}

/* display_print/println：boot 阶段 banner；横屏逻辑坐标为 PB_COORD（284×240）。 */
void display_text_layout_reset(int16_t sx, int16_t sy) {
  s_display_text_sx = sx;
  s_display_text_sy = sy;
}

static void display_text_blit_at(int16_t sx, int16_t sy, const char* text, uint8_t text_size) {
  /* 禁止直写 SPI 逐字：会引起顶栏渐变花屏；canvas 上画完再整帧推送。 */
  if (s_canvas && s_canvas->getBuffer()) {
    display_canvas_ensure_black();
    display_text_draw(s_canvas, sx, sy, text, text_size, DESKBOT_DISPLAY_COLOR_WHITE);
    pb_canvas_push();
  } else {
    display_text_draw(&g_display, sx, sy, text, text_size, DESKBOT_DISPLAY_COLOR_WHITE);
  }
  display_flush_timed();
}

void display_println_server(int16_t sx, int16_t sy, String text, int delay_time) {
  display_text_blit_at(sx, sy, text.c_str(), kDisplayTextServerSize);
  vTaskDelay(pdMS_TO_TICKS(delay_time));
}

void display_print(String text, int delay_time) {
  display_text_blit_at(s_display_text_sx, s_display_text_sy, text.c_str(), kDisplayTextServerSize);
  vTaskDelay(pdMS_TO_TICKS(delay_time));
}

void display_println(String text, int delay_time) {
  display_println_server(s_display_text_sx, s_display_text_sy, text, delay_time);
  s_display_text_sy += display_text_line_height(kDisplayTextServerSize);
}

namespace {

/* pb 矢量帧：同层同下标且 shape 一致时在 anim[k].ms 内按 t 插值；否则画本帧。 */
static constexpr uint32_t kPbDisplayBudgetMs = 13;
static constexpr uint8_t  kPbMaxPrimsPerLayer   = 16;
static constexpr UBaseType_t kPbDisplayQueueDepth = DESKBOT_PB_EXECUTOR_QUEUE_DEPTH;
/** text 图元：服务端预换行后下发；单行 UTF-8 按字节截断（约 42 个汉字）。 */
static constexpr size_t kPbMaxTextChars = 128;

/* pb 图元：与服务端 anim[] 实际下发的 shape 对齐。 */
enum class PbShape : uint8_t {
  None = 0,
  Rect,
  RectOutline,
  Circle,
  CircleOutline,
  Line,
  Ellipse,
  EllipseFill,
  RoundRect,
  RoundRectOutline,
  Text,
};

struct StoredPrim {
  PbShape shape;
  uint16_t color; /* RGB565；图元字段 c/color，缺省为白 */
  int16_t x;
  int16_t y;
  int16_t w;
  int16_t h;
  int16_t r;
  int16_t x1;
  int16_t y1;
  int16_t x2;
  int16_t y2;
  uint8_t text_size; /* 仅 Text：1–3，与 setTextSize 一致 */
  char    text[kPbMaxTextChars + 1];
};

struct StoredLayer {
  uint8_t     count;
  StoredPrim  prims[kPbMaxPrimsPerLayer];
};

/*
 * 插值前帧 + 当前帧共 12 层，原先为内部 DRAM BSS。它们仅由 display_render 串行访问，
 * 不是 DMA/ISR 数据，迁到 PSRAM 可回收约 30KB 内部 DRAM 给 Wi-Fi/lwIP 使用。
 */
struct StoredLayerPool {
  StoredLayer prev_bg;
  StoredLayer prev_nose;
  StoredLayer prev_mouth;
  StoredLayer prev_eye_l;
  StoredLayer prev_eye_r;
  StoredLayer prev_extra;
  StoredLayer curr_bg;
  StoredLayer curr_nose;
  StoredLayer curr_mouth;
  StoredLayer curr_eye_l;
  StoredLayer curr_eye_r;
  StoredLayer curr_extra;
};

static StoredLayerPool* s_layer_pool = nullptr;
static bool s_have_prev = false;

static bool ensure_stored_layer_pool() {
  if (s_layer_pool) {
    return true;
  }
  s_layer_pool = static_cast<StoredLayerPool*>(
      heap_caps_calloc(1, sizeof(StoredLayerPool), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!s_layer_pool) {
    log_error("[DISPLAY] PSRAM StoredLayer pool alloc failed bytes=%u",
              (unsigned)sizeof(StoredLayerPool));
    return false;
  }
  log_info("[DISPLAY] StoredLayer pool in PSRAM bytes=%u", (unsigned)sizeof(StoredLayerPool));
  return true;
}

static void pb_vector_interp_reset() {
  if (!ensure_stored_layer_pool()) {
    return;
  }
  s_have_prev = false;
  memset(&s_layer_pool->prev_bg, 0, sizeof(s_layer_pool->prev_bg));
  memset(&s_layer_pool->prev_nose, 0, sizeof(s_layer_pool->prev_nose));
  memset(&s_layer_pool->prev_mouth, 0, sizeof(s_layer_pool->prev_mouth));
  memset(&s_layer_pool->prev_eye_l, 0, sizeof(s_layer_pool->prev_eye_l));
  memset(&s_layer_pool->prev_eye_r, 0, sizeof(s_layer_pool->prev_eye_r));
  memset(&s_layer_pool->prev_extra, 0, sizeof(s_layer_pool->prev_extra));
}

static int lerp_i16(int16_t a, int16_t b, float t) {
  return (int)lroundf((1.f - t) * (float)a + t * (float)b);
}

static uint16_t lerp_rgb565(uint16_t a, uint16_t b, float t) {
  const int r0 = (a >> 11) & 0x1f;
  const int g0 = (a >> 5) & 0x3f;
  const int b0 = a & 0x1f;
  const int r1 = (b >> 11) & 0x1f;
  const int g1 = (b >> 5) & 0x3f;
  const int b1 = b & 0x1f;
  const int r = (int)lroundf((1.f - t) * (float)r0 + t * (float)r1);
  const int g = (int)lroundf((1.f - t) * (float)g0 + t * (float)g1);
  const int bl = (int)lroundf((1.f - t) * (float)b0 + t * (float)b1);
  return (uint16_t)(((r & 0x1f) << 11) | ((g & 0x3f) << 5) | (bl & 0x1f));
}

static void layer_clear(StoredLayer* L) {
  L->count = 0;
}

/** 空层不覆盖：口型 chunk 常只带 mouth，prev 须保留眼/鼻等。 */
static void pb_commit_layer(StoredLayer* dst, const StoredLayer& curr) {
  if (curr.count > 0) {
    memcpy(dst, &curr, sizeof(curr));
  }
}

static PbShape pb_shape_from_element(pb_anim_element_shape shape) {
  switch (shape) {
    case pb_anim_element_shape::rect: return PbShape::Rect;
    case pb_anim_element_shape::rect_outline: return PbShape::RectOutline;
    case pb_anim_element_shape::circle: return PbShape::Circle;
    case pb_anim_element_shape::circle_outline: return PbShape::CircleOutline;
    case pb_anim_element_shape::line: return PbShape::Line;
    case pb_anim_element_shape::ellipse: return PbShape::Ellipse;
    case pb_anim_element_shape::ellipse_fill: return PbShape::EllipseFill;
    case pb_anim_element_shape::round_rect: return PbShape::RoundRect;
    case pb_anim_element_shape::round_rect_outline: return PbShape::RoundRectOutline;
    case pb_anim_element_shape::text: return PbShape::Text;
    default: return PbShape::None;
  }
}

static bool prim_from_pb_element(const pb_anim_element& in, StoredPrim& p) {
  memset(&p, 0, sizeof(p));
  p.shape = pb_shape_from_element(in.shape);
  if (p.shape == PbShape::None) {
    return false;
  }
  p.color = in.color;
  p.x = (int16_t)in.x;
  p.y = (int16_t)in.y;
  p.w = (int16_t)in.w;
  p.h = (int16_t)in.h;
  p.r = (int16_t)in.r;
  p.x1 = (int16_t)in.x1;
  p.y1 = (int16_t)in.y1;
  p.x2 = (int16_t)in.x2;
  p.y2 = (int16_t)in.y2;
  if (p.shape == PbShape::Text) {
    if (!in.text[0]) {
      return false;
    }
    strncpy(p.text, in.text, kPbMaxTextChars);
    p.text[kPbMaxTextChars] = '\0';
    int tsz = in.text_size;
    if (tsz < 1) tsz = 1;
    if (tsz > 3) tsz = 3;
    p.text_size = (uint8_t)tsz;
  }
  return true;
}

static void layer_fill_from_pb_elements(const pb_anim_element* elements, size_t count,
                                        pb_anim_element_layer layer, StoredLayer* out) {
  layer_clear(out);
  if (!elements || count == 0) {
    return;
  }
  for (size_t i = 0; i < count; ++i) {
    const pb_anim_element& in = elements[i];
    if (in.layer != layer || out->count >= kPbMaxPrimsPerLayer) {
      continue;
    }
    StoredPrim& p = out->prims[out->count];
    if (!prim_from_pb_element(in, p)) {
      continue;
    }
    out->count++;
  }
}

static void stored_from_pb_elements(const pb_anim_element* elements, size_t count, StoredLayer* bg,
                                    StoredLayer* nose, StoredLayer* mouth, StoredLayer* eye_l,
                                    StoredLayer* eye_r, StoredLayer* extra) {
  layer_fill_from_pb_elements(elements, count, pb_anim_element_layer::bg, bg);
  layer_fill_from_pb_elements(elements, count, pb_anim_element_layer::nose, nose);
  layer_fill_from_pb_elements(elements, count, pb_anim_element_layer::mouth, mouth);
  layer_fill_from_pb_elements(elements, count, pb_anim_element_layer::eye_l, eye_l);
  layer_fill_from_pb_elements(elements, count, pb_anim_element_layer::eye_r, eye_r);
  layer_fill_from_pb_elements(elements, count, pb_anim_element_layer::extra, extra);
}

static void draw_prim(const StoredPrim& p) {
  const uint16_t col = p.color;
  switch (p.shape) {
    case PbShape::Rect:
      if (p.w > 0 && p.h > 0) {
        (*s_draw_gfx).fillRect(p.x, p.y, p.w, p.h, col);
      }
      break;
    case PbShape::RectOutline:
      if (p.w > 0 && p.h > 0) {
        (*s_draw_gfx).drawRect(p.x, p.y, p.w, p.h, col);
      }
      break;
    case PbShape::Circle:
      if (p.r > 0) {
        (*s_draw_gfx).fillCircle(p.x, p.y, p.r, col);
      }
      break;
    case PbShape::CircleOutline:
      if (p.r > 0) {
        (*s_draw_gfx).drawCircle(p.x, p.y, p.r, col);
      }
      break;
    case PbShape::Line:
      (*s_draw_gfx).drawLine(p.x1, p.y1, p.x2, p.y2, col);
      break;
    case PbShape::Ellipse:
      if (p.w > 0 && p.h > 0) {
        (*s_draw_gfx).drawEllipse(p.x, p.y, p.w, p.h, col);
      }
      break;
    case PbShape::EllipseFill:
      if (p.w > 0 && p.h > 0) {
        (*s_draw_gfx).fillEllipse(p.x, p.y, p.w, p.h, col);
      }
      break;
    case PbShape::RoundRect:
      if (p.w > 0 && p.h > 0) {
        if (p.r > 0) {
          (*s_draw_gfx).fillRoundRect(p.x, p.y, p.w, p.h, p.r, col);
        } else {
          (*s_draw_gfx).fillRect(p.x, p.y, p.w, p.h, col);
        }
      }
      break;
    case PbShape::RoundRectOutline:
      if (p.w > 0 && p.h > 0) {
        if (p.r > 0) {
          (*s_draw_gfx).drawRoundRect(p.x, p.y, p.w, p.h, p.r, col);
        } else {
          (*s_draw_gfx).drawRect(p.x, p.y, p.w, p.h, col);
        }
      }
      break;
    case PbShape::Text:
      if (p.text[0] != '\0') {
        uint8_t sz = p.text_size ? p.text_size : 1;
        if (sz > 3) {
          sz = 3;
        }
        display_text_draw(s_draw_gfx, p.x, p.y, p.text, sz, col);
      }
      break;
    case PbShape::None:
    default:
      break;
  }
}

static void draw_prim_lerp(const StoredPrim* prev, const StoredPrim& curr, float t) {
  if (!prev || prev->shape != curr.shape || curr.shape == PbShape::None) {
    draw_prim(curr);
    return;
  }
  const uint16_t col = lerp_rgb565(prev->color, curr.color, t);
  switch (curr.shape) {
    case PbShape::Rect:
    case PbShape::RectOutline: {
      int x = lerp_i16(prev->x, curr.x, t);
      int y = lerp_i16(prev->y, curr.y, t);
      int w = lerp_i16(prev->w, curr.w, t);
      int h = lerp_i16(prev->h, curr.h, t);
      if (w < 1) {
        w = 1;
      }
      if (h < 1) {
        h = 1;
      }
      if (curr.shape == PbShape::Rect) {
        (*s_draw_gfx).fillRect(x, y, w, h, col);
      } else {
        (*s_draw_gfx).drawRect(x, y, w, h, col);
      }
    } break;
    case PbShape::Circle:
    case PbShape::CircleOutline: {
      int x = lerp_i16(prev->x, curr.x, t);
      int y = lerp_i16(prev->y, curr.y, t);
      int r = lerp_i16(prev->r, curr.r, t);
      if (r > 0) {
        if (curr.shape == PbShape::Circle) {
          (*s_draw_gfx).fillCircle(x, y, r, col);
        } else {
          (*s_draw_gfx).drawCircle(x, y, r, col);
        }
      }
    } break;
    case PbShape::Line: {
      int x1 = lerp_i16(prev->x1, curr.x1, t);
      int y1 = lerp_i16(prev->y1, curr.y1, t);
      int x2 = lerp_i16(prev->x2, curr.x2, t);
      int y2 = lerp_i16(prev->y2, curr.y2, t);
      (*s_draw_gfx).drawLine(x1, y1, x2, y2, col);
    } break;
    case PbShape::Ellipse:
    case PbShape::EllipseFill: {
      int x = lerp_i16(prev->x, curr.x, t);
      int y = lerp_i16(prev->y, curr.y, t);
      int rw = lerp_i16(prev->w, curr.w, t);
      int rh = lerp_i16(prev->h, curr.h, t);
      if (rw < 1) {
        rw = 1;
      }
      if (rh < 1) {
        rh = 1;
      }
      if (curr.shape == PbShape::EllipseFill) {
        (*s_draw_gfx).fillEllipse(x, y, rw, rh, col);
      } else {
        (*s_draw_gfx).drawEllipse(x, y, rw, rh, col);
      }
    } break;
    case PbShape::RoundRect:
    case PbShape::RoundRectOutline: {
      int x = lerp_i16(prev->x, curr.x, t);
      int y = lerp_i16(prev->y, curr.y, t);
      int w = lerp_i16(prev->w, curr.w, t);
      int h = lerp_i16(prev->h, curr.h, t);
      int rad = lerp_i16(prev->r, curr.r, t);
      if (w < 1) {
        w = 1;
      }
      if (h < 1) {
        h = 1;
      }
      if (curr.shape == PbShape::RoundRect) {
        if (rad > 0) {
          (*s_draw_gfx).fillRoundRect(x, y, w, h, rad, col);
        } else {
          (*s_draw_gfx).fillRect(x, y, w, h, col);
        }
      } else if (rad > 0) {
        (*s_draw_gfx).drawRoundRect(x, y, w, h, rad, col);
      } else {
        (*s_draw_gfx).drawRect(x, y, w, h, col);
      }
    } break;
    case PbShape::Text: {
      if (strcmp(prev->text, curr.text) != 0 || prev->text_size != curr.text_size) {
        draw_prim(curr);
        break;
      }
      int x = lerp_i16(prev->x, curr.x, t);
      int y = lerp_i16(prev->y, curr.y, t);
      uint8_t sz = curr.text_size ? curr.text_size : 1;
      if (sz > 3) {
        sz = 3;
      }
      display_text_draw(s_draw_gfx, x, y, curr.text, sz, col);
    } break;
    case PbShape::None:
    default:
      break;
  }
}

static void draw_layer_lerp(const StoredLayer* prev, const StoredLayer& curr, float t) {
  const uint8_t ncurr = curr.count;
  if (ncurr == 0) {
    /* 口型 chunk 常只带 mouth：未指定的图层沿用上一帧（如 eye_l/eye_r）。 */
    if (prev && prev->count > 0) {
      for (uint8_t i = 0; i < prev->count; i++) {
        draw_prim(prev->prims[i]);
      }
    }
    return;
  }
  const uint8_t nprev = prev ? prev->count : 0;
  for (uint8_t i = 0; i < ncurr; i++) {
    const StoredPrim& c = curr.prims[i];
    if (i < nprev) {
      draw_prim_lerp(&prev->prims[i], c, t);
    } else {
      draw_prim(c);
    }
  }
}

/** extra 层与其它层相同插值规则。 */
static void draw_extra_layer_ordered(const StoredLayer* prev, const StoredLayer& curr, float t) {
  draw_layer_lerp(prev, curr, t);
}

static void draw_stored_interpolated(const StoredLayer* pbg, const StoredLayer* pn, const StoredLayer* pm,
                                     const StoredLayer* pel, const StoredLayer* per, const StoredLayer* pex,
                                     const StoredLayer& cbg, const StoredLayer& cn, const StoredLayer& cm,
                                     const StoredLayer& cel, const StoredLayer& cer, const StoredLayer& cex,
                                     float t) {
  draw_layer_lerp(pbg, cbg, t);
  draw_layer_lerp(pn, cn, t);
  draw_layer_lerp(pm, cm, t);
  draw_layer_lerp(pel, cel, t);
  draw_layer_lerp(per, cer, t);
  draw_extra_layer_ordered(pex, cex, t);
}

static constexpr size_t kPbMaxAnimSegsPerChunk = 64;

static void pb_commit_prev(const StoredLayer& bg, const StoredLayer& nose, const StoredLayer& mouth,
                           const StoredLayer& eye_l, const StoredLayer& eye_r, const StoredLayer& extra) {
  pb_commit_layer(&s_layer_pool->prev_bg, bg);
  pb_commit_layer(&s_layer_pool->prev_nose, nose);
  pb_commit_layer(&s_layer_pool->prev_mouth, mouth);
  pb_commit_layer(&s_layer_pool->prev_eye_l, eye_l);
  pb_commit_layer(&s_layer_pool->prev_eye_r, eye_r);
  pb_commit_layer(&s_layer_pool->prev_extra, extra);
  s_have_prev = true;
}

static std::atomic<bool> s_need_cancel{false};
static bool poll_cancel();

/** @return false 若中途 need_cancel。 */
static bool pb_play_layers_interpolated(const StoredLayer* pbg, const StoredLayer* pn, const StoredLayer* pm,
                                        const StoredLayer* pl, const StoredLayer* pr, const StoredLayer* px,
                                        const StoredLayer& cbg, const StoredLayer& cn, const StoredLayer& cm,
                                        const StoredLayer& cel, const StoredLayer& cer, const StoredLayer& cex,
                                        uint32_t segment_ms, uint16_t bg_rgb565) {
  if (poll_cancel()) {
    return false;
  }
  if (segment_ms == 0) {
    (*s_draw_gfx).fillScreen(bg_rgb565);
    draw_stored_interpolated(pbg, pn, pm, pl, pr, px, cbg, cn, cm, cel, cer, cex, 1.f);
    display_draw_mic_indicator();
    pb_canvas_push();
    return true;
  }

  uint32_t budget = segment_ms;
  if (budget > 300000u) {
    budget = 300000u;
  }

  const uint32_t t0 = millis();
  uint32_t pushes = 0;
  uint32_t first_push_ms = 0;
  while (true) {
    if (poll_cancel()) {
      return false;
    }
    const uint32_t elapsed = millis() - t0;
    if (elapsed >= budget) {
      break;
    }
    float t = (float)elapsed / (float)budget;
    if (t > 1.f) {
      t = 1.f;
    }
    const uint32_t t_push0 = millis();
    (*s_draw_gfx).fillScreen(bg_rgb565);
    draw_stored_interpolated(pbg, pn, pm, pl, pr, px, cbg, cn, cm, cel, cer, cex, t);
    display_draw_mic_indicator();
    pb_canvas_push();
    const uint32_t push_ms = millis() - t_push0;
    pushes++;
    if (pushes == 1) {
      first_push_ms = push_ms;
    }

    const uint32_t after_draw = millis();
    uint32_t remain = (t0 + budget) - after_draw;
    if (remain < kPbDisplayBudgetMs) {
      if (remain > 0) {
        vTaskDelay(pdMS_TO_TICKS(remain));
      }
      break;
    }
    const uint32_t after_disp = millis();
    remain = (t0 + budget) - after_disp;
    if (remain == 0) {
      break;
    }
    if (remain > 0) {
      vTaskDelay(pdMS_TO_TICKS(1));
    }
  }

  while ((int32_t)(millis() - t0) < (int32_t)budget) {
    if (poll_cancel()) {
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  const uint32_t wall = millis() - t0;
  log_warn("[PB_LAT] display_seg budget=%u wall=%u pushes=%u first_push_ms=%u", (unsigned)budget,
           (unsigned)wall, (unsigned)pushes, (unsigned)first_push_ms);
  return true;
}

static void pb_render_anim_frames_timed(const pb_anim_frame* frames, size_t frame_count) {
  if (!s_layer_pool || !frames || frame_count == 0) {
    return;
  }
  uint32_t budget_sum = 0;
  for (size_t i = 0; i < frame_count; ++i) {
    budget_sum += frames[i].ms > 0 ? (uint32_t)frames[i].ms : 1u;
  }
  const uint32_t job_t0 = millis();
  log_warn("[PB_LAT] display_job_begin frames=%u budget_sum=%u", (unsigned)frame_count,
           (unsigned)budget_sum);
  size_t seg_idx = 0;
  for (size_t i = 0; i < frame_count; ++i) {
    if (poll_cancel()) {
      return;
    }
    if (seg_idx >= kPbMaxAnimSegsPerChunk) {
      log_warn("[DISPLAY] anim[] truncated at %u", (unsigned)kPbMaxAnimSegsPerChunk);
      break;
    }
    const pb_anim_frame& seg = frames[i];
    uint32_t seg_ms = seg.ms > 0 ? (uint32_t)seg.ms : 1u;

    StoredLayer& cbg = s_layer_pool->curr_bg;
    StoredLayer& cn = s_layer_pool->curr_nose;
    StoredLayer& cm = s_layer_pool->curr_mouth;
    StoredLayer& cel = s_layer_pool->curr_eye_l;
    StoredLayer& cer = s_layer_pool->curr_eye_r;
    StoredLayer& cex = s_layer_pool->curr_extra;
    stored_from_pb_elements(seg.elements, seg.element_count, &cbg, &cn, &cm, &cel, &cer, &cex);

    const uint16_t seg_bg = DESKBOT_DISPLAY_COLOR_BLACK;

    const StoredLayer* pbg = s_have_prev ? &s_layer_pool->prev_bg : nullptr;
    const StoredLayer* pn = s_have_prev ? &s_layer_pool->prev_nose : nullptr;
    const StoredLayer* pm = s_have_prev ? &s_layer_pool->prev_mouth : nullptr;
    const StoredLayer* pl = s_have_prev ? &s_layer_pool->prev_eye_l : nullptr;
    const StoredLayer* pr = s_have_prev ? &s_layer_pool->prev_eye_r : nullptr;
    const StoredLayer* px = s_have_prev ? &s_layer_pool->prev_extra : nullptr;

    if (!pb_play_layers_interpolated(pbg, pn, pm, pl, pr, px, cbg, cn, cm, cel, cer, cex, seg_ms,
                                     seg_bg)) {
      return;
    }
    pb_commit_prev(cbg, cn, cm, cel, cer, cex);
    seg_idx++;
  }
  log_warn("[PB_LAT] display_job_end frames=%u budget_sum=%u wall=%u", (unsigned)frame_count,
           (unsigned)budget_sum, (unsigned)(millis() - job_t0));
}

struct DisplayRequest {
  DisplayJobType type = DISPLAY_JOB_PB_ANIM_FRAMES;
  pb_anim_frame* anim_frames = nullptr;
  size_t anim_frame_count = 0;
  SemaphoreHandle_t notify_sem = nullptr;
};

static void display_free_request_anim_frames(DisplayRequest& req) {
  if (req.anim_frames) {
    pb_anim_frames_free(req.anim_frames, req.anim_frame_count);
    req.anim_frames = nullptr;
    req.anim_frame_count = 0;
  }
}

static void free_display_request(DisplayRequest& req) {
  if (req.type == DISPLAY_JOB_PB_ANIM_FRAMES) {
    display_free_request_anim_frames(req);
  }
  if (req.notify_sem) {
    xSemaphoreGive(req.notify_sem);
    req.notify_sem = nullptr;
  }
}

QueueHandle_t     s_queue       = nullptr;
TaskHandle_t      s_task        = nullptr;
SemaphoreHandle_t s_done_sem    = nullptr;
SemaphoreHandle_t s_caller_lock = nullptr;

/**
 * need_cancel==false → false。
 * 否则非阻塞丢弃旧任务，见到 cancel 则清 flag 并 return true（其后新任务保留）。
 * 队列空且未见 cancel → return true，保持 need_cancel。
 */
static bool poll_cancel() {
  if (!s_need_cancel.load(std::memory_order_acquire)) {
    return false;
  }
  DisplayRequest j{};
  while (xQueueReceive(s_queue, &j, 0) == pdTRUE) {
    if (j.type == DISPLAY_JOB_CANCEL) {
      s_need_cancel.store(false, std::memory_order_release);
      pb_vector_interp_reset();
      log_info("[DISPLAY] cancel");
      return true;
    }
    free_display_request(j);
  }
  return true;
}

static void execute_display_job(DisplayRequest& req) {
  if (req.type == DISPLAY_JOB_PB_ANIM_FRAMES) {
    pb_render_anim_frames_timed(req.anim_frames, req.anim_frame_count);
    display_free_request_anim_frames(req);
  }
  if (req.notify_sem) {
    xSemaphoreGive(req.notify_sem);
    req.notify_sem = nullptr;
  }
}

static void display_render_task(void* /*arg*/) {
  DisplayRequest req{};
  for (;;) {
    (void)poll_cancel();
    if (xQueueReceive(s_queue, &req, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    if (req.type == DISPLAY_JOB_CANCEL) {
      /*
       * 空闲时 task 阻塞在 Receive，Cancel 会直接出队，不会经过 poll_cancel。
       * 若不在此清 flag，s_need_cancel 会永久为 true，后续口型/表情全被丢掉。
       */
      if (s_need_cancel.exchange(false, std::memory_order_acq_rel)) {
        pb_vector_interp_reset();
        log_info("[DISPLAY] cancel");
      }
      continue;
    }
    execute_display_job(req);
  }
}

}  // namespace

void task_setup_display() {
  if (s_queue && s_task && s_done_sem && s_caller_lock) {
    return;
  }
  if (!ensure_stored_layer_pool()) {
    log_error("[DISPLAY] StoredLayer pool unavailable");
    return;
  }
  if (!s_queue) {
    /* 满则 submit 走 drop-oldest，永不阻塞 caller（WS 回调）。 */
    s_queue = xQueueCreate(kPbDisplayQueueDepth, sizeof(DisplayRequest));
  }
  if (!s_done_sem) {
    s_done_sem = xSemaphoreCreateBinary();
  }
  if (!s_caller_lock) {
    s_caller_lock = xSemaphoreCreateMutex();
  }
  if (!s_task) {
    /* U8g2 drawUTF8(gb2312) 栈较深；10KB 会触发 canary。 */
    xTaskCreatePinnedToCore(display_render_task, "display_render", 24 * 1024, nullptr, 2, &s_task,
                            APP_CPU_NUM);
  }
}

static void display_enqueue_request(DisplayRequest& req, bool wait_done) {
  if (!s_queue) {
    log_warn("[DISPLAY] queue not ready; free submit");
    free_display_request(req);
    return;
  }
  if (wait_done) {
    xSemaphoreTake(s_caller_lock, portMAX_DELAY);
    xSemaphoreTake(s_done_sem, 0);
    req.notify_sem = s_done_sem;
    xQueueSend(s_queue, &req, portMAX_DELAY);
    xSemaphoreTake(s_done_sem, portMAX_DELAY);
    xSemaphoreGive(s_caller_lock);
    return;
  }

  req.notify_sem = nullptr;
  if (xQueueSend(s_queue, &req, 0) != pdTRUE) {
    DisplayRequest dropped{};
    if (xQueueReceive(s_queue, &dropped, 0) == pdTRUE) {
      free_display_request(dropped);
    }
    if (xQueueSend(s_queue, &req, 0) != pdTRUE) {
      log_warn("[DISPLAY] queue full after drop-oldest; free submit");
      free_display_request(req);
    }
  }
}

void display_render_submit_pb_anim_frames_owned(pb_anim_frame* frames, size_t frame_count,
                                                bool wait_done) {
  if (!frames || frame_count == 0) {
    if (frames) {
      pb_anim_frames_free(frames, frame_count);
    }
    return;
  }
  DisplayRequest req{};
  req.type = DISPLAY_JOB_PB_ANIM_FRAMES;
  req.anim_frames = frames;
  req.anim_frame_count = frame_count;
  display_enqueue_request(req, wait_done);
}

void display_abort() {
  if (!s_queue) {
    return;
  }
  DisplayRequest req{};
  req.type = DISPLAY_JOB_CANCEL;
  s_need_cancel.store(true, std::memory_order_release);
  (void)xQueueSend(s_queue, &req, portMAX_DELAY);
}

void display_render_reset() { display_abort(); }

unsigned display_render_input_queue_depth(void) {
  if (!s_queue) {
    return 0;
  }
  return (unsigned)uxQueueMessagesWaiting(s_queue);
}
