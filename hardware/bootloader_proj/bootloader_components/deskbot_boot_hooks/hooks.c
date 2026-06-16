/*
 * 二级 Bootloader 钩子：尽早把舵机 PWM 脚（GPIO43/44 = D6/D7）从 UART0 切到 GPIO 输出 LOW。
 * 与 firmware/deskbot_config.h 中 DESKBOT_ROM_Y_PIN / DESKBOT_ROM_X_PIN 保持一致。
 *
 * 注意：ROM 下载模式烧录期间本钩子不运行。
 */
#include "esp_rom_gpio.h"
#include "hal/gpio_hal.h"
#include "soc/gpio_periph.h"
#include "soc/gpio_sig_map.h"
#include "soc/gpio_struct.h"

#define DESKBOT_SERVO_Y_GPIO 43
#define DESKBOT_SERVO_X_GPIO 44

void bootloader_hooks_include(void) {}

static void boot_hold_servo_low(int pin) {
  gpio_hal_iomux_func_sel(GPIO_PIN_MUX_REG[pin], PIN_FUNC_GPIO);
  esp_rom_gpio_pad_select_gpio(pin);
  esp_rom_gpio_connect_out_signal(pin, SIG_GPIO_OUT_IDX, false, false);
  gpio_ll_output_enable(&GPIO, pin);
  gpio_ll_set_level(&GPIO, pin, 0);
}

void bootloader_before_init(void) {
  boot_hold_servo_low(DESKBOT_SERVO_Y_GPIO);
  boot_hold_servo_low(DESKBOT_SERVO_X_GPIO);
}

void bootloader_after_init(void) {
  boot_hold_servo_low(DESKBOT_SERVO_Y_GPIO);
  boot_hold_servo_low(DESKBOT_SERVO_X_GPIO);
}
