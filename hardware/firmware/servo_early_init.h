#pragma once

#include "deskbot_config.h"

/** setup() 入口再次把 D6/D7 从 UART0 切为 GPIO 输出 LOW（constructor 已做过一次）。 */
void deskbot_servo_pins_claim_low(void);
