// Deskbot — XIAO ESP32S3 Sense：摄像头 + asr_chat(pb) + 音频 + LCD + 舵机
#include <WiFi.h>
#include "oled_display.h"
#include "camera_ws.h"
#include "camera_init.h"
#include "camera_http.h"
#include "deskbot_config.h"
#include "wifi_provision.h"
#include "common.h"
#include "oled.h"
#include "audio_player.h"
#include "audio_capture.h"
#include "asr_chat_client.h"
#include "camera_uplink_client.h"
#include "head.h"
#include "cmd.h"
#include "task_trace.h"

AsrChatClient asrChatClient;

unsigned long loop_start_time = 0;

static void on_wifi_link_down() {
  asrChatClient.onLinkDown("wifi lost");
  cameraUplinkClient.onLinkDown("wifi lost");
}

static void on_wifi_link_up() {
  asrChatClient.onLinkUp();
  cameraUplinkClient.onLinkUp();
}

static void deskbot_network_poll() {
  wifi_provision_maintain();
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

  /* 预归中（GPIO 位bang 中位脉宽，不 attach）；永久 attach 在 camera 之后。 */
  setup_head();

  /* 音频用 I2S，与 camera LEDC / WiFi 无冲突；提前到联网前，配网阻塞时麦任务已在跑。 */
  setup_audio();
  task_setup_mic_capture();

  static bool s_camera_ok = false;
  s_camera_ok = setup_camera();
  head_servo_boot_attach();
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
  wifi_provision_set_link_handlers(on_wifi_link_down, on_wifi_link_up);

  task_setup_audio_play();
  if (!asrChatClient.task_setup_ws_uplink()) {
    log_error("[BOOT] ws_uplink task start failed");
  }
  task_setup_display();

  log_info("[BOOT] firmware=%s %s %s", VERSION, __DATE__, __TIME__);
  log_info("[BOOT] device_id=%s ws=%s:%u api_key=%s", get_device_id(), DESKBOT_WS_HOST,
           (unsigned)DESKBOT_WS_PORT, deskbot_api_key_configured() ? "set" : "MISSING");
  log_info("PSRAM size=%u free=%u", (unsigned)ESP.getPsramSize(), (unsigned)ESP.getFreePsram());

  if (s_camera_ok) {
    task_setup_camera_capture();
    startCameraServer();
  } else {
    log_warn("[BOOT] Skipping camera server / vision tasks (no camera)");
  }

  oled_boot_show_ready();
  log_info("%s is Ready. http://%s", PRODUCT_NAME, WiFi.localIP().toString().c_str());
  log_warn("[BOOT] ready device=%s ws=%s:%u wifi_ip=%s",
           get_device_id(), DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT,
           WiFi.localIP().toString().c_str());
  log_set_level(LOG_LEVEL_WARN);
  loop_start_time = millis();
  start_chat = true;
}

void loop() {
  handle_cmd();
  deskbot_network_poll();
  websocket_loop();
  log_task_tick();

  if (start_chat) {
    start_chat = false;
    loop_start_time = millis();
    if (CHAT_LOOP_MAX_MS > 0) {
      log_warn("[CHAT] enter asr_chat loop (max %lu ms)", (unsigned long)CHAT_LOOP_MAX_MS);
    } else {
      log_warn("[CHAT] enter asr_chat loop (no auto exit)");
    }
    for (;;) {
      handle_cmd();
      deskbot_network_poll();
      log_task_tick();

      if (CHAT_LOOP_MAX_MS > 0 && (millis() - loop_start_time >= (unsigned long)CHAT_LOOP_MAX_MS)) {
        break;
      }

      cameraUplinkClient.serviceLoop();
      asrChatClient.serviceLoop(/*allow_camera=*/false);

      if (!asrChatClient.runVoiceRound(RECORD_TIME)) {
        log_error("[CHAT] asr_chat round failed, reconnect retry in 2s");
        blink_led(COLOR_RED, 3);
        delay(2000);
      }
    }
    log_info("[CHAT] leave chat loop");
  }

  delay(10);
}
