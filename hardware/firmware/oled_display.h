#pragma once

#include "deskbot_config.h"

#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>

#ifndef DESKBOT_LCD_SPI_HZ
#define DESKBOT_LCD_SPI_HZ 27000000UL
#endif

#define DESKBOT_LCD_COLOR_BLACK ST77XX_BLACK
#define DESKBOT_LCD_COLOR_WHITE ST77XX_WHITE
#define DESKBOT_LCD_COLOR_YELLOW ST77XX_YELLOW
#define DESKBOT_LCD_COLOR_RED ST77XX_RED
#define DESKBOT_LCD_COLOR_GREEN ST77XX_GREEN
#define DESKBOT_LCD_COLOR_BLUE ST77XX_BLUE

/** ST7789P 240×284 */
class DeskbotLcd : public Adafruit_ST7789 {
public:
  DeskbotLcd(int8_t cs, int8_t dc, int8_t rst) : Adafruit_ST7789(cs, dc, rst) {}

  void setupPanel();
  void applyOffsets(int8_t col, int8_t row) { setColRowStart(col, row); }
  void syncPanelSize();
  void setRotation(uint8_t r) override;
};

void deskbot_lcd_log_wiring_required();
void deskbot_lcd_backlight_on();
