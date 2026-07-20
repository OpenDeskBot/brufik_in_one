#include "led.h"
#include "logger.h"

void setup_led() {
  /* 状态 LED 引脚与音频/相机复用，无硬件可初始化；不算失败。 */
  log_info("[LED] setup skipped (pin used by audio/camera)");
}

void set_led(uint32_t color, uint8_t brightness) {
  (void)color;
  (void)brightness;
}

void blink_led(uint32_t color, uint8_t times, uint8_t brightness) {
  (void)color;
  (void)times;
  (void)brightness;
}
