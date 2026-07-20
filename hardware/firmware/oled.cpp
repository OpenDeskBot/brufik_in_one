#include "oled.h"
#include "oled_utf8_text.h"

#include <WiFi.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include <ArduinoJson.h>
#include <JPEGDEC.h>
#include <cstring>
#include "esp_heap_caps.h"

DeskbotLcd oled(DESKBOT_LCD_CS, DESKBOT_LCD_DC, -1);

/* ── PSRAM 帧缓冲 ──
 * PB 矢量动画先在 PSRAM 里绘制（速度快，0 SPI 流量），完成后整帧 DMA 推送。
 * 效果：消除逐像素撕裂，动画平滑；若 ps_malloc 失败则回退直写模式。
 */
class PsramCanvas16 : public GFXcanvas16 {
public:
  PsramCanvas16(uint16_t w, uint16_t h) : GFXcanvas16(w, h, /*alloc=*/false) {
    buffer = (uint16_t*)ps_malloc((uint32_t)w * h * 2u);
    if (buffer) memset(buffer, 0, (uint32_t)w * h * 2u);
  }
  ~PsramCanvas16() { if (buffer) { free(buffer); buffer = nullptr; } }
};

static PsramCanvas16* s_canvas   = nullptr;
static Adafruit_GFX*  s_draw_gfx = &oled;   /* 渲染目标：canvas 或直写 oled */

bool deskbot_mic_uplink_active(void) { return false; }

void deskbot_mic_uplink_set_active(bool active) { (void)active; }

/** canvas 整帧单次 SPI 推送（横屏 canvas 在 x=CANVAS_X0 对齐）。 */
static inline void pb_canvas_push() {
  if (s_canvas && s_canvas->getBuffer()) {
    oled.drawRGBBitmap(DESKBOT_LCD_CANVAS_X0, 0, s_canvas->getBuffer(),
                       DESKBOT_PB_COORD_W, DESKBOT_PB_COORD_H);
  }
}

/* canvas 文字叠写：首次调用先刷黑背景，后续调用直接在上面叠加 */
static bool s_canvas_text_bg = false;

static void oled_canvas_text_reset() {
  s_canvas_text_bg = false;
}

void oled_boot_screen_reset() { oled_canvas_text_reset(); }

static constexpr uint8_t kOledBootTextSize = DESKBOT_OLED_BOOT_TEXT_SIZE;

void oled_boot_header_lines(char* line1, size_t line1_len, char* line2, size_t line2_len) {
  snprintf(line1, line1_len, "%s v%s", PRODUCT_NAME, VERSION);
  snprintf(line2, line2_len, "device_id: %s", get_device_id());
}

static void oled_boot_draw_lines(const char* line1, const char* line2, const char* line3,
                                 const char* line4 = nullptr) {
  oled_boot_screen_reset();
  const int16_t x0 = DESKBOT_OLED_BOOT_SX;
  const int16_t y0 = DESKBOT_OLED_BOOT_SY0;
  const int16_t dy = oled_utf8_text_line_height(kOledBootTextSize);

  Adafruit_GFX* target = (s_canvas && s_canvas->getBuffer()) ? static_cast<Adafruit_GFX*>(s_canvas)
                                                             : static_cast<Adafruit_GFX*>(&oled);
  if (s_canvas && s_canvas->getBuffer()) {
    s_canvas->fillScreen(DESKBOT_LCD_COLOR_BLACK);
    s_canvas_text_bg = true;
  } else {
    oled.fillScreen(DESKBOT_LCD_COLOR_BLACK);
  }

  int16_t row = 0;
  auto draw_row = [&](const char* text) {
    if (!text || text[0] == '\0') {
      return;
    }
    oled_utf8_text_draw(target, x0, y0 + row * dy, text, kOledBootTextSize, DESKBOT_LCD_COLOR_WHITE);
    row++;
  };
  draw_row(line1);
  draw_row(line2);
  draw_row(line3);
  draw_row(line4);

  if (s_canvas && s_canvas->getBuffer()) {
    pb_canvas_push();
  }
  oled_display_timed();
}

void oled_boot_show3(const char* line1, const char* line2, const char* line3) {
  oled_boot_draw_lines(line1, line2, line3, nullptr);
}

void oled_boot_show4(const char* line1, const char* line2, const char* line3, const char* line4) {
  oled_boot_draw_lines(line1, line2, line3, line4);
}

void oled_boot_show(const char* status_line3, const char* status_line4) {
  char line1[48];
  char line2[40];
  oled_boot_header_lines(line1, sizeof(line1), line2, sizeof(line2));
  oled_boot_draw_lines(line1, line2, status_line3, status_line4);
}

void oled_boot_show_ready() {
  char line1[48];
  char line2[40];
  char line3[48];
  oled_boot_header_lines(line1, sizeof(line1), line2, sizeof(line2));
  snprintf(line3, sizeof(line3), "WiFi:%.16s %s", WiFi.SSID().c_str(),
           WiFi.localIP().toString().c_str());
  oled_boot_draw_lines(line1, line2, line3, "请试试问我: 现在几点了?");
}

static void oled_canvas_ensure_black() {
  if (s_canvas && s_canvas->getBuffer() && !s_canvas_text_bg) {
    s_canvas->fillScreen(DESKBOT_LCD_COLOR_BLACK);
    s_canvas_text_bg = true;
  }
}

void oled_clear_display_timed() { oled.fillScreen(DESKBOT_LCD_COLOR_BLACK); }

void oled_display_timed() { /* TFT 直写显存，无需 display() */ }

static int16_t s_oled_text_sx = 8;
static int16_t s_oled_text_sy = 8;
static constexpr int16_t kOledTextServerLineDy = 14;

void setup_oled() {
  oled.setupPanel();
  if (oled.width() <= 0 || oled.height() <= 0) {
    log_error("[LCD] setup_oled panel size invalid w=%d h=%d", (int)oled.width(),
              (int)oled.height());
  }

  s_canvas = new PsramCanvas16(DESKBOT_DRAW_W, DESKBOT_DRAW_H);
  if (s_canvas && s_canvas->getBuffer()) {
    s_draw_gfx = s_canvas;
    log_info("[LCD] PSRAM canvas %dx%d ok (%.0f KB)",
             DESKBOT_DRAW_W, DESKBOT_DRAW_H,
             (float)(DESKBOT_DRAW_W * DESKBOT_DRAW_H * 2) / 1024.f);
  } else {
    log_error("[LCD] PSRAM canvas alloc failed, fallback direct-write (anim may tear)");
    delete s_canvas;
    s_canvas = nullptr;
    s_draw_gfx = &oled;
  }

  oled.fillScreen(DESKBOT_LCD_COLOR_BLACK);
  oled_canvas_text_reset();
  log_info("[LCD] ready ST7789 %dx%d off=%d,%d SPI mosi=%d sck=%d cs=%d dc=%d",
           (int)oled.width(), (int)oled.height(), DESKBOT_LCD_COL_OFFSET,
           DESKBOT_LCD_ROW_OFFSET, DESKBOT_LCD_MOSI, DESKBOT_LCD_SCK, DESKBOT_LCD_CS,
           DESKBOT_LCD_DC);
  oled.setTextSize(kOledBootTextSize);
  oled.setTextColor(DESKBOT_LCD_COLOR_WHITE, DESKBOT_LCD_COLOR_BLACK);
  oled_boot_show("初始化中...", nullptr);
}

/* oled_print/println：boot 阶段 banner；ROTATION=3 时用服务端 280×240 坐标 */
void oled_text_layout_reset(int16_t sx, int16_t sy) {
  s_oled_text_sx = sx;
  s_oled_text_sy = sy;
}

void oled_println_server(int16_t sx, int16_t sy, String text, int delay_time) {
  /* 禁止直写 SPI 逐字：会引起顶栏渐变花屏。
   * 改在 canvas 上画完再整帧推送（单次 setAddrWindow + 大块写）。 */
  if (s_canvas && s_canvas->getBuffer()) {
    oled_canvas_ensure_black();
    oled_utf8_text_draw(s_canvas, sx, sy, text.c_str(), 2, DESKBOT_LCD_COLOR_WHITE);
    pb_canvas_push();
    oled_display_timed();
    vTaskDelay(pdMS_TO_TICKS(delay_time));
    return;
  }

  /* 无 canvas 时降级直写 */
  oled.setCursor(sx, sy);
  oled.println(text);
  oled_display_timed();
  vTaskDelay(pdMS_TO_TICKS(delay_time));
}

void oled_print(String text, int delay_time) {
  oled.setCursor(s_oled_text_sx, s_oled_text_sy);
  oled.print(text);
  oled_display_timed();
  vTaskDelay(pdMS_TO_TICKS(delay_time));
}

void oled_println(String text, int delay_time) {
  oled_println_server(s_oled_text_sx, s_oled_text_sy, text, delay_time);
  s_oled_text_sy += kOledTextServerLineDy;
}

namespace {

/* pb 矢量帧：同层同下标且 shape 一致时在 anim[k].ms 内按 t 插值；否则画本帧。 */
static constexpr uint32_t kPbOledDisplayBudgetMs = 13;
static constexpr uint8_t  kPbMaxPrimsPerLayer   = 16;
/** text 图元：服务端预换行后下发；单行 UTF-8 按字节截断（约 42 个汉字）。 */
static constexpr size_t kPbMaxTextChars = 128;
static constexpr uint8_t kOledMaxPbAssets = 8;
static constexpr uint16_t kPbDefaultPrimColor = 65535u;

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
  Image,
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
  uint8_t asset_index; /* 仅 Image：assets[] 下标 */
};

struct StoredLayer {
  uint8_t     count;
  StoredPrim  prims[kPbMaxPrimsPerLayer];
};

static StoredLayer s_prev_bg{};
static StoredLayer s_prev_nose{};
static StoredLayer s_prev_mouth{};
static StoredLayer s_prev_eye_l{};
static StoredLayer s_prev_eye_r{};
static StoredLayer s_prev_extra{};
static bool        s_have_prev = false;

/** pb 矢量解析用的「当前帧」图层；放静态区避免 oled_render 任务栈过大。仅渲染任务串行访问。 */
static StoredLayer s_pb_curr_bg{};
static StoredLayer s_pb_curr_nose{};
static StoredLayer s_pb_curr_mouth{};
static StoredLayer s_pb_curr_eye_l{};
static StoredLayer s_pb_curr_eye_r{};
static StoredLayer s_pb_curr_extra{};

struct OledPbAssetBlob {
  uint8_t* data = nullptr;
  size_t   len  = 0;
};
static OledPbAssetBlob s_render_assets[kOledMaxPbAssets]{};
static uint8_t         s_render_asset_count = 0;

struct JpegBlitCtx {
  uint16_t*     canvas_buf;
  int16_t       canvas_w;
  int16_t       canvas_h;
  int16_t       dx;
  int16_t       dy;
  int16_t       dw;
  int16_t       dh;
  int           iw;
  int           ih;
};
/** JPEG 解码器放静态区：JPEGDEC 解码 284×240 时栈帧较大，避免 oled_render 栈溢出。 */
static JPEGDEC     s_jpeg_dec;
static JpegBlitCtx s_jpeg_blit_ctx{};

static void pb_vector_interp_reset() {
  s_have_prev = false;
  memset(&s_prev_bg, 0, sizeof(s_prev_bg));
  memset(&s_prev_nose, 0, sizeof(s_prev_nose));
  memset(&s_prev_mouth, 0, sizeof(s_prev_mouth));
  memset(&s_prev_eye_l, 0, sizeof(s_prev_eye_l));
  memset(&s_prev_eye_r, 0, sizeof(s_prev_eye_r));
  memset(&s_prev_extra, 0, sizeof(s_prev_extra));
}

static void oled_free_render_assets() {
  for (uint8_t i = 0; i < s_render_asset_count; i++) {
    if (s_render_assets[i].data) {
      heap_caps_free(s_render_assets[i].data);
      s_render_assets[i].data = nullptr;
    }
    s_render_assets[i].len = 0;
  }
  s_render_asset_count = 0;
}

static int lerp_i16(int16_t a, int16_t b, float t) {
  return (int)lroundf((1.f - t) * (float)a + t * (float)b);
}

static void layer_clear(StoredLayer* L) {
  L->count = 0;
}

/** 从 JSON 读取 RGB565（低 16 位）；缺省 default_rgb565。 */
static uint16_t pb_json_rgb565_field(JsonObjectConst obj, const char* key, uint16_t default_rgb565) {
  if (obj[key].isNull()) {
    return default_rgb565;
  }
  if (obj[key].is<uint32_t>()) {
    return (uint16_t)(obj[key].as<uint32_t>() & 0xFFFFu);
  }
  if (obj[key].is<int>()) {
    return (uint16_t)((uint32_t)obj[key].as<int>() & 0xFFFFu);
  }
  if (obj[key].is<double>()) {
    return (uint16_t)((uint32_t)(int)obj[key].as<double>() & 0xFFFFu);
  }
  return default_rgb565;
}

static uint16_t pb_json_prim_color(JsonObjectConst it) {
  if (!it["c"].isNull()) {
    return pb_json_rgb565_field(it, "c", kPbDefaultPrimColor);
  }
  if (!it["color"].isNull()) {
    return pb_json_rgb565_field(it, "color", kPbDefaultPrimColor);
  }
  return kPbDefaultPrimColor;
}

static void json_fill_layer(JsonArrayConst arr, StoredLayer* out) {
  layer_clear(out);
  if (arr.isNull()) {
    return;
  }
  for (JsonObjectConst it : arr) {
    if (out->count >= kPbMaxPrimsPerLayer) {
      break;
    }
    StoredPrim& p = out->prims[out->count];
    memset(&p, 0, sizeof(p));
    p.color = pb_json_prim_color(it);
    const char* shape = it["shape"] | "";
    if (strcmp(shape, "rect") == 0 || strcmp(shape, "fill_rect") == 0) {
      p.shape = PbShape::Rect;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.w = (int16_t)(it["w"] | 0);
      p.h = (int16_t)(it["h"] | 0);
    } else if (strcmp(shape, "rect_outline") == 0 || strcmp(shape, "draw_rect") == 0) {
      p.shape = PbShape::RectOutline;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.w = (int16_t)(it["w"] | 0);
      p.h = (int16_t)(it["h"] | 0);
    } else if (strcmp(shape, "circle") == 0 || strcmp(shape, "fill_circle") == 0) {
      p.shape = PbShape::Circle;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.r = (int16_t)(it["r"] | 0);
    } else if (strcmp(shape, "circle_outline") == 0 || strcmp(shape, "draw_circle") == 0) {
      p.shape = PbShape::CircleOutline;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.r = (int16_t)(it["r"] | 0);
    } else if (strcmp(shape, "line") == 0) {
      p.shape = PbShape::Line;
      p.x1 = (int16_t)(it["x1"] | 0);
      p.y1 = (int16_t)(it["y1"] | 0);
      p.x2 = (int16_t)(it["x2"] | 0);
      p.y2 = (int16_t)(it["y2"] | 0);
    } else if (strcmp(shape, "ellipse") == 0 || strcmp(shape, "draw_ellipse") == 0) {
      p.shape = PbShape::Ellipse;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.w = (int16_t)(it["rw"] | it["w"] | 0);
      p.h = (int16_t)(it["rh"] | it["h"] | 0);
    } else if (strcmp(shape, "ellipse_fill") == 0 || strcmp(shape, "fill_ellipse") == 0) {
      p.shape = PbShape::EllipseFill;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.w = (int16_t)(it["rw"] | it["w"] | 0);
      p.h = (int16_t)(it["rh"] | it["h"] | 0);
    } else if (strcmp(shape, "round_rect") == 0 || strcmp(shape, "fill_round_rect") == 0) {
      p.shape = PbShape::RoundRect;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.w = (int16_t)(it["w"] | 0);
      p.h = (int16_t)(it["h"] | 0);
      p.r = (int16_t)(it["radius"] | it["r"] | 0);
    } else if (strcmp(shape, "round_rect_outline") == 0 || strcmp(shape, "draw_round_rect") == 0) {
      p.shape = PbShape::RoundRectOutline;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.w = (int16_t)(it["w"] | 0);
      p.h = (int16_t)(it["h"] | 0);
      p.r = (int16_t)(it["radius"] | it["r"] | 0);
    } else if (strcmp(shape, "text") == 0 || strcmp(shape, "print") == 0 || strcmp(shape, "label") == 0) {
      p.shape = PbShape::Text;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      const char* tstr = nullptr;
      if (!it["text"].isNull()) {
        tstr = it["text"].as<const char*>();
      } else if (!it["s"].isNull()) {
        tstr = it["s"].as<const char*>();
      } else if (!it["str"].isNull()) {
        tstr = it["str"].as<const char*>();
      }
      if (!tstr || !tstr[0]) {
        continue;
      }
      strncpy(p.text, tstr, kPbMaxTextChars);
      p.text[kPbMaxTextChars] = '\0';
      int tsz = 1;
      if (!it["size"].isNull()) {
        tsz = it["size"].as<int>();
      } else if (!it["text_size"].isNull()) {
        tsz = it["text_size"].as<int>();
      }
      if (tsz < 1) {
        tsz = 1;
      }
      if (tsz > 3) {
        tsz = 3;
      }
      p.text_size = (uint8_t)tsz;
    } else if (strcmp(shape, "image") == 0) {
      p.shape = PbShape::Image;
      p.x = (int16_t)(it["x"] | 0);
      p.y = (int16_t)(it["y"] | 0);
      p.w = (int16_t)(it["w"] | 0);
      p.h = (int16_t)(it["h"] | 0);
      p.asset_index = (uint8_t)constrain((int)(it["asset"] | 0), 0, 255);
      if (p.w < 1 || p.h < 1) {
        continue;
      }
    } else {
      continue;
    }
    out->count++;
  }
}

static void stored_from_elements_v(JsonVariantConst elements_v, StoredLayer* bg, StoredLayer* nose,
                                   StoredLayer* mouth, StoredLayer* eye_l, StoredLayer* eye_r,
                                   StoredLayer* extra) {
  layer_clear(bg);
  layer_clear(nose);
  layer_clear(mouth);
  layer_clear(eye_l);
  layer_clear(eye_r);
  layer_clear(extra);
  if (elements_v.isNull()) {
    return;
  }
  JsonObjectConst elements = elements_v.as<JsonObjectConst>();
  if (!elements["bg"].isNull()) {
    json_fill_layer(elements["bg"].as<JsonArrayConst>(), bg);
  }
  if (!elements["nose"].isNull()) {
    json_fill_layer(elements["nose"].as<JsonArrayConst>(), nose);
  }
  if (!elements["mouth"].isNull()) {
    json_fill_layer(elements["mouth"].as<JsonArrayConst>(), mouth);
  }
  if (!elements["eye_l"].isNull()) {
    json_fill_layer(elements["eye_l"].as<JsonArrayConst>(), eye_l);
  }
  if (!elements["eye_r"].isNull()) {
    json_fill_layer(elements["eye_r"].as<JsonArrayConst>(), eye_r);
  }
  if (!elements["extra"].isNull()) {
    json_fill_layer(elements["extra"].as<JsonArrayConst>(), extra);
  }
}

static int pb_jpeg_draw_cb(JPEGDRAW* pDraw) {
  JpegBlitCtx* ctx = static_cast<JpegBlitCtx*>(pDraw->pUser);
  if (!ctx || !ctx->canvas_buf || !pDraw->pPixels || ctx->iw <= 0 || ctx->ih <= 0 || ctx->dw <= 0 ||
      ctx->dh <= 0 || ctx->canvas_w <= 0 || ctx->canvas_h <= 0) {
    return 0;
  }
  const uint16_t* pixels = pDraw->pPixels;
  const int src_row_w = (pDraw->iWidthUsed > 0) ? pDraw->iWidthUsed : pDraw->iWidth;
  for (int row_i = 0; row_i < pDraw->iHeight; row_i++) {
    const int src_y = pDraw->y + row_i;
    const int dst_y = ctx->dy + (src_y * ctx->dh) / ctx->ih;
    if (dst_y < 0 || dst_y >= ctx->canvas_h) {
      continue;
    }
    uint16_t* dst_row = ctx->canvas_buf + (int32_t)dst_y * (int32_t)ctx->canvas_w;
    for (int col_i = 0; col_i < src_row_w; col_i++) {
      const int src_x = pDraw->x + col_i;
      const int dst_x = ctx->dx + (src_x * ctx->dw) / ctx->iw;
      if (dst_x < 0 || dst_x >= ctx->canvas_w) {
        continue;
      }
      dst_row[dst_x] = pixels[row_i * pDraw->iWidth + col_i];
    }
  }
  return 1;
}

static void draw_jpeg_asset(const StoredPrim& p) {
  if (p.asset_index >= s_render_asset_count) {
    log_warn("[OLED] image asset=%u missing (have %u)", (unsigned)p.asset_index,
             (unsigned)s_render_asset_count);
    return;
  }
  const OledPbAssetBlob& blob = s_render_assets[p.asset_index];
  if (!blob.data || blob.len < 4) {
    return;
  }
  if (!s_canvas || !s_canvas->getBuffer() || s_draw_gfx != s_canvas) {
    log_warn("[OLED] JPEG skip: need PSRAM canvas target");
    return;
  }
  if (s_jpeg_dec.openRAM(blob.data, (int)blob.len, pb_jpeg_draw_cb) != 1) {
    log_warn("[OLED] JPEG openRAM failed asset=%u len=%u", (unsigned)p.asset_index, (unsigned)blob.len);
    return;
  }
  s_jpeg_blit_ctx.canvas_buf = s_canvas->getBuffer();
  s_jpeg_blit_ctx.canvas_w = (int16_t)DESKBOT_DRAW_W;
  s_jpeg_blit_ctx.canvas_h = (int16_t)DESKBOT_DRAW_H;
  s_jpeg_blit_ctx.dx = p.x;
  s_jpeg_blit_ctx.dy = p.y;
  s_jpeg_blit_ctx.dw = p.w;
  s_jpeg_blit_ctx.dh = p.h;
  s_jpeg_blit_ctx.iw = s_jpeg_dec.getWidth();
  s_jpeg_blit_ctx.ih = s_jpeg_dec.getHeight();
  if (s_jpeg_blit_ctx.iw <= 0 || s_jpeg_blit_ctx.ih <= 0) {
    s_jpeg_dec.close();
    return;
  }
  /* 与 GFXcanvas16 / drawRGBBitmap 一致：ESP32 上为小端 RGB565；BIG_ENDIAN 会花屏。 */
  s_jpeg_dec.setPixelType(RGB565_LITTLE_ENDIAN);
  s_jpeg_dec.setUserPointer(&s_jpeg_blit_ctx);
  s_jpeg_dec.decode(0, 0, 0);
  s_jpeg_dec.close();
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
      if (p.w > 0 && p.h > 0 && p.r > 0) {
        (*s_draw_gfx).fillRoundRect(p.x, p.y, p.w, p.h, p.r, col);
      }
      break;
    case PbShape::RoundRectOutline:
      if (p.w > 0 && p.h > 0 && p.r > 0) {
        (*s_draw_gfx).drawRoundRect(p.x, p.y, p.w, p.h, p.r, col);
      }
      break;
    case PbShape::Text:
      if (p.text[0] != '\0') {
        uint8_t sz = p.text_size ? p.text_size : 1;
        if (sz > 3) {
          sz = 3;
        }
        oled_utf8_text_draw(s_draw_gfx, p.x, p.y, p.text, sz, col);
      }
      break;
    case PbShape::Image:
      draw_jpeg_asset(p);
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
  const uint16_t col = curr.color;
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
      if (rad < 1) {
        rad = 1;
      }
      if (curr.shape == PbShape::RoundRect) {
        (*s_draw_gfx).fillRoundRect(x, y, w, h, rad, col);
      } else {
        (*s_draw_gfx).drawRoundRect(x, y, w, h, rad, col);
      }
    } break;
    case PbShape::Image:
      draw_prim(curr);
      break;
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
      oled_utf8_text_draw(s_draw_gfx, x, y, curr.text, sz, col);
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

/** extra 层：先画 image 再画 text/其它，避免全屏 JPEG 盖住「正在拍照」等文案。 */
static void draw_extra_layer_ordered(const StoredLayer* prev, const StoredLayer& curr, float t) {
  const uint8_t ncurr = curr.count;
  if (ncurr == 0) {
    return;
  }
  const uint8_t nprev = prev ? prev->count : 0;
  for (uint8_t pass = 0; pass < 2; pass++) {
    for (uint8_t i = 0; i < ncurr; i++) {
      const StoredPrim& c = curr.prims[i];
      const bool is_image = (c.shape == PbShape::Image);
      if ((pass == 0) != is_image) {
        continue;
      }
      if (i < nprev) {
        draw_prim_lerp(&prev->prims[i], c, t);
      } else {
        draw_prim(c);
      }
    }
  }
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
  memcpy(&s_prev_bg, &bg, sizeof(bg));
  memcpy(&s_prev_nose, &nose, sizeof(nose));
  memcpy(&s_prev_mouth, &mouth, sizeof(mouth));
  memcpy(&s_prev_eye_l, &eye_l, sizeof(eye_l));
  memcpy(&s_prev_eye_r, &eye_r, sizeof(eye_r));
  memcpy(&s_prev_extra, &extra, sizeof(extra));
  s_have_prev = true;
}

static void pb_play_layers_interpolated(const StoredLayer* pbg, const StoredLayer* pn, const StoredLayer* pm,
                                        const StoredLayer* pl, const StoredLayer* pr, const StoredLayer* px,
                                        const StoredLayer& cbg, const StoredLayer& cn, const StoredLayer& cm,
                                        const StoredLayer& cel, const StoredLayer& cer, const StoredLayer& cex,
                                        uint32_t segment_ms, uint16_t bg_rgb565) {
  if (segment_ms == 0) {
    (*s_draw_gfx).fillScreen(bg_rgb565);
    draw_stored_interpolated(pbg, pn, pm, pl, pr, px, cbg, cn, cm, cel, cer, cex, 1.f);
    pb_canvas_push();
    return;
  }

  uint32_t budget = segment_ms;
  if (budget > 300000u) {
    budget = 300000u;
  }

  const uint32_t t0 = millis();
  while (true) {
    const uint32_t elapsed = millis() - t0;
    if (elapsed >= budget) {
      break;
    }
    float t = (float)elapsed / (float)budget;
    if (t > 1.f) {
      t = 1.f;
    }
    (*s_draw_gfx).fillScreen(bg_rgb565);
    draw_stored_interpolated(pbg, pn, pm, pl, pr, px, cbg, cn, cm, cel, cer, cex, t);
    pb_canvas_push();

    const uint32_t after_draw = millis();
    uint32_t remain = (t0 + budget) - after_draw;
    if (remain < kPbOledDisplayBudgetMs) {
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
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

static void pb_render_anim_array_timed(JsonArrayConst anim_arr) {
  if (anim_arr.isNull() || anim_arr.size() == 0) {
    return;
  }

  uint32_t sum_ms = 0;
  for (JsonObjectConst seg : anim_arr) {
    int ms = seg["ms"].is<int>() ? seg["ms"].as<int>() : 0;
    if (ms < 1) {
      ms = 1;
    }
    sum_ms += (uint32_t)ms;
  }

  size_t seg_idx = 0;
  for (JsonObjectConst seg : anim_arr) {
    if (seg_idx >= kPbMaxAnimSegsPerChunk) {
      log_warn("[OLED] anim[] truncated at %u", (unsigned)kPbMaxAnimSegsPerChunk);
      break;
    }
    uint32_t seg_ms = seg["ms"].is<uint32_t>() ? seg["ms"].as<uint32_t>() : 0u;
    if (seg_ms < 1) {
      seg_ms = 1;
    }

    StoredLayer& cbg = s_pb_curr_bg;
    StoredLayer& cn = s_pb_curr_nose;
    StoredLayer& cm = s_pb_curr_mouth;
    StoredLayer& cel = s_pb_curr_eye_l;
    StoredLayer& cer = s_pb_curr_eye_r;
    StoredLayer& cex = s_pb_curr_extra;
    JsonVariantConst elements_v = seg["elements"];
    stored_from_elements_v(elements_v, &cbg, &cn, &cm, &cel, &cer, &cex);

    const uint16_t seg_bg = pb_json_rgb565_field(seg, "bg", DESKBOT_LCD_COLOR_BLACK);

    const StoredLayer* pbg = s_have_prev ? &s_prev_bg : nullptr;
    const StoredLayer* pn = s_have_prev ? &s_prev_nose : nullptr;
    const StoredLayer* pm = s_have_prev ? &s_prev_mouth : nullptr;
    const StoredLayer* pl = s_have_prev ? &s_prev_eye_l : nullptr;
    const StoredLayer* pr = s_have_prev ? &s_prev_eye_r : nullptr;
    const StoredLayer* px = s_have_prev ? &s_prev_extra : nullptr;

    pb_play_layers_interpolated(pbg, pn, pm, pl, pr, px, cbg, cn, cm, cel, cer, cex, seg_ms, seg_bg);
    pb_commit_prev(cbg, cn, cm, cel, cer, cex);
    seg_idx++;
  }
}

/* pb_ver 2：anim[] 为 [{ elements, ms, bg?, phoneme? }, …]；bg/c 为 RGB565（缺省 bg 黑、c 白）。 */
static void pb_render_vector_json(const char* json, size_t json_len) {
  if (!json || json_len == 0) {
    return;
  }
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, json, json_len);
  if (err) {
    log_warn("[OLED] pb vector json parse failed: %s", err.c_str());
    return;
  }

  if (doc.is<JsonArrayConst>()) {
    pb_render_anim_array_timed(doc.as<JsonArrayConst>());
    return;
  }

  log_warn("[OLED] pb anim payload must be anim[] array (pb_ver 2)");
}

struct OledRequest {
  OledScene scene;
  int32_t arg;
  char* json_payload; /* 仅 OLED_SCENE_PB_VECTOR_JSON 使用；malloc 分配，任务内 free。 */
  size_t json_len;
  uint8_t asset_count = 0;
  OledPbAssetBlob assets[kOledMaxPbAssets]{};
  SemaphoreHandle_t notify_sem;
};

static void oled_free_request_assets(OledRequest& req) {
  for (uint8_t i = 0; i < req.asset_count; i++) {
    if (req.assets[i].data) {
      heap_caps_free(req.assets[i].data);
      req.assets[i].data = nullptr;
    }
    req.assets[i].len = 0;
  }
  req.asset_count = 0;
}

QueueHandle_t     s_queue       = nullptr;
TaskHandle_t      s_task        = nullptr;
SemaphoreHandle_t s_done_sem    = nullptr;
SemaphoreHandle_t s_caller_lock = nullptr;

void oled_render_task(void* /*arg*/) {
  OledRequest req{};
  for (;;) {
    if (xQueueReceive(s_queue, &req, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    if (req.scene == OLED_SCENE_PB_VECTOR_JSON) {
      oled_free_render_assets();
      s_render_asset_count = req.asset_count;
      for (uint8_t i = 0; i < req.asset_count && i < kOledMaxPbAssets; i++) {
        s_render_assets[i] = req.assets[i];
        req.assets[i].data = nullptr;
        req.assets[i].len = 0;
      }
      req.asset_count = 0;
      pb_render_vector_json(req.json_payload, req.json_len);
      oled_free_render_assets();
      if (req.json_payload) {
        ::free(req.json_payload);
      }
    } else if (req.scene == OLED_SCENE_RESET) {
      pb_vector_interp_reset();
      oled_free_render_assets();
    }
    if (req.notify_sem) {
      xSemaphoreGive(req.notify_sem);
    }
  }
}

void ensure_render_task() {
  if (s_queue && s_task && s_done_sem && s_caller_lock) {
    return;
  }
  if (!s_queue) {
    /* 队列容量 32：与音频/舵机队列对齐，可缓冲 ~32 个 chunk_ms（~3.2~6.4s）的口型动画。
     * 满则 oled_render_submit_pb_vector_json 走 drop-oldest，永不阻塞 caller（WS 回调）。 */
    s_queue = xQueueCreate(32, sizeof(OledRequest));
  }
  if (!s_done_sem) {
    s_done_sem = xSemaphoreCreateBinary();
  }
  if (!s_caller_lock) {
    s_caller_lock = xSemaphoreCreateMutex();
  }
  if (!s_task) {
    /* U8g2 drawUTF8(gb2312) + JPEGDEC 解码全屏图栈较深；10KB 会触发 canary（见拍照 overlay）。 */
    xTaskCreatePinnedToCore(oled_render_task, "oled_render", 32 * 1024, nullptr, 2, &s_task,
                            APP_CPU_NUM);
  }
}

}  // namespace

void task_setup_display() {
  ensure_render_task();
}

static void oled_fill_request_assets(OledRequest& req, uint8_t* const* asset_bufs,
                                     const size_t* asset_lens, uint8_t asset_count) {
  req.asset_count = 0;
  if (!asset_bufs || !asset_lens || asset_count == 0) {
    return;
  }
  for (uint8_t i = 0; i < asset_count && i < kOledMaxPbAssets; i++) {
    if (!asset_bufs[i] || asset_lens[i] == 0) {
      continue;
    }
    req.assets[req.asset_count].data = asset_bufs[i];
    req.assets[req.asset_count].len = asset_lens[i];
    req.asset_count++;
  }
}

static void oled_enqueue_pb_vector_request(OledRequest& req, bool wait_done) {
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
    OledRequest dropped{};
    xQueueReceive(s_queue, &dropped, 0);
    if (dropped.scene == OLED_SCENE_PB_VECTOR_JSON) {
      if (dropped.json_payload) {
        ::free(dropped.json_payload);
      }
      oled_free_request_assets(dropped);
    }
    xQueueSend(s_queue, &req, 0);
  }
}

void oled_render_submit_pb_vector_json(const char* json, size_t json_len, bool wait_done) {
  oled_render_submit_pb_vector_json(json, json_len, nullptr, nullptr, 0, wait_done);
}

void oled_render_submit_pb_vector_json(const char* json, size_t json_len, uint8_t* const* asset_bufs,
                                       const size_t* asset_lens, uint8_t asset_count,
                                       bool wait_done) {
  ensure_render_task();
  if (!json || json_len == 0) {
    return;
  }
  char* copy = (char*)::malloc(json_len + 1);
  if (!copy) {
    return;
  }
  memcpy(copy, json, json_len);
  copy[json_len] = '\0';

  OledRequest req{};
  req.scene = OLED_SCENE_PB_VECTOR_JSON;
  req.arg = 0;
  req.json_payload = copy;
  req.json_len = json_len;
  oled_fill_request_assets(req, asset_bufs, asset_lens, asset_count);
  oled_enqueue_pb_vector_request(req, wait_done);
}

void oled_render_submit_pb_vector_json_owned(char* json, size_t json_len, uint8_t* const* asset_bufs,
                                             const size_t* asset_lens, uint8_t asset_count,
                                             bool wait_done) {
  ensure_render_task();
  if (!json || json_len == 0) {
    if (json) {
      ::free(json);
    }
    return;
  }
  json[json_len] = '\0';

  OledRequest req{};
  req.scene = OLED_SCENE_PB_VECTOR_JSON;
  req.arg = 0;
  req.json_payload = json;
  req.json_len = json_len;
  oled_fill_request_assets(req, asset_bufs, asset_lens, asset_count);
  oled_enqueue_pb_vector_request(req, wait_done);
}

void oled_render_reset() {
  ensure_render_task();
  xSemaphoreTake(s_caller_lock, portMAX_DELAY);

  /* 1. drain 队列里所有未渲染 req：释放 json_payload + 唤醒 sync caller 防永久阻塞。
   *    drain 必须在入队 RESET 之前，否则 RESET 会被同一轮 drain 误吃。 */
  OledRequest dropped{};
  while (xQueueReceive(s_queue, &dropped, 0) == pdTRUE) {
    if (dropped.scene == OLED_SCENE_PB_VECTOR_JSON) {
      if (dropped.json_payload) {
        ::free(dropped.json_payload);
      }
      oled_free_request_assets(dropped);
    }
    if (dropped.notify_sem) {
      xSemaphoreGive(dropped.notify_sem);
    }
  }

  /* 2. 入队 RESET 到队尾：渲染任务完成"当前正在渲染的 req"（含 vTaskDelay）后 receive 到，
   *    再做 noop 收尾（不清屏、保留当前画面）。 */
  OledRequest req{};
  req.scene = OLED_SCENE_RESET;
  req.arg = 0;
  req.json_payload = nullptr;
  req.json_len = 0;
  req.notify_sem = nullptr;
  xQueueSend(s_queue, &req, portMAX_DELAY);

  xSemaphoreGive(s_caller_lock);
}

unsigned oled_render_input_queue_depth(void) {
  ensure_render_task();
  return s_queue ? (unsigned)uxQueueMessagesWaiting(s_queue) : 0u;
}
