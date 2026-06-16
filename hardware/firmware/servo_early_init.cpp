// 在 setup() 之前把舵机信号线从 UART0 切到 GPIO 输出 LOW。
#include "deskbot_config.h"
#include <driver/gpio.h>
#include <soc/gpio_periph.h>
#include <soc/io_mux_reg.h>

static void deskbot_servo_pin_claim_low(gpio_num_t pin) {
  PIN_FUNC_SELECT(GPIO_PIN_MUX_REG[pin], PIN_FUNC_GPIO);
  gpio_config_t cfg = {};
  cfg.pin_bit_mask = 1ULL << pin;
  cfg.mode = GPIO_MODE_OUTPUT;
  cfg.pull_up_en = GPIO_PULLUP_DISABLE;
  cfg.pull_down_en = GPIO_PULLDOWN_ENABLE;
  cfg.intr_type = GPIO_INTR_DISABLE;
  gpio_config(&cfg);
  gpio_set_level(pin, 0);
}

__attribute__((constructor(101))) static void deskbot_servo_pins_early_init(void) {
  deskbot_servo_pin_claim_low((gpio_num_t)DESKBOT_ROM_X_PIN);
  deskbot_servo_pin_claim_low((gpio_num_t)DESKBOT_ROM_Y_PIN);
}

void deskbot_servo_pins_claim_low(void) {
  deskbot_servo_pin_claim_low((gpio_num_t)DESKBOT_ROM_X_PIN);
  deskbot_servo_pin_claim_low((gpio_num_t)DESKBOT_ROM_Y_PIN);
}
