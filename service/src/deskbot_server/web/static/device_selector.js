(function () {
  "use strict";

  const API_LIST = "/app/api/devices";
  const API_SELECT = "/app/api/devices/select";
  const API_BIND = "/app/api/devices";

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function statusLabel(d) {
    return d.online ? "在线" : "离线";
  }

  function dispatchDeviceChanged(deviceId) {
    window.__CURRENT_DEVICE_ID__ = deviceId || "";
    window.dispatchEvent(
      new CustomEvent("deskbot:device-changed", {
        detail: { device_id: deviceId || "" },
      })
    );
  }

  async function apiJson(url, opts) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    return data;
  }

  function initDeviceSelector(root) {
    const trigger = $(".app-device-trigger", root);
    const menu = $(".app-device-menu", root);
    const listEl = $(".app-device-list", root);
    const triggerLabel = $(".app-device-trigger-label", root);
    const triggerMeta = $(".app-device-trigger-meta", root);
    const manageBtn = $(".app-device-manage-btn", root);
    const bindBtn = $(".app-device-bind-btn", root);
    const modal = document.getElementById("deviceManageModal");
    const modalBody = modal ? $(".device-manage-body", modal) : null;
    const modalClose = modal ? $(".device-modal-close", modal) : null;
    const bindModal = document.getElementById("deviceBindModal");
    const bindForm = bindModal ? $("#deviceBindForm", bindModal) : null;
    const bindInput = bindModal ? $("#deviceBindInput", bindModal) : null;
    const bindMsg = bindModal ? $("#deviceBindMsg", bindModal) : null;
    const bindClose = bindModal ? $(".device-modal-close", bindModal) : null;

    let devices = [];
    let currentId = window.__CURRENT_DEVICE_ID__ || "";
    let open = false;

    function currentDevice() {
      return devices.find((d) => d.device_id === currentId) || null;
    }

    function updateTrigger() {
      const cur = currentDevice();
      if (!devices.length) {
        triggerLabel.textContent = "我的设备";
        triggerMeta.textContent = "暂无绑定";
        return;
      }
      if (cur) {
        triggerLabel.textContent = cur.display_name || cur.device_id;
        triggerMeta.textContent = `${statusLabel(cur)} · ${cur.last_seen || "—"}`;
      } else {
        triggerLabel.textContent = "我的设备";
        triggerMeta.textContent = "请选择设备";
      }
    }

    function renderList() {
      if (!devices.length) {
        listEl.innerHTML = '<p class="app-device-empty muted">暂无绑定设备，请点击「绑定设备」</p>';
        return;
      }
      listEl.innerHTML = devices
        .map((d) => {
          const active = d.device_id === currentId ? " active" : "";
          const onlineCls = d.online ? "online" : "offline";
          return (
            `<button type="button" class="app-device-item${active}" data-id="${escapeHtml(d.device_id)}">` +
            `<span class="app-device-item-id mono">${escapeHtml(d.device_id)}</span>` +
            `<span class="app-device-item-status ${onlineCls}">${escapeHtml(statusLabel(d))}</span>` +
            `<span class="app-device-item-seen muted">最近 ${escapeHtml(d.last_seen || "—")}</span>` +
            `</button>`
          );
        })
        .join("");
    }

    function renderManageTable() {
      if (!modalBody) return;
      if (!devices.length) {
        modalBody.innerHTML = '<p class="muted">暂无绑定设备</p>';
        return;
      }
      const rows = devices
        .map((d) => {
          const onlineCls = d.online ? "online" : "offline";
          const isCurrent = d.device_id === currentId;
          return (
            "<tr>" +
            `<td class="mono">${escapeHtml(d.device_id)}</td>` +
            `<td><span class="status-pill ${onlineCls}">${escapeHtml(statusLabel(d))}</span>` +
            `<br><span class="muted sm">最近 ${escapeHtml(d.last_seen || "—")}</span></td>` +
            "<td>" +
            (isCurrent
              ? '<span class="agent-badge green sm">当前</span> '
              : `<button type="button" class="agent-btn secondary sm dm-select" data-id="${escapeHtml(d.device_id)}">选为当前</button> `) +
            `<button type="button" class="agent-btn ghost sm dm-unbind" data-id="${escapeHtml(d.device_id)}">解绑</button>` +
            "</td></tr>"
          );
        })
        .join("");
      modalBody.innerHTML =
        '<table class="agent-table device-manage-table">' +
        "<thead><tr><th>设备 ID</th><th>状态</th><th>操作</th></tr></thead>" +
        `<tbody>${rows}</tbody></table>`;
    }

    function setOpen(next) {
      open = next;
      root.classList.toggle("open", open);
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function openModal(el) {
      if (!el) return;
      el.hidden = false;
      document.body.classList.add("device-modal-open");
    }

    function closeModal(el) {
      if (!el) return;
      el.hidden = true;
      if (!document.querySelector(".device-modal:not([hidden])")) {
        document.body.classList.remove("device-modal-open");
      }
    }

    async function refresh() {
      const data = await apiJson(API_LIST, { cache: "no-store" });
      devices = Array.isArray(data.devices) ? data.devices : [];
      currentId = data.current_device_id || currentId || "";
      window.__CURRENT_DEVICE_ID__ = currentId;
      renderList();
      updateTrigger();
      renderManageTable();
    }

    async function selectDevice(deviceId) {
      await apiJson(API_SELECT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId || "" }),
      });
      currentId = deviceId || "";
      devices.forEach((d) => {
        d.is_current = d.device_id === currentId;
      });
      updateTrigger();
      renderList();
      renderManageTable();
      dispatchDeviceChanged(currentId);
      setOpen(false);
    }

    async function bindDevice(deviceId) {
      await apiJson(API_BIND, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId }),
      });
      await refresh();
      if (deviceId) await selectDevice(deviceId);
    }

    async function unbindDevice(deviceId) {
      await apiJson(`/app/api/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE" });
      if (currentId === deviceId) {
        currentId = "";
        dispatchDeviceChanged("");
      }
      await refresh();
    }

    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      setOpen(!open);
    });

    listEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".app-device-item");
      if (!btn) return;
      const id = btn.dataset.id || "";
      if (id && id !== currentId) void selectDevice(id);
      else setOpen(false);
    });

    manageBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      renderManageTable();
      openModal(modal);
    });

    bindBtn.addEventListener("click", (e) => {
      e.preventDefault();
      setOpen(false);
      if (bindInput) bindInput.value = "";
      if (bindMsg) bindMsg.textContent = "";
      openModal(bindModal);
      bindInput?.focus();
    });

    document.addEventListener("click", (e) => {
      if (!root.contains(e.target)) setOpen(false);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        setOpen(false);
        closeModal(modal);
        closeModal(bindModal);
      }
    });

    modal?.addEventListener("click", (e) => {
      if (e.target === modal) closeModal(modal);
      const sel = e.target.closest(".dm-select");
      if (sel) void selectDevice(sel.dataset.id || "").then(() => closeModal(modal));
      const unbind = e.target.closest(".dm-unbind");
      if (unbind) {
        const id = unbind.dataset.id || "";
        if (!id) return;
        if (!confirm(`确认解绑设备 ${id}？`)) return;
        void unbindDevice(id);
      }
    });

    modalClose?.addEventListener("click", () => closeModal(modal));

    bindModal?.addEventListener("click", (e) => {
      if (e.target === bindModal) closeModal(bindModal);
    });
    bindClose?.addEventListener("click", () => closeModal(bindModal));

    bindForm?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const deviceId = (bindInput?.value || "").trim();
      if (!deviceId) {
        if (bindMsg) bindMsg.textContent = "请输入 device_id";
        return;
      }
      if (bindMsg) bindMsg.textContent = "绑定中…";
      try {
        await bindDevice(deviceId);
        closeModal(bindModal);
      } catch (err) {
        if (bindMsg) bindMsg.textContent = err.message || "绑定失败";
      }
    });

    void refresh();
    setInterval(() => {
      void refresh().catch(() => {});
    }, 30000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("appDeviceBar");
    if (root) initDeviceSelector(root);
  });
})();
