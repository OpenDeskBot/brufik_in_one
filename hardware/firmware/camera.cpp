#include "camera.h"

#include "deskbot_config.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "ws_transport.h"

#include <Arduino.h>
#include "esp_camera.h"
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
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

static bool s_camera_ok = false;
static volatile uint32_t s_interval_ms = 1000u;
static QueueHandle_t s_notify_q = nullptr;
static uint32_t s_seq = 0;
/** queue 尚未创建时暂存最新通知（boot 阶段 camera_ws ready 可能早于 task_setup_camera）。 */
static bool s_has_pending_notify = false;
static CamNotify s_pending_notify = kCamStop;

static void camera_capture_task(void*);

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

  s_notify_q = xQueueCreate(1, sizeof(CamNotify));
  if (!s_notify_q) {
    log_error("[CAMERA] notify queue failed");
    return;
  }

  BaseType_t ok = xTaskCreatePinnedToCore(camera_capture_task, "camera_cap", 4096, nullptr, 1, nullptr, 0);
  if (ok != pdPASS) {
    log_error("[CAMERA] task create failed");
    return;
  }
  log_warn("[CAMERA] capture task started (notify-queue gated) interval=%ums",
           (unsigned)s_interval_ms);

  /* 补放 boot 期间暂存的 notify（常见：WS ready 早于本任务）。 */
  if (s_has_pending_notify) {
    const CamNotify n = s_pending_notify;
    s_has_pending_notify = false;
    xQueueReset(s_notify_q);
    (void)xQueueSend(s_notify_q, &n, 0);
    log_warn("[CAMERA] flushed pending notify=%d", (int)n);
  }
}

void camera_set_fps(uint32_t fps) {
  if (fps == 0) {
    return;
  }
  s_interval_ms = 1000u / fps;
  log_warn("[CAMERA] set fps=%u interval=%ums", (unsigned)fps, (unsigned)s_interval_ms);
}

void camera_notify_capture(CamNotify n) {
  if (!kCameraCaptureEnabled && n == kCamGo) {
    return;
  }
  if (!s_notify_q) {
    s_pending_notify = n;
    s_has_pending_notify = true;
    return;
  }
  /* 单槽：先清空再放入最新通知。 */
  xQueueReset(s_notify_q);
  (void)xQueueSend(s_notify_q, &n, 0);
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

  /* 入队成功后所有权交给 ws_transport：发完/丢弃时调 releaser；失败则本处立刻 return。 */
  if (!ws_transport_enqueue_image_borrow(header, fb->buf, fb->len, release_camera_fb, fb)) {
    esp_camera_fb_return(fb);
    return false;
  }
  return true;
}

static void camera_capture_task(void*) {
  for (;;) {
    CamNotify n;
    if (xQueueReceive(s_notify_q, &n, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    if (n == kCamStop) {
      log_warn("[CAMERA] capture stopped");
      continue;
    }

    /* GO：先按 fps 间隔 delay；STOP 若在 delay 期间入队，随后 peek 会停住。 */
    vTaskDelay(pdMS_TO_TICKS(s_interval_ms));

    if (!kCameraCaptureEnabled) {
      continue;
    }

    CamNotify peek;
    if (xQueueReceive(s_notify_q, &peek, 0) == pdTRUE) {
      if (peek == kCamStop) {
        log_warn("[CAMERA] capture stopped before shoot");
        continue;
      }
      /* 多余 GO 丢弃 */
    }

    if (!kCameraCaptureEnabled) {
      continue;
    }

    if (!capture_and_enqueue_one()) {
      vTaskDelay(pdMS_TO_TICKS(100));
      camera_notify_capture(kCamGo);
    }
  }
}
