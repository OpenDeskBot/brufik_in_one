#include "camera.h"

#include "deskbot_config.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <Arduino.h>
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "img_converters.h"
#include <atomic>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

/* Seeed XIAO ESP32S3 Sense 摄像头引脚（esp32-camera 示例同源） */
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39
#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

static constexpr bool kCameraCaptureEnabled = true;
static constexpr size_t kMaxJpegBin = 32 * 1024;
static constexpr uint32_t kFbNullLogIntervalMs = 30000u;
static constexpr uint8_t kJpegQuality = 18;
/** WiFi 后重 init 时丢掉前几帧，让 OV2640 AWB/AGC 收敛（否则常年偏绿）。 */
static constexpr int kAwakenDiscardFrames = 30;
/** warmup 连续 fb_get 失败次数：JPEG 模式会阻塞数秒/帧，不可空转满 12 次。 */
static constexpr int kAwakenMaxNulls = 2;

static bool s_camera_ok = false;
static bool s_hw_inited = false;
static bool s_task_ready = false;
static std::atomic<uint32_t> s_interval_ms{1000u};
static uint32_t s_last_capture_ms = 0;
static uint32_t s_last_fb_null_log_ms = 0;
static uint32_t s_seq = 0;
static uint32_t s_fb_null_count = 0;
static TaskHandle_t s_task = nullptr;

static constexpr uint32_t kCameraTaskStack = 16 * 1024;
static constexpr UBaseType_t kCameraTaskPrio = 3;

static void camera_fill_pins(camera_config_t& config) {
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;
  config.frame_size = FRAMESIZE_QVGA;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = kJpegQuality;
  config.fb_count = 2;
}

static void camera_tune_sensor(void) {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    return;
  }
  if (s->set_special_effect) {
    s->set_special_effect(s, 0);
  }
  /* 荧光灯：AWB+Auto；关 awb_gain 会整幅偏绿，固定 Home/Office 也不稳。 */
  if (s->set_whitebal) {
    s->set_whitebal(s, 1);
  }
  if (s->set_awb_gain) {
    s->set_awb_gain(s, 1);
  }
  if (s->set_wb_mode) {
    s->set_wb_mode(s, 0); /* Auto */
  }
  if (s->set_saturation) {
    s->set_saturation(s, 0);
  }
  if (s->set_brightness) {
    s->set_brightness(s, 0);
  }
  if (s->set_contrast) {
    s->set_contrast(s, 0);
  }
  if (s->set_lenc) {
    s->set_lenc(s, 1);
  }
  if (s->set_bpc) {
    s->set_bpc(s, 1);
  }
  if (s->set_wpc) {
    s->set_wpc(s, 1);
  }
  if (s->set_raw_gma) {
    s->set_raw_gma(s, 1);
  }
}

/**
 * S3 上 RGB565 上行常呈品红/绿斑：DMA 缓冲实际可能是 YUV422。
 * 编码前在 RGB565 / YUYV / UYVY 间择优，再压 JPEG。
 */
enum class CamPack : uint8_t {
  kRgbBe = 0,
  kRgbLe = 1,
  kBgrBe = 2,
  kBgrLe = 3,
  kYuyv = 4,
  kUyvy = 5,
};

static CamPack s_cam_pack = CamPack::kYuyv;

static const char* cam_pack_name(CamPack p) {
  switch (p) {
    case CamPack::kRgbBe:
      return "RGB-BE";
    case CamPack::kRgbLe:
      return "RGB-LE";
    case CamPack::kBgrBe:
      return "BGR-BE";
    case CamPack::kBgrLe:
      return "BGR-LE";
    case CamPack::kYuyv:
      return "YUYV";
    case CamPack::kUyvy:
      return "UYVY";
  }
  return "?";
}

static inline uint8_t cam_clamp_u8(int v) {
  if (v < 0) {
    return 0;
  }
  if (v > 255) {
    return 255;
  }
  return (uint8_t)v;
}

static inline void yuv_to_rgb(int y, int u, int v, uint8_t* r, uint8_t* g, uint8_t* b) {
  const int c = y - 16;
  const int d = u - 128;
  const int e = v - 128;
  *r = cam_clamp_u8((298 * c + 409 * e + 128) >> 8);
  *g = cam_clamp_u8((298 * c - 100 * d - 208 * e + 128) >> 8);
  *b = cam_clamp_u8((298 * c + 516 * d + 128) >> 8);
}

static inline void unpack_rgb565(const uint8_t* p, CamPack pack, uint8_t* r, uint8_t* g, uint8_t* b) {
  uint8_t hi = p[0];
  uint8_t lo = p[1];
  if (pack == CamPack::kRgbLe || pack == CamPack::kBgrLe) {
    const uint8_t t = hi;
    hi = lo;
    lo = t;
  }
  const uint8_t c0 = hi & 0xF8;
  const uint8_t c1 = (uint8_t)(((hi & 0x07) << 5) | ((lo & 0xE0) >> 3));
  const uint8_t c2 = (uint8_t)((lo & 0x1F) << 3);
  if (pack == CamPack::kBgrBe || pack == CamPack::kBgrLe) {
    *b = c0;
    *g = c1;
    *r = c2;
  } else {
    *r = c0;
    *g = c1;
    *b = c2;
  }
}

static inline void unpack_sample(const uint8_t* buf, size_t off, CamPack pack, uint8_t* r, uint8_t* g,
                                 uint8_t* b) {
  if (pack == CamPack::kYuyv || pack == CamPack::kUyvy) {
    const size_t base = off & ~((size_t)3);
    uint8_t y;
    uint8_t u;
    uint8_t v;
    if (pack == CamPack::kYuyv) {
      y = (off & 2u) ? buf[base + 2] : buf[base + 0];
      u = buf[base + 1];
      v = buf[base + 3];
    } else {
      y = (off & 2u) ? buf[base + 3] : buf[base + 1];
      u = buf[base + 0];
      v = buf[base + 2];
    }
    yuv_to_rgb(y, u, v, r, g, b);
    return;
  }
  unpack_rgb565(buf + off, pack, r, g, b);
}

/** 越小越好：亮部应接近中性（白灯管），并惩罚品红/过绿。 */
static float camera_pack_badness(const uint8_t* buf, size_t len, CamPack pack) {
  if (!buf || len < 8) {
    return 1e9f;
  }
  uint64_t bright_chroma = 0;
  uint32_t bright_n = 0;
  uint32_t green_dom = 0;
  uint32_t magenta_dom = 0;
  uint32_t samples = 0;
  uint64_t sum_r = 0;
  uint64_t sum_g = 0;
  uint64_t sum_b = 0;
  for (size_t i = 0; i + 3 < len; i += 16) {
    uint8_t r;
    uint8_t g;
    uint8_t b;
    unpack_sample(buf, i, pack, &r, &g, &b);
    sum_r += r;
    sum_g += g;
    sum_b += b;
    samples += 1;
    const int mx = (int)r > (int)g ? ((int)r > (int)b ? (int)r : (int)b) : ((int)g > (int)b ? (int)g : (int)b);
    if (mx >= 180) {
      bright_n += 1;
      bright_chroma += (uint32_t)abs((int)r - (int)g) + (uint32_t)abs((int)g - (int)b) +
                       (uint32_t)abs((int)b - (int)r);
    }
    if ((int)g > (int)r + 20 && (int)g > (int)b + 20) {
      green_dom += 1;
    }
    if ((int)r > (int)g + 25 && (int)b > (int)g + 25) {
      magenta_dom += 1;
    }
  }
  if (samples == 0) {
    return 1e9f;
  }
  const float inv = 1.0f / (float)samples;
  const float mr = (float)sum_r * inv;
  const float mg = (float)sum_g * inv;
  const float mb = (float)sum_b * inv;
  const float bright_pen =
      (bright_n > 0) ? ((float)bright_chroma / (float)bright_n) / 255.0f : 0.5f;
  const float mean_chroma =
      (fabsf(mr - mg) + fabsf(mg - mb) + fabsf(mb - mr)) / 255.0f;
  /* 非对称项：室内肤色/暖光通常 R 略高于 B；用于区分 RGB vs BGR。 */
  const float rb_bias = (mb > mr + 5.0f) ? (mb - mr) / 255.0f : 0.0f;
  return bright_pen * 4.0f + (float)magenta_dom * inv * 3.0f + (float)green_dom * inv * 2.0f +
         mean_chroma + rb_bias * 1.5f;
}

static CamPack camera_pick_pack(const uint8_t* buf, size_t len) {
  const CamPack cands[] = {CamPack::kRgbBe, CamPack::kRgbLe, CamPack::kBgrBe,
                           CamPack::kBgrLe, CamPack::kYuyv,  CamPack::kUyvy};
  CamPack best = CamPack::kUyvy;
  float best_score = 1e9f;
  for (CamPack p : cands) {
    const float sc = camera_pack_badness(buf, len, p);
    log_warn("[CAMERA] pack probe %s score=%.3f", cam_pack_name(p), (double)sc);
    if (sc < best_score) {
      best_score = sc;
      best = p;
    }
  }
  /* 实测 UYVY 更差；保持分数最优（通常 RGB-BE）。 */
  return best;
}

/** 解包为 BGR888（供 fmt2jpg RGB888：库内会 BGR→RGB）。 */
static bool camera_buf_to_jpg(const uint8_t* src, size_t src_len, uint16_t width, uint16_t height,
                              CamPack pack, uint8_t quality, uint8_t** jpg, size_t* jpg_len) {
  if (!src || !jpg || !jpg_len || width == 0 || height == 0) {
    return false;
  }
  const size_t px = (size_t)width * (size_t)height;
  if (src_len < px * 2u) {
    return false;
  }
  uint8_t* bgr = (uint8_t*)psram_malloc(px * 3u);
  if (!bgr) {
    return false;
  }
  uint8_t* dst = bgr;
  for (size_t i = 0; i < px; ++i) {
    uint8_t r;
    uint8_t g;
    uint8_t b;
    unpack_sample(src, i * 2u, pack, &r, &g, &b);
    dst[0] = b;
    dst[1] = g;
    dst[2] = r;
    dst += 3;
  }
  const bool ok = fmt2jpg(bgr, px * 3u, width, height, PIXFORMAT_RGB888, quality, jpg, jpg_len);
  free(bgr);
  return ok;
}

static bool camera_fb_to_jpg(const camera_fb_t* fb, uint8_t quality, uint8_t** jpg, size_t* jpg_len) {
  if (!fb) {
    return false;
  }
  if (fb->format == PIXFORMAT_JPEG) {
    return false;
  }
  return camera_buf_to_jpg(fb->buf, fb->len, fb->width, fb->height, s_cam_pack, quality, jpg, jpg_len);
}

/**
 * 优先传感器硬件 JPEG（OV2640 内部出图，颜色通常正确）。
 * fb_get 过慢则回落 RGB565 + 手动解包编码。
 */
static bool camera_init_hw(void) {
  if (s_hw_inited) {
    esp_camera_deinit();
    s_hw_inited = false;
    delay(30);
  }

  auto probe_after_init = [](const char* mode_tag) -> bool {
    sensor_t* s = esp_camera_sensor_get();
    if (!s) {
      log_error("[CAMERA] sensor_get returned null after init");
      return false;
    }
    log_info("[CAMERA] sensor PID=0x%x%s mode=%s", (unsigned)s->id.PID,
             s->id.PID == OV2640_PID ? " OV2640" : "",
             mode_tag);
    camera_tune_sensor();
    /* JPEG 模式冷启动常连续 null，多等一会再抓。 */
    if (strstr(mode_tag, "JPEG") != nullptr) {
      delay(300);
    }
    const int warm_need = (strstr(mode_tag, "JPEG") != nullptr) ? 3 : kAwakenDiscardFrames;
    const int warm_null_max = (strstr(mode_tag, "JPEG") != nullptr) ? 15 : kAwakenMaxNulls;
    int got = 0;
    int nulls = 0;
    for (int i = 0; i < warm_need; ++i) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (!fb) {
        nulls += 1;
        if (nulls >= warm_null_max) {
          break;
        }
        delay(strstr(mode_tag, "JPEG") != nullptr ? 200 : 30);
        continue;
      }
      nulls = 0;
      got += 1;
      esp_camera_fb_return(fb);
    }
    log_info("[CAMERA] discarded %d warmup frames for AWB (%s)", got, mode_tag);
    if (got <= 0) {
      log_error("[CAMERA] warmup got no frames (%s)", mode_tag);
      return false;
    }

    const uint32_t t0 = millis();
    camera_fb_t* probe = esp_camera_fb_get();
    const uint32_t got_ms = millis() - t0;
    if (!probe) {
      log_error("[CAMERA] probe fb_get failed (%s) ms=%u", mode_tag, (unsigned)got_ms);
      return false;
    }
    log_warn("[CAMERA] probe ok fmt=%u len=%uB %ux%u fb_get_ms=%u", (unsigned)probe->format,
             (unsigned)probe->len, (unsigned)probe->width, (unsigned)probe->height,
             (unsigned)got_ms);
    if (got_ms > 2500u) {
      log_warn("[CAMERA] probe fb_get too slow (%u ms), reject mode", (unsigned)got_ms);
      esp_camera_fb_return(probe);
      return false;
    }

    if (probe->format == PIXFORMAT_JPEG) {
      const bool ok = probe->len > 0 && probe->len <= kMaxJpegBin;
      if (!ok) {
        log_error("[CAMERA] probe jpeg len invalid %u", (unsigned)probe->len);
      } else {
        log_warn("[CAMERA] using hardware JPEG");
      }
      esp_camera_fb_return(probe);
      return ok;
    }

    if (probe->format == PIXFORMAT_YUV422) {
      /* 传感器 YUV422 + 库内 YUYV→JPEG（本机硬件 JPEG 不可用）。 */
      s_cam_pack = CamPack::kYuyv;
      uint8_t* jpg = nullptr;
      size_t jpg_len = 0;
      const bool jpg_ok = frame2jpg(probe, kJpegQuality, &jpg, &jpg_len);
      log_warn("[CAMERA] pack=YUYV (lib frame2jpg) fmt=YUV422");
      esp_camera_fb_return(probe);
      if (!jpg_ok || !jpg || jpg_len == 0) {
        log_error("[CAMERA] probe YUV frame2jpg failed");
        if (jpg) {
          free(jpg);
        }
        return false;
      }
      log_info("[CAMERA] probe jpeg=%uB", (unsigned)jpg_len);
      free(jpg);
      return true;
    }

    s_cam_pack = camera_pick_pack(probe->buf, probe->len);
    log_warn("[CAMERA] pack=%s (manual encode)", cam_pack_name(s_cam_pack));
    if (probe->len >= 8) {
      log_warn("[CAMERA] fb head %02x %02x %02x %02x %02x %02x %02x %02x", probe->buf[0],
               probe->buf[1], probe->buf[2], probe->buf[3], probe->buf[4], probe->buf[5],
               probe->buf[6], probe->buf[7]);
    }

    uint8_t* jpg = nullptr;
    size_t jpg_len = 0;
    const bool jpg_ok = camera_fb_to_jpg(probe, kJpegQuality, &jpg, &jpg_len);
    esp_camera_fb_return(probe);
    if (!jpg_ok || !jpg || jpg_len == 0) {
      log_error("[CAMERA] probe encode failed");
      if (jpg) {
        free(jpg);
      }
      return false;
    }
    log_info("[CAMERA] probe jpeg=%uB", (unsigned)jpg_len);
    free(jpg);
    return true;
  };

  /* 硬件 JPEG 在本机始终 fb_get null，跳过以免拖慢启动。 */

  /* ---- YUV422（传感器原生，颜色矩阵正常；再手动压 JPEG）---- */
  {
    camera_config_t config = {};
    camera_fill_pins(config);
    config.pixel_format = PIXFORMAT_YUV422;
    config.frame_size = FRAMESIZE_QVGA;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.xclk_freq_hz = 20000000;
    const esp_err_t err = esp_camera_init(&config);
    if (err == ESP_OK) {
      s_hw_inited = true;
      s_cam_pack = CamPack::kYuyv; /* frame2jpg 默认 YUYV；probe 仍会重选 */
      if (probe_after_init("YUV422")) {
        return true;
      }
      esp_camera_deinit();
      s_hw_inited = false;
      delay(50);
      log_warn("[CAMERA] YUV422 probe failed, fallback RGB565");
    } else {
      log_warn("[CAMERA] esp_camera_init YUV422 failed 0x%x, fallback RGB565", err);
    }
  }

  camera_config_t config = {};
  camera_fill_pins(config);
  config.pixel_format = PIXFORMAT_RGB565;
  config.xclk_freq_hz = 20000000;
  const esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    log_error("[CAMERA] esp_camera_init RGB565 failed 0x%x", err);
    return false;
  }
  s_hw_inited = true;
  if (!probe_after_init("RGB565->JPEG")) {
    esp_camera_deinit();
    s_hw_inited = false;
    return false;
  }
  return true;
}

static void task_loop_camera(void* /*arg*/) {
  uint32_t last_enq_fail_log_ms = 0;
  for (;;) {
    const uint32_t interval = s_interval_ms.load(std::memory_order_relaxed);
    /* WS 未就绪时不抓不压：避免断线期间 frame2jpg 继续吃内部 heap，拖垮重连。 */
    if (!ws_transport_ok() || !ws_transport_ready()) {
      vTaskDelay(pdMS_TO_TICKS(interval > 0 ? interval : 1000u));
      continue;
    }
    uint8_t* packed = nullptr;
    size_t packed_len = 0;
    if (camera_try_capture_packed(&packed, &packed_len)) {
      if (!ws_transport_enqueue_camera(packed, packed_len)) {
        const uint32_t now = millis();
        if (last_enq_fail_log_ms == 0 || (uint32_t)(now - last_enq_fail_log_ms) >= 5000u) {
          last_enq_fail_log_ms = now;
          log_warn("[CAMERA] enqueue failed len=%u", (unsigned)packed_len);
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(interval > 0 ? interval : 1000u));
  }
}

bool setup_camera() {
  if (!camera_init_hw()) {
    s_camera_ok = false;
    return false;
  }

  s_camera_ok = true;
  s_fb_null_count = 0;
  log_info("[CAMERA] setup_camera ok framesize=QVGA");
  return true;
}

void camera_deinit() {
  if (s_hw_inited) {
    esp_camera_deinit();
    s_hw_inited = false;
  }
  s_camera_ok = false;
  s_task_ready = false;
}

void task_setup_camera() {
  if (!s_camera_ok) {
    log_warn("[CAMERA] task_setup_camera skipped (setup_camera not ok)");
    return;
  }
  if (s_task) {
    return;
  }
  s_task_ready = true;
  BaseType_t rc = utils_task_create_pinned(task_loop_camera, "camera", kCameraTaskStack, nullptr,
                                          kCameraTaskPrio, &s_task, APP_CPU_NUM);
  if (rc != pdPASS) {
    log_error("[CAMERA] task create failed rc=%d", (int)rc);
    s_task = nullptr;
    s_task_ready = false;
    return;
  }
  log_warn("[CAMERA] task OK stack=%u prio=%u interval=%ums", (unsigned)kCameraTaskStack,
           (unsigned)kCameraTaskPrio, (unsigned)s_interval_ms.load(std::memory_order_relaxed));
}

void camera_set_fps(uint32_t fps) {
  if (fps == 0) {
    return;
  }
  const uint32_t interval = 1000u / fps;
  s_interval_ms.store(interval, std::memory_order_relaxed);
  log_warn("[CAMERA] set fps=%u interval=%ums", (unsigned)fps, (unsigned)interval);
}

bool camera_try_capture_packed(uint8_t** packed, size_t* packed_len) {
  if (!packed || !packed_len) {
    return false;
  }
  *packed = nullptr;
  *packed_len = 0;
  if (!s_camera_ok || !s_task_ready || !kCameraCaptureEnabled || !s_hw_inited) {
    return false;
  }

  const uint32_t now = millis();
  const uint32_t interval = s_interval_ms.load(std::memory_order_relaxed);
  if ((uint32_t)(now - s_last_capture_ms) < interval) {
    return false;
  }

  const uint32_t total_t0 = millis();

  const uint32_t fb_t0 = millis();
  camera_fb_t* fb = esp_camera_fb_get();
  const uint32_t fb_get_ms = millis() - fb_t0;
  if (!fb) {
    s_last_capture_ms = now;
    s_fb_null_count += 1;
    if (s_last_fb_null_log_ms == 0 ||
        (uint32_t)(now - s_last_fb_null_log_ms) >= kFbNullLogIntervalMs) {
      s_last_fb_null_log_ms = now;
      log_warn("[CAMERA] fb_get null fb_get_ms=%u total_ms=%u count=%u", (unsigned)fb_get_ms,
               (unsigned)(millis() - total_t0), (unsigned)s_fb_null_count);
    }
    return false;
  }

  uint8_t* jpg = nullptr;
  size_t jpg_len = 0;
  bool jpg_ok = false;
  const uint32_t jpg_t0 = millis();
  if (fb->format == PIXFORMAT_JPEG) {
    if (fb->len > 0 && fb->len <= kMaxJpegBin) {
      jpg = (uint8_t*)malloc(fb->len);
      if (jpg) {
        memcpy(jpg, fb->buf, fb->len);
        jpg_len = fb->len;
        jpg_ok = true;
      }
    }
  } else if (fb->format == PIXFORMAT_YUV422) {
    jpg_ok = frame2jpg(fb, kJpegQuality, &jpg, &jpg_len);
    if (jpg_ok && jpg_len > kMaxJpegBin) {
      log_warn("[CAMERA] jpeg too large %u", (unsigned)jpg_len);
      free(jpg);
      jpg = nullptr;
      jpg_len = 0;
      jpg_ok = false;
    }
  } else {
    jpg_ok = camera_fb_to_jpg(fb, kJpegQuality, &jpg, &jpg_len);
    if (jpg_ok && jpg_len > kMaxJpegBin) {
      log_warn("[CAMERA] jpeg too large %u", (unsigned)jpg_len);
      free(jpg);
      jpg = nullptr;
      jpg_len = 0;
      jpg_ok = false;
    }
  }
  const uint32_t jpg_ms = millis() - jpg_t0;
  esp_camera_fb_return(fb);

  if (!jpg_ok || !jpg || jpg_len == 0) {
    s_last_capture_ms = now;
    if (jpg) {
      free(jpg);
    }
    log_warn("[CAMERA] encode failed fb_get_ms=%u jpg_ms=%u total_ms=%u", (unsigned)fb_get_ms,
             (unsigned)jpg_ms, (unsigned)(millis() - total_t0));
    return false;
  }

  s_seq += 1;
  const uint32_t seq = s_seq;
  const size_t len = jpg_len;
  const int servo_x = head_read_x();
  const int servo_y = head_read_y_logic();
  const int volume = speaker_get_volume();

  char header[256];
  const int n = snprintf(
      header,
      sizeof(header),
      "{\"type\":\"camera_frame\",\"codec\":\"jpeg\",\"next_bin_len\":%u,\"seq\":%u,"
      "\"volume\":%d,\"servo\":{\"x\":%d,\"y\":%d,\"x_min\":%d,\"x_max\":%d,\"y_min\":%d,\"y_max\":%d}}",
      (unsigned)len,
      (unsigned)seq,
      volume,
      servo_x,
      servo_y,
      X_MIN_LIMIT,
      X_MAX_LIMIT,
      Y_MIN_LIMIT,
      Y_MAX_LIMIT);
  if (n <= 0 || (size_t)n >= sizeof(header)) {
    free(jpg);
    s_last_capture_ms = now;
    return false;
  }

  size_t out_len = 0;
  uint8_t* out = new_packed_bin(header, jpg, jpg_len, &out_len);
  free(jpg);
  if (!out) {
    s_last_capture_ms = now;
    return false;
  }

  s_last_capture_ms = now;
  s_fb_null_count = 0;
  *packed = out;
  *packed_len = out_len;
  const uint32_t total_ms = millis() - total_t0;
  if (seq <= 1u || (seq % 30u) == 0u) {
    log_warn("[CAMERA] frame seq=%u jpeg=%uB fb_get_ms=%u jpg_ms=%u total_ms=%u", (unsigned)seq,
             (unsigned)len, (unsigned)fb_get_ms, (unsigned)jpg_ms, (unsigned)total_ms);
  }
  return true;
}
