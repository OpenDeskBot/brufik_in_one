#pragma once

#include <Arduino.h>
#include <WebServer.h>
#include <WiFi.h>

#include "wifi_provision.h"

/** 模块间共享状态与内部 API（勿被业务直接 include）。 */

constexpr int kWifiMaxReconnectAttempts = 40;
constexpr int kWifiMaintainConnectAttempts = 20;
constexpr unsigned long kWifiCheckIntervalDownMs = 2000;
constexpr unsigned long kWifiQuickReconnectWaitMs = 8000;
constexpr unsigned long kWifiConfigPortalTimeoutMs = 5UL * 60UL * 1000UL;

extern char g_wifi_ap_ssid[32];
extern WebServer g_wifi_server;
extern bool g_wifi_done_config;
extern bool g_wifi_portal_exit_continue;
extern String g_wifi_ssid;
extern String g_wifi_password;

extern WifiLinkHandler g_wifi_link_down_handler;
extern WifiLinkHandler g_wifi_link_up_handler;
extern bool g_wifi_handlers_registered;
extern bool g_wifi_was_up;
extern bool g_wifi_reconnect_pending;
extern bool g_wifi_quick_reconnect_active;
extern unsigned long g_wifi_last_check_ms;
extern unsigned long g_wifi_reconnect_backoff_ms;
extern unsigned long g_wifi_last_reconnect_ms;
extern unsigned long g_wifi_quick_reconnect_start_ms;
extern volatile bool g_wifi_event_disconnected;
extern volatile bool g_wifi_event_got_ip;

void wifi_build_ap_ssid(void);
bool wifi_link_up(void);
void wifi_apply_runtime_keepalive(void);

/** 扫可见已保存 → 逐个连；再试编译期默认。 */
bool wifi_connect_saved_then_default(const char* tag, int max_attempts);

void wifi_register_event_handlers_once(void);
void wifi_notify_link_down(void);
void wifi_notify_link_up(void);
void wifi_handle_events_in_main_context(void);

void wifi_portal_setup_http(void);
void wifi_portal_stop(bool power_off);
