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

String json_escape(const String& raw) {
  String out;
  out.reserve(raw.length() + 8);
  for (size_t i = 0; i < raw.length(); ++i) {
    const char c = raw.charAt(i);
    switch (c) {
      case '\\': out += "\\\\"; break;
      case '"': out += "\\\""; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if ((uint8_t)c < 0x20) {
          char buf[7];
          snprintf(buf, sizeof(buf), "\\u%04x", (unsigned)c);
          out += buf;
        } else {
          out += c;
        }
        break;
    }
  }
  return out;
}

const char index_html[] PROGMEM = R"rawliteral(
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>小歪配网</title>
  <style>
    :root{--bg:#e9e7de;--panel:#fff;--panel2:#f2f0e8;--ink:#16171b;--dim:#5b5b52;--line:#16171b;--accent:#ff6700;--bw:3px;--shadow:5px 5px 0 var(--line);--r:8px;font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
    *{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--ink);background-image:linear-gradient(rgba(0,0,0,.06) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,.06) 1px,transparent 1px);background-size:28px 28px;padding:16px}
    .wrap{max-width:760px;margin:0 auto}.top{display:flex;align-items:center;gap:12px;margin:8px 0 14px}.mark{width:42px;height:42px;background:var(--accent);border:var(--bw) solid var(--line);box-shadow:3px 3px 0 var(--line);color:#fff;font-weight:900;display:grid;place-items:center}.brand b{display:block;font-size:18px;letter-spacing:.08em}.brand span{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);font-size:12px}
    .card{background:var(--panel);border:var(--bw) solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);padding:18px;margin-bottom:16px}.hero{background:#16171b;color:#f8f5ed;position:relative;overflow:hidden}.hero:after{content:"";position:absolute;left:0;right:0;top:0;height:5px;background:var(--accent)}.eyebrow{display:inline-block;background:var(--accent);border:2px solid var(--line);color:#fff;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;font-weight:800;letter-spacing:.08em;padding:4px 8px;box-shadow:2px 2px 0 var(--line)}h1{font-size:30px;margin:14px 0 8px;line-height:1.05}.hero p{color:#d8d4ca;margin:0;line-height:1.5}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:16px}.step{border:2px solid #f8f5ed;padding:10px;min-height:84px}.step b{display:block;color:#fff}.step span{display:block;color:#c8c4ba;font-size:12px;margin-top:5px;line-height:1.35}
    .status{display:grid;grid-template-columns:1fr 1fr;gap:10px}.pill{border:var(--bw) solid var(--line);background:var(--panel2);padding:10px}.pill span{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;color:var(--dim);font-weight:800}.pill b{display:block;margin-top:4px;word-break:break-all}
    .section-title{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.section-title h2{margin:0;font-size:20px}.actions{display:flex;gap:8px;flex-wrap:wrap}button{border:var(--bw) solid var(--line);border-radius:6px;background:var(--panel);box-shadow:3px 3px 0 var(--line);padding:10px 13px;font-weight:800;cursor:pointer;color:var(--ink)}button.primary{background:var(--accent);color:#fff}button:disabled{opacity:.6;cursor:not-allowed}.list{display:grid;gap:8px;margin:10px 0 0}.network{width:100%;display:flex;align-items:center;justify-content:space-between;text-align:left;background:var(--panel2);box-shadow:2px 2px 0 var(--line)}.network.on{background:var(--accent);color:#fff}.network small{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700;opacity:.75}
    label{display:block;font-weight:800;margin:12px 0 6px}.hint{color:var(--dim);font-size:13px;line-height:1.45}input{width:100%;border:var(--bw) solid var(--line);border-radius:6px;padding:12px;background:#fff;font-size:16px;color:var(--ink)}.form-grid{display:grid;gap:10px}.msg{border:var(--bw) solid var(--line);background:var(--panel2);padding:12px;margin-top:12px;font-weight:700}.msg.ok{background:#dff5df}.msg.err{background:#ffe1dc}.footer{text-align:center;color:var(--dim);font-size:12px;margin:18px 0}.hidden{display:none}.spin{display:inline-block;width:14px;height:14px;border:3px solid rgba(0,0,0,.18);border-top-color:var(--line);border-radius:50%;animation:spin .8s linear infinite;margin-right:6px;vertical-align:-2px}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:620px){body{padding:10px}.steps,.status{grid-template-columns:1fr}h1{font-size:26px}.card{padding:15px}}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="top"><div class="mark">歪</div><div class="brand"><b>BRUFIK</b><span>ONBOARDING</span></div></div>
    <section class="card hero">
      <span class="eyebrow">ONBOARDING · WIFI</span>
      <h1>给小歪连上家里的 Wi‑Fi</h1>
      <p>按照屏幕上的地址打开本页，选择路由器并输入密码。保存后设备会关闭热点并自动连接新网络。</p>
      <div class="steps">
        <div class="step"><b>1 连接小歪热点</b><span>手机或电脑加入 Deskbot_Rom</span></div>
        <div class="step"><b>2 打开屏幕上的网址</b><span>通常是 http://192.168.4.1</span></div>
        <div class="step"><b>3 选择家里的 Wi‑Fi</b><span>保存后看设备屏幕上的连接结果</span></div>
      </div>
    </section>

    <section class="card">
      <div class="status">
        <div class="pill"><span>设备热点</span><b id="ap-ssid">Deskbot_Rom</b></div>
        <div class="pill"><span>配网地址</span><b id="ap-ip">http://192.168.4.1</b></div>
        <div class="pill"><span>设备 ID</span><b id="device-id">读取中</b></div>
        <div class="pill"><span>连接设备数</span><b id="station-count">0</b></div>
      </div>
    </section>

    <section class="card">
      <div class="section-title">
        <h2>选择 Wi‑Fi</h2>
        <div class="actions">
          <button type="button" id="scan-btn" onclick="scanNetworks()"><span id="scan-spinner" class="spin hidden"></span><span id="scan-text">扫描网络</span></button>
          <button type="button" onclick="showManual()">隐藏网络</button>
        </div>
      </div>
      <p class="hint">如果没有看到你的路由器，可以重新扫描，或使用“隐藏网络”手动输入 SSID。</p>
      <div id="networks-list" class="list"></div>
      <div id="message" class="msg hidden"></div>
    </section>

    <section class="card" id="password-card">
      <h2>填写网络密码</h2>
      <form id="wifi-form" class="form-grid">
        <input type="hidden" id="ssid-input" name="ssid">
        <label for="manual-ssid-input">Wi‑Fi 名称</label>
        <input type="text" id="manual-ssid-input" placeholder="选择网络后自动填入，也可手动输入">
        <label for="password-input">Wi‑Fi 密码</label>
        <input type="password" id="password-input" name="password" autocomplete="current-password" placeholder="留空表示开放网络">
        <button type="submit" id="save-btn" class="primary">保存并连接</button>
      </form>
    </section>

    <p class="footer">Open‑Deskbot · 小歪配网</p>
  </main>

  <script>
    let selectedNetwork = null;

    function setMessage(text, type) {
      const el = document.getElementById('message');
      el.textContent = text;
      el.className = 'msg ' + (type || '');
      el.classList.remove('hidden');
    }

    function signalText(rssi) {
      if (rssi > -50) return '强';
      if (rssi > -70) return '优';
      if (rssi > -80) return '中';
      return '弱';
    }

    function loadStatus() {
      fetch('/status')
        .then(r => r.json())
        .then(s => {
          if (s.ap_ssid) document.getElementById('ap-ssid').textContent = s.ap_ssid;
          if (s.ap_ip) document.getElementById('ap-ip').textContent = 'http://' + s.ap_ip;
          if (s.device_id) document.getElementById('device-id').textContent = s.device_id;
          if (typeof s.station_count !== 'undefined') document.getElementById('station-count').textContent = s.station_count;
        })
        .catch(() => {});
    }

    function scanNetworks() {
      const scanBtn = document.getElementById('scan-btn');
      const scanSpinner = document.getElementById('scan-spinner');
      const scanText = document.getElementById('scan-text');
      const networksList = document.getElementById('networks-list');

      scanSpinner.classList.remove('hidden');
      scanText.innerText = '扫描中...';
      scanBtn.disabled = true;
      networksList.innerHTML = '';
      document.getElementById('message').classList.add('hidden');

      fetch('/scan-wifi')
        .then(response => response.json())
        .then(data => {
          scanSpinner.classList.add('hidden');
          scanText.innerText = '扫描网络';
          scanBtn.disabled = false;

          if (data.length === 0) {
            setMessage('未找到网络。请靠近路由器后重新扫描，或手动输入隐藏网络。', 'err');
            return;
          }

          data.forEach(network => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'network';
            btn.setAttribute('data-ssid', network.ssid);
            btn.innerHTML = '<span>' + network.ssid + '</span><small>' + signalText(network.rssi) + ' · ' + network.rssi + ' dBm</small>';
            btn.addEventListener('click', () => selectNetwork(network.ssid));
            networksList.appendChild(btn);
          });
        })
        .catch(error => {
          scanSpinner.classList.add('hidden');
          scanText.innerText = '扫描网络';
          scanBtn.disabled = false;

          setMessage('扫描网络错误: ' + error.message, 'err');
        });
    }

    function selectNetwork(ssid) {
      selectedNetwork = ssid;

      const networkItems = document.querySelectorAll('.network');
      networkItems.forEach(item => {
        if (item.getAttribute('data-ssid') === ssid) {
          item.classList.add('on');
        } else {
          item.classList.remove('on');
        }
      });

      document.getElementById('ssid-input').value = ssid;
      document.getElementById('manual-ssid-input').value = ssid;
      document.getElementById('password-input').focus();
    }

    function showManual() {
      selectedNetwork = '';
      document.querySelectorAll('.network').forEach(item => item.classList.remove('on'));
      document.getElementById('ssid-input').value = '';
      document.getElementById('manual-ssid-input').focus();
    }

    document.getElementById('wifi-form').addEventListener('submit', function(e) {
      e.preventDefault();

      const ssid = (document.getElementById('manual-ssid-input').value || document.getElementById('ssid-input').value).trim();
      const password = document.getElementById('password-input').value;
      const saveBtn = document.getElementById('save-btn');

      if (!ssid) {
        setMessage('请选择一个网络，或手动输入 Wi‑Fi 名称。', 'err');
        return;
      }

      saveBtn.disabled = true;
      saveBtn.textContent = '保存中...';
      setMessage('保存配置中，设备马上会尝试连接新网络。', '');

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
          setMessage('WiFi 配置已保存。设备正在连接新 Wi‑Fi，请回到小歪屏幕查看新的 IP 地址。', 'ok');
        } else {
          setMessage('错误: ' + data.message, 'err');
          saveBtn.disabled = false;
          saveBtn.textContent = '保存并连接';
        }
      })
      .catch(error => {
        setMessage('保存配置错误: ' + error.message, 'err');
        saveBtn.disabled = false;
        saveBtn.textContent = '保存并连接';
      });
    });

    window.onload = function() {
      loadStatus();
      setTimeout(scanNetworks, 700);
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

  server.on("/status", HTTP_GET, []() {
    const IPAddress ip = WiFi.softAPIP();
    String json = "{";
    json += "\"ok\":true,";
    json += "\"ap_ssid\":\"" + json_escape(kApSsid) + "\",";
    json += "\"ap_ip\":\"" + ip.toString() + "\",";
    json += "\"device_id\":\"" + json_escape(String(get_device_id())) + "\",";
    json += "\"station_count\":" + String(WiFi.softAPgetStationNum());
    json += "}";
    server.send(200, "application/json", json);
  });

  server.on("/scan-wifi", HTTP_GET, []() {
    String json = "[";
    int n = WiFi.scanNetworks();

    for (int i = 0; i < n; ++i) {
      if (i > 0) json += ",";
      json += "{";
      json += "\"ssid\":\"" + json_escape(WiFi.SSID(i)) + "\",";
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

  server.onNotFound([]() {
    server.sendHeader("Location", "/", true);
    server.send(302, "text/plain", "");
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
