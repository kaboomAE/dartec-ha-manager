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


def _render_js(config: dict[str, Any]) -> str:
    logo_file = {"mark": "dartec-mark", "lockup": "dartec-lockup"}.get(config.get("logo") or "")
    payload = {
        "enabled": bool(config.get("enabled")),
        "title": str(config.get("title") or "").strip(),
        "tabSuffix": bool(config.get("tab_suffix")),
        "logoBase": f"{URL_BASE}/{logo_file}" if logo_file else "",
        "stamp": stamp(config),
    }
    return _JS_TEMPLATE.replace("__DARTEC_CONFIG__", json.dumps(payload))


# The module runs on every page load of the customer's HA frontend.
_JS_TEMPLATE = r"""
// Dartec HA Manager — installer branding.
// Injected via frontend.add_extra_js_url by the dartec_ha_manager integration.
(() => {
  "use strict";
  const CFG = __DARTEC_CONFIG__;
  if (!CFG.enabled || !CFG.title) return;

  const MAX_TRIES = 60;          // ~30 s of retries, then give up quietly
  let tries = 0;

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

  function isDark() {
    try {
      const bg = getComputedStyle(document.documentElement)
        .getPropertyValue("--primary-background-color").trim();
      if (!bg) return window.matchMedia("(prefers-color-scheme: dark)").matches;
      const m = bg.match(/\d+/g);
      if (!m) return window.matchMedia("(prefers-color-scheme: dark)").matches;
      const [r, g, b] = m.map(Number);
      return (0.299 * r + 0.587 * g + 0.114 * b) < 128;   // perceived luminance
    } catch (e) { return false; }
  }

  function apply() {
    const sidebar = findSidebar();
    const node = titleNode(sidebar);
    if (!node) return false;
    if (node.dataset && node.dataset.dartecStamp === CFG.stamp) return true;

    try {
      node.textContent = "";
      if (CFG.logoBase) {
        const img = document.createElement("img");
        img.src = CFG.logoBase + (isDark() ? "-dark.svg" : "-light.svg");
        img.alt = CFG.title;
        img.style.cssText = "height:22px;width:auto;display:block;max-width:100%";
        img.onerror = () => { img.remove(); node.textContent = CFG.title; };
        node.appendChild(img);
      } else {
        node.textContent = CFG.title;
      }
      node.style.display = "flex";
      node.style.alignItems = "center";
      if (node.dataset) node.dataset.dartecStamp = CFG.stamp;
    } catch (e) {
      return false;   // never let branding break the sidebar
    }
    return true;
  }

  function applyTabTitle() {
    if (!CFG.tabSuffix) return;
    const suffix = " — " + CFG.title;
    if (document.title && !document.title.endsWith(suffix)) {
      document.title = document.title.replace(/ — Home Assistant$/, "") + suffix;
    }
  }

  function tick() {
    applyTabTitle();
    const done = apply();
    tries += 1;
    if (!done && tries < MAX_TRIES) setTimeout(tick, 500);
  }

  // HA is a SPA and re-renders the sidebar on navigation and theme changes, so
  // re-assert rather than assuming one pass sticks.
  const reassert = () => { tries = 0; tick(); };
  window.addEventListener("location-changed", reassert);
  window.addEventListener("settheme", reassert);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) reassert(); });
  new MutationObserver(applyTabTitle)
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
    # Version the URL so a settings change is picked up without a hard refresh.
    add_extra_js_url(hass, f"{JS_PATH}?v={stamp(_config(hass))}")
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
