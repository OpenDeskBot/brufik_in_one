#include "camera.h"

#include "deskbot_config.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "ws_transport.h"

#include <Arduino.h>
#include "esp_camera.h"
#include <atomic>

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

static bool s_camera_ok = false;
static bool s_task_ready = false;
static std::atomic<uint32_t> s_interval_ms{1000u};
static std::atomic<bool> s_capture_enabled{false};
static uint32_t s_last_capture_ms = 0;
static uint32_t s_seq = 0;

bool setup_camera() {
  camera_config_t config;
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
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_QVGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 18;
  config.fb_count = 1;

  if (psramFound()) {
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    log_error("[CAMERA] setup_camera failed 0x%x", err);
    s_camera_ok = false;
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    log_error("[CAMERA] setup_camera sensor_get returned null after init");
    s_camera_ok = false;
    return false;
  }
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }

  s_camera_ok = true;
  log_info("[CAMERA] setup_camera ok framesize=QVGA");
  return true;
}

void task_setup_camera() {
  if (!s_camera_ok) {
    log_warn("[CAMERA] task_setup_camera skipped (setup_camera not ok)");
    return;
  }
  s_task_ready = true;
  log_warn("[CAMERA] capture folded into ws_transport (no camera_cap task) interval=%ums",
           (unsigned)s_interval_ms.load(std::memory_order_relaxed));
}

void camera_set_fps(uint32_t fps) {
  if (fps == 0) {
    return;
  }
  const uint32_t interval = 1000u / fps;
  s_interval_ms.store(interval, std::memory_order_relaxed);
  log_warn("[CAMERA] set fps=%u interval=%ums", (unsigned)fps, (unsigned)interval);
}

void camera_notify_capture(CamNotify n) {
  if (!kCameraCaptureEnabled && n == kCamGo) {
    return;
  }
  if (n == kCamStop) {
    s_capture_enabled.store(false, std::memory_order_release);
    return;
  }
  /* GO：发一张的额度；先等满 interval 再拍（对齐旧 delay-after-GO）。 */
  s_last_capture_ms = millis();
  s_capture_enabled.store(true, std::memory_order_release);
}

static void release_camera_fb(void* ctx) {
  if (ctx) {
    esp_camera_fb_return(static_cast<camera_fb_t*>(ctx));
  }
}

static bool capture_and_enqueue_one() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    return false;
  }
  if (fb->format != PIXFORMAT_JPEG || fb->len == 0 || fb->len > 32 * 1024) {
    esp_camera_fb_return(fb);
    return false;
  }

  s_seq += 1;
  const uint32_t seq = s_seq;
  const size_t len = fb->len;
  const int servo_x = head_read_x();
  const int servo_y = head_read_y_logic();
  const int volume = speaker_get_volume();
  if (seq <= 1u || seq % 30u == 0u) {
    log_warn("[CAMERA] enqueue frame seq=%u jpeg=%uB servo=(%d,%d) volume=%d", (unsigned)seq,
             (unsigned)len, servo_x, servo_y, volume);
  }

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
    esp_camera_fb_return(fb);
    log_warn("[CAMERA] header snprintf truncated");
    return false;
  }

  /* 入队成功后所有权交给 ws_transport：打包后立刻 return fb。 */
  if (!ws_transport_enqueue_image_borrow(header, fb->buf, fb->len, release_camera_fb, fb)) {
    esp_camera_fb_return(fb);
    return false;
  }
  return true;
}

bool camera_try_capture_and_enqueue(void) {
  if (!s_camera_ok || !s_task_ready || !kCameraCaptureEnabled) {
    return false;
  }
  if (!s_capture_enabled.load(std::memory_order_acquire)) {
    return false;
  }
  const uint32_t now = millis();
  const uint32_t interval = s_interval_ms.load(std::memory_order_relaxed);
  if ((uint32_t)(now - s_last_capture_ms) < interval) {
    return false;
  }
  /* TX 满则下轮再试，不推进节拍（避免长时间无图）。 */
  if (ws_transport_tx_slots_free() == 0) {
    return false;
  }
  /* camera_ws 未就绪时 enqueue 会失败；推进节拍避免空转狂抓，保留额度下轮再试。 */
  if (!capture_and_enqueue_one()) {
    s_last_capture_ms = now;
    return false;
  }
  s_last_capture_ms = now;
  /* 一张在途：等 drain_tx 完成后再 kCamGo（与旧 notify 握手一致）。 */
  s_capture_enabled.store(false, std::memory_order_release);
  return true;
}
