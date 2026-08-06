#include "camera.h"

#include "deskbot_config.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <Arduino.h>
#include "esp_camera.h"
#include "img_converters.h"
#include <atomic>
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
static constexpr int kAwakenDiscardFrames = 12;
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
  /* 保持自动曝光/白平衡，但给 AWB 明确起点；偏绿多见于冷启动未收敛。 */
  if (s->set_whitebal) {
    s->set_whitebal(s, 1);
  }
  if (s->set_awb_gain) {
    s->set_awb_gain(s, 1);
  }
  if (s->set_wb_mode) {
    /* 0=Auto；室内偏绿时可试 3=Office */
    s->set_wb_mode(s, 0);
  }
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }
}

/** @return 实际丢掉的帧数；连续 null 过多则提前退出（避免 JPEG 路径卡死数十秒）。 */
static int camera_discard_warmup_frames(void) {
  int got = 0;
  int nulls = 0;
  for (int i = 0; i < kAwakenDiscardFrames; ++i) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      nulls += 1;
      if (nulls >= kAwakenMaxNulls) {
        log_warn("[CAMERA] warmup abort after %d null fb_get (got=%d)", nulls, got);
        return got;
      }
      delay(30);
      continue;
    }
    nulls = 0;
    got += 1;
    esp_camera_fb_return(fb);
  }
  log_info("[CAMERA] discarded %d warmup frames for AWB", got);
  return got;
}

/**
 * RGB565 采样代价：越小越好。
 * 惩罚过绿，以及 R≪B（错 endian 常见青/青色偏）。
 */
static float camera_rgb565_badness(const uint8_t* buf, size_t len, bool be) {
  if (!buf || len < 4) {
    return 1e9f;
  }
  uint64_t sum_r = 0;
  uint64_t sum_g = 0;
  uint64_t sum_b = 0;
  uint32_t green_dom = 0;
  uint32_t samples = 0;
  for (size_t i = 0; i + 1 < len; i += 32) {
    uint8_t r;
    uint8_t g;
    uint8_t b;
    if (be) {
      r = buf[i] & 0xF8;
      g = (uint8_t)(((buf[i] & 0x07) << 5) | ((buf[i + 1] & 0xE0) >> 3));
      b = (uint8_t)((buf[i + 1] & 0x1F) << 3);
    } else {
      r = buf[i + 1] & 0xF8;
      g = (uint8_t)(((buf[i + 1] & 0x07) << 5) | ((buf[i] & 0xE0) >> 3));
      b = (uint8_t)((buf[i] & 0x1F) << 3);
    }
    sum_r += r;
    sum_g += g;
    sum_b += b;
    if ((int)g > (int)r + 20 && (int)g > (int)b + 20) {
      green_dom += 1;
    }
    samples += 1;
  }
  if (samples == 0) {
    return 1e9f;
  }
  const float inv = 1.0f / (float)samples;
  const float mr = (float)sum_r * inv;
  const float mg = (float)sum_g * inv;
  const float mb = (float)sum_b * inv;
  const float green_ratio = (float)green_dom * inv;
  /* R/B 失衡（错字节序常把肤色打成青色）。 */
  const float rb_skew = (mb > mr + 8.0f) ? (mb - mr) / 255.0f : 0.0f;
  return green_ratio * 2.0f + rb_skew + (mg > mr + 25.0f && mg > mb + 25.0f ? 0.5f : 0.0f);
}

/**
 * 本机（XIAO S3 Sense / OV2640）硬件 JPEG 的 fb_get 会长时间超时，
 * 不可再优先试 JPEG（warmup 会卡 ~分钟级，看起来像初始化失败）。
 * 直接 RGB565 + frame2jpg；勿用 YUV422（YUYV 假设不对 → 绿色马赛克）。
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
             s->id.PID == OV2640_PID ? " OV2640" : (s->id.PID == OV3660_PID ? " OV3660" : ""),
             mode_tag);
    camera_tune_sensor();
    if (camera_discard_warmup_frames() <= 0) {
      log_error("[CAMERA] warmup got no frames (%s)", mode_tag);
      return false;
    }

    camera_fb_t* probe = esp_camera_fb_get();
    if (!probe) {
      log_error("[CAMERA] probe fb_get failed (%s)", mode_tag);
      return false;
    }
    log_info("[CAMERA] probe ok fmt=%u len=%uB %ux%u", (unsigned)probe->format,
             (unsigned)probe->len, (unsigned)probe->width, (unsigned)probe->height);

    if (probe->format == PIXFORMAT_RGB565) {
      const float score_be = camera_rgb565_badness(probe->buf, probe->len, true);
      const float score_le = camera_rgb565_badness(probe->buf, probe->len, false);
      const bool use_be = score_be <= score_le;
      jpgSetRgb565BE(use_be);
      log_info("[CAMERA] rgb565 endian=%s score be=%.3f le=%.3f", use_be ? "BE" : "LE",
               (double)score_be, (double)score_le);
    }

    uint8_t* jpg = nullptr;
    size_t jpg_len = 0;
    const bool jpg_ok = frame2jpg(probe, kJpegQuality, &jpg, &jpg_len);
    esp_camera_fb_return(probe);
    if (!jpg_ok || !jpg || jpg_len == 0) {
      log_error("[CAMERA] probe frame2jpg failed");
      if (jpg) {
        free(jpg);
      }
      return false;
    }
    log_info("[CAMERA] probe jpeg=%uB", (unsigned)jpg_len);
    free(jpg);
    return true;
  };

  camera_config_t config = {};
  camera_fill_pins(config);
  config.pixel_format = PIXFORMAT_RGB565;
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

static void camera_task(void* /*arg*/) {
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
  BaseType_t rc = utils_task_create_pinned(camera_task, "camera", kCameraTaskStack, nullptr,
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
  } else {
    jpg_ok = frame2jpg(fb, kJpegQuality, &jpg, &jpg_len);
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
