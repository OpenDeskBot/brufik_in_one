#include "wifi_provision.h"

#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include "common.h"
#include "deskbot_config.h"
#include "oled.h"

namespace {

constexpr char kApSsid[] = "Deskbot_Rom";
constexpr char kPrefsNs[] = "deskbot_wifi";
constexpr char kPrefsCountKey[] = "cnt";
constexpr char kLegacySsidKey[] = "ssid";
constexpr char kLegacyPassKey[] = "pass";
constexpr int kMaxSavedWifi = 10;
constexpr int kMaxReconnectAttempts = 40;

struct WifiCredential {
  String ssid;
  String password;
};

WebServer server(80);
bool done_config = false;
String ssid;
String password;

static const char *wifiStatusStr(wl_status_t s) {
  switch (s) {
    case WL_IDLE_STATUS: return "IDLE";
    case WL_NO_SSID_AVAIL: return "NO_SSID";
    case WL_SCAN_COMPLETED: return "SCAN_DONE";
    case WL_CONNECTED: return "CONNECTED";
    case WL_CONNECT_FAILED: return "AUTH_FAILED";
    case WL_CONNECTION_LOST: return "LOST";
    case WL_DISCONNECTED: return "DISCONNECTED";
    default: return "?";
  }
}

static bool scan_target_ssid_visible() {
  int n = WiFi.scanNetworks();
  bool found = false;
  for (int i = 0; i < n; ++i) {
    if (WiFi.SSID(i) == ssid) {
      Serial.printf("[wifi] scan: found %s rssi=%d ch=%u\r\n",
                    ssid.c_str(), WiFi.RSSI(i), (unsigned)WiFi.channel(i));
      found = true;
      break;
    }
  }
  if (!found) {
    Serial.printf("[wifi] scan: %s not visible (seen %d networks)\r\n", ssid.c_str(), n);
  }
  WiFi.scanDelete();
  return found;
}

static void oled_wifi_ssid_line(char* out, size_t out_len) {
  snprintf(out, out_len, "SSID:%.20s", ssid.c_str());
}

static void oled_show_wifi_connecting() {
  char line1[48];
  char line2[40];
  char line3[28];
  char ssid_line[28];
  oled_boot_header_lines(line1, sizeof(line1), line2, sizeof(line2));
  snprintf(line3, sizeof(line3), "WiFi 连接中...");
  oled_wifi_ssid_line(ssid_line, sizeof(ssid_line));
  oled_boot_show4(line1, line2, line3, ssid_line);
}

/** 根据扫描与 WiFi.status() 在屏幕上展示失败原因。 */
static void oled_show_wifi_fail(wl_status_t st, bool ssid_in_scan, const char* next_hint) {
  char line1[48];
  char line2[40];
  char line3[28];
  char line4[40];
  oled_boot_header_lines(line1, sizeof(line1), line2, sizeof(line2));
  const char* detail;
  if (st == WL_CONNECT_FAILED) {
    detail = "密码错误";
  } else if (st == WL_NO_SSID_AVAIL || !ssid_in_scan) {
    detail = "未找到 SSID";
  } else {
    detail = "WiFi 连接失败";
  }
  snprintf(line3, sizeof(line3), "WiFi 失败: %s", detail);
  if (next_hint && next_hint[0] != '\0') {
    snprintf(line4, sizeof(line4), "%s", next_hint);
  } else {
    line4[0] = '\0';
  }
  oled_boot_show4(line1, line2, line3, line4[0] != '\0' ? line4 : nullptr);
}

static void oled_show_wifi_connected() {
  char line1[48];
  char line2[40];
  char line3[40];
  char line4[40];
  oled_boot_header_lines(line1, sizeof(line1), line2, sizeof(line2));
  snprintf(line3, sizeof(line3), "WiFi:%.18s", WiFi.SSID().c_str());
  snprintf(line4, sizeof(line4), "IP:%s", WiFi.localIP().toString().c_str());
  oled_boot_show4(line1, line2, line3, line4);
}

const char index_html[] PROGMEM = R"rawliteral(
<!DOCTYPE HTML>
<html>
<head>
  <title>WiFi 配置</title>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 0;
      padding: 20px;
      background-color: #f5f5f5;
      color: #333;
    }
    .container {
      max-width: 800px;
      margin: 0 auto;
      padding: 20px;
      background-color: white;
      border-radius: 8px;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
    }
    h1 {
      color: #0066cc;
      text-align: center;
      margin-bottom: 30px;
    }
    h2 {
      color: #009688;
      border-bottom: 1px solid #eee;
      padding-bottom: 10px;
    }
    .info-section {
      margin-bottom: 30px;
    }
    .status {
      background-color: #e8f5e9;
      padding: 15px;
      border-radius: 5px;
      margin: 20px 0;
    }
    .footer {
      text-align: center;
      margin-top: 30px;
      font-size: 0.9em;
      color: #666;
    }
    button {
      background-color: #4CAF50;
      border: none;
      color: white;
      padding: 10px 20px;
      text-align: center;
      text-decoration: none;
      display: inline-block;
      font-size: 16px;
      margin: 10px 2px;
      cursor: pointer;
      border-radius: 4px;
    }
    #networks-list {
      list-style-type: none;
      padding: 0;
    }
    .network-item {
      padding: 12px 15px;
      border-bottom: 1px solid #ddd;
      cursor: pointer;
      transition: background-color 0.3s;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .network-item:hover {
      background-color: #f0f0f0;
    }
    .network-item.selected {
      background-color: #e3f2fd;
    }
    .wifi-strength {
      margin-left: 10px;
      font-size: 0.9em;
      color: #666;
    }
    .password-form {
      margin-top: 20px;
      padding: 15px;
      background-color: #f9f9f9;
      border-radius: 5px;
      display: none;
    }
    input[type="text"], input[type="password"] {
      width: 100%;
      padding: 8px;
      margin: 8px 0;
      box-sizing: border-box;
      border: 1px solid #ddd;
      border-radius: 4px;
    }
    .message {
      padding: 10px;
      margin: 10px 0;
      border-radius: 4px;
    }
    .success {
      background-color: #d4edda;
      color: #155724;
    }
    .error {
      background-color: #f8d7da;
      color: #721c24;
    }
    .spinner {
      border: 4px solid rgba(0, 0, 0, 0.1);
      width: 20px;
      height: 20px;
      border-radius: 50%;
      border-top: 4px solid #007bff;
      animation: spin 1s linear infinite;
      display: inline-block;
      margin-right: 10px;
      vertical-align: middle;
    }
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    .hidden {
      display: none;
    }
    .scan-btn {
      background-color: #007bff;
      margin-bottom: 20px;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>WiFi 配置</h1>

    <div class="status">
      <p><strong>状态:</strong> Deskbot Rom 准备配置 Wi-Fi 网络</p>
    </div>

    <div class="info-section">
      <h2>可用 Wi-Fi 网络</h2>
      <p>请选择一个网络连接：</p>

      <button id="scan-btn" class="scan-btn" onclick="scanNetworks()">
        <span id="scan-spinner" class="spinner hidden"></span>
        <span id="scan-text">扫描网络</span>
      </button>

      <div id="message" class="message hidden"></div>

      <ul id="networks-list"></ul>

      <div id="password-form" class="password-form">
        <h3 id="selected-network">网络名称</h3>
        <form id="wifi-form">
          <input type="hidden" id="ssid-input" name="ssid">
          <label for="password-input">密码：</label>
          <input type="password" id="password-input" name="password" placeholder="请输入密码">
          <button type="submit">保存配置</button>
        </form>
      </div>
    </div>

    <div class="footer">
      <p>Open-Deskbot | &copy; 2026</p>
    </div>
  </div>

  <script>
    let selectedNetwork = null;

    function scanNetworks() {
      const scanBtn = document.getElementById('scan-btn');
      const scanSpinner = document.getElementById('scan-spinner');
      const scanText = document.getElementById('scan-text');
      const messageDiv = document.getElementById('message');
      const networksList = document.getElementById('networks-list');

      scanSpinner.classList.remove('hidden');
      scanText.innerText = '扫描中...';
      scanBtn.disabled = true;
      messageDiv.classList.add('hidden');
      networksList.innerHTML = '';
      document.getElementById('password-form').style.display = 'none';

      fetch('/scan-wifi')
        .then(response => response.json())
        .then(data => {
          scanSpinner.classList.add('hidden');
          scanText.innerText = '扫描网络';
          scanBtn.disabled = false;

          if (data.length === 0) {
            messageDiv.innerHTML = '未找到网络';
            messageDiv.className = 'message error';
            messageDiv.classList.remove('hidden');
            return;
          }

          data.forEach(network => {
            const listItem = document.createElement('li');
            listItem.className = 'network-item';
            listItem.setAttribute('data-ssid', network.ssid);

            let strengthText = '';
            if (network.rssi > -50) {
              strengthText = '强';
            } else if (network.rssi > -70) {
              strengthText = '优';
            } else if (network.rssi > -80) {
              strengthText = '中';
            } else {
              strengthText = '弱';
            }

            listItem.innerHTML = `
              <span>${network.ssid}</span>
              <span class="wifi-strength">${strengthText} (${network.rssi} dBm)</span>
            `;

            listItem.addEventListener('click', () => selectNetwork(network.ssid));
            networksList.appendChild(listItem);
          });
        })
        .catch(error => {
          scanSpinner.classList.add('hidden');
          scanText.innerText = '扫描网络';
          scanBtn.disabled = false;

          messageDiv.innerHTML = '扫描网络错误: ' + error.message;
          messageDiv.className = 'message error';
          messageDiv.classList.remove('hidden');
        });
    }

    function selectNetwork(ssid) {
      selectedNetwork = ssid;

      const networkItems = document.querySelectorAll('.network-item');
      networkItems.forEach(item => {
        if (item.getAttribute('data-ssid') === ssid) {
          item.classList.add('selected');
        } else {
          item.classList.remove('selected');
        }
      });

      const passwordForm = document.getElementById('password-form');
      document.getElementById('selected-network').innerText = ssid;
      document.getElementById('ssid-input').value = ssid;
      passwordForm.style.display = 'block';
      document.getElementById('password-input').focus();
    }

    document.getElementById('wifi-form').addEventListener('submit', function(e) {
      e.preventDefault();

      const ssid = document.getElementById('ssid-input').value;
      const password = document.getElementById('password-input').value;
      const messageDiv = document.getElementById('message');
      const networksList = document.getElementById('networks-list');

      if (!ssid) {
        messageDiv.innerHTML = '请选择一个网络';
        messageDiv.className = 'message error';
        messageDiv.classList.remove('hidden');
        return;
      }

      messageDiv.innerHTML = '保存配置中...';
      messageDiv.className = 'message';
      messageDiv.classList.remove('hidden');

      fetch('/save-wifi', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: `ssid=${encodeURIComponent(ssid)}&password=${encodeURIComponent(password)}`
      })
      .then(response => response.json())
      .then(data => {
        if (data.success) {
          messageDiv.innerHTML = 'WiFi 设置成功！';
          messageDiv.className = 'message success';
          networksList.innerHTML = '';
        } else {
          messageDiv.innerHTML = '错误: ' + data.message;
          messageDiv.className = 'message error';
        }
      })
      .catch(error => {
        messageDiv.innerHTML = '保存配置错误: ' + error.message;
        messageDiv.className = 'message error';
      });
    });

    window.onload = function() {
      setTimeout(scanNetworks, 1000);
    };
  </script>
</body>
</html>
)rawliteral";

bool wifi_defaults_configured() {
  return WIFI_DEFAULT_SSID[0] != '\0';
}

static void migrate_legacy_wifi_prefs(Preferences& prefs) {
  if (!prefs.isKey(kLegacySsidKey)) {
    return;
  }
  String old_ssid = prefs.getString(kLegacySsidKey, "");
  String old_pass = prefs.getString(kLegacyPassKey, "");
  old_ssid.trim();
  if (old_ssid.length() > 0 && prefs.getUChar(kPrefsCountKey, 0) == 0) {
    prefs.putString("s0", old_ssid);
    prefs.putString("p0", old_pass);
    prefs.putUChar(kPrefsCountKey, 1);
    Serial.println("[wifi] migrated legacy single credential to list");
  }
  prefs.remove(kLegacySsidKey);
  prefs.remove(kLegacyPassKey);
}

int load_saved_wifi_list(WifiCredential* out, int max_out) {
  if (out == nullptr || max_out <= 0) {
    return 0;
  }
  Preferences prefs;
  if (!prefs.begin(kPrefsNs, false)) {
    return 0;
  }
  migrate_legacy_wifi_prefs(prefs);
  int n = prefs.getUChar(kPrefsCountKey, 0);
  if (n > kMaxSavedWifi) {
    n = kMaxSavedWifi;
  }
  int count = 0;
  for (int i = 0; i < n && count < max_out; ++i) {
    char key_s[4];
    char key_p[4];
    snprintf(key_s, sizeof(key_s), "s%d", i);
    snprintf(key_p, sizeof(key_p), "p%d", i);
    String saved_ssid = prefs.getString(key_s, "");
    saved_ssid.trim();
    if (saved_ssid.length() == 0) {
      continue;
    }
    out[count].ssid = saved_ssid;
    out[count].password = prefs.getString(key_p, "");
    count++;
  }
  prefs.end();
  return count;
}

bool save_wifi_to_prefs(const String& new_ssid, const String& new_password) {
  WifiCredential existing[kMaxSavedWifi];
  const int existing_count = load_saved_wifi_list(existing, kMaxSavedWifi);

  WifiCredential merged[kMaxSavedWifi];
  int merged_count = 0;
  merged[merged_count].ssid = new_ssid;
  merged[merged_count].password = new_password;
  merged_count++;

  for (int i = 0; i < existing_count && merged_count < kMaxSavedWifi; ++i) {
    if (existing[i].ssid == new_ssid) {
      continue;
    }
    merged[merged_count++] = existing[i];
  }

  Preferences prefs;
  if (!prefs.begin(kPrefsNs, false)) {
    return false;
  }
  migrate_legacy_wifi_prefs(prefs);
  prefs.putUChar(kPrefsCountKey, (uint8_t)merged_count);
  for (int i = 0; i < merged_count; ++i) {
    char key_s[4];
    char key_p[4];
    snprintf(key_s, sizeof(key_s), "s%d", i);
    snprintf(key_p, sizeof(key_p), "p%d", i);
    prefs.putString(key_s, merged[i].ssid);
    prefs.putString(key_p, merged[i].password);
  }
  for (int i = merged_count; i < kMaxSavedWifi; ++i) {
    char key_s[4];
    char key_p[4];
    snprintf(key_s, sizeof(key_s), "s%d", i);
    snprintf(key_p, sizeof(key_p), "p%d", i);
    prefs.remove(key_s);
    prefs.remove(key_p);
  }
  prefs.end();
  return true;
}

void clear_wifi_prefs() {
  Preferences prefs;
  if (prefs.begin(kPrefsNs, false)) {
    prefs.clear();
    prefs.end();
    Serial.println("[wifi] cleared saved credentials");
  }
}

/** 在扫描结果中找出已保存且可见的 WiFi，按 RSSI 从高到低排序。 */
int build_visible_saved_candidates(const WifiCredential* saved, int saved_count,
                                   WifiCredential* out, int max_out) {
  if (saved == nullptr || saved_count <= 0 || out == nullptr || max_out <= 0) {
    return 0;
  }

  struct Match {
    WifiCredential cred;
    int rssi;
  };
  Match matches[kMaxSavedWifi];
  int match_count = 0;

  int n = WiFi.scanNetworks();
  for (int s = 0; s < saved_count; ++s) {
    for (int i = 0; i < n; ++i) {
      if (WiFi.SSID(i) == saved[s].ssid) {
        matches[match_count].cred = saved[s];
        matches[match_count].rssi = WiFi.RSSI(i);
        Serial.printf("[wifi] scan: saved %s visible rssi=%d ch=%u\r\n",
                      saved[s].ssid.c_str(), WiFi.RSSI(i), (unsigned)WiFi.channel(i));
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
    out[out_count++] = matches[i].cred;
  }
  if (match_count == 0) {
    Serial.printf("[wifi] scan: no saved SSID visible (saved=%d, seen=%d)\r\n", saved_count, n);
  }
  return out_count;
}

void setup_http_server() {
  done_config = false;

  Serial.printf("[wifi] opening AP %s\r\n", kApSsid);
  WiFi.softAP(kApSsid);

  server.on("/", HTTP_GET, []() {
    server.send(200, "text/html", index_html);
  });

  server.on("/scan-wifi", HTTP_GET, []() {
    String json = "[";
    int n = WiFi.scanNetworks();

    for (int i = 0; i < n; ++i) {
      if (i > 0) json += ",";
      json += "{";
      json += "\"ssid\":\"" + WiFi.SSID(i) + "\",";
      json += "\"rssi\":" + String(WiFi.RSSI(i));
      json += "}";
    }
    json += "]";

    server.send(200, "application/json", json);
    WiFi.scanDelete();
  });

  server.on("/save-wifi", HTTP_POST, []() {
    String new_ssid = server.arg("ssid");
    String new_password = server.arg("password");

    if (new_ssid.length() == 0) {
      server.send(400, "application/json", "{\"success\":false,\"message\":\"SSID cannot be empty\"}");
      return;
    }

    if (!save_wifi_to_prefs(new_ssid, new_password)) {
      server.send(500, "application/json", "{\"success\":false,\"message\":\"Failed to save credentials\"}");
      return;
    }

    Serial.printf("[wifi] credentials saved ssid=%s\r\n", new_ssid.c_str());
    server.send(200, "application/json", "{\"success\":true,\"message\":\"WiFi configuration saved\"}");
    done_config = true;
  });

  server.begin();

  IPAddress ip = WiFi.softAPIP();
  Serial.printf("[wifi] config portal http://%s SSID=%s\r\n", ip.toString().c_str(), kApSsid);

  char line1[48];
  char line2[40];
  char line3[40];
  char line4[40];
  oled_boot_header_lines(line1, sizeof(line1), line2, sizeof(line2));
  snprintf(line3, sizeof(line3), "连接热点 %s", kApSsid);
  snprintf(line4, sizeof(line4), "浏览器打开 http://%s", ip.toString().c_str());
  oled_boot_show4(line1, line2, line3, line4);
}

void config_wifi() {
  Serial.println("[wifi] enter config mode");
  WiFi.disconnect(true, true);
  delay(200);
  setup_http_server();

  while (!done_config) {
    server.handleClient();
    delay(10);
  }

  server.close();
  WiFi.softAPdisconnect(true);
  WiFi.disconnect(true, true);
  delay(200);
  Serial.println("[wifi] config saved, reconnecting...");
}

bool start_wifi_sta(const char* source_label, bool* out_ssid_in_scan) {
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.disconnect(true, true);
  delay(300);
  WiFi.mode(WIFI_STA);
  delay(100);
  const bool ssid_in_scan = scan_target_ssid_visible();
  if (out_ssid_in_scan != nullptr) {
    *out_ssid_in_scan = ssid_in_scan;
  }
  oled_show_wifi_connecting();
  if (!ssid_in_scan) {
    oled_show_wifi_fail(WL_NO_SSID_AVAIL, false, "重试中...");
  }
  WiFi.begin(ssid.c_str(), password.c_str());
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  esp_wifi_set_ps(WIFI_PS_NONE);
  Serial.printf("[wifi] connecting ssid=%s pass_len=%u visible=%d (%s)\r\n", ssid.c_str(),
                (unsigned)password.length(), (int)ssid_in_scan, source_label);
  return true;
}

bool try_connect_credential(const char* source_label, int max_attempts) {
  bool ssid_in_scan = false;
  wl_status_t last_status = WL_IDLE_STATUS;
  wl_status_t last_oled_fail_status = WL_IDLE_STATUS;
  bool last_oled_ssid_missing = false;

  start_wifi_sta(source_label, &ssid_in_scan);

  for (int connection_attempts = 1; connection_attempts <= max_attempts; ++connection_attempts) {
    delay(1000);
    Serial.print(".");

    wl_status_t st = WiFi.status();
    last_status = st;
    if (connection_attempts == 1 || (connection_attempts % 5) == 0 || st == WL_CONNECT_FAILED ||
        st == WL_NO_SSID_AVAIL) {
      Serial.printf("\r\n[wifi] status=%s(%d) attempt=%d ssid=%s\r\n", wifiStatusStr(st), (int)st,
                    connection_attempts, ssid.c_str());
    }

    if (st == WL_CONNECTED) {
      Serial.println("");
      return true;
    }

    if (st == WL_CONNECT_FAILED && last_oled_fail_status != WL_CONNECT_FAILED) {
      oled_show_wifi_fail(st, ssid_in_scan, "检查密码");
      last_oled_fail_status = WL_CONNECT_FAILED;
    } else if (st == WL_NO_SSID_AVAIL && last_oled_fail_status != WL_NO_SSID_AVAIL) {
      oled_show_wifi_fail(st, ssid_in_scan, "检查路由器");
      last_oled_fail_status = WL_NO_SSID_AVAIL;
    } else if (!ssid_in_scan && !last_oled_ssid_missing) {
      oled_show_wifi_fail(WL_NO_SSID_AVAIL, false, "检查 SSID");
      last_oled_ssid_missing = true;
    }
  }

  Serial.println("");
  oled_show_wifi_fail(last_status, ssid_in_scan, nullptr);
  WiFi.disconnect(true, true);
  delay(200);
  return false;
}

}  // namespace

bool wifi_provision_connect() {
  Serial.println("[wifi] connecting...");
  WiFi.persistent(false);

  while (WiFi.status() != WL_CONNECTED) {
    WifiCredential saved[kMaxSavedWifi];
    const int saved_count = load_saved_wifi_list(saved, kMaxSavedWifi);

    WifiCredential visible[kMaxSavedWifi];
    const int visible_count =
        build_visible_saved_candidates(saved, saved_count, visible, kMaxSavedWifi);
    Serial.printf("[wifi] saved=%d visible_in_scan=%d\r\n", saved_count, visible_count);

    for (int i = 0; i < visible_count; ++i) {
      ssid = visible[i].ssid;
      password = visible[i].password;
      Serial.printf("[wifi] try saved visible [%d/%d] ssid=%s\r\n", i + 1, visible_count,
                    ssid.c_str());
      if (try_connect_credential("saved", kMaxReconnectAttempts)) {
        Serial.printf("[wifi] connected IP=%s RSSI=%d dBm\r\n", WiFi.localIP().toString().c_str(),
                      WiFi.RSSI());
        oled_show_wifi_connected();
        return true;
      }
    }

    if (wifi_defaults_configured()) {
      ssid = WIFI_DEFAULT_SSID;
      password = WIFI_DEFAULT_PASSWORD;
      Serial.printf("[wifi] try compile-time default ssid=%s\r\n", ssid.c_str());
      if (try_connect_credential("defaults", kMaxReconnectAttempts)) {
        Serial.printf("[wifi] connected IP=%s RSSI=%d dBm\r\n", WiFi.localIP().toString().c_str(),
                      WiFi.RSSI());
        oled_show_wifi_connected();
        return true;
      }
    } else if (saved_count == 0) {
      config_wifi();
      continue;
    }

    Serial.println("\r\n[wifi] all credentials failed, enter config mode");
    WiFi.disconnect(true, true);
    delay(200);
    config_wifi();
  }

  Serial.printf("[wifi] connected IP=%s RSSI=%d dBm\r\n", WiFi.localIP().toString().c_str(),
                WiFi.RSSI());
  oled_show_wifi_connected();
  return true;
}

void wifi_provision_reset() {
  clear_wifi_prefs();
  Serial.println("[wifi] reset: rebooting...");
  delay(500);
  ESP.restart();
}
