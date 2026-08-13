#include "display.h"
#include "display_text.h"
#include "pb_model.h"

#include "logger.h"
#include "utils/utils.h"

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include <atomic>
#include <cstring>
#include "esp_heap_caps.h"

/* ──────────────────────────────────────────────────────────────────────
 * 全局对象 & PSRAM 帧缓冲
 * ──────────────────────────────────────────────────────────────────── */

DeskbotDisplay g_display(DESKBOT_DISPLAY_CS, DESKBOT_DISPLAY_DC, -1);

/** PSRAM canvas：矢量动画先在 PSRAM 绘制，完成后整帧 DMA 推送，消除逐像素撕裂。 */
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

/* ──────────────────────────────────────────────────────────────────────
 * FreeRTOS 渲染任务
 * ────────────────────────────────────────────────────────────────────── */

namespace {

static constexpr uint32_t kPbDisplayBudgetMs      = 40;
static constexpr uint8_t  kPbMaxPrimsPerLayer      = 16;
static constexpr UBaseType_t kPbDisplayQueueDepth  = DESKBOT_PB_EXECUTOR_QUEUE_DEPTH;
static constexpr size_t   kPbMaxTextChars           = 128;
static constexpr size_t   kPbMaxAnimSegsPerChunk    = 64;
static constexpr int      kLayerCount               = 6;  /* bg,nose,mouth,eye_l,eye_r,extra */

/* ── PB 图元 & 层存储 ── */

struct StoredPrim {
  pb_anim_element_shape shape;
  uint16_t color;
  int16_t x, y, w, h, r;
  int16_t x1, y1, x2, y2;
  uint8_t text_size;
  char    text[kPbMaxTextChars + 1];
};

struct StoredLayer {
  uint8_t     count;
  StoredPrim  prims[kPbMaxPrimsPerLayer];
};

struct StoredLayerPool {
  StoredLayer prev[kLayerCount];
  StoredLayer curr[kLayerCount];
};

static StoredLayerPool* s_layer_pool = nullptr;
static bool s_have_prev = false;

static bool ensure_stored_layer_pool() {
  if (s_layer_pool) return true;
  s_layer_pool = static_cast<StoredLayerPool*>(
      heap_caps_calloc(1, sizeof(StoredLayerPool), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!s_layer_pool) {
    log_error("[DISPLAY] PSRAM StoredLayer pool alloc failed bytes=%u", (unsigned)sizeof(StoredLayerPool));
    return false;
  }
  log_info("[DISPLAY] StoredLayer pool in PSRAM bytes=%u", (unsigned)sizeof(StoredLayerPool));
  return true;
}

/* ── 插值工具 ── */

static int lerp_i16(int16_t a, int16_t b, float t) {
  return (int)lroundf((1.f - t) * (float)a + t * (float)b);
}

static uint16_t lerp_rgb565(uint16_t a, uint16_t b, float t) {
  const int r0 = (a >> 11) & 0x1f, g0 = (a >> 5) & 0x3f, b0 = a & 0x1f;
  const int r1 = (b >> 11) & 0x1f, g1 = (b >> 5) & 0x3f, b1 = b & 0x1f;
  const int r = (int)lroundf((1.f - t) * (float)r0 + t * (float)r1);
  const int g = (int)lroundf((1.f - t) * (float)g0 + t * (float)g1);
  const int bl = (int)lroundf((1.f - t) * (float)b0 + t * (float)b1);
  return (uint16_t)(((r & 0x1f) << 11) | ((g & 0x3f) << 5) | (bl & 0x1f));
}

static void pb_vector_interp_reset() {
  if (!ensure_stored_layer_pool()) return;
  s_have_prev = false;
  memset(s_layer_pool->prev, 0, sizeof(s_layer_pool->prev));
}

/* ── 图元转换 & 绘制 ── */

static bool prim_from_pb_element(const pb_anim_element& in, StoredPrim& p) {
  memset(&p, 0, sizeof(p));
  if (in.shape == pb_anim_element_shape::none) return false;
  p.shape = in.shape;
  p.color = in.color;
  p.x  = (int16_t)in.x;   p.y  = (int16_t)in.y;
  p.w  = (int16_t)in.w;   p.h  = (int16_t)in.h;   p.r = (int16_t)in.r;
  p.x1 = (int16_t)in.x1;  p.y1 = (int16_t)in.y1;
  p.x2 = (int16_t)in.x2;  p.y2 = (int16_t)in.y2;
  if (p.shape == pb_anim_element_shape::text) {
    if (!in.text[0]) return false;
    strncpy(p.text, in.text, kPbMaxTextChars);
    p.text[kPbMaxTextChars] = '\0';
    int tsz = in.text_size;
    if (tsz < 1) tsz = 1;
    if (tsz > 3) tsz = 3;
    p.text_size = (uint8_t)tsz;
  }
  return true;
}

static void layer_fill_from_pb(const pb_anim_element* elements, size_t count,
                               pb_anim_element_layer layer, StoredLayer* out) {
  out->count = 0;
  if (!elements || count == 0) return;
  for (size_t i = 0; i < count; ++i) {
    if (elements[i].layer != layer || out->count >= kPbMaxPrimsPerLayer) continue;
    if (prim_from_pb_element(elements[i], out->prims[out->count])) {
      out->count++;
    }
  }
}

static void stored_from_pb(const pb_anim_element* elements, size_t count, StoredLayer* out) {
  for (int i = 0; i < kLayerCount; ++i) {
    layer_fill_from_pb(elements, count, static_cast<pb_anim_element_layer>(i), &out[i]);
  }
}

static void draw_prim(const StoredPrim& p) {
  const uint16_t col = p.color;
  switch (p.shape) {
    case pb_anim_element_shape::rect:
      if (p.w > 0 && p.h > 0) s_draw_gfx->fillRect(p.x, p.y, p.w, p.h, col);
      break;
    case pb_anim_element_shape::rect_outline:
      if (p.w > 0 && p.h > 0) s_draw_gfx->drawRect(p.x, p.y, p.w, p.h, col);
      break;
    case pb_anim_element_shape::circle:
      if (p.r > 0) s_draw_gfx->fillCircle(p.x, p.y, p.r, col);
      break;
    case pb_anim_element_shape::circle_outline:
      if (p.r > 0) s_draw_gfx->drawCircle(p.x, p.y, p.r, col);
      break;
    case pb_anim_element_shape::line:
      s_draw_gfx->drawLine(p.x1, p.y1, p.x2, p.y2, col);
      break;
    case pb_anim_element_shape::ellipse:
      if (p.w > 0 && p.h > 0) s_draw_gfx->drawEllipse(p.x, p.y, p.w, p.h, col);
      break;
    case pb_anim_element_shape::ellipse_fill:
      if (p.w > 0 && p.h > 0) s_draw_gfx->fillEllipse(p.x, p.y, p.w, p.h, col);
      break;
    case pb_anim_element_shape::round_rect:
      if (p.w > 0 && p.h > 0) {
        if (p.r > 0) s_draw_gfx->fillRoundRect(p.x, p.y, p.w, p.h, p.r, col);
        else         s_draw_gfx->fillRect(p.x, p.y, p.w, p.h, col);
      }
      break;
    case pb_anim_element_shape::round_rect_outline:
      if (p.w > 0 && p.h > 0) {
        if (p.r > 0) s_draw_gfx->drawRoundRect(p.x, p.y, p.w, p.h, p.r, col);
        else         s_draw_gfx->drawRect(p.x, p.y, p.w, p.h, col);
      }
      break;
    case pb_anim_element_shape::text:
      if (p.text[0] != '\0') {
        display_text_draw(s_draw_gfx, p.x, p.y, p.text, p.text_size, col);
      }
      break;
    default: break;
  }
}

/* ── 帧间插值绘制 ── */

static void draw_prim_lerp(const StoredPrim* prev, const StoredPrim& curr, float t) {
  if (!prev || prev->shape != curr.shape || curr.shape == pb_anim_element_shape::none) {
    draw_prim(curr);
    return;
  }
  const uint16_t col = lerp_rgb565(prev->color, curr.color, t);
  switch (curr.shape) {
    case pb_anim_element_shape::rect:
    case pb_anim_element_shape::rect_outline: {
      int x = lerp_i16(prev->x, curr.x, t), y = lerp_i16(prev->y, curr.y, t);
      int w = lerp_i16(prev->w, curr.w, t), h = lerp_i16(prev->h, curr.h, t);
      if (w < 1) w = 1; if (h < 1) h = 1;
      if (curr.shape == pb_anim_element_shape::rect)
        s_draw_gfx->fillRect(x, y, w, h, col);
      else
        s_draw_gfx->drawRect(x, y, w, h, col);
    } break;
    case pb_anim_element_shape::circle:
    case pb_anim_element_shape::circle_outline: {
      int x = lerp_i16(prev->x, curr.x, t), y = lerp_i16(prev->y, curr.y, t);
      int r = lerp_i16(prev->r, curr.r, t);
      if (r > 0) {
        if (curr.shape == pb_anim_element_shape::circle)
          s_draw_gfx->fillCircle(x, y, r, col);
        else
          s_draw_gfx->drawCircle(x, y, r, col);
      }
    } break;
    case pb_anim_element_shape::line: {
      int x1 = lerp_i16(prev->x1, curr.x1, t), y1 = lerp_i16(prev->y1, curr.y1, t);
      int x2 = lerp_i16(prev->x2, curr.x2, t), y2 = lerp_i16(prev->y2, curr.y2, t);
      s_draw_gfx->drawLine(x1, y1, x2, y2, col);
    } break;
    case pb_anim_element_shape::ellipse:
    case pb_anim_element_shape::ellipse_fill: {
      int x = lerp_i16(prev->x, curr.x, t), y = lerp_i16(prev->y, curr.y, t);
      int rw = lerp_i16(prev->w, curr.w, t), rh = lerp_i16(prev->h, curr.h, t);
      if (rw < 1) rw = 1; if (rh < 1) rh = 1;
      if (curr.shape == pb_anim_element_shape::ellipse_fill)
        s_draw_gfx->fillEllipse(x, y, rw, rh, col);
      else
        s_draw_gfx->drawEllipse(x, y, rw, rh, col);
    } break;
    case pb_anim_element_shape::round_rect:
    case pb_anim_element_shape::round_rect_outline: {
      int x = lerp_i16(prev->x, curr.x, t), y = lerp_i16(prev->y, curr.y, t);
      int w = lerp_i16(prev->w, curr.w, t), h = lerp_i16(prev->h, curr.h, t);
      int rad = lerp_i16(prev->r, curr.r, t);
      if (w < 1) w = 1; if (h < 1) h = 1;
      if (curr.shape == pb_anim_element_shape::round_rect) {
        if (rad > 0) s_draw_gfx->fillRoundRect(x, y, w, h, rad, col);
        else         s_draw_gfx->fillRect(x, y, w, h, col);
      } else {
        if (rad > 0) s_draw_gfx->drawRoundRect(x, y, w, h, rad, col);
        else         s_draw_gfx->drawRect(x, y, w, h, col);
      }
    } break;
    case pb_anim_element_shape::text: {
      /* 文本内容或字号变化时直接画当前帧，不插值 */
      if (strcmp(prev->text, curr.text) != 0 || prev->text_size != curr.text_size) {
        draw_prim(curr);
        break;
      }
      int x = lerp_i16(prev->x, curr.x, t), y = lerp_i16(prev->y, curr.y, t);
      display_text_draw(s_draw_gfx, x, y, curr.text, curr.text_size, col);
    } break;
    default: break;
  }
}

static void draw_layer_lerp(const StoredLayer* prev, const StoredLayer& curr, float t) {
  const uint8_t ncurr = curr.count;
  if (ncurr == 0) {
    /* 口型 chunk 常只带 mouth：未指定的图层沿用上一帧 */
    if (prev && prev->count > 0) {
      for (uint8_t i = 0; i < prev->count; i++) draw_prim(prev->prims[i]);
    }
    return;
  }
  const uint8_t nprev = prev ? prev->count : 0;
  for (uint8_t i = 0; i < ncurr; i++) {
    if (i < nprev) draw_prim_lerp(&prev->prims[i], curr.prims[i], t);
    else           draw_prim(curr.prims[i]);
  }
}

/** canvas 整帧推送到物理屏。setAddrWindow+writePixels 批量写入，避免 drawRGBBitmap 逐像素开销。 */
static inline void pb_canvas_push() {
  if (s_canvas && s_canvas->getBuffer()) {
    g_display.startWrite();
    g_display.setAddrWindow(DESKBOT_DISPLAY_CANVAS_X0, 0,
                            DESKBOT_PB_COORD_W, DESKBOT_PB_COORD_H);
    g_display.writePixels(s_canvas->getBuffer(),
                          (uint32_t)DESKBOT_PB_COORD_W * DESKBOT_PB_COORD_H);
    g_display.endWrite();
  }
}

/* ── 队列 & cancel ── */

struct DisplayRequest {
  DisplayJobType type = DISPLAY_JOB_PB_ANIM_FRAMES;
  pb_anim_frame* anim_frames = nullptr;
  size_t anim_frame_count = 0;
  SemaphoreHandle_t notify_sem = nullptr;
};

static std::atomic<bool>   s_need_cancel{false};
static QueueHandle_t       s_queue       = nullptr;
static TaskHandle_t        s_task        = nullptr;
static SemaphoreHandle_t   s_done_sem    = nullptr;
static SemaphoreHandle_t   s_caller_lock = nullptr;

static void display_free_request_anim_frames(DisplayRequest& req) {
  if (req.anim_frames) {
    pb_anim_frames_free(req.anim_frames, req.anim_frame_count);
    req.anim_frames = nullptr;
    req.anim_frame_count = 0;
  }
}

static void free_display_request(DisplayRequest& req) {
  if (req.type == DISPLAY_JOB_PB_ANIM_FRAMES) display_free_request_anim_frames(req);
  if (req.notify_sem) { xSemaphoreGive(req.notify_sem); req.notify_sem = nullptr; }
}

/**
 * need_cancel==false → false。
 * 否则非阻塞丢弃旧任务，见到 cancel 则清 flag 并 return true。
 * 队列空且未见 cancel → return true，保持 need_cancel。
 */
static bool poll_cancel() {
  if (!s_need_cancel.load(std::memory_order_acquire)) return false;
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

/** 按 segment_ms 在 prev↔curr 之间插值渲染。@return false 若中途取消。 */
static bool pb_play_layers_interpolated(const StoredLayer* prev, const StoredLayer* curr,
                                        uint32_t segment_ms, uint16_t bg_rgb565) {
  if (poll_cancel()) return false;
  if (segment_ms == 0) {
    s_draw_gfx->fillScreen(bg_rgb565);
    for (int i = 0; i < kLayerCount; ++i) draw_layer_lerp(prev ? &prev[i] : nullptr, curr[i], 1.f);
    pb_canvas_push();
    return true;
  }

  uint32_t budget = segment_ms;
  if (budget > 300000u) budget = 300000u;

  const uint32_t t0 = millis();
  uint32_t pushes = 0, first_draw_ms = 0, first_push_ms = 0;
  while (true) {
    if (poll_cancel()) return false;
    const uint32_t elapsed = millis() - t0;
    if (elapsed >= budget) break;
    float t = (float)elapsed / (float)budget;
    if (t > 1.f) t = 1.f;

    const uint32_t t_draw0 = millis();
    s_draw_gfx->fillScreen(bg_rgb565);
    for (int i = 0; i < kLayerCount; ++i)
      draw_layer_lerp(prev ? &prev[i] : nullptr, curr[i], t);
    const uint32_t draw_ms = millis() - t_draw0;

    const uint32_t t_push0 = millis();
    pb_canvas_push();
    const uint32_t push_ms = millis() - t_push0;
    pushes++;
    if (pushes == 1) { first_draw_ms = draw_ms; first_push_ms = push_ms; }

    const uint32_t remain = (t0 + budget) - millis();
    if (remain < kPbDisplayBudgetMs) {
      if (remain > 0) vTaskDelay(pdMS_TO_TICKS(remain));
      break;
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }

  /* 对齐 segment_ms 时长 */
  while ((int32_t)(millis() - t0) < (int32_t)budget) {
    if (poll_cancel()) return false;
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  const uint32_t wall = millis() - t0;
  log_warn("[PB_LAT] display_seg budget=%u wall=%u pushes=%u first_draw_ms=%u first_push_ms=%u",
           (unsigned)budget, (unsigned)wall, (unsigned)pushes,
           (unsigned)first_draw_ms, (unsigned)first_push_ms);
  return true;
}

/* ── 帧序列渲染 ── */

static void pb_render_anim_frames_timed(const pb_anim_frame* frames, size_t frame_count) {
  if (!s_layer_pool || !frames || frame_count == 0) return;
  uint32_t budget_sum = 0;
  for (size_t i = 0; i < frame_count; ++i)
    budget_sum += frames[i].ms > 0 ? (uint32_t)frames[i].ms : 1u;
  const uint32_t job_t0 = millis();
  log_warn("[PB_LAT] display_job_begin frames=%u budget_sum=%u",
           (unsigned)frame_count, (unsigned)budget_sum);

  size_t seg_idx = 0;
  for (size_t i = 0; i < frame_count; ++i) {
    if (poll_cancel()) return;
    if (seg_idx >= kPbMaxAnimSegsPerChunk) {
      log_warn("[DISPLAY] anim[] truncated at %u", (unsigned)kPbMaxAnimSegsPerChunk);
      break;
    }
    const pb_anim_frame& seg = frames[i];
    uint32_t seg_ms = seg.ms > 0 ? (uint32_t)seg.ms : 1u;

    stored_from_pb(seg.elements, seg.element_count, s_layer_pool->curr);
    const StoredLayer* prev = s_have_prev ? s_layer_pool->prev : nullptr;
    if (!pb_play_layers_interpolated(prev, s_layer_pool->curr, seg_ms,
                                     DESKBOT_DISPLAY_COLOR_BLACK)) {
      return;
    }
    /* 提交 curr → prev（空层保留旧值，保证增量 chunk 正确） */
    for (int j = 0; j < kLayerCount; ++j) {
      if (s_layer_pool->curr[j].count > 0)
        memcpy(&s_layer_pool->prev[j], &s_layer_pool->curr[j], sizeof(StoredLayer));
    }
    s_have_prev = true;
    seg_idx++;
  }
  log_warn("[PB_LAT] display_job_end frames=%u budget_sum=%u wall=%u",
           (unsigned)frame_count, (unsigned)budget_sum, (unsigned)(millis() - job_t0));
}

static void execute_display_job(DisplayRequest& req) {
  if (req.type == DISPLAY_JOB_PB_ANIM_FRAMES) {
    pb_render_anim_frames_timed(req.anim_frames, req.anim_frame_count);
    display_free_request_anim_frames(req);
  }
  if (req.notify_sem) { xSemaphoreGive(req.notify_sem); req.notify_sem = nullptr; }
}

static void task_loop_display_render(void*) {
  DisplayRequest req{};
  for (;;) {
    (void)poll_cancel();
    if (xQueueReceive(s_queue, &req, portMAX_DELAY) != pdTRUE) continue;
    if (req.type == DISPLAY_JOB_CANCEL) {
      /* 空闲时 task 阻塞在 Receive，Cancel 直接出队，不经过 poll_cancel。
       * 若不在此清 flag，s_need_cancel 永久为 true，后续口型/表情全被丢掉。 */
      if (s_need_cancel.exchange(false, std::memory_order_acq_rel)) {
        pb_vector_interp_reset();
        log_info("[DISPLAY] cancel");
      }
      continue;
    }
    execute_display_job(req);
  }
}

static void display_enqueue_request(DisplayRequest& req, bool wait_done) {
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
    if (xQueueReceive(s_queue, &dropped, 0) == pdTRUE) free_display_request(dropped);
    if (xQueueSend(s_queue, &req, 0) != pdTRUE) {
      log_warn("[DISPLAY] queue full after drop-oldest; free submit");
      free_display_request(req);
    }
  }
}

}  // namespace

/* ──────────────────────────────────────────────────────────────────────
 * Public API — 初始化 & 任务启动
 * ──────────────────────────────────────────────────────────────────── */

void setup_display() {
  g_display.setupPanel();
  if (g_display.width() <= 0 || g_display.height() <= 0) {
    log_error("[DISPLAY] panel size invalid w=%d h=%d", (int)g_display.width(), (int)g_display.height());
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
  log_info("[DISPLAY] ready ST7789 %dx%d off=%d,%d SPI mosi=%d sck=%d cs=%d dc=%d",
           (int)g_display.width(), (int)g_display.height(), DESKBOT_DISPLAY_COL_OFFSET,
           DESKBOT_DISPLAY_ROW_OFFSET, DESKBOT_DISPLAY_MOSI, DESKBOT_DISPLAY_SCK,
           DESKBOT_DISPLAY_CS, DESKBOT_DISPLAY_DC);
  g_display.setTextSize(DESKBOT_DISPLAY_BOOT_TEXT_SIZE);
  g_display.setTextColor(DESKBOT_DISPLAY_COLOR_WHITE, DESKBOT_DISPLAY_COLOR_BLACK);
}

void task_setup_display() {
  if (s_queue && s_task && s_done_sem && s_caller_lock) return;
  if (!ensure_stored_layer_pool()) {
    log_error("[DISPLAY] StoredLayer pool unavailable");
    return;
  }
  if (!s_queue)       s_queue       = xQueueCreate(kPbDisplayQueueDepth, sizeof(DisplayRequest));
  if (!s_done_sem)    s_done_sem    = xSemaphoreCreateBinary();
  if (!s_caller_lock) s_caller_lock = xSemaphoreCreateMutex();
  if (!s_task) {
    /* U8g2 drawUTF8(gb2312) 栈较深；10KB 会触发 canary。
     * prio 贴近 speaker(7)：预取可达 1s，过低会被音频饿死导致嘴形滞后。 */
    const BaseType_t rc =
        utils_task_create_pinned(task_loop_display_render, "display_render", 24 * 1024, nullptr, 6,
                                 &s_task, APP_CPU_NUM);
    if (rc != pdPASS) {
      log_error("[DISPLAY] task create failed rc=%d", (int)rc);
      s_task = nullptr;
    }
  }
}

/* ──────────────────────────────────────────────────────────────────────
 * Public API — 提交 & 控制
 * ──────────────────────────────────────────────────────────────────── */

Adafruit_GFX* display_guide_target_begin(bool clear_black) {
  Adafruit_GFX* target = (s_canvas && s_canvas->getBuffer())
                             ? static_cast<Adafruit_GFX*>(s_canvas)
                             : static_cast<Adafruit_GFX*>(&g_display);
  if (clear_black) target->fillScreen(DESKBOT_DISPLAY_COLOR_BLACK);
  return target;
}

void display_guide_target_end() {
  if (s_canvas && s_canvas->getBuffer()) pb_canvas_push();
}

void display_render_submit_pb_anim_frames_owned(pb_anim_frame* frames, size_t frame_count,
                                                bool wait_done) {
  if (!frames || frame_count == 0) {
    if (frames) pb_anim_frames_free(frames, frame_count);
    return;
  }
  DisplayRequest req{};
  req.type = DISPLAY_JOB_PB_ANIM_FRAMES;
  req.anim_frames = frames;
  req.anim_frame_count = frame_count;
  display_enqueue_request(req, wait_done);
}

void display_abort() {
  if (!s_queue) return;
  DisplayRequest req{};
  req.type = DISPLAY_JOB_CANCEL;
  s_need_cancel.store(true, std::memory_order_release);
  (void)xQueueSend(s_queue, &req, portMAX_DELAY);
}

void display_render_reset() { display_abort(); }

unsigned display_render_input_queue_depth(void) {
  return s_queue ? (unsigned)uxQueueMessagesWaiting(s_queue) : 0;
}
