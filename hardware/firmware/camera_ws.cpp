/* camera_ws.cpp
 * JPEG 经 /asr_chat next_bin_len 发送；本模块只负责捕获并存入 PSRAM，发送由 AsrChatClient::loop() 驱动。
 */
#include "camera_ws.h"
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

static constexpr uint32_t kDefaultUploadFps = 10;

/* 采集间隔（ms）；默认 10fps；运行时由 camera_ws_set_fps / 服务端 cam_fps 调整。 */
static volatile uint32_t s_cap_interval_ms = 1000u / kDefaultUploadFps;

static constexpr size_t kJpegBufSize = 64 * 1024;

static SemaphoreHandle_t s_mutex      = nullptr;
static uint8_t*          s_buf        = nullptr;
static size_t            s_len        = 0;
static uint32_t          s_seq        = 0;
static bool              s_ready      = false;
static bool              s_init_ok    = false;

static void cameraCaptureTask(void*) {
  uint32_t last_cap_ms = 0;
  for (;;) {
    if (deskbot_vision_uplink_paused()) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    const uint32_t now = millis();
    const uint32_t cap_interval_ms = s_cap_interval_ms;
    if (cap_interval_ms > 0 && now - last_cap_ms < cap_interval_ms) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    if (fb->format == PIXFORMAT_JPEG && fb->len > 0 && fb->len <= kJpegBufSize) {
      if (xSemaphoreTake(s_mutex, 0) == pdTRUE) {
        memcpy(s_buf, fb->buf, fb->len);
        s_len   = fb->len;
        s_seq  += 1;
        s_ready = true;
        xSemaphoreGive(s_mutex);
        last_cap_ms = now;
      }
    }

    esp_camera_fb_return(fb);
    vTaskDelay(1);
  }
}

bool camera_ws_take_frame(const uint8_t** out_buf, size_t* out_len, uint32_t* out_seq) {
  if (!s_init_ok || !s_ready) {
    return false;
  }
  if (xSemaphoreTake(s_mutex, 0) != pdTRUE) {
    return false;
  }
  if (!s_ready) {
    xSemaphoreGive(s_mutex);
    return false;
  }
  s_ready   = false;
  *out_buf  = s_buf;
  *out_len  = s_len;
  *out_seq  = s_seq;
  return true;
}

void camera_ws_release_frame(void) {
  if (s_mutex) {
    xSemaphoreGive(s_mutex);
  }
}

void camera_ws_discard_pending(void) {
  if (!s_mutex) {
    return;
  }
  if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(20)) != pdTRUE) {
    return;
  }
  s_ready = false;
  s_len = 0;
  xSemaphoreGive(s_mutex);
}

void camera_ws_set_fps(uint32_t fps) {
  if (fps == 0) {
    return;
  }
  s_cap_interval_ms = 1000u / fps;
  Serial.printf("[camera_ws] set fps=%u interval=%ums\r\n", (unsigned)fps, (unsigned)s_cap_interval_ms);
}

void task_setup_camera_capture(void) {
  s_mutex = xSemaphoreCreateMutex();
  if (!s_mutex) {
    Serial.println("[camera_ws] mutex create failed");
    return;
  }

  s_buf = (uint8_t*)heap_caps_malloc(kJpegBufSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!s_buf) {
    Serial.println("[camera_ws] PSRAM alloc failed");
    return;
  }

  s_init_ok = true;
  Serial.printf("[camera_ws] init default_fps=%u interval=%ums (via /asr_chat)\r\n",
                (unsigned)kDefaultUploadFps, (unsigned)s_cap_interval_ms);

  BaseType_t ok = xTaskCreatePinnedToCore(
      cameraCaptureTask, "camera_cap", 4096, nullptr, 1, nullptr, 0);
  if (ok != pdPASS) {
    Serial.println("[camera_ws] task create failed");
  }
}
