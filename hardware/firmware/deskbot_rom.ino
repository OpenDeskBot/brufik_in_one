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
#include "deskbot_state.h"
#include "head.h"
#include "cmd.h"
#include "led.h"
#include "logger.h"
#include "task_trace.h"
#include "utils/utils.h"
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

  /* ---- 阶段 A：硬件 setup_*（一般不出错；不启动上行生产者任务）---- */
  setup_display();
  setup_FFat();
  setup_led();

  /* 预归中（GPIO 位bang，不 attach）；永久 attach 须在 camera 之后（LEDC vs MCPWM）。 */
  setup_head();
  setup_mic();
  setup_speaker();

  static bool s_camera_ok = false;
  s_camera_ok = setup_camera();
  /* attach：永久 PWM；motor_task 由阶段 B task_setup_head 启动（boot 回中会兜底）。 */
  head_servo_boot_attach();
  display_backlight_on();
  if (!s_camera_ok) {
    log_warn("[BOOT] Camera absent or failed — continuing without camera");
    display_boot_show("无摄像头", "继续启动...");
  }

  if (!wifi_provision_connect()) {
    log_error("WiFi connect failed");
    display_boot_show("WiFi 连接失败", "请重启或配网");
    return;
  }
  wifi_provision_set_link_handlers(on_wifi_link_down, on_wifi_link_up);

  /* ---- 阶段 B：音频上行 + WS（先占内部 RAM，避免 display 大栈挤掉 ws_transport）---- */
  task_setup_speaker();
  task_setup_mic();
  if (!setup_pb_runtime()) {
    log_error("[BOOT] pb_runtime setup failed");
  } else if (!setup_ws_transport()) {
    log_error("[BOOT] ws_transport setup failed");
  } else if (!task_setup_ws_transport()) {
    log_error("[BOOT] ws_transport task_setup failed");
  } else if (!task_setup_pb_runtime()) {
    log_error("[BOOT] pb_runtime task_setup failed");
  }

  /* ---- 阶段 C：显示 / 舵机 / 统计 ---- */
  task_setup_display();
  task_setup_head();
  // task_setup_cpu_runtime_stats();

  /* ---- 阶段 D：其余上行生产者 ---- */
  task_setup_deskbot_state();
  if (s_camera_ok) {
    task_setup_camera();
  } else {
    log_warn("[BOOT] Skipping camera uplink task (no camera)");
  }

  log_info("[BOOT] firmware=%s %s %s", VERSION, __DATE__, __TIME__);
  log_info("[BOOT] device_id=%s ws=%s:%u api_key=%s", get_device_id(), DESKBOT_WS_HOST,
           (unsigned)DESKBOT_WS_PORT, deskbot_api_key_configured() ? "set" : "MISSING");
  log_info("PSRAM size=%u free=%u", (unsigned)ESP.getPsramSize(), (unsigned)ESP.getFreePsram());

  display_boot_show_ready();
  log_info("%s is Ready. http://%s", PRODUCT_NAME, WiFi.localIP().toString().c_str());
  log_warn("[BOOT] ready device=%s ws=%s:%u wifi_ip=%s",
           get_device_id(), DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT,
           WiFi.localIP().toString().c_str());
  log_set_level(LOG_LEVEL_WARN);
}

void loop() {
  handle_cmd();
  wifi_provision_maintain();
  log_task_tick();
  yield();
}
