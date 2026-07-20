#include "camera.h"

#include "asr_chat_client.h"
#include "audio_player.h"
#include "common.h"
#include "deskbot_config.h"
#include "deskbot_uplink_state.h"
#include "logger.h"

#include <Arduino.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include "esp_camera.h"
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

namespace {

bool s_camera_ok = false;
volatile uint32_t s_interval_ms = DESKBOT_CAMERA_UPLINK_INTERVAL_MS;

WebSocketsClient s_ws;
bool s_ws_ready = false;
bool s_needs_reconnect = true;
bool s_handlers_registered = false;
unsigned long s_reconnect_backoff_ms = 2000;
unsigned long s_last_reconnect_ms = 0;
unsigned long s_connected_at_ms = 0;
uint32_t s_seq = 0;

bool capture_paused() {
  return asr_chat_voice_uplink_busy() || deskbot_uplink_speaker_audible() ||
         audio_play_speaker_busy();
}

uint32_t upload_interval_ms() {
  uint32_t ms = s_interval_ms;
  if (ms == 0) {
    ms = DESKBOT_CAMERA_UPLINK_INTERVAL_MS;
  }
  if (deskbot_uplink_capture_allowed()) {
    const uint32_t listen_ms = DESKBOT_CAMERA_UPLINK_INTERVAL_DURING_LISTEN_MS;
    if (listen_ms > ms) {
      ms = listen_ms;
    }
  }
  return ms;
}

void register_handlers() {
  if (s_handlers_registered) {
    return;
  }
  s_handlers_registered = true;
  s_ws.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
    if (type == WStype_CONNECTED) {
      s_ws_ready = false;
      s_connected_at_ms = millis();
      return;
    }
    if (type == WStype_DISCONNECTED) {
      s_ws_ready = false;
      s_needs_reconnect = true;
      s_connected_at_ms = 0;
      return;
    }
    if (type == WStype_TEXT && payload && length > 0) {
      const char* txt = reinterpret_cast<const char*>(payload);
      if (strstr(txt, "\"type\":\"ready\"") != nullptr ||
          strstr(txt, "\"type\": \"ready\"") != nullptr) {
        s_ws_ready = true;
        s_needs_reconnect = false;
        log_warn("[CAMERA] uplink ready");
      }
    }
  });
}

void disconnect_ws() {
  s_ws.disconnect();
  s_ws_ready = false;
  s_needs_reconnect = true;
  s_connected_at_ms = 0;
}

bool ensure_connected() {
  if (WiFi.status() != WL_CONNECTED || !deskbot_camera_uplink_enabled()) {
    return false;
  }
  if (s_ws.isConnected() && s_ws_ready && !s_needs_reconnect) {
    return true;
  }
  if (!deskbot_api_key_configured() || DESKBOT_WS_HOST[0] == '\0') {
    return false;
  }

  register_handlers();
  s_ws.loop();

  if (s_ws.isConnected()) {
    if (!s_ws_ready && s_connected_at_ms != 0 &&
        (millis() - s_connected_at_ms) > 3000UL) {
      log_warn("[CAMERA] no ready JSON, continue anyway");
      s_ws_ready = true;
      s_needs_reconnect = false;
    }
    return s_ws_ready;
  }

  const unsigned long now = millis();
  if (s_last_reconnect_ms != 0 &&
      (now - s_last_reconnect_ms) < s_reconnect_backoff_ms) {
    return false;
  }
  s_last_reconnect_ms = now;

  disconnect_ws();
  for (unsigned i = 0; i < 20; ++i) {
    s_ws.loop();
    delay(5);
  }

  char path[80];
  snprintf(path, sizeof(path), "%s?device_id=%s", DESKBOT_CAMERA_WS_PATH, get_device_id());
  char auth_header[96];
  snprintf(auth_header, sizeof(auth_header), "X-API-Key: %s", DESKBOT_API_KEY);
  s_ws.setExtraHeaders(auth_header);
  s_ws.setReconnectInterval(500);
  log_warn("[CAMERA] connecting ws://%s:%u%s", DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT, path);
  s_ws.begin(DESKBOT_WS_HOST, DESKBOT_WS_PORT, path);

  const unsigned long deadline = millis() + (unsigned long)DESKBOT_WS_CONNECT_TIMEOUT_MS;
  while ((long)(millis() - deadline) < 0) {
    s_ws.loop();
    if (s_ws.isConnected() && s_ws_ready) {
      s_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
      s_needs_reconnect = false;
      s_reconnect_backoff_ms = 2000;
      log_warn("[CAMERA] connected");
      return true;
    }
    if (s_ws.isConnected() && s_connected_at_ms != 0 &&
        (millis() - s_connected_at_ms) > 3000UL) {
      s_ws_ready = true;
      s_needs_reconnect = false;
      s_reconnect_backoff_ms = 2000;
      s_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
      log_warn("[CAMERA] connected (ready timeout)");
      return true;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }

  log_warn("[CAMERA] connect timeout, skip cycle");
  disconnect_ws();
  if (s_reconnect_backoff_ms < 30000UL) {
    s_reconnect_backoff_ms *= 2;
    if (s_reconnect_backoff_ms > 30000UL) {
      s_reconnect_backoff_ms = 30000UL;
    }
  }
  return false;
}

bool try_upload_one_frame() {
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
  if (seq <= 1u || seq % 30u == 0u) {
    log_warn("[CAMERA] sendBIN seq=%u jpeg=%uB", (unsigned)seq, (unsigned)len);
  }

  s_ws.loop();
  asr_chat_cooperative_pump();
  const bool ok = s_ws.sendBIN(fb->buf, fb->len);
  s_ws.loop();
  asr_chat_cooperative_pump();
  esp_camera_fb_return(fb);

  if (!ok) {
    log_warn("[CAMERA] sendBIN fail seq=%u, skip cycle", (unsigned)seq);
    disconnect_ws();
    return false;
  }
  return true;
}

void camera_uplink_task(void*) {
  unsigned long last_upload_ms = 0;
  for (;;) {
    if (!deskbot_camera_uplink_enabled()) {
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }

    if (WiFi.status() != WL_CONNECTED) {
      if (s_ws.isConnected()) {
        disconnect_ws();
      }
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }

    s_ws.loop();

    if (capture_paused()) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    const uint32_t interval = upload_interval_ms();
    const unsigned long now = millis();
    if (last_upload_ms != 0 && (now - last_upload_ms) < interval) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    if (!ensure_connected()) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    if (try_upload_one_frame()) {
      last_upload_ms = millis();
    } else {
      /* 网络/抓帧失败：本周期跳过，等下一轮。 */
      last_upload_ms = millis();
      vTaskDelay(pdMS_TO_TICKS(50));
    }
  }
}

}  // namespace

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
  config.frame_size = FRAMESIZE_UXGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 12;
  config.fb_count = 1;

  if (psramFound()) {
    config.jpeg_quality = 10;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size = FRAMESIZE_SVGA;
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
  if (config.pixel_format == PIXFORMAT_JPEG) {
    s->set_framesize(s, FRAMESIZE_QVGA);
    /* 略降画质 (~4–5KB JPEG)，减轻上行压力。 */
    s->set_quality(s, 18);
  }

  s_camera_ok = true;
  log_info("[CAMERA] setup_camera ok framesize=QVGA");
  return true;
}

void camera_set_fps(uint32_t fps) {
  if (fps == 0) {
    return;
  }
  s_interval_ms = 1000u / fps;
  log_warn("[CAMERA] set fps=%u interval=%ums", (unsigned)fps, (unsigned)s_interval_ms);
}

void task_setup_camera() {
  if (!s_camera_ok) {
    log_warn("[CAMERA] task_setup_camera skipped (setup_camera not ok)");
    return;
  }
  if (!deskbot_camera_uplink_enabled()) {
    log_warn("[CAMERA] uplink disabled (DESKBOT_CAMERA_UPLINK_ENABLED=0)");
    return;
  }

  BaseType_t ok = xTaskCreatePinnedToCore(
      camera_uplink_task, "camera_up", 6144, nullptr, 1, nullptr, 0);
  if (ok != pdPASS) {
    log_error("[CAMERA] task create failed");
    return;
  }
  log_warn("[CAMERA] uplink task started interval=%ums path=%s",
           (unsigned)s_interval_ms, DESKBOT_CAMERA_WS_PATH);
}
