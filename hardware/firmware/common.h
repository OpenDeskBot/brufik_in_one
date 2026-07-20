#ifndef Common_h
#define Common_h

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <FFat.h>
#include "logger.h"
#include "led.h"
#include "display.h"

#define VERSION "0.0.5"
#define PRODUCT_NAME "Deskbot"
#define AP_SSID "Deskbot_Rom"
#define UDP_DISCOVERY_PREFIX "Deskbot"

#ifndef RECORD_TIME
#define RECORD_TIME DESKBOT_UPLINK_MAX_SEC
#endif

/* asr_chat 主循环最长运行时间（毫秒）。0 = 上电后一直对话，不自动 leave。原固件为 600000（10 分钟）。 */
#ifndef CHAT_LOOP_MAX_MS
#define CHAT_LOOP_MAX_MS 0
#endif

extern bool start_chat;

void setup_FFat();
String get_mac_address();
/** 设备唯一 ID，格式 deskbot_<mac>（基于 WiFi STA MAC） */
const char* get_device_id();
String get_local_ip();

#endif
