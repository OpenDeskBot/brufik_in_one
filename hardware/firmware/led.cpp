#include "led.h"
#include "logger.h"

void setup_led() { log_info("Status LED disabled (pin used by audio/camera)"); }

void set_led(uint32_t color, uint8_t brightness) {
  (void)color;
  (void)brightness;
}

void blink_led(uint32_t color, uint8_t times, uint8_t brightness) {
  (void)color;
  (void)times;
  (void)brightness;
}
