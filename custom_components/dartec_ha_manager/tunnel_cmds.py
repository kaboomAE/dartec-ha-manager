"""The Cloudflare tunnel on a customer's home: finding it, and taking it down.

**Setting one up is retired** (0.24.0, the owner's decision, 2026-09-27).
Dartec Link (link_cmds.py) replaces it: the tunnel gave a house a public
hostname with a Home Assistant login page on the open internet, and the
tunnel token the manager sent decided whose Cloudflare account the house was
published through, which nothing on the home could check. `tunnel_setup` is
refused by name (service_policy.RETIRED_ACTIONS), whatever consent the home
has given.

What is left only reads or takes away: `tunnel_status` finds a cloudflared
add-on a home may still run from before, and `tunnel_stop` stops it.

Deliberately does NOT support Container/Core installs: those have no
Supervisor, so there is no add-on to install, and quietly doing something
different there would be worse than saying no.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
CLOUDFLARED_SLUG_SUFFIX = "_cloudflared"


def _fail(msg: str) -> dict:
    return {"ok": False, "detail": msg}


async def _supervisor(hass: HomeAssistant, method: str, path: str,
                      json_body: dict | None = None, timeout: int = 180) -> dict:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {"_no_supervisor": True}
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    async with session.request(method, f"{SUPERVISOR_URL}{path}", json=json_body,
                               headers={"Authorization": f"Bearer {token}"},
                               timeout=timeout) as resp:
        try:
            body = await resp.json()
        except Exception:  # noqa: BLE001
            body = {"raw": (await resp.text())[:300]}
        return {"status": resp.status, "body": body}


async def _find_addon(hass: HomeAssistant) -> dict | None:
    listing = await _supervisor(hass, "GET", "/addons")
    if listing.get("_no_supervisor"):
        return None
    addons = ((listing.get("body") or {}).get("data") or {}).get("addons") or []
    return next((a for a in addons
                 if str(a.get("slug", "")).endswith(CLOUDFLARED_SLUG_SUFFIX)), None)


async def tunnel_status(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return {"ok": True, "supported": False,
                "detail": "Container/Core install — no Supervisor, so no add-on tunnel. "
                          "Use a reverse proxy or Nabu Casa on this home."}
    addon = await _find_addon(hass)
    if addon is None:
        return {"ok": True, "supported": True, "installed": False,
                "detail": "cloudflared add-on is not installed"}
    info = await _supervisor(hass, "GET", f"/addons/{addon['slug']}/info")
    options = ((info.get("body") or {}).get("data") or {}).get("options") or {}
    return {"ok": True, "supported": True, "installed": True,
            "slug": addon["slug"], "state": addon.get("state"),
            "version": addon.get("version"),
            "hostname": options.get("external_hostname") or "",
            "detail": f"cloudflared {addon.get('state')}"}


async def tunnel_stop(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    addon = await _find_addon(hass)
    if addon is None:
        return _fail("cloudflared add-on is not installed on this home")
    stopped = await _supervisor(hass, "POST", f"/addons/{addon['slug']}/stop", timeout=120)
    if stopped.get("status") != 200:
        return _fail(f"stop failed: {stopped.get('body')}")
    return {"ok": True, "detail": "cloudflared stopped; the public hostname is now offline"}


HANDLERS = {"tunnel_status": tunnel_status, "tunnel_stop": tunnel_stop}
