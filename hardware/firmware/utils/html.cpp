#include "html.h"

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
