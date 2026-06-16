#include "oled_display.h"

#include "common.h"

#include <SPI.h>

void DeskbotLcd::setRotation(uint8_t r) {
  (void)r;
  Adafruit_ST7789::setRotation(3);
  int16_t xstart = (int16_t)((320 - DESKBOT_LCD_HEIGHT) / 2) +
                   (int16_t)DESKBOT_LCD_ROT3_XSTART_ADJ;
  if (xstart < 0) {
    xstart = 0;
  }
  _xstart = xstart;
  _ystart = DESKBOT_LCD_COL_OFFSET;
  _width  = DESKBOT_LCD_HEIGHT;
  _height = DESKBOT_LCD_WIDTH;
}

void DeskbotLcd::syncPanelSize() {
  setRotation(0);
}

void DeskbotLcd::setupPanel() {
  SPI.begin(DESKBOT_LCD_SCK, -1, DESKBOT_LCD_MOSI, DESKBOT_LCD_CS);
  init(DESKBOT_LCD_WIDTH, DESKBOT_LCD_HEIGHT, SPI_MODE3);
  applyOffsets(DESKBOT_LCD_COL_OFFSET, DESKBOT_LCD_ROW_OFFSET);
  syncPanelSize();
  setSPISpeed(DESKBOT_LCD_SPI_HZ);
  invertDisplay(true);
  uint8_t ctrl = 0x2C, bri = 255;
  sendCommand(0x53, &ctrl, 1);
  sendCommand(0x51, &bri, 1);
  deskbot_lcd_backlight_on();
  log_info("[LCD] ST7789P init hw_rot=3 landscape %dx%d xstart=%d adj=%d %luHz invert=1",
           (int)_width, (int)_height, (int)_xstart, (int)DESKBOT_LCD_ROT3_XSTART_ADJ,
           (unsigned long)DESKBOT_LCD_SPI_HZ);
}

void deskbot_lcd_log_wiring_required() {
  log_info("[LCD] RST: hardware 3.3V");
  log_info("[LCD] BL: hardware 3.3V");
}

void deskbot_lcd_backlight_on() { deskbot_lcd_log_wiring_required(); }
