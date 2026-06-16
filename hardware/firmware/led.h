#ifndef LED_H
#define LED_H

#include <Arduino.h>

#define COLOR_RED      0xFF0000
#define COLOR_GREEN    0x00FF00
#define COLOR_BLUE     0x0000FF
#define COLOR_WHITE    0xFFFFFF
#define COLOR_BLACK    0x000000
#define COLOR_YELLOW   0xFFFF00
#define COLOR_CYAN     0x00FFFF
#define COLOR_MAGENTA  0xFF00FF
#define COLOR_ORANGE   0xFF8000
#define COLOR_PURPLE   0x8000FF

void setup_led();
void set_led(uint32_t color, uint8_t brightness = 100);
void blink_led(uint32_t color = COLOR_BLUE, uint8_t times = 1, uint8_t brightness = 5);

#endif
