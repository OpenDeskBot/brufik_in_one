#include "wifi_provision.h"

#include <string.h>

#include "utils/nvs_config_utils.h"
#include "utils/utils.h"
#include "wifi_provision_internal.h"

char g_wifi_ap_ssid[32] = {};
WebServer g_wifi_server(80);
bool g_wifi_done_config = false;
bool g_wifi_portal_exit_continue = false;
String g_wifi_ssid;
String g_wifi_password;

WifiLinkHandler g_wifi_link_down_handler = nullptr;
WifiLinkHandler g_wifi_link_up_handler = nullptr;
bool g_wifi_handlers_registered = false;
bool g_wifi_was_up = false;
bool g_wifi_reconnect_pending = false;
bool g_wifi_quick_reconnect_active = false;
unsigned long g_wifi_last_check_ms = 0;
unsigned long g_wifi_reconnect_backoff_ms = 3000;
unsigned long g_wifi_last_reconnect_ms = 0;
unsigned long g_wifi_quick_reconnect_start_ms = 0;
volatile bool g_wifi_event_disconnected = false;
volatile bool g_wifi_event_got_ip = false;

void wifi_build_ap_ssid(void) {
  strncpy(g_wifi_ap_ssid, get_device_id(), sizeof(g_wifi_ap_ssid) - 1);
  g_wifi_ap_ssid[sizeof(g_wifi_ap_ssid) - 1] = '\0';
}

bool wifi_provision_connect() {
  (void)wifi_provision_ap_offer(nvs_get_ap_offer_timeout_ms());
  if (wifi_provision_connect_sta()) {
    return true;
  }
  wifi_provision_config_portal();
  return wifi_provision_connect_sta();
}

bool wifi_provision_is_connected() {
  return wifi_link_up();
}

void wifi_provision_reset() {
  nvs_wifi_clear();
  Serial.println("[wifi] reset: rebooting...");
  delay(500);
  ESP.restart();
}
