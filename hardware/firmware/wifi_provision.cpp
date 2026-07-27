#include "wifi_provision.h"

#include <WebServer.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include "boot_guide.h"
#include "deskbot_config.h"
#include "utils/nvs_config_utils.h"
#include "utils/utils.h"

namespace {

constexpr int kMaxReconnectAttempts = 40;

char s_ap_ssid[32] = {};

static void build_ap_ssid() {
  strncpy(s_ap_ssid, get_device_id(), sizeof(s_ap_ssid) - 1);
  s_ap_ssid[sizeof(s_ap_ssid) - 1] = '\0';
}

static bool config_ap_running() {
  return WiFi.getMode() == WIFI_AP || WiFi.getMode() == WIFI_AP_STA;
}

static void ensure_config_ap_running() {
  build_ap_ssid();
  if (config_ap_running() && WiFi.softAPIP() != IPAddress(0, 0, 0, 0)) {
    return;
  }
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.mode(WIFI_AP_STA);
  delay(100);
  WiFi.softAP(s_ap_ssid);
  Serial.printf("[wifi] AP started ssid=%s (open) ip=%s\r\n", s_ap_ssid,
                WiFi.softAPIP().toString().c_str());
}

static void display_show_config_portal() {
  char url[32];
  snprintf(url, sizeof(url), "http://%s/", WiFi.softAPIP().toString().c_str());
  boot_guide_provision_show(s_ap_ssid, url, -1);
}

static void display_show_wifi_connected();
bool try_connect_credential(const char* source_label, int max_attempts,
                            bool ssid_already_visible);
int build_visible_saved_candidates(const NvsWifiCredential* saved, int saved_count,
                                   NvsWifiCredential* out, int max_out);
bool wifi_defaults_configured();
static bool wifi_connect_sta_once();

WebServer server(80);
bool done_config = false;
bool s_portal_exit_continue = false;
String ssid;
String password;

WifiLinkHandler s_link_down_handler = nullptr;
WifiLinkHandler s_link_up_handler = nullptr;
bool s_wifi_handlers_registered = false;
bool s_wifi_was_up = false;
bool s_wifi_reconnect_pending = false;
bool s_wifi_quick_reconnect_active = false;
unsigned long s_wifi_last_check_ms = 0;
unsigned long s_wifi_reconnect_backoff_ms = 3000;
unsigned long s_wifi_last_reconnect_ms = 0;
unsigned long s_wifi_quick_reconnect_start_ms = 0;
volatile bool s_wifi_event_disconnected = false;
volatile bool s_wifi_event_got_ip = false;

static constexpr unsigned long kWifiCheckIntervalDownMs = 2000;
static constexpr unsigned long kWifiQuickReconnectWaitMs = 8000;
static constexpr unsigned long kConfigPortalTimeoutMs = 5UL * 60UL * 1000UL;
static constexpr int kWifiMaintainConnectAttempts = 20;

static bool wifi_link_up() {
  return WiFi.status() == WL_CONNECTED && WiFi.localIP() != IPAddress(0, 0, 0, 0);
}

static void apply_wifi_runtime_keepalive() {
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  esp_wifi_set_ps(WIFI_PS_NONE);
}

static void notify_wifi_link_down() {
  s_wifi_was_up = false;
  s_wifi_reconnect_pending = true;
  s_wifi_last_reconnect_ms = 0;
  if (s_link_down_handler != nullptr) {
    s_link_down_handler();
  }
}

static void notify_wifi_link_up() {
  s_wifi_reconnect_pending = false;
  s_wifi_quick_reconnect_active = false;
  s_wifi_reconnect_backoff_ms = 3000;
  apply_wifi_runtime_keepalive();
  display_show_wifi_connected();
  if (!s_wifi_was_up && s_link_up_handler != nullptr) {
    s_link_up_handler();
  }
  s_wifi_was_up = true;
}

static void register_wifi_event_handlers_once() {
  if (s_wifi_handlers_registered) {
    return;
  }
  s_wifi_handlers_registered = true;
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    (void)info;
#if defined(ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      s_wifi_event_disconnected = true;
    } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      s_wifi_event_got_ip = true;
    }
#elif defined(SYSTEM_EVENT_STA_DISCONNECTED)
    if (event == SYSTEM_EVENT_STA_DISCONNECTED) {
      s_wifi_event_disconnected = true;
    } else if (event == SYSTEM_EVENT_STA_GOT_IP) {
      s_wifi_event_got_ip = true;
    }
#endif
  });
}

static bool attempt_runtime_wifi_reconnect() {
  if (ssid.length() == 0) {
    ssid = WiFi.SSID();
  }

  NvsWifiCredential saved[NVS_MAX_SAVED_WIFI];
  const int saved_count = nvs_wifi_list(saved, NVS_MAX_SAVED_WIFI);

  NvsWifiCredential visible[NVS_MAX_SAVED_WIFI];
  const int visible_count =
      build_visible_saved_candidates(saved, saved_count, visible, NVS_MAX_SAVED_WIFI);
  for (int i = 0; i < visible_count; ++i) {
    ssid = visible[i].ssid;
    password = visible[i].password;
    Serial.printf("[wifi] maintain try saved visible [%d/%d] ssid=%s\r\n", i + 1,
                  visible_count, ssid.c_str());
    if (try_connect_credential("maintain", kWifiMaintainConnectAttempts, true)) {
      return true;
    }
  }

  if (wifi_defaults_configured()) {
    ssid = WIFI_DEFAULT_SSID;
    password = WIFI_DEFAULT_PASSWORD;
    Serial.printf("[wifi] maintain try default ssid=%s\r\n", ssid.c_str());
    if (try_connect_credential("maintain-default", kWifiMaintainConnectAttempts, false)) {
      return true;
    }
  }

  return false;
}

static void handle_wifi_events_in_main_context() {
  if (s_wifi_event_disconnected) {
    s_wifi_event_disconnected = false;
    Serial.println("[wifi] event: disconnected");
    notify_wifi_link_down();
  }
  if (s_wifi_event_got_ip) {
    s_wifi_event_got_ip = false;
    if (wifi_link_up()) {
      Serial.printf("[wifi] event: got IP=%s RSSI=%d dBm\r\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      notify_wifi_link_up();
    }
  }
}

static void wifi_try_reconnect_now() {
  if (ssid.length() == 0) {
    ssid = WiFi.SSID();
  }
  if (ssid.length() > 0) {
    Serial.printf("[wifi] maintain quick reconnect ssid=%s\r\n", ssid.c_str());
    WiFi.reconnect();
    s_wifi_quick_reconnect_active = true;
    s_wifi_quick_reconnect_start_ms = millis();
    return;
  }

  Serial.println("[wifi] maintain full reconnect");
  if (attempt_runtime_wifi_reconnect()) {
    Serial.printf("[wifi] maintain reconnected IP=%s RSSI=%d dBm\r\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
    notify_wifi_link_up();
    return;
  }

  if (s_wifi_reconnect_backoff_ms < 60000UL) {
    s_wifi_reconnect_backoff_ms *= 2;
    if (s_wifi_reconnect_backoff_ms > 60000UL) {
      s_wifi_reconnect_backoff_ms = 60000UL;
    }
  }
  Serial.printf("[wifi] maintain reconnect failed, next in %lu ms\r\n",
                (unsigned long)s_wifi_reconnect_backoff_ms);
}

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

static void display_show_wifi_connecting() {
  boot_guide_wifi_connecting(ssid.c_str());
}

/** 根据扫描与 WiFi.status() 在屏幕上展示失败原因。 */
static void display_show_wifi_fail(wl_status_t st, bool ssid_in_scan, const char* next_hint) {
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
  boot_guide_wifi_result(false, ssid.c_str(), detail);
}

static void display_show_wifi_connected() {
  boot_guide_wifi_on_connected(WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
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
        <div class="step"><b>1 连接小歪热点</b><span>手机或电脑加入屏幕上的 Device ID 同名 Wi‑Fi</span></div>
        <div class="step"><b>2 打开屏幕上的网址</b><span>通常是 http://192.168.4.1</span></div>
        <div class="step"><b>3 选择家里的 Wi‑Fi</b><span>保存后看设备屏幕上的连接结果</span></div>
      </div>
    </section>

    <section class="card">
      <div class="status">
        <div class="pill"><span>设备热点</span><b id="ap-ssid">deskbot_000000000000</b></div>
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

    <section class="card" id="device-config-card">
      <h2>设备配置</h2>
      <p class="hint">管理配网热点窗口、设备绑定 PIN、已保存 Wi‑Fi 与恢复出厂。</p>

      <label for="ap-offer-input">配网热点窗口（秒）</label>
      <div class="form-grid" style="grid-template-columns:1fr auto;align-items:end">
        <input type="number" id="ap-offer-input" min="5" max="60" step="1" value="20">
        <button type="button" id="ap-offer-save-btn" onclick="saveApOfferSec()">保存</button>
      </div>
      <p class="hint" id="ap-offer-hint">范围 5–60 秒，默认 20 秒；下次开机生效。</p>

      <label>PIN Code（绑定设备）</label>
      <div class="status" style="margin-bottom:8px">
        <div class="pill"><span>当前 PIN</span><b id="pin-code">----</b></div>
        <div class="pill"><span>设备 ID</span><b id="config-device-id">----</b></div>
      </div>
      <button type="button" onclick="resetPin()">重置 PIN</button>

      <label style="margin-top:16px">已保存 Wi‑Fi</label>
      <div id="saved-wifi-list" class="list"></div>
      <p class="hint hidden" id="saved-wifi-empty">暂无已保存 Wi‑Fi。</p>

      <label style="margin-top:16px">云服务器</label>
      <p class="hint">格式 ws://主机:端口 或 wss://主机:端口，可选路径前缀；设备连接 /asr_chat（语音与相机帧同连接）。</p>
      <div id="ws-server-list" class="list"></div>
      <p class="hint hidden" id="ws-server-empty">暂无自定义云服务器。</p>
      <label for="ws-server-url-input">添加云服务器</label>
      <div class="form-grid" style="grid-template-columns:1fr auto;align-items:end">
        <input type="text" id="ws-server-url-input" placeholder="ws://192.168.1.1:9000">
        <button type="button" id="ws-server-add-btn" onclick="addWsServer()">添加</button>
      </div>

      <div style="margin-top:18px;padding-top:14px;border-top:2px dashed var(--line)">
        <p class="hint">恢复出厂将清除已保存 Wi‑Fi、云服务器、重置 PIN 与启动时间，设备随后重启。</p>
        <button type="button" id="factory-reset-btn" onclick="factoryReset()">恢复出厂设置</button>
      </div>
      <div id="config-message" class="msg hidden"></div>
    </section>

    <section class="card">
      <p class="hint">配网或查看设置完成后，可关闭热点并继续正常启动（将尝试连接已保存 Wi‑Fi）。</p>
      <button type="button" id="continue-boot-btn" class="primary" style="width:100%"
              onclick="continueBoot()">继续启动</button>
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

    function setConfigMessage(text, type) {
      const el = document.getElementById('config-message');
      el.textContent = text;
      el.className = 'msg ' + (type || '');
      el.classList.remove('hidden');
    }

    function loadDeviceConfig() {
      fetch('/device-config')
        .then(r => r.json())
        .then(c => {
          if (!c.ok) return;
          if (c.device_id) {
            document.getElementById('config-device-id').textContent = c.device_id;
          }
          if (c.pin_code) {
            document.getElementById('pin-code').textContent = c.pin_code;
          }
          const apInput = document.getElementById('ap-offer-input');
          if (typeof c.ap_offer_sec !== 'undefined') {
            apInput.value = c.ap_offer_sec;
          }
          if (typeof c.ap_offer_min !== 'undefined') {
            apInput.min = c.ap_offer_min;
          }
          if (typeof c.ap_offer_max !== 'undefined') {
            apInput.max = c.ap_offer_max;
          }
          if (typeof c.ap_offer_min !== 'undefined' && typeof c.ap_offer_max !== 'undefined') {
            document.getElementById('ap-offer-hint').textContent =
              '范围 ' + c.ap_offer_min + '–' + c.ap_offer_max + ' 秒，默认 20 秒；下次开机生效。';
          }

          const listEl = document.getElementById('saved-wifi-list');
          const emptyEl = document.getElementById('saved-wifi-empty');
          listEl.innerHTML = '';
          const saved = c.saved_wifi || [];
          if (saved.length === 0) {
            emptyEl.classList.remove('hidden');
          } else {
            emptyEl.classList.add('hidden');
            saved.forEach(ssid => {
              const row = document.createElement('div');
              row.className = 'network';
              row.style.cursor = 'default';
              row.innerHTML = '<span>' + ssid + '</span>';
              const delBtn = document.createElement('button');
              delBtn.type = 'button';
              delBtn.textContent = '删除';
              delBtn.style.marginLeft = '8px';
              delBtn.onclick = () => deleteSavedWifi(ssid);
              row.appendChild(delBtn);
              listEl.appendChild(row);
            });
          }

          renderWsServers(c);
        })
        .catch(() => {});
    }

    function renderWsServers(c) {
      const listEl = document.getElementById('ws-server-list');
      const emptyEl = document.getElementById('ws-server-empty');
      listEl.innerHTML = '';
      const active = c.ws_active || 'builtin';
      const rows = [{ id: 'builtin', url: c.ws_builtin_url || '', label: '内置（默认）' }];
      (c.ws_servers || []).forEach(s => rows.push({ id: s.id, url: s.url, label: s.id }));

      if ((c.ws_servers || []).length === 0) {
        emptyEl.classList.remove('hidden');
      } else {
        emptyEl.classList.add('hidden');
      }

      rows.forEach(row => {
        const item = document.createElement('div');
        item.className = 'network' + (row.id === active ? ' on' : '');
        item.style.cursor = 'default';
        const text = document.createElement('span');
        text.innerHTML = '<b>' + row.label + '</b><br><small>' + row.url + '</small>';
        item.appendChild(text);

        const actions = document.createElement('span');
        actions.style.display = 'flex';
        actions.style.gap = '8px';

        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.textContent = row.id === active ? '当前' : '使用';
        useBtn.disabled = row.id === active;
        useBtn.onclick = () => selectWsServer(row.id);
        actions.appendChild(useBtn);

        if (row.id !== 'builtin') {
          const delBtn = document.createElement('button');
          delBtn.type = 'button';
          delBtn.textContent = '删除';
          delBtn.onclick = () => deleteWsServer(row.id);
          actions.appendChild(delBtn);
        }

        item.appendChild(actions);
        listEl.appendChild(item);
      });
    }

    function addWsServer() {
      const input = document.getElementById('ws-server-url-input');
      const url = (input.value || '').trim();
      if (!url) {
        setConfigMessage('请输入云服务器地址。', 'err');
        return;
      }
      const btn = document.getElementById('ws-server-add-btn');
      btn.disabled = true;
      fetch('/device-config/ws-servers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'url=' + encodeURIComponent(url)
      })
        .then(r => r.json())
        .then(data => {
          btn.disabled = false;
          if (data.success) {
            input.value = '';
            setConfigMessage('云服务器已添加。', 'ok');
            loadDeviceConfig();
          } else {
            setConfigMessage('添加失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => {
          btn.disabled = false;
          setConfigMessage('添加失败: ' + err.message, 'err');
        });
    }

    function selectWsServer(id) {
      fetch('/device-config/ws-servers/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'id=' + encodeURIComponent(id)
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            setConfigMessage('已切换云服务器，继续启动后生效。', 'ok');
            loadDeviceConfig();
          } else {
            setConfigMessage('切换失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => setConfigMessage('切换失败: ' + err.message, 'err'));
    }

    function deleteWsServer(id) {
      if (!confirm('确定删除该云服务器？')) return;
      fetch('/device-config/ws-servers/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'id=' + encodeURIComponent(id)
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            setConfigMessage('云服务器已删除。', 'ok');
            loadDeviceConfig();
          } else {
            setConfigMessage('删除失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => setConfigMessage('删除失败: ' + err.message, 'err'));
    }

    function saveApOfferSec() {
      const sec = parseInt(document.getElementById('ap-offer-input').value, 10);
      const btn = document.getElementById('ap-offer-save-btn');
      btn.disabled = true;
      fetch('/device-config/ap-offer-sec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'sec=' + encodeURIComponent(sec)
      })
        .then(r => r.json())
        .then(data => {
          btn.disabled = false;
          if (data.success) {
            setConfigMessage('启动时间已保存为 ' + data.ap_offer_sec + ' 秒，下次开机生效。', 'ok');
          } else {
            setConfigMessage('保存失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => {
          btn.disabled = false;
          setConfigMessage('保存失败: ' + err.message, 'err');
        });
    }

    function resetPin() {
      if (!confirm('确定重置 PIN？旧 PIN 将无法再用于绑定。')) return;
      fetch('/device-config/reset-pin', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.success && data.pin_code) {
            document.getElementById('pin-code').textContent = data.pin_code;
            setConfigMessage('PIN 已重置为 ' + data.pin_code, 'ok');
          } else {
            setConfigMessage('重置失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => setConfigMessage('重置失败: ' + err.message, 'err'));
    }

    function deleteSavedWifi(ssid) {
      if (!confirm('确定删除已保存 Wi‑Fi「' + ssid + '」？')) return;
      fetch('/device-config/delete-wifi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'ssid=' + encodeURIComponent(ssid)
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            setConfigMessage('已删除 Wi‑Fi「' + ssid + '」', 'ok');
            loadDeviceConfig();
          } else {
            setConfigMessage('删除失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => setConfigMessage('删除失败: ' + err.message, 'err'));
    }

    function factoryReset() {
      if (!confirm('确定恢复出厂？将清除已保存 Wi‑Fi、重置 PIN 与启动时间，设备随后重启。')) return;
      const btn = document.getElementById('factory-reset-btn');
      btn.disabled = true;
      fetch('/device-config/factory-reset', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            setConfigMessage('已恢复出厂，设备正在重启…', 'ok');
          } else {
            btn.disabled = false;
            setConfigMessage('恢复失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => {
          btn.disabled = false;
          setConfigMessage('恢复失败: ' + err.message, 'err');
        });
    }

    function continueBoot() {
      const btn = document.getElementById('continue-boot-btn');
      btn.disabled = true;
      btn.textContent = '正在继续启动…';
      fetch('/device-config/continue-boot', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (!data.success) {
            btn.disabled = false;
            btn.textContent = '继续启动';
            setConfigMessage('操作失败: ' + (data.message || '未知错误'), 'err');
          }
        })
        .catch(err => {
          btn.disabled = false;
          btn.textContent = '继续启动';
          setConfigMessage('操作失败: ' + err.message, 'err');
        });
    }

    window.onload = function() {
      loadStatus();
      loadDeviceConfig();
      setTimeout(scanNetworks, 700);
    };
  </script>
</body>
</html>
)rawliteral";

bool wifi_defaults_configured() {
  return WIFI_DEFAULT_SSID[0] != '\0';
}

/** 在扫描结果中找出已保存且可见的 WiFi，按 RSSI 从高到低排序。 */
int build_visible_saved_candidates(const NvsWifiCredential* saved, int saved_count,
                                   NvsWifiCredential* out, int max_out) {
  if (saved == nullptr || saved_count <= 0 || out == nullptr || max_out <= 0) {
    return 0;
  }

  struct Match {
    NvsWifiCredential cred;
    int rssi;
  };
  Match matches[NVS_MAX_SAVED_WIFI];
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
  s_portal_exit_continue = false;
  ensure_config_ap_running();

  server.on("/", HTTP_GET, []() {
    server.send(200, "text/html", index_html);
  });

  server.on("/status", HTTP_GET, []() {
    const IPAddress ip = WiFi.softAPIP();
    String json = "{";
    json += "\"ok\":true,";
    json += "\"ap_ssid\":\"" + json_escape(String(s_ap_ssid)) + "\",";
    json += "\"ap_ip\":\"" + ip.toString() + "\",";
    json += "\"device_id\":\"" + json_escape(String(get_device_id())) + "\",";
    json += "\"station_count\":" + String(WiFi.softAPgetStationNum());
    json += "}";
    server.send(200, "application/json", json);
  });

  server.on("/scan-wifi", HTTP_GET, []() {
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
      if (i > 0) json += ",";
      json += "{";
      json += "\"ssid\":\"" + json_escape(uniq_ssid[i]) + "\",";
      json += "\"rssi\":" + String(uniq_rssi[i]);
      json += "}";
    }
    json += "]";

    server.send(200, "application/json", json);
  });

  server.on("/save-wifi", HTTP_POST, []() {
    String new_ssid = server.arg("ssid");
    String new_password = server.arg("password");

    if (new_ssid.length() == 0) {
      server.send(400, "application/json", "{\"success\":false,\"message\":\"SSID cannot be empty\"}");
      return;
    }

    if (!nvs_wifi_upsert(new_ssid.c_str(), new_password.c_str())) {
      server.send(500, "application/json", "{\"success\":false,\"message\":\"Failed to save credentials\"}");
      return;
    }

    Serial.printf("[wifi] credentials saved ssid=%s\r\n", new_ssid.c_str());
    server.send(200, "application/json", "{\"success\":true,\"message\":\"WiFi configuration saved\"}");
    done_config = true;
  });

  server.on("/device-config", HTTP_GET, []() {
    NvsWifiCredential saved[NVS_MAX_SAVED_WIFI];
    const int saved_count = nvs_wifi_list(saved, NVS_MAX_SAVED_WIFI);
    String json = "{";
    json += "\"ok\":true,";
    json += "\"device_id\":\"" + json_escape(String(get_device_id())) + "\",";
    json += "\"pin_code\":\"" + json_escape(String(nvs_get_pin_code())) + "\",";
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
    server.send(200, "application/json", json);
  });

  server.on("/device-config/ap-offer-sec", HTTP_POST, []() {
    const int sec = server.arg("sec").toInt();
    if (!nvs_set_ap_offer_timeout_sec((unsigned)sec)) {
      server.send(400, "application/json",
                  "{\"success\":false,\"message\":\"启动时间须在 5–60 秒之间\"}");
      return;
    }
    String json = "{\"success\":true,\"ap_offer_sec\":" + String(nvs_get_ap_offer_timeout_sec()) + "}";
    server.send(200, "application/json", json);
  });

  server.on("/device-config/reset-pin", HTTP_POST, []() {
    const char* pin = nvs_reset_pin_code();
    String json = "{\"success\":true,\"pin_code\":\"" + json_escape(String(pin)) + "\"}";
    server.send(200, "application/json", json);
  });

  server.on("/device-config/delete-wifi", HTTP_POST, []() {
    const String target = server.arg("ssid");
    if (!nvs_wifi_delete(target.c_str())) {
      server.send(404, "application/json",
                  "{\"success\":false,\"message\":\"未找到该 Wi‑Fi\"}");
      return;
    }
    server.send(200, "application/json", "{\"success\":true}");
  });

  server.on("/device-config/ws-servers", HTTP_POST, []() {
    String url = server.arg("url");
    url.trim();
    if (url.length() == 0) {
      server.send(400, "application/json",
                  "{\"success\":false,\"message\":\"URL 不能为空\"}");
      return;
    }
    DeskbotWsTarget parsed;
    if (!utils_parse_ws_url(url.c_str(), &parsed)) {
      server.send(400, "application/json",
                  "{\"success\":false,\"message\":\"URL 格式须为 ws:// 或 wss://\"}");
      return;
    }
    char new_id[8];
    if (!nvs_ws_add_custom(url.c_str(), new_id, sizeof(new_id))) {
      server.send(400, "application/json",
                  "{\"success\":false,\"message\":\"添加失败或已达上限\"}");
      return;
    }
    String json = "{\"success\":true,\"id\":\"" + json_escape(String(new_id)) + "\"}";
    server.send(200, "application/json", json);
  });

  server.on("/device-config/ws-servers/select", HTTP_POST, []() {
    const String id = server.arg("id");
    if (!nvs_ws_set_active_id(id.c_str())) {
      server.send(400, "application/json",
                  "{\"success\":false,\"message\":\"无效的服务器\"}");
      return;
    }
    server.send(200, "application/json", "{\"success\":true}");
  });

  server.on("/device-config/ws-servers/delete", HTTP_POST, []() {
    const String id = server.arg("id");
    if (!nvs_ws_delete_custom(id.c_str())) {
      server.send(404, "application/json",
                  "{\"success\":false,\"message\":\"未找到该服务器\"}");
      return;
    }
    server.send(200, "application/json", "{\"success\":true}");
  });

  server.on("/device-config/factory-reset", HTTP_POST, []() {
    nvs_wifi_clear();
    nvs_device_factory_reset();
    nvs_ws_factory_reset();
    server.send(200, "application/json", "{\"success\":true,\"message\":\"factory reset\"}");
    delay(500);
    ESP.restart();
  });

  server.on("/device-config/continue-boot", HTTP_POST, []() {
    s_portal_exit_continue = true;
    done_config = true;
    server.send(200, "application/json", "{\"success\":true}");
  });

  server.onNotFound([]() {
    server.sendHeader("Location", "/", true);
    server.send(302, "text/plain", "");
  });

  server.begin();

  IPAddress ip = WiFi.softAPIP();
  Serial.printf("[wifi] config portal http://%s SSID=%s\r\n", ip.toString().c_str(), s_ap_ssid);
  display_show_config_portal();
}

void config_wifi() {
  Serial.println("[wifi] enter config mode");
  setup_http_server();

  const unsigned long portal_start_ms = millis();
  while (!done_config) {
    server.handleClient();
    if (millis() - portal_start_ms >= kConfigPortalTimeoutMs) {
      Serial.println("[wifi] config portal timeout, retry connect");
      break;
    }
    delay(10);
  }

  server.close();
  WiFi.softAPdisconnect(true);
  WiFi.disconnect(true, true);
  delay(200);
  if (s_portal_exit_continue) {
    Serial.println("[wifi] continue boot, closing portal...");
    s_portal_exit_continue = false;
  } else if (done_config) {
    Serial.println("[wifi] config saved, reconnecting...");
  }
}

bool start_wifi_sta(const char* source_label, bool* out_ssid_in_scan, bool ssid_already_visible) {
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.disconnect(true, true);
  delay(300);
  WiFi.mode(WIFI_STA);
  delay(100);
  const bool ssid_in_scan = ssid_already_visible ? true : scan_target_ssid_visible();
  if (out_ssid_in_scan != nullptr) {
    *out_ssid_in_scan = ssid_in_scan;
  }
  display_show_wifi_connecting();
  if (!ssid_in_scan) {
    display_show_wifi_fail(WL_NO_SSID_AVAIL, false, "重试中...");
  }
  WiFi.begin(ssid.c_str(), password.c_str());
  apply_wifi_runtime_keepalive();
  Serial.printf("[wifi] connecting ssid=%s pass_len=%u visible=%d (%s)\r\n", ssid.c_str(),
                (unsigned)password.length(), (int)ssid_in_scan, source_label);
  return true;
}

bool try_connect_credential(const char* source_label, int max_attempts, bool ssid_already_visible) {
  bool ssid_in_scan = false;
  wl_status_t last_status = WL_IDLE_STATUS;
  wl_status_t last_display_fail_status = WL_IDLE_STATUS;
  bool last_display_ssid_missing = false;

  start_wifi_sta(source_label, &ssid_in_scan, ssid_already_visible);

  // 扫描已确认不可见时缩短等待；密码/SSID 错误则提前 abort
  int attempts = max_attempts;
  if (!ssid_in_scan && attempts > 8) {
    attempts = 8;
  }

  for (int connection_attempts = 1; connection_attempts <= attempts; ++connection_attempts) {
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

    if (st == WL_CONNECT_FAILED) {
      if (last_display_fail_status != WL_CONNECT_FAILED) {
        display_show_wifi_fail(st, ssid_in_scan, "检查密码");
        last_display_fail_status = WL_CONNECT_FAILED;
      }
      Serial.println("\r\n[wifi] abort: auth failed");
      break;
    }
    if (st == WL_NO_SSID_AVAIL) {
      if (last_display_fail_status != WL_NO_SSID_AVAIL) {
        display_show_wifi_fail(st, ssid_in_scan, "检查路由器");
        last_display_fail_status = WL_NO_SSID_AVAIL;
      }
      Serial.println("\r\n[wifi] abort: ssid not available");
      break;
    }
    if (!ssid_in_scan && !last_display_ssid_missing) {
      display_show_wifi_fail(WL_NO_SSID_AVAIL, false, "检查 SSID");
      last_display_ssid_missing = true;
    }
  }

  Serial.println("");
  display_show_wifi_fail(last_status, ssid_in_scan, nullptr);
  WiFi.disconnect(true, true);
  delay(200);
  return false;
}

static bool wifi_connect_sta_once() {
  NvsWifiCredential saved[NVS_MAX_SAVED_WIFI];
  const int saved_count = nvs_wifi_list(saved, NVS_MAX_SAVED_WIFI);

  NvsWifiCredential visible[NVS_MAX_SAVED_WIFI];
  const int visible_count =
      build_visible_saved_candidates(saved, saved_count, visible, NVS_MAX_SAVED_WIFI);
  Serial.printf("[wifi] saved=%d visible_in_scan=%d\r\n", saved_count, visible_count);

  for (int i = 0; i < visible_count; ++i) {
    ssid = visible[i].ssid;
    password = visible[i].password;
    Serial.printf("[wifi] try saved visible [%d/%d] ssid=%s\r\n", i + 1, visible_count,
                  ssid.c_str());
    if (try_connect_credential("saved", kMaxReconnectAttempts, true)) {
      Serial.printf("[wifi] connected IP=%s RSSI=%d dBm\r\n", WiFi.localIP().toString().c_str(),
                    WiFi.RSSI());
      apply_wifi_runtime_keepalive();
      s_wifi_was_up = true;
      display_show_wifi_connected();
      return true;
    }
  }

  if (wifi_defaults_configured()) {
    ssid = WIFI_DEFAULT_SSID;
    password = WIFI_DEFAULT_PASSWORD;
    Serial.printf("[wifi] try compile-time default ssid=%s\r\n", ssid.c_str());
    if (try_connect_credential("defaults", kMaxReconnectAttempts, false)) {
      Serial.printf("[wifi] connected IP=%s RSSI=%d dBm\r\n", WiFi.localIP().toString().c_str(),
                    WiFi.RSSI());
      apply_wifi_runtime_keepalive();
      s_wifi_was_up = true;
      display_show_wifi_connected();
      return true;
    }
  }

  return false;
}

}  // namespace

bool wifi_provision_ap_offer(unsigned timeout_ms) {
  build_ap_ssid();

  WiFi.persistent(false);
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.mode(WIFI_AP);
  delay(100);
  if (!WiFi.softAP(s_ap_ssid)) {
    Serial.println("[wifi] AP offer: softAP failed");
    return false;
  }

  char portal_url[32];
  snprintf(portal_url, sizeof(portal_url), "http://%s/", WiFi.softAPIP().toString().c_str());

  Serial.printf("[wifi] AP offer ssid=%s (open) ip=%s timeout=%u ms\r\n", s_ap_ssid, portal_url,
                timeout_ms);

  unsigned long remaining_ms = timeout_ms;
  unsigned long last_tick_ms = millis();
  int last_display_key = -2;
  bool http_started = false;
  done_config = false;
  s_portal_exit_continue = false;

  while (true) {
    const unsigned long now = millis();
    const unsigned long elapsed = now - last_tick_ms;
    last_tick_ms = now;

    const uint8_t stations = WiFi.softAPgetStationNum();
    const bool paused = stations > 0;

    if (!paused && remaining_ms > 0) {
      if (elapsed >= remaining_ms) {
        remaining_ms = 0;
      } else {
        remaining_ms -= elapsed;
      }
    }

    if (paused && !http_started) {
      Serial.printf("[wifi] AP offer: station connected (%u), countdown paused\r\n",
                    (unsigned)stations);
      setup_http_server();
      http_started = true;
    }

    if (http_started) {
      server.handleClient();
      if (done_config) {
        server.close();
        WiFi.softAPdisconnect(true);
        WiFi.disconnect(true, true);
        delay(200);
        if (s_portal_exit_continue) {
          Serial.println("[wifi] AP offer: continue boot from portal");
          s_portal_exit_continue = false;
        } else {
          Serial.println("[wifi] AP offer: config saved from portal");
        }
        return true;
      }
    }

    if (remaining_ms == 0 && !paused) {
      Serial.println("[wifi] AP offer: timeout, no station");
      if (http_started) {
        server.close();
      }
      WiFi.softAPdisconnect(true);
      WiFi.mode(WIFI_OFF);
      delay(100);
      return false;
    }

    int display_key;
    if (paused) {
      display_key = -1;
    } else {
      display_key = (int)((remaining_ms + 999UL) / 1000UL);
    }

    if (display_key != last_display_key) {
      last_display_key = display_key;
      boot_guide_provision_show(s_ap_ssid, portal_url, display_key);
      if (!paused) {
        Serial.printf("[wifi] AP offer: countdown %d s\r\n", display_key);
      }
    }

    delay(100);
  }
}

bool wifi_provision_connect_sta() {
  Serial.println("[wifi] STA connect...");
  WiFi.persistent(false);
  register_wifi_event_handlers_once();
  return wifi_connect_sta_once();
}

void wifi_provision_config_portal() { config_wifi(); }

bool wifi_provision_connect() {
  (void)wifi_provision_ap_offer(nvs_get_ap_offer_timeout_ms());
  if (wifi_provision_connect_sta()) {
    return true;
  }
  wifi_provision_config_portal();
  return wifi_provision_connect_sta();
}

void wifi_provision_set_link_handlers(WifiLinkHandler on_down, WifiLinkHandler on_up) {
  s_link_down_handler = on_down;
  s_link_up_handler = on_up;
}

bool wifi_provision_is_connected() {
  return wifi_link_up();
}

void wifi_provision_maintain() {
  register_wifi_event_handlers_once();
  handle_wifi_events_in_main_context();

  const unsigned long now = millis();
  if (wifi_link_up()) {
    s_wifi_reconnect_pending = false;
    s_wifi_quick_reconnect_active = false;
    if (!s_wifi_was_up) {
      Serial.printf("[wifi] link restored IP=%s RSSI=%d dBm\r\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      notify_wifi_link_up();
    }
    return;
  }

  if (s_wifi_was_up) {
    Serial.println("[wifi] link lost (poll)");
    notify_wifi_link_down();
  }

  if (s_wifi_quick_reconnect_active) {
    if (wifi_link_up()) {
      Serial.printf("[wifi] quick reconnect ok IP=%s RSSI=%d dBm\r\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      notify_wifi_link_up();
      return;
    }
    if (now - s_wifi_quick_reconnect_start_ms < kWifiQuickReconnectWaitMs) {
      return;
    }
    s_wifi_quick_reconnect_active = false;
  }

  if (!s_wifi_reconnect_pending && now - s_wifi_last_check_ms < kWifiCheckIntervalDownMs) {
    return;
  }
  s_wifi_last_check_ms = now;

  if (now - s_wifi_last_reconnect_ms < s_wifi_reconnect_backoff_ms) {
    return;
  }
  s_wifi_last_reconnect_ms = now;
  wifi_try_reconnect_now();
}

void wifi_provision_reset() {
  nvs_wifi_clear();
  Serial.println("[wifi] reset: rebooting...");
  delay(500);
  ESP.restart();
}
