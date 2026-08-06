#include "wifi_provision_internal.h"

#include <string.h>

#include <esp_wifi.h>

#include "boot_guide.h"
#include "deskbot_config.h"
#include "utils/nvs_config_utils.h"

namespace {

const char* wifi_status_str(wl_status_t s) {
  switch (s) {
    case WL_IDLE_STATUS:
      return "IDLE";
    case WL_NO_SSID_AVAIL:
      return "NO_SSID";
    case WL_SCAN_COMPLETED:
      return "SCAN_DONE";
    case WL_CONNECTED:
      return "CONNECTED";
    case WL_CONNECT_FAILED:
      return "AUTH_FAILED";
    case WL_CONNECTION_LOST:
      return "LOST";
    case WL_DISCONNECTED:
      return "DISCONNECTED";
    default:
      return "?";
  }
}

/** CN 默认对 ch12/13 只做被动扫描：能扫到但 begin 可能报 NO_SSID。MANUAL 允许主动关联。 */
void wifi_apply_rf_country() {
  wifi_country_t country = {};
  strncpy(country.cc, "CN", sizeof(country.cc));
  country.schan = 1;
  country.nchan = 13;
  country.max_tx_power = 84;  // 21 dBm, 单位 0.25 dBm
  country.policy = WIFI_COUNTRY_POLICY_MANUAL;
  const esp_err_t err = esp_wifi_set_country(&country);
  if (err != ESP_OK) {
    Serial.printf("[wifi] set_country CN/1-13 failed err=%d\r\n", (int)err);
  }
}

void wifi_prepare_sta() {
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.disconnect(true, true);
  delay(300);
  WiFi.mode(WIFI_STA);
  // Extra settle after WIFI_OFF (portal stop) so netif/cb registration can complete.
  delay(300);
  wifi_apply_rf_country();
}

void show_wifi_fail(wl_status_t st, bool ssid_in_scan, const char* next_hint) {
  const char* detail;
  if (st == WL_CONNECT_FAILED) {
    detail = "密码错误";
  } else if (st == WL_NO_SSID_AVAIL || !ssid_in_scan) {
    detail = "未找到 SSID";
  } else {
    detail = "WiFi 连接失败";
  }
  char detail_buf[48];
  if (next_hint && next_hint[0] != '\0') {
    snprintf(detail_buf, sizeof(detail_buf), "%s · %s", detail, next_hint);
    detail = detail_buf;
  }
  boot_guide_wifi_result(false, g_wifi_ssid.c_str(), detail);
}

/** 扫描目标 SSID；找到则返回信道 (1–13)，否则 0。 */
int scan_target_ssid_channel() {
  const int n = WiFi.scanNetworks();
  int channel = 0;
  for (int i = 0; i < n; ++i) {
    if (WiFi.SSID(i) == g_wifi_ssid) {
      channel = WiFi.channel(i);
      Serial.printf("[wifi] scan: found %s rssi=%d ch=%u\r\n", g_wifi_ssid.c_str(), WiFi.RSSI(i),
                    (unsigned)channel);
      break;
    }
  }
  if (channel == 0) {
    Serial.printf("[wifi] scan: %s not visible (seen %d networks)\r\n", g_wifi_ssid.c_str(), n);
  }
  WiFi.scanDelete();
  return channel;
}

int build_visible_saved_candidates(const NvsWifiCredential* saved, int saved_count,
                                   NvsWifiCredential* out, uint8_t* out_channel, int max_out) {
  if (!saved || saved_count <= 0 || !out || !out_channel || max_out <= 0) {
    return 0;
  }

  struct Match {
    NvsWifiCredential cred;
    int rssi;
    uint8_t channel;
  };
  Match matches[NVS_MAX_SAVED_WIFI];
  int match_count = 0;

  wifi_prepare_sta();
  const int n = WiFi.scanNetworks();
  for (int s = 0; s < saved_count; ++s) {
    for (int i = 0; i < n; ++i) {
      if (WiFi.SSID(i) == saved[s].ssid) {
        matches[match_count].cred = saved[s];
        matches[match_count].rssi = WiFi.RSSI(i);
        matches[match_count].channel = (uint8_t)WiFi.channel(i);
        Serial.printf("[wifi] scan: saved %s visible rssi=%d ch=%u\r\n", saved[s].ssid.c_str(),
                      WiFi.RSSI(i), (unsigned)matches[match_count].channel);
        match_count++;
        break;
      }
    }
  }
  WiFi.scanDelete();

  for (int i = 0; i < match_count; ++i) {
    for (int j = i + 1; j < match_count; ++j) {
      if (matches[j].rssi > matches[i].rssi) {
        Match tmp = matches[i];
        matches[i] = matches[j];
        matches[j] = tmp;
      }
    }
  }

  int out_count = 0;
  for (int i = 0; i < match_count && out_count < max_out; ++i) {
    out[out_count] = matches[i].cred;
    out_channel[out_count] = matches[i].channel;
    out_count++;
  }
  if (match_count == 0) {
    Serial.printf("[wifi] scan: no saved SSID visible (saved=%d, seen=%d)\r\n", saved_count, n);
  }
  return out_count;
}

bool try_connect_credential(const char* source_label, int max_attempts, int known_channel) {
  wifi_prepare_sta();

  int channel = known_channel;
  const bool ssid_in_scan = channel > 0;
  if (!ssid_in_scan) {
    channel = scan_target_ssid_channel();
  }
  const bool visible = channel > 0;
  boot_guide_wifi_connecting(g_wifi_ssid.c_str());
  if (!visible) {
    show_wifi_fail(WL_NO_SSID_AVAIL, false, "重试中...");
  }

  if (channel > 0) {
    WiFi.begin(g_wifi_ssid.c_str(), g_wifi_password.c_str(), channel);
  } else {
    WiFi.begin(g_wifi_ssid.c_str(), g_wifi_password.c_str());
  }
  wifi_apply_runtime_keepalive();
  Serial.printf("[wifi] connecting ssid=%s pass_len=%u visible=%d ch=%d (%s)\r\n",
                g_wifi_ssid.c_str(), (unsigned)g_wifi_password.length(), (int)visible, channel,
                source_label);

  int attempts = max_attempts;
  if (!visible && attempts > 8) {
    attempts = 8;
  }

  wl_status_t last_status = WL_IDLE_STATUS;
  wl_status_t last_fail_shown = WL_IDLE_STATUS;
  bool missing_shown = false;

  for (int i = 1; i <= attempts; ++i) {
    delay(1000);
    Serial.print(".");
    const wl_status_t st = WiFi.status();
    last_status = st;
    if (i == 1 || (i % 5) == 0 || st == WL_CONNECT_FAILED || st == WL_NO_SSID_AVAIL) {
      Serial.printf("\r\n[wifi] status=%s(%d) attempt=%d ssid=%s\r\n", wifi_status_str(st), (int)st,
                    i, g_wifi_ssid.c_str());
    }
    if (st == WL_CONNECTED) {
      Serial.println("");
      return true;
    }
    if (st == WL_CONNECT_FAILED) {
      if (last_fail_shown != WL_CONNECT_FAILED) {
        show_wifi_fail(st, visible, "检查密码");
        last_fail_shown = WL_CONNECT_FAILED;
      }
      Serial.println("\r\n[wifi] abort: auth failed");
      break;
    }
    if (st == WL_NO_SSID_AVAIL) {
      if (last_fail_shown != WL_NO_SSID_AVAIL) {
        show_wifi_fail(st, visible, "检查路由器");
        last_fail_shown = WL_NO_SSID_AVAIL;
      }
      Serial.println("\r\n[wifi] abort: ssid not available");
      break;
    }
    if (!visible && !missing_shown) {
      show_wifi_fail(WL_NO_SSID_AVAIL, false, "检查 SSID");
      missing_shown = true;
    }
  }

  Serial.println("");
  show_wifi_fail(last_status, visible, nullptr);
  WiFi.disconnect(true, true);
  delay(200);
  return false;
}

}  // namespace

bool wifi_link_up(void) {
  return WiFi.status() == WL_CONNECTED && WiFi.localIP() != IPAddress(0, 0, 0, 0);
}

void wifi_apply_runtime_keepalive(void) {
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  esp_wifi_set_ps(WIFI_PS_NONE);
}

bool wifi_connect_saved_then_default(const char* tag, int max_attempts) {
  NvsWifiCredential saved[NVS_MAX_SAVED_WIFI];
  const int saved_count = nvs_wifi_list(saved, NVS_MAX_SAVED_WIFI);
  NvsWifiCredential visible[NVS_MAX_SAVED_WIFI];
  uint8_t visible_ch[NVS_MAX_SAVED_WIFI] = {};
  const int visible_count = build_visible_saved_candidates(saved, saved_count, visible, visible_ch,
                                                           NVS_MAX_SAVED_WIFI);
  Serial.printf("[wifi] %s saved=%d visible=%d\r\n", tag, saved_count, visible_count);

  // Prefer strongest visible saved network for maintain fallback (not compile-time default).
  String preferred_ssid;
  String preferred_password;
  if (visible_count > 0) {
    preferred_ssid = visible[0].ssid;
    preferred_password = visible[0].password;
  } else if (saved_count > 0) {
    preferred_ssid = saved[0].ssid;
    preferred_password = saved[0].password;
  }

  for (int i = 0; i < visible_count; ++i) {
    g_wifi_ssid = visible[i].ssid;
    g_wifi_password = visible[i].password;
    Serial.printf("[wifi] %s try saved [%d/%d] ssid=%s\r\n", tag, i + 1, visible_count,
                  g_wifi_ssid.c_str());
    if (try_connect_credential(tag, max_attempts, visible_ch[i])) {
      Serial.printf("[wifi] %s connected IP=%s RSSI=%d dBm\r\n", tag,
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
  }

  if (WIFI_DEFAULT_SSID[0] != '\0') {
    g_wifi_ssid = WIFI_DEFAULT_SSID;
    g_wifi_password = WIFI_DEFAULT_PASSWORD;
    Serial.printf("[wifi] %s try default ssid=%s\r\n", tag, g_wifi_ssid.c_str());
    if (try_connect_credential(tag, max_attempts, 0)) {
      Serial.printf("[wifi] %s connected IP=%s RSSI=%d dBm\r\n", tag,
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
  }

  // Restore preferred saved creds so maintain does not stick on the last failed default.
  if (preferred_ssid.length() > 0) {
    g_wifi_ssid = preferred_ssid;
    g_wifi_password = preferred_password;
  }
  return false;
}

bool wifi_provision_connect_sta() {
  Serial.println("[wifi] STA connect...");
  WiFi.persistent(false);
  wifi_register_event_handlers_once();
  if (!wifi_connect_saved_then_default("boot", kWifiMaxReconnectAttempts)) {
    return false;
  }
  wifi_apply_runtime_keepalive();
  g_wifi_was_up = true;
  boot_guide_wifi_on_connected(WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
  return true;
}
