#include "wifi_provision_internal.h"

#include "boot_guide.h"

void wifi_notify_link_down(void) {
  g_wifi_was_up = false;
  g_wifi_reconnect_pending = true;
  g_wifi_last_reconnect_ms = 0;
  if (g_wifi_link_down_handler) {
    g_wifi_link_down_handler();
  }
}

void wifi_notify_link_up(void) {
  g_wifi_reconnect_pending = false;
  g_wifi_quick_reconnect_active = false;
  g_wifi_reconnect_backoff_ms = 3000;
  wifi_apply_runtime_keepalive();
  boot_guide_wifi_on_connected(WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
  if (!g_wifi_was_up && g_wifi_link_up_handler) {
    g_wifi_link_up_handler();
  }
  g_wifi_was_up = true;
}

void wifi_register_event_handlers_once(void) {
  if (g_wifi_handlers_registered) {
    return;
  }
  g_wifi_handlers_registered = true;
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    (void)info;
#if defined(ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      g_wifi_event_disconnected = true;
    } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      g_wifi_event_got_ip = true;
    }
#elif defined(SYSTEM_EVENT_STA_DISCONNECTED)
    if (event == SYSTEM_EVENT_STA_DISCONNECTED) {
      g_wifi_event_disconnected = true;
    } else if (event == SYSTEM_EVENT_STA_GOT_IP) {
      g_wifi_event_got_ip = true;
    }
#endif
  });
}

void wifi_handle_events_in_main_context(void) {
  if (g_wifi_event_disconnected) {
    g_wifi_event_disconnected = false;
    Serial.println("[wifi] event: disconnected");
    wifi_notify_link_down();
  }
  if (g_wifi_event_got_ip) {
    g_wifi_event_got_ip = false;
    if (wifi_link_up()) {
      Serial.printf("[wifi] event: got IP=%s RSSI=%d dBm\r\n", WiFi.localIP().toString().c_str(),
                    WiFi.RSSI());
      wifi_notify_link_up();
    }
  }
}

namespace {

void wifi_try_reconnect_now() {
  if (g_wifi_ssid.length() == 0) {
    g_wifi_ssid = WiFi.SSID();
  }
  // reconnect() only works when the driver still holds a STA config (prior link).
  // After boot/portal failure, disconnect(true) cleared it — use full begin() path.
  if (g_wifi_ssid.length() > 0 && WiFi.SSID().length() > 0) {
    Serial.printf("[wifi] maintain quick reconnect ssid=%s\r\n", g_wifi_ssid.c_str());
    WiFi.reconnect();
    g_wifi_quick_reconnect_active = true;
    g_wifi_quick_reconnect_start_ms = millis();
    return;
  }

  Serial.println("[wifi] maintain full reconnect");
  if (wifi_connect_saved_then_default("maintain", kWifiMaintainConnectAttempts)) {
    wifi_notify_link_up();
    return;
  }
  if (g_wifi_reconnect_backoff_ms < 60000UL) {
    g_wifi_reconnect_backoff_ms *= 2;
    if (g_wifi_reconnect_backoff_ms > 60000UL) {
      g_wifi_reconnect_backoff_ms = 60000UL;
    }
  }
  Serial.printf("[wifi] maintain reconnect failed, next in %lu ms\r\n",
                (unsigned long)g_wifi_reconnect_backoff_ms);
}

}  // namespace

void wifi_provision_set_link_handlers(WifiLinkHandler on_down, WifiLinkHandler on_up) {
  g_wifi_link_down_handler = on_down;
  g_wifi_link_up_handler = on_up;
}

void wifi_provision_maintain() {
  wifi_register_event_handlers_once();
  wifi_handle_events_in_main_context();

  const unsigned long now = millis();
  if (wifi_link_up()) {
    g_wifi_reconnect_pending = false;
    g_wifi_quick_reconnect_active = false;
    if (!g_wifi_was_up) {
      Serial.printf("[wifi] link restored IP=%s RSSI=%d dBm\r\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      wifi_notify_link_up();
    }
    return;
  }

  if (g_wifi_was_up) {
    Serial.println("[wifi] link lost (poll)");
    wifi_notify_link_down();
  }

  if (g_wifi_quick_reconnect_active) {
    if (wifi_link_up()) {
      Serial.printf("[wifi] quick reconnect ok IP=%s RSSI=%d dBm\r\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      wifi_notify_link_up();
      return;
    }
    if (now - g_wifi_quick_reconnect_start_ms < kWifiQuickReconnectWaitMs) {
      return;
    }
    g_wifi_quick_reconnect_active = false;
  }

  if (!g_wifi_reconnect_pending && now - g_wifi_last_check_ms < kWifiCheckIntervalDownMs) {
    return;
  }
  g_wifi_last_check_ms = now;
  if (now - g_wifi_last_reconnect_ms < g_wifi_reconnect_backoff_ms) {
    return;
  }
  g_wifi_last_reconnect_ms = now;
  wifi_try_reconnect_now();
}
