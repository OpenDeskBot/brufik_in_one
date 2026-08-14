#include "wifi_provision_internal.h"

#include "boot_guide.h"
#include "utils/html.h"
#include "utils/nvs_config_utils.h"
#include "utils/utils.h"

namespace {

void send_json(int code, const String& body) {
  g_wifi_server.send(code, "application/json", body);
}

void send_ok() {
  send_json(200, "{\"success\":true}");
}

void send_ok_body(const String& extra_fields) {
  send_json(200, String("{\"success\":true,") + extra_fields + "}");
}

void send_err(int code, const char* msg) {
  send_json(code,
            String("{\"success\":false,\"message\":\"") + json_escape(String(msg)) + "\"}");
}

void ensure_config_ap_running() {
  wifi_build_ap_ssid();
  const wifi_mode_t mode = WiFi.getMode();
  if ((mode == WIFI_AP || mode == WIFI_AP_STA) && WiFi.softAPIP() != IPAddress(0, 0, 0, 0)) {
    return;
  }
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.mode(WIFI_AP_STA);
  delay(100);
  WiFi.softAP(g_wifi_ap_ssid);
  Serial.printf("[wifi] AP started ssid=%s (open) ip=%s\r\n", g_wifi_ap_ssid,
                WiFi.softAPIP().toString().c_str());
}

}  // namespace

void wifi_portal_stop(bool power_off) {
  g_wifi_server.close();
  // Do not softAPdisconnect(true) while a phone may still be associated: it races
  // wifi stop and yields "netstack cb reg failed with 12308", then STA never associates.
  // WIFI_OFF tears down AP+STA cleanly; STA reconnect will re-init afterwards.
  WiFi.mode(WIFI_OFF);
  delay(power_off ? 200 : 500);
}

void wifi_portal_setup_http(void) {
  g_wifi_done_config = false;
  g_wifi_portal_exit_continue = false;
  ensure_config_ap_running();

  g_wifi_server.on("/", HTTP_GET, []() { g_wifi_server.send(200, "text/html", index_html); });

  g_wifi_server.on("/status", HTTP_GET, []() {
    String json = "{";
    json += "\"ok\":true,";
    json += "\"ap_ssid\":\"" + json_escape(String(g_wifi_ap_ssid)) + "\",";
    json += "\"ap_ip\":\"" + WiFi.softAPIP().toString() + "\",";
    json += "\"device_id\":\"" + json_escape(String(get_device_id())) + "\",";
    json += "\"station_count\":" + String(WiFi.softAPgetStationNum());
    json += "}";
    send_json(200, json);
  });

  g_wifi_server.on("/scan-wifi", HTTP_GET, []() {
    constexpr int kMaxScanOut = 32;
    String uniq_ssid[kMaxScanOut];
    int uniq_rssi[kMaxScanOut];
    int uniq_count = 0;

    const int n = WiFi.scanNetworks();
    for (int i = 0; i < n; ++i) {
      const String s = WiFi.SSID(i);
      if (s.length() == 0) {
        continue;
      }
      const int r = WiFi.RSSI(i);
      int found = -1;
      for (int j = 0; j < uniq_count; ++j) {
        if (uniq_ssid[j] == s) {
          found = j;
          break;
        }
      }
      if (found >= 0) {
        if (r > uniq_rssi[found]) {
          uniq_rssi[found] = r;
        }
      } else if (uniq_count < kMaxScanOut) {
        uniq_ssid[uniq_count] = s;
        uniq_rssi[uniq_count] = r;
        uniq_count++;
      }
    }
    WiFi.scanDelete();

    for (int i = 0; i < uniq_count; ++i) {
      for (int j = i + 1; j < uniq_count; ++j) {
        if (uniq_rssi[j] > uniq_rssi[i]) {
          const int tmp_r = uniq_rssi[i];
          uniq_rssi[i] = uniq_rssi[j];
          uniq_rssi[j] = tmp_r;
          String tmp_s = uniq_ssid[i];
          uniq_ssid[i] = uniq_ssid[j];
          uniq_ssid[j] = tmp_s;
        }
      }
    }

    String json = "[";
    for (int i = 0; i < uniq_count; ++i) {
      if (i > 0) {
        json += ",";
      }
      json += "{\"ssid\":\"" + json_escape(uniq_ssid[i]) + "\",\"rssi\":" + String(uniq_rssi[i]) +
              "}";
    }
    json += "]";
    send_json(200, json);
  });

  g_wifi_server.on("/save-wifi", HTTP_POST, []() {
    const String new_ssid = g_wifi_server.arg("ssid");
    const String new_password = g_wifi_server.arg("password");
    if (new_ssid.length() == 0) {
      send_err(400, "SSID cannot be empty");
      return;
    }
    if (!nvs_wifi_upsert(new_ssid.c_str(), new_password.c_str())) {
      send_err(500, "Failed to save credentials");
      return;
    }
    g_wifi_ssid = new_ssid;
    g_wifi_password = new_password;
    Serial.printf("[wifi] credentials saved ssid=%s\r\n", new_ssid.c_str());
    send_ok_body("\"message\":\"WiFi configuration saved\"");
  });

  g_wifi_server.on("/device-config", HTTP_GET, []() {
    NvsWifiCredential saved[NVS_MAX_SAVED_WIFI];
    const int saved_count = nvs_wifi_list(saved, NVS_MAX_SAVED_WIFI);
    String json = "{";
    json += "\"ok\":true,";
    json += "\"device_id\":\"" + json_escape(String(get_device_id())) + "\",";
    json += "\"version\":\"" VERSION "\",";
    json += "\"ap_offer_sec\":" + String(nvs_get_ap_offer_timeout_sec()) + ",";
    json += "\"ap_offer_min\":" + String(nvs_get_ap_offer_timeout_min_sec()) + ",";
    json += "\"ap_offer_max\":" + String(nvs_get_ap_offer_timeout_max_sec()) + ",";
    json += "\"saved_wifi\":[";
    for (int i = 0; i < saved_count; ++i) {
      if (i > 0) {
        json += ",";
      }
      json += "\"" + json_escape(saved[i].ssid) + "\"";
    }
    json += "],";

    char builtin_url[96];
    deskbot_ws_format_builtin_url(builtin_url, sizeof(builtin_url));
    json += "\"ws_active\":\"" + json_escape(String(nvs_ws_get_active_id())) + "\",";
    json += "\"ws_builtin_url\":\"" + json_escape(String(builtin_url)) + "\",";
    json += "\"ws_servers\":[";
    NvsWsServerEntry ws_entries[NVS_MAX_CUSTOM_WS];
    const int ws_count = nvs_ws_list_custom(ws_entries, NVS_MAX_CUSTOM_WS);
    for (int i = 0; i < ws_count; ++i) {
      if (i > 0) {
        json += ",";
      }
      json += "{\"id\":\"" + json_escape(String(ws_entries[i].id)) + "\",";
      json += "\"url\":\"" + json_escape(String(ws_entries[i].url)) + "\"}";
    }
    json += "]}";
    send_json(200, json);
  });

  g_wifi_server.on("/device-config/ap-offer-sec", HTTP_POST, []() {
    if (!nvs_set_ap_offer_timeout_sec((unsigned)g_wifi_server.arg("sec").toInt())) {
      send_err(400, "启动时间须在 5–60 秒之间");
      return;
    }
    send_ok_body("\"ap_offer_sec\":" + String(nvs_get_ap_offer_timeout_sec()));
  });

  g_wifi_server.on("/device-config/reset-device-id", HTTP_POST, []() {
    nvs_reset_device_suffix();
    send_ok_body("\"device_id\":\"" + json_escape(String(get_device_id())) + "\"");
  });

  g_wifi_server.on("/device-config/delete-wifi", HTTP_POST, []() {
    if (!nvs_wifi_delete(g_wifi_server.arg("ssid").c_str())) {
      send_err(404, "未找到该 Wi‑Fi");
      return;
    }
    send_ok();
  });

  g_wifi_server.on("/device-config/ws-servers", HTTP_POST, []() {
    String url = g_wifi_server.arg("url");
    url.trim();
    if (url.length() == 0) {
      send_err(400, "URL 不能为空");
      return;
    }
    WsProto parsed;
    if (!parse_ws_proto(url.c_str(), parsed)) {
      send_err(400, "URL 格式须为 ws:// 或 wss://");
      return;
    }
    char new_id[8];
    if (!nvs_ws_add_custom(url.c_str(), new_id, sizeof(new_id))) {
      send_err(400, "添加失败或已达上限");
      return;
    }
    send_ok_body("\"id\":\"" + json_escape(String(new_id)) + "\"");
  });

  g_wifi_server.on("/device-config/ws-servers/select", HTTP_POST, []() {
    if (!nvs_ws_set_active_id(g_wifi_server.arg("id").c_str())) {
      send_err(400, "无效的服务器");
      return;
    }
    send_ok();
  });

  g_wifi_server.on("/device-config/ws-servers/delete", HTTP_POST, []() {
    if (!nvs_ws_delete_custom(g_wifi_server.arg("id").c_str())) {
      send_err(404, "未找到该服务器");
      return;
    }
    send_ok();
  });

  g_wifi_server.on("/device-config/factory-reset", HTTP_POST, []() {
    nvs_wifi_clear();
    nvs_device_factory_reset();
    nvs_ws_factory_reset();
    send_ok_body("\"message\":\"factory reset\"");
    delay(500);
    ESP.restart();
  });

  g_wifi_server.on("/device-config/continue-boot", HTTP_POST, []() {
    g_wifi_portal_exit_continue = true;
    g_wifi_done_config = true;
    send_ok();
  });

  g_wifi_server.onNotFound([]() {
    g_wifi_server.sendHeader("Location", "/", true);
    g_wifi_server.send(302, "text/plain", "");
  });

  g_wifi_server.begin();
  Serial.printf("[wifi] config portal http://%s SSID=%s\r\n", WiFi.softAPIP().toString().c_str(),
                g_wifi_ap_ssid);
  char url[32];
  snprintf(url, sizeof(url), "http://%s/", WiFi.softAPIP().toString().c_str());
  boot_guide_provision_show(g_wifi_ap_ssid, url, -1);
}

bool wifi_provision_ap_offer(unsigned timeout_ms) {
  wifi_build_ap_ssid();
  WiFi.persistent(false);
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.mode(WIFI_AP);
  delay(100);
  if (!WiFi.softAP(g_wifi_ap_ssid)) {
    Serial.println("[wifi] AP offer: softAP failed");
    return false;
  }

  char portal_url[32];
  snprintf(portal_url, sizeof(portal_url), "http://%s/", WiFi.softAPIP().toString().c_str());
  Serial.printf("[wifi] AP offer ssid=%s (open) ip=%s timeout=%u ms\r\n", g_wifi_ap_ssid, portal_url,
                timeout_ms);

  unsigned long remaining_ms = timeout_ms;
  unsigned long last_tick_ms = millis();
  int last_display_key = -2;
  bool http_started = false;
  g_wifi_done_config = false;
  g_wifi_portal_exit_continue = false;

  while (true) {
    const unsigned long now = millis();
    const unsigned long elapsed = now - last_tick_ms;
    last_tick_ms = now;

    const bool paused = WiFi.softAPgetStationNum() > 0;
    if (!paused && remaining_ms > 0) {
      remaining_ms = (elapsed >= remaining_ms) ? 0 : (remaining_ms - elapsed);
    }

    if (paused && !http_started) {
      Serial.printf("[wifi] AP offer: station connected (%u), countdown paused\r\n",
                    (unsigned)WiFi.softAPgetStationNum());
      wifi_portal_setup_http();
      http_started = true;
    }

    if (http_started) {
      g_wifi_server.handleClient();
      if (g_wifi_done_config) {
        wifi_portal_stop(false);
        Serial.println(g_wifi_portal_exit_continue ? "[wifi] AP offer: continue boot from portal"
                                                   : "[wifi] AP offer: config saved from portal");
        g_wifi_portal_exit_continue = false;
        return true;
      }
    }

    if (remaining_ms == 0 && !paused) {
      Serial.println("[wifi] AP offer: timeout, no station");
      if (http_started) {
        g_wifi_server.close();
      }
      WiFi.mode(WIFI_OFF);
      delay(200);
      return false;
    }

    const int display_key = paused ? -1 : (int)((remaining_ms + 999UL) / 1000UL);
    if (display_key != last_display_key) {
      last_display_key = display_key;
      boot_guide_provision_show(g_wifi_ap_ssid, portal_url, display_key);
      if (!paused) {
        Serial.printf("[wifi] AP offer: countdown %d s\r\n", display_key);
      }
    }
    delay(100);
  }
}

void wifi_provision_config_portal() {
  Serial.println("[wifi] enter config mode");
  wifi_portal_setup_http();

  const unsigned long portal_start_ms = millis();
  while (!g_wifi_done_config) {
    g_wifi_server.handleClient();
    if (millis() - portal_start_ms >= kWifiConfigPortalTimeoutMs) {
      Serial.println("[wifi] config portal timeout, retry connect");
      break;
    }
    delay(10);
  }

  wifi_portal_stop(false);
  if (g_wifi_portal_exit_continue) {
    Serial.println("[wifi] continue boot, closing portal...");
    g_wifi_portal_exit_continue = false;
  } else if (g_wifi_done_config) {
    Serial.println("[wifi] config saved, reconnecting...");
  }
}
