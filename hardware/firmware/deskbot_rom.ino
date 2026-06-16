// Deskbot — XIAO ESP32S3 Sense：摄像头 + asr_chat(pb) + 音频 + LCD + 舵机
#include "esp_camera.h"
#include <WiFi.h>
#include "oled_display.h"
#include "img_converters.h"
#include "camera_ws.h"
#include "deskbot_config.h"
#include "wifi_provision.h"
#include "common.h"
#include "oled.h"
#include "audio_player.h"
#include "audio_capture.h"
#include "asr_chat_client.h"
#include "head.h"
#include "cmd.h"
#include "task_trace.h"

#include "camera_pins.h"

void startCameraServer();

AsrChatClient asrChatClient;

unsigned long loop_start_time = 0;

static bool setup_camera() {
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
    log_error("Camera init failed 0x%x", err);
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }
  if (config.pixel_format == PIXFORMAT_JPEG) {
    s->set_framesize(s, FRAMESIZE_QVGA);
  }
  return true;
}

void websocket_loop() {
  asrChatClient.loop();
}

void setup() {
  Serial.begin(115200);
  Serial.flush();
  /* USB CDC 在 ESP32-S3 上 !Serial 永远为 false，用固定 delay 等待监视器连接。
   * 3s 足够 Linux/macOS 完成 USB CDC 枚举并让 flash_rom.sh 启动监视器。
   * 独立运行（无 USB）时同样只多等 3s，不影响正常功能。 */
  delay(3000);
  log_set_level(LOG_LEVEL_INFO);
  log_info("Initializing Deskbot...");
  log_info("[BOOT] device_id=%s", get_device_id());

  setup_oled();
  setup_FFat();
  setup_led();

  /* 归中用 LEDC timer2（在 camera 占 timer0 之前）；attach 在 camera 之后。 */
  setup_head();

  static bool s_camera_ok = false;
  s_camera_ok = setup_camera();
  head_servo_boot_attach();
  log_set_level(LOG_LEVEL_WARN);
  deskbot_lcd_backlight_on();
  if (!s_camera_ok) {
    log_warn("[BOOT] Camera absent or failed — continuing without camera");
    oled_boot_show("无摄像头", "继续启动...");
  }
  if (!wifi_provision_connect()) {
    log_error("WiFi connect failed");
    oled_boot_show("WiFi 连接失败", "请重启或配网");
    return;
  }

  setup_audio();
  mic_capture_setup();
  audio_play_task_setup();
  display_task_setup();

  log_info("[BOOT] firmware=%s %s %s", VERSION, __DATE__, __TIME__);
  log_info("[BOOT] device_id=%s ws=%s:%u api_key=%s", get_device_id(), DESKBOT_WS_HOST,
           (unsigned)DESKBOT_WS_PORT, deskbot_api_key_configured() ? "set" : "MISSING");
  log_info("PSRAM size=%u free=%u", (unsigned)ESP.getPsramSize(), (unsigned)ESP.getFreePsram());

  if (s_camera_ok) {
    camera_ws_init();
    startCameraServer();
  } else {
    log_warn("[BOOT] Skipping camera server / vision tasks (no camera)");
  }

  oled_boot_show_ready();
  log_info("%s is Ready. http://%s", PRODUCT_NAME, WiFi.localIP().toString().c_str());
  loop_start_time = millis();
  start_chat = true;
}

void loop() {
  handle_cmd();
  websocket_loop();
  log_task_tick();

  if (start_chat) {
    start_chat = false;
    loop_start_time = millis();
    if (CHAT_LOOP_MAX_MS > 0) {
      log_info("[CHAT] enter asr_chat loop (max %lu ms)", (unsigned long)CHAT_LOOP_MAX_MS);
    } else {
      log_info("[CHAT] enter asr_chat loop (no auto exit)");
    }
    for (;;) {
      /* 长会话内也要读串口，否则 factory 等命令无响应 */
      handle_cmd();
      log_task_tick();
      if (CHAT_LOOP_MAX_MS > 0 && (millis() - loop_start_time >= (unsigned long)CHAT_LOOP_MAX_MS)) {
        break;
      }
      if (!asrChatClient.runVoiceRound(RECORD_TIME)) {
        log_error("[CHAT] asr_chat round failed, reconnect retry in 2s");
        blink_led(COLOR_RED, 3);
        delay(2000);
        continue;
      }
      websocket_loop();
      delay(500);
    }
    log_info("[CHAT] leave chat loop");
  }

  delay(10);
}
