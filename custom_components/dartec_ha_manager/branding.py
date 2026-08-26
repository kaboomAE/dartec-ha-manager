"""Installer branding for the customer's Home Assistant.

Puts the installer's name (and optionally a logo) in the HA sidebar and the
browser tab, so the customer's system looks like it came from the company
that installed it.

Mechanism: the same one HACS uses for its own frontend assets —
`frontend.add_extra_js_url()` plus a static path — so nothing in the
customer's `configuration.yaml` is touched and removing the integration
removes the branding. Settings live in the config entry's options, so they
survive restarts without the manager being reachable.

The JS itself is deliberately defensive: HA's sidebar lives in shadow DOM
that is not public API, so if the expected node isn't found it does nothing
at all rather than risk breaking the customer's UI.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

URL_BASE = "/dartec_branding"
JS_PATH = f"{URL_BASE}/branding.js"
CONFIG_PATH = f"{URL_BASE}/config.json"

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "title": "Dartec",           # sidebar title
    "logo": "mark",              # "mark" | "lockup" | "none"
    "tab_suffix": True,          # append the name to the browser tab title
}


def _config(hass: HomeAssistant) -> dict[str, Any]:
    return {**DEFAULTS, **(hass.data.get(f"{__name__}.config") or {})}


def stamp(config: dict[str, Any]) -> str:
    """Short hash of the settings — used as the JS URL's cache buster so a
    branding change reaches browsers without a hard refresh."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:10]


class BrandingScriptView(HomeAssistantView):
    """Serves the branding module.

    Unauthenticated on purpose: `extra_module_url` scripts are loaded by the
    browser as plain <script type="module">, with no auth header — the same
    reason HACS's iconset.js is served without auth. The payload is a company
    name and a logo choice, nothing sensitive.
    """

    url = JS_PATH
    name = "dartec:branding"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request):
        from aiohttp import web

        config = _config(self._hass)
        body = _render_js(config)
        return web.Response(text=body, content_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})


class BrandingConfigView(HomeAssistantView):
    """The current settings as JSON, so a page that is already open can notice
    that branding was turned off.

    Without this, removing branding only takes effect the next time the
    customer reloads: the module is fetched once per page load, so a tab left
    open keeps running the copy that was current when it opened, and no
    later change can reach it. Unauthenticated for the same reason as the
    script itself — it carries a company name and a logo choice.
    """

    url = CONFIG_PATH
    name = "dartec:branding:config"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request):
        from aiohttp import web

        return web.json_response(_payload(_config(self._hass)),
                                 headers={"Cache-Control": "no-store"})


def _payload(config: dict[str, Any]) -> dict[str, Any]:
    logo_file = {"mark": "dartec-mark", "lockup": "dartec-lockup"}.get(config.get("logo") or "")
    return {
        "enabled": bool(config.get("enabled")),
        "title": str(config.get("title") or "").strip(),
        "tabSuffix": bool(config.get("tab_suffix")),
        "logoBase": f"{URL_BASE}/{logo_file}" if logo_file else "",
        "stamp": stamp(config),
    }


def _render_js(config: dict[str, Any]) -> str:
    return (_JS_TEMPLATE
            .replace("__DARTEC_CONFIG__", json.dumps(_payload(config)))
            .replace("__DARTEC_CONFIG_URL__", CONFIG_PATH))


# The module runs on every page load of the customer's HA frontend.
_JS_TEMPLATE = r"""
// Dartec HA Manager — installer branding.
// Injected via frontend.add_extra_js_url by the dartec_ha_manager integration.
(() => {
  "use strict";
  // Mutable: the settings can change while this page stays open, and the
  // script re-reads them so that turning branding off actually removes it
  // instead of waiting for the customer to reload.
  let CFG = __DARTEC_CONFIG__;
  const CONFIG_URL = "__DARTEC_CONFIG_URL__";

  const MAX_TRIES = 60;          // ~30 s of retries, then give up quietly
  let tries = 0;
  let appliedSuffix = "";        // the tab-title suffix we are responsible for

  const shadow = (el, sel) => (el && el.shadowRoot ? el.shadowRoot.querySelector(sel) : null);

  function findSidebar() {
    const ha = document.querySelector("home-assistant");
    const main = shadow(ha, "home-assistant-main");
    if (!main || !main.shadowRoot) return null;
    // ha-sidebar sits in ha-drawer's light DOM in current HA; fall back to a
    // direct query for older/newer arrangements.
    return main.shadowRoot.querySelector("ha-sidebar")
        || (shadow(main, "ha-drawer") ? shadow(main, "ha-drawer").querySelector("ha-sidebar") : null)
        || main.querySelector("ha-sidebar");
  }

  function titleNode(sidebar) {
    const root = sidebar && sidebar.shadowRoot;
    if (!root) return null;
    return root.querySelector(".menu .title")
        || root.querySelector(".title")
        || [...root.querySelectorAll("div, span")]
             .find((n) => n.children.length === 0 && n.textContent.trim() === "Home Assistant")
        || null;
  }

  // Resolve any CSS colour (hex, rgb(), hsl(), named) to perceived luminance by
  // letting the browser normalise it — HA themes state colours as hex, and
  // regex-parsing "#17181a" for digits yields nonsense.
  function luminance(value) {
    if (!value) return null;
    const probe = document.createElement("span");
    probe.style.cssText = "display:none;color:" + value;
    document.body.appendChild(probe);
    const resolved = getComputedStyle(probe).color;
    probe.remove();
    const m = resolved && resolved.match(/[\d.]+/g);
    if (!m || m.length < 3) return null;
    return 0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2];
  }

  function isDark() {
    try {
      const cs = getComputedStyle(document.documentElement);
      const lum = luminance(cs.getPropertyValue("--primary-background-color").trim())
               ?? luminance(cs.getPropertyValue("--card-background-color").trim());
      if (lum === null || lum === undefined) {
        return window.matchMedia("(prefers-color-scheme: dark)").matches;
      }
      return lum < 128;
    } catch (e) {
      return window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
  }

  function apply() {
    const sidebar = findSidebar();
    const node = titleNode(sidebar);
    if (!node) return false;
    // The marker carries the theme too, so switching light/dark re-renders
    // with the matching logo instead of short-circuiting as "already done".
    const dark = isDark();
    const marker = CFG.stamp + (dark ? "-d" : "-l");
    if (node.dataset && node.dataset.dartecStamp === marker) return true;

    try {
      // Remember what was there so branding can be taken off again cleanly.
      if (node.dataset && node.dataset.dartecOriginal === undefined) {
        node.dataset.dartecOriginal = node.textContent.trim();
      }
      node.textContent = "";
      if (CFG.logoBase) {
        const img = document.createElement("img");
        img.src = CFG.logoBase + (dark ? "-dark.svg" : "-light.svg");
        img.alt = CFG.title;
        img.style.cssText = "height:22px;width:auto;display:block;max-width:100%";
        img.onerror = () => { img.remove(); node.textContent = CFG.title; };
        node.appendChild(img);
      } else {
        node.textContent = CFG.title;
      }
      node.style.display = "flex";
      node.style.alignItems = "center";
      if (node.dataset) node.dataset.dartecStamp = marker;
    } catch (e) {
      return false;   // never let branding break the sidebar
    }
    return true;
  }

  // Put the sidebar back exactly as it was found. Only touches a node this
  // script branded — anything without our marker is left alone.
  function revert() {
    const node = titleNode(findSidebar());
    if (!node || !node.dataset || node.dataset.dartecStamp === undefined) return true;
    try {
      node.textContent = node.dataset.dartecOriginal || "Home Assistant";
      node.style.removeProperty("display");
      node.style.removeProperty("align-items");
      delete node.dataset.dartecStamp;
      delete node.dataset.dartecOriginal;
    } catch (e) { /* leave the sidebar alone rather than half-break it */ }
    return true;
  }

  function applyTabTitle() {
    if (!CFG.tabSuffix || !CFG.title) return;
    const suffix = " — " + CFG.title;
    appliedSuffix = suffix;
    if (document.title && !document.title.endsWith(suffix)) {
      document.title = document.title.replace(/ — Home Assistant$/, "") + suffix;
    }
  }

  function revertTabTitle() {
    if (!appliedSuffix) return;
    if (document.title && document.title.endsWith(appliedSuffix)) {
      document.title = document.title.slice(0, -appliedSuffix.length) || "Home Assistant";
    }
    appliedSuffix = "";
  }

  function branded() { return CFG.enabled && CFG.title; }

  function tick() {
    if (!branded()) { revertTabTitle(); revert(); return; }
    applyTabTitle();
    const done = apply();
    tries += 1;
    if (!done && tries < MAX_TRIES) setTimeout(tick, 500);
  }

  // Re-read the settings. Called on the same events that re-assert branding,
  // so a change reaches an open tab on the customer's next navigation or when
  // they come back to it — no manual refresh, and no polling timer.
  function refreshConfig() {
    try {
      fetch(CONFIG_URL, { cache: "no-store", credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : null))
        .then((next) => {
          if (!next || next.stamp === CFG.stamp) return;
          const wasBranded = branded();
          CFG = next;
          tries = 0;
          if (wasBranded && !branded()) { revertTabTitle(); revert(); }
          else tick();
        })
        .catch(() => {});
    } catch (e) { /* HA restarting; keep what we have */ }
  }

  // HA is a SPA and re-renders the sidebar on navigation and theme changes, so
  // re-assert rather than assuming one pass sticks.
  const reassert = () => { tries = 0; tick(); refreshConfig(); };
  window.addEventListener("location-changed", reassert);
  window.addEventListener("settheme", reassert);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) reassert(); });
  new MutationObserver(() => { if (branded()) applyTabTitle(); })
    .observe(document.querySelector("title") || document.head, { childList: true, subtree: true });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tick);
  } else {
    tick();
  }
})();
"""


async def async_setup_branding(hass: HomeAssistant, config: dict[str, Any] | None) -> None:
    """Register assets + module once per HA run; safe to call again on update."""
    hass.data[f"{__name__}.config"] = {**DEFAULTS, **(config or {})}

    if hass.data.get(f"{__name__}.registered"):
        return
    try:
        from homeassistant.components.http import StaticPathConfig

        www_dir = Path(__file__).parent / "www"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(URL_BASE, str(www_dir), True)])
    except Exception as err:  # noqa: BLE001 — older cores, or already registered
        _LOGGER.debug("branding static path registration: %s", err)

    hass.http.register_view(BrandingScriptView(hass))
    hass.http.register_view(BrandingConfigView(hass))
    # No version query: extra module URLs are registered once per HA run, so a
    # stamp here would go stale the moment branding changed. Freshness comes
    # from the view's Cache-Control: no-cache instead — the payload is ~4 KB.
    add_extra_js_url(hass, JS_PATH)
    hass.data[f"{__name__}.registered"] = True


async def branding_set(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Remote command: update branding and persist it to the config entry."""
    from .const import DOMAIN

    settings = {**DEFAULTS}
    for key in ("enabled", "title", "logo", "tab_suffix"):
        if key in cmd:
            settings[key] = cmd[key]
    if settings["logo"] not in ("mark", "lockup", "none"):
        return {"ok": False, "detail": "logo must be 'mark', 'lockup' or 'none'"}
    settings["title"] = str(settings["title"] or "").strip()[:60]
    if settings["enabled"] and not settings["title"]:
        return {"ok": False, "detail": "title required when branding is enabled"}

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {"ok": False, "detail": "integration entry not found"}
    hass.config_entries.async_update_entry(
        entries[0], options={**dict(entries[0].options), "branding": settings})

    await async_setup_branding(hass, settings)
    return {"ok": True,
            "detail": (f"branding applied: '{settings['title']}'"
                       f" (logo: {settings['logo']})" if settings["enabled"]
                       else "branding disabled"),
            "note": "browser refresh required to see it"}
