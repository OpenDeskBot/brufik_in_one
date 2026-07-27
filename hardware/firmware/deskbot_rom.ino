// Deskbot — XIAO ESP32S3 Sense：摄像头 + pb + 音频 + 显示屏 + 舵机
#include <WiFi.h>
#include "display_panel.h"
#include "camera.h"
#include "deskbot_config.h"
#include "wifi_provision.h"
#include "display.h"
#include "speaker.h"
#include "mic.h"
#include "pb_runtime.h"
#include "asr_ws.h"
#include "head.h"
#include "cmd.h"
#include "led.h"
#include "logger.h"
#include "task_trace.h"
#include "utils/utils.h"
#include "utils/nvs_config_utils.h"
#include "boot_guide.h"
#include "ws_transport.h"

/* loopTask 只做 cmd / wifi maintain / yield；Opus encode 在 mic、decode 在 pb_runtime。
 * 覆盖弱符号 getArduinoLoopTaskStackSize（platformio.ini 另有 -DARDUINO_LOOP_STACK_SIZE）。 */
size_t getArduinoLoopTaskStackSize() {
  return 24 * 1024;
}

static void on_wifi_link_down() {
  asr_ws_on_link_down("wifi lost");
}

static void on_wifi_link_up() {
  asr_ws_on_link_up();
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

  /* ---- 阶段 A：显示 + 基础硬件 ---- */
  setup_display();
  display_backlight_on();
  setup_FFat();
  setup_led();

  setup_head();
  setup_mic();
  setup_speaker();

  log_info("[BOOT] pin=%s hotspot=%s", nvs_get_pin_code(), get_device_id());

  (void)wifi_provision_ap_offer(nvs_get_ap_offer_timeout_ms());
  if (!wifi_provision_connect_sta()) {
    wifi_provision_config_portal();
    if (!wifi_provision_connect_sta()) {
      log_error("WiFi connect failed");
      boot_guide_wifi_result(false, nullptr, "请重启或配网");
      return;
    }
  }
  wifi_provision_set_link_handlers(on_wifi_link_down, on_wifi_link_up);

  /* ---- 阶段 B：云服务器连接（屏幕引导）---- */
  task_setup_speaker();
  task_setup_mic();
  if (!setup_ws_transport()) {
    log_error("[BOOT] ws_transport setup failed");
    boot_guide_server_result(false, "初始化失败");
    delay(1200);
  } else if (!task_setup_ws_transport()) {
    log_error("[BOOT] ws_transport task_setup failed");
    boot_guide_server_result(false, "任务启动失败");
    delay(1200);
  } else {
    (void)boot_guide_wait_ws_ready(DESKBOT_WS_CONNECT_TIMEOUT_MS);
  }

  static bool s_camera_ok = false;
  s_camera_ok = setup_camera();
  head_servo_boot_attach();
  if (!s_camera_ok) {
    log_warn("[BOOT] Camera absent or failed — continuing without camera");
  }

  /* ---- 阶段 C：pb / 显示任务 ---- */
  if (!setup_pb_runtime()) {
    log_error("[BOOT] pb_runtime setup failed");
  } else if (!task_setup_pb_runtime()) {
    log_error("[BOOT] pb_runtime task_setup failed");
  }

  task_setup_display();
  task_setup_head();

  if (s_camera_ok) {
    task_setup_camera(); /* 无独立任务；抓帧并入 ws_transport */
  } else {
    log_warn("[BOOT] Skipping camera uplink (no camera)");
  }

  log_info("[BOOT] firmware=%s %s %s", VERSION, __DATE__, __TIME__);
  char ws_url[128];
  deskbot_ws_format_active_url(ws_url, sizeof(ws_url));
  log_info("[BOOT] device_id=%s ws=%s pin=%s", get_device_id(), ws_url, nvs_get_pin_code());
  log_info("PSRAM size=%u free=%u", (unsigned)ESP.getPsramSize(), (unsigned)ESP.getFreePsram());

  boot_guide_show_ready();
  log_info("%s is Ready. http://%s", PRODUCT_NAME, WiFi.localIP().toString().c_str());
  log_warn("[BOOT] ready device=%s ws=%s wifi_ip=%s",
           get_device_id(), ws_url, WiFi.localIP().toString().c_str());
  log_set_level(LOG_LEVEL_WARN);
}

void loop() {
  handle_cmd();
  wifi_provision_maintain();
  log_task_tick();
  yield();
}
