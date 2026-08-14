#include "camera.h"

#include "deskbot_config.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <Arduino.h>
#include "esp_camera.h"
#include <atomic>
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
  /* OV2640 在 XIAO ESP32S3 Sense 上 XCLK 用 10MHz；20MHz 会导致 DMA 数据损坏（绿屏）。 */
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

/** OV2640 硬件 JPEG 初始化。 */
static bool camera_init_hw(void) {
  if (s_hw_inited) {
    esp_camera_deinit();
    s_hw_inited = false;
    delay(30);
  }

  camera_config_t config = {};
  camera_fill_pins(config);
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;
  config.fb_count = 2;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  const esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    log_error("[CAMERA] esp_camera_init failed 0x%x", err);
    return false;
  }
  s_hw_inited = true;

  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    log_error("[CAMERA] sensor_get returned null after init");
    return false;
  }
  log_info("[CAMERA] sensor PID=0x%x%s mode=JPEG", (unsigned)s->id.PID,
           s->id.PID == OV2640_PID ? " OV2640" : "");
  camera_tune_sensor();

  /* JPEG 冷启动常连续 null，多等一会再抓。 */
  delay(300);
  int got = 0;
  int nulls = 0;
  for (int i = 0; i < 3; ++i) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      nulls += 1;
      if (nulls >= 15) {
        break;
      }
      delay(200);
      continue;
    }
    nulls = 0;
    got += 1;
    esp_camera_fb_return(fb);
  }
  log_info("[CAMERA] discarded %d warmup frames for AWB", got);
  if (got <= 0) {
    log_error("[CAMERA] warmup got no frames");
    return false;
  }

  const uint32_t t0 = millis();
  camera_fb_t* probe = esp_camera_fb_get();
  const uint32_t got_ms = millis() - t0;
  if (!probe) {
    log_error("[CAMERA] probe fb_get failed ms=%u", (unsigned)got_ms);
    return false;
  }
  log_warn("[CAMERA] probe ok fmt=%u len=%uB %ux%u fb_get_ms=%u", (unsigned)probe->format,
           (unsigned)probe->len, (unsigned)probe->width, (unsigned)probe->height,
           (unsigned)got_ms);

  if (got_ms > 2500u) {
    log_warn("[CAMERA] probe fb_get too slow (%u ms)", (unsigned)got_ms);
    esp_camera_fb_return(probe);
    return false;
  }

  const bool ok = probe->format == PIXFORMAT_JPEG && probe->len > 0 && probe->len <= kMaxJpegBin;
  if (!ok) {
    log_error("[CAMERA] probe failed: fmt=%u len=%u", (unsigned)probe->format, (unsigned)probe->len);
  } else {
    log_warn("[CAMERA] using hardware JPEG");
  }
  esp_camera_fb_return(probe);
  return ok;
}

static void task_loop_camera(void* /*arg*/) {
  uint32_t last_enq_fail_log_ms = 0;
  for (;;) {
    const uint32_t interval = s_interval_ms.load(std::memory_order_relaxed);
    /* WS 未就绪时不抓不压：避免断线期间 JPEG 编码继续吃内部 heap，拖垮重连。 */
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
  const int servo_y = head_read_y();
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
