(function () {
  "use strict";

  const MAIN_SEL = "#app-main";
  const NAV_SEL = ".app-nav";
  const SCRIPTS_SLOT = "#app-page-scripts";
  const HEAD_START = "#app-page-head-start";
  const HEAD_END = "#app-page-head-end";
  const FULL_RELOAD_PREFIXES = [
    "/debug/devices",
    "/debug/simulation",
    "/debug/online",
  ];

  let pageHeadAssets = [];
  let loading = false;

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function shouldUsePartialNav(pathname) {
    if (FULL_RELOAD_PREFIXES.some((p) => pathname.startsWith(p))) return false;
    if (pathname.startsWith("/app")) return true;
    if (pathname.startsWith("/debug")) return true;
    return false;
  }

  function shouldPartialNav(anchor) {
    if (anchor.target === "_blank") return false;
    if (anchor.hasAttribute("download")) return false;
    if (anchor.dataset.fullNav !== undefined) return false;
    if (anchor.closest("form")) return false;
    let url;
    try {
      url = new URL(anchor.href, location.origin);
    } catch (_) {
      return false;
    }
    if (url.origin !== location.origin) return false;
    return shouldUsePartialNav(url.pathname);
  }

  function getPageHeadNodes(doc) {
    const start = doc.querySelector(HEAD_START);
    const end = doc.querySelector(HEAD_END);
    if (!start || !end) return [];
    const nodes = [];
    let el = start.nextElementSibling;
    while (el && el !== end) {
      nodes.push(el);
      el = el.nextElementSibling;
    }
    return nodes;
  }

  function clearPageHeadAssets() {
    pageHeadAssets.forEach((el) => el.remove());
    pageHeadAssets = [];
  }

  function headAssetExists(node) {
    const url = node.src || node.href;
    if (!url) return false;
    const sel = node.tagName === "LINK" ? "link[href]" : "script[src]";
    for (const existing of document.head.querySelectorAll(sel)) {
      if ((existing.src || existing.href) === url) return true;
    }
    return false;
  }

  async function injectPageHeadNode(node) {
    const endMarker = document.querySelector(HEAD_END);
    if (!endMarker) return;

    if (node.tagName === "SCRIPT") {
      if (node.src && headAssetExists(node)) return;
      const script = document.createElement("script");
      for (const attr of node.attributes) {
        script.setAttribute(attr.name, attr.value);
      }
      script.textContent = node.textContent;
      endMarker.before(script);
      pageHeadAssets.push(script);
      if (script.src) {
        await new Promise((resolve, reject) => {
          script.onload = () => resolve();
          script.onerror = () => reject(new Error(`failed to load ${script.src}`));
        });
      }
      return;
    }

    if (node.tagName === "LINK" && headAssetExists(node)) return;

    const clone = document.importNode(node, true);
    endMarker.before(clone);
    pageHeadAssets.push(clone);
  }

  async function applyPageHeadAssets(newDoc) {
    clearPageHeadAssets();
    for (const node of getPageHeadNodes(newDoc)) {
      await injectPageHeadNode(node);
    }
  }

  function teardownPageApps() {
    document.querySelectorAll("#app").forEach((el) => {
      const app = el.__vue_app__;
      if (app && typeof app.unmount === "function") {
        app.unmount();
      }
    });
  }

  function activateOneScript(old) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      for (const attr of old.attributes) {
        script.setAttribute(attr.name, attr.value);
      }
      script.textContent = old.textContent;
      if (script.src) {
        script.onload = () => resolve();
        script.onerror = () => reject(new Error(`failed to load ${script.src}`));
      }
      old.replaceWith(script);
      if (!script.src) resolve();
    });
  }

  async function activateScripts(container) {
    for (const old of container.querySelectorAll("script")) {
      await activateOneScript(old);
    }
  }

  async function replacePageScripts(newDoc) {
    const slot = $(SCRIPTS_SLOT);
    const newSlot = newDoc.querySelector(SCRIPTS_SLOT);
    if (!slot) return;
    slot.innerHTML = "";
    if (!newSlot) return;
    for (const old of newSlot.querySelectorAll("script")) {
      const script = document.createElement("script");
      for (const attr of old.attributes) {
        script.setAttribute(attr.name, attr.value);
      }
      script.textContent = old.textContent;
      slot.appendChild(script);
      if (script.src) {
        await new Promise((resolve, reject) => {
          script.onload = () => resolve();
          script.onerror = () => reject(new Error(`failed to load ${script.src}`));
        });
      }
    }
  }

  function updateNavActive(pathname) {
    document.querySelectorAll(`${NAV_SEL} a`).forEach((a) => {
      try {
        const linkPath = new URL(a.href, location.origin).pathname;
        a.classList.toggle("active", linkPath === pathname);
      } catch (_) {
        /* ignore */
      }
    });
  }

  function setLoading(on) {
    const main = $(MAIN_SEL);
    if (main) main.classList.toggle("is-loading", on);
  }

  async function loadPartial(url, pushState) {
    if (loading) return;
    loading = true;
    setLoading(true);
    try {
      const res = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });
      if (res.redirected && new URL(res.url).pathname.startsWith("/login")) {
        window.location.href = res.url;
        return;
      }
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const html = await res.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const newMain = doc.querySelector(MAIN_SEL);
      const main = $(MAIN_SEL);
      if (!newMain || !main) {
        window.location.href = url;
        return;
      }

      await applyPageHeadAssets(doc);
      teardownPageApps();
      main.innerHTML = newMain.innerHTML;
      await activateScripts(main);
      await replacePageScripts(doc);

      const title = doc.querySelector("title");
      if (title) document.title = title.textContent;

      const pathname = new URL(url, location.origin).pathname;
      updateNavActive(pathname);

      if (pushState !== false) {
        history.pushState({ appShell: true }, "", url);
      }
      window.scrollTo(0, 0);
    } finally {
      loading = false;
      setLoading(false);
    }
  }

  window.__appShellNavigate = function (url, pushState) {
    const abs = new URL(url, location.origin).href;
    if (!shouldUsePartialNav(new URL(abs).pathname)) {
      window.location.href = abs;
      return Promise.resolve();
    }
    return loadPartial(abs, pushState).catch(() => {
      window.location.href = abs;
    });
  };

  document.addEventListener("click", (e) => {
    if (e.defaultPrevented) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const anchor = e.target.closest("a[href]");
    if (!anchor || !anchor.closest(".app-shell")) return;
    if (!shouldPartialNav(anchor)) return;
    e.preventDefault();
    window.__appShellNavigate(anchor.href);
  });

  window.addEventListener("popstate", () => {
    if (!shouldUsePartialNav(location.pathname)) {
      window.location.reload();
      return;
    }
    loadPartial(location.href, false).catch(() => {
      window.location.reload();
    });
  });
})();
