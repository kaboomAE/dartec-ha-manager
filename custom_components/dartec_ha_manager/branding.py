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
DASHBOARD_FIX_PATH = f"{URL_BASE}/dashboard-fix.js"

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "title": "Baytec",           # sidebar title (the Latin name)
    "title_ar": "",              # the name for Arabic viewers; "" = see arabic_title()
    "logo": "baytec-lockup",     # a key of LOGOS
    "tab_suffix": True,          # append the name to the browser tab title
}

# The logo choices and the files each one draws, from www/. Each file exists
# as "<file>-light.svg" and "<file>-dark.svg".
#
# "mark" and "lockup" are the Dartec files and stay exactly as they were:
# homes store them in their options, and a stored value has to keep rendering
# the way it did until someone re-applies branding from the manager.
#
# "baytec-lockup" is the mark and the name together, in the viewer's own
# language (owner, 2026-10-08): the Latin lockup 26 px tall and the Arabic
# «بيتك» lockup 30 px tall, each at its own legibility floor — below 30 px the
# three dots under ب and ي fuse on a 1x screen. Outlined SVGs, so no live
# Lateef text and nothing to clip the dots. "baytec-mark" is the mark alone
# (the same artwork as "mark", from the Baytec files).
LOGOS: dict[str, dict[str, Any] | None] = {
    "mark": {"file": "dartec-mark", "height": 22},
    "lockup": {"file": "dartec-lockup", "height": 22},
    "baytec-mark": {"file": "baytec-mark", "height": 22},
    "baytec-lockup": {"file": "baytec-lockup-en", "height": 26,
                      "file_ar": "baytec-lockup-ar", "height_ar": 30},
    "none": None,
}

# A Latin title whose Arabic name is known, used when no `title_ar` is set, so
# a manager that sends only {"title": "Baytec"} still puts «بيتك» in an Arabic
# viewer's tab. Deliberately not "Dartec": a home branded before the rename
# keeps showing exactly what it showed.
_KNOWN_ARABIC = {"baytec": "بيتك"}


def arabic_title(config: dict[str, Any]) -> str:
    """The name an Arabic viewer sees: `title_ar`, else the known Arabic for
    the title, else "" (the Latin title is used)."""
    explicit = str(config.get("title_ar") or "").strip()
    if explicit:
        return explicit
    return _KNOWN_ARABIC.get(str(config.get("title") or "").strip().casefold(), "")


def _config(hass: HomeAssistant) -> dict[str, Any]:
    return {**DEFAULTS, **(hass.data.get(f"{__name__}.config") or {})}


def _home_language(hass: HomeAssistant) -> str:
    try:
        return str(getattr(hass.config, "language", "") or "")
    except Exception:  # noqa: BLE001 — a fallback only; never fail the script for it
        return ""


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
        body = _render_js(config, _home_language(self._hass))
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

        return web.json_response(_payload(_config(self._hass), _home_language(self._hass)),
                                 headers={"Cache-Control": "no-store"})


def _payload(config: dict[str, Any], lang: str = "") -> dict[str, Any]:
    """What the browser module is given.

    `logoBase` and `logoHeight` are the Latin (or only) logo; `logoBaseAr` and
    `logoHeightAr` are set only for a logo with an Arabic version, and the
    module picks between them by the viewer's language. `lang` is the home's
    own language, the fallback when the page does not say the viewer's.
    """
    logo = LOGOS.get(str(config.get("logo") or "")) or {}
    title = str(config.get("title") or "").strip()
    return {
        "enabled": bool(config.get("enabled")),
        "title": title,
        "titleAr": arabic_title(config),
        "tabSuffix": bool(config.get("tab_suffix")),
        "logoBase": f"{URL_BASE}/{logo['file']}" if logo.get("file") else "",
        "logoHeight": int(logo.get("height") or 22),
        "logoBaseAr": f"{URL_BASE}/{logo['file_ar']}" if logo.get("file_ar") else "",
        "logoHeightAr": int(logo.get("height_ar") or logo.get("height") or 22),
        "lang": str(lang or ""),
        "stamp": stamp({**config, "_lang": str(lang or "")}),
    }


def _render_js(config: dict[str, Any], lang: str = "") -> str:
    return (_JS_TEMPLATE
            .replace("__DARTEC_CONFIG__", json.dumps(_payload(config, lang)))
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

  // The viewer's language, not the home's: the sidebar is drawn per person,
  // and an English-speaking member of an Arabic household should see the
  // Latin name. Home Assistant's own state first (the language this person
  // chose, resolved), then the page's lang attribute, which HA sets from the
  // same choice, then the home's own language.
  function viewerLang() {
    let lang = "";
    try {
      const h = document.querySelector("home-assistant");
      const hass = h && h.hass;
      lang = (hass && ((hass.locale && hass.locale.language) || hass.language)) || "";
    } catch (e) { lang = ""; }
    if (!lang) lang = (document.documentElement && document.documentElement.lang) || CFG.lang || "";
    return String(lang).toLowerCase().split(/[-_]/)[0] === "ar" ? "ar" : "en";
  }

  function nameFor(lang) { return (lang === "ar" && CFG.titleAr) || CFG.title; }

  // Text, when there is no logo or it failed to load. Lateef and other Arabic
  // faces hang dots below the baseline, so the line is given room and never
  // clipped.
  function showText(node, name) {
    node.textContent = name;
    node.style.lineHeight = "1.35";
  }

  function apply() {
    const sidebar = findSidebar();
    const node = titleNode(sidebar);
    if (!node) return false;
    // The marker carries the theme and the language too, so switching
    // light/dark or the viewer's language re-renders with the matching logo
    // instead of short-circuiting as "already done".
    const dark = isDark();
    const lang = viewerLang();
    const marker = CFG.stamp + (dark ? "-d" : "-l") + "-" + lang;
    if (node.dataset && node.dataset.dartecStamp === marker) return true;

    try {
      // Remember what was there so branding can be taken off again cleanly.
      if (node.dataset && node.dataset.dartecOriginal === undefined) {
        node.dataset.dartecOriginal = node.textContent.trim();
      }
      const name = nameFor(lang);
      const arabicLogo = lang === "ar" && !!CFG.logoBaseAr;
      const base = arabicLogo ? CFG.logoBaseAr : CFG.logoBase;
      const height = (arabicLogo ? CFG.logoHeightAr : CFG.logoHeight) || 22;
      node.textContent = "";
      node.style.removeProperty("line-height");
      if (base) {
        const img = document.createElement("img");
        img.src = base + (dark ? "-dark.svg" : "-light.svg");
        img.alt = name;
        // Fixed height, natural width: a lockup is drawn at its own floor and
        // never squeezed (object-fit keeps its shape if the row is narrow).
        img.style.cssText = "height:" + height + "px;width:auto;display:block;"
          + "max-width:100%;object-fit:contain;flex:none";
        img.onerror = () => { img.remove(); showText(node, name); };
        node.appendChild(img);
      } else {
        showText(node, name);
      }
      node.style.display = "flex";
      node.style.alignItems = "center";
      node.style.overflow = "visible";
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
      node.style.removeProperty("overflow");
      node.style.removeProperty("line-height");
      delete node.dataset.dartecStamp;
      delete node.dataset.dartecOriginal;
    } catch (e) { /* leave the sidebar alone rather than half-break it */ }
    return true;
  }

  function applyTabTitle() {
    if (!CFG.tabSuffix || !CFG.title) return;
    const suffix = " — " + nameFor(viewerLang());
    let current = document.title || "";
    // The name changes with the viewer's language: take ours off before
    // putting the new one on, never stacking both.
    if (appliedSuffix && appliedSuffix !== suffix && current.endsWith(appliedSuffix)) {
      current = current.slice(0, -appliedSuffix.length);
    }
    appliedSuffix = suffix;
    if (!current) return;
    // Home Assistant ends its titles " – Home Assistant" (an en dash; older
    // releases an em dash): the installer's name takes its place.
    const next = current.endsWith(suffix)
      ? current : current.replace(/ [–—-] Home Assistant$/, "") + suffix;
    if (next !== document.title) document.title = next;
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
    www_dir = Path(__file__).parent / "www"
    try:
        from homeassistant.components.http import StaticPathConfig

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

    # A separate, static module that makes the Dwains dashboard's notification
    # panel readable on a dark theme — it hardcodes a white row background with
    # no variable to override, so a theme cannot reach it. Injected here rather
    # than given its own registration because it rides the same static path and
    # the same mechanism; it carries no configuration and does nothing on homes
    # that do not run that dashboard. See www/dashboard-fix.js. It also
    # declares the brand's fonts (www/fonts), and keeps a value and its unit
    # in order in right-to-left languages (#55).
    #
    # A content hash as the cache buster: the static path is served with a
    # 31-day cache, so without it a browser could keep the previous agent's
    # copy for a month after an update. An update restarts Home Assistant,
    # which registers the URL again with the new hash.
    fix_stamp = await hass.async_add_executor_job(_file_stamp, www_dir / "dashboard-fix.js")
    add_extra_js_url(hass, f"{DASHBOARD_FIX_PATH}?v={fix_stamp}")
    hass.data[f"{__name__}.registered"] = True


def _file_stamp(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def logo_choices() -> list[str]:
    """The logo values this agent accepts, for the snapshot and refusals, so
    a manager can see whether a home can draw a logo before sending it."""
    return list(LOGOS)


async def branding_set(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Remote command: update branding and persist it to the config entry.

    Keys: `enabled`, `title`, `title_ar`, `logo`, `tab_suffix`. A key left
    out takes its default, as it always has. `title_ar` is the name Arabic
    viewers see; left empty, "Baytec" gets «بيتك» and any other title is shown
    as it is. An unknown `logo` is refused with `code: "invalid_logo"` and the
    accepted `logos`, and nothing is changed: older agents refuse it too, with
    the sentence "logo must be 'mark', 'lockup' or 'none'" and no code.
    """
    from .const import DOMAIN

    settings = {**DEFAULTS}
    for key in ("enabled", "title", "title_ar", "logo", "tab_suffix"):
        if key in cmd:
            settings[key] = cmd[key]
    if settings["logo"] not in LOGOS:
        choices = logo_choices()
        return {"ok": False, "code": "invalid_logo", "logos": choices,
                "detail": "logo must be one of "
                          + ", ".join(f"'{c}'" for c in choices)}
    settings["title"] = str(settings["title"] or "").strip()[:60]
    settings["title_ar"] = str(settings["title_ar"] or "").strip()[:60]
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
                       + (f" / '{arabic_title(settings)}'" if arabic_title(settings) else "")
                       + f" (logo: {settings['logo']})" if settings["enabled"]
                       else "branding disabled"),
            "note": "browser refresh required to see it"}
