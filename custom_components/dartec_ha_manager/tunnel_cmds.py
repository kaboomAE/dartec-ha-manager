"""Cloudflare Tunnel setup on the customer's home (HA OS / Supervised only).

Gives a home a public hostname without opening a router port: the community
`cloudflared` add-on dials out to Cloudflare and traffic arrives through the
tunnel. The manager creates the tunnel and DNS record on Cloudflare's side and
passes the tunnel token here; this module installs, configures and starts the
add-on via the Supervisor API.

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
CLOUDFLARED_REPO = "https://github.com/brenner-tobias/addon-cloudflared"
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


async def tunnel_setup(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Install (if needed), configure with the manager-supplied tunnel token,
    and start the cloudflared add-on."""
    token = (cmd.get("tunnel_token") or "").strip()
    hostname = (cmd.get("hostname") or "").strip().lower()
    if not token or not hostname:
        return _fail("tunnel_token and hostname required")
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return _fail("this home has no Supervisor (Container/Core install), "
                     "so the cloudflared add-on cannot be installed")

    addon = await _find_addon(hass)
    if addon is None:
        added = await _supervisor(hass, "POST", "/store/repositories",
                                  {"repository": CLOUDFLARED_REPO})
        if added.get("status") not in (200, 400):   # 400 = already added
            return _fail(f"could not add the cloudflared add-on repository: {added.get('body')}")
        await _supervisor(hass, "POST", "/store/reload", timeout=120)
        addon = await _find_addon(hass)
        if addon is None:
            return _fail("cloudflared add-on not found after adding its repository")

    slug = addon["slug"]
    if not addon.get("version"):        # not installed yet
        install = await _supervisor(hass, "POST", f"/store/addons/{slug}/install", timeout=600)
        if install.get("status") != 200:
            return _fail(f"add-on install failed: {install.get('body')}")

    # `additional_hosts` stays untouched — a home may already publish other
    # services through this tunnel and clobbering that would break them.
    options = await _supervisor(hass, "POST", f"/addons/{slug}/options",
                                {"options": {"external_hostname": hostname,
                                             "tunnel_token": token}})
    if options.get("status") != 200:
        return _fail(f"add-on configuration failed: {options.get('body')}")

    action = "restart" if addon.get("state") == "started" else "start"
    started = await _supervisor(hass, "POST", f"/addons/{slug}/{action}", timeout=300)
    if started.get("status") != 200:
        return _fail(f"add-on {action} failed: {started.get('body')}")

    return {"ok": True, "hostname": hostname,
            "detail": f"cloudflared configured for {hostname} and {action}ed"}


async def tunnel_stop(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    addon = await _find_addon(hass)
    if addon is None:
        return _fail("cloudflared add-on is not installed on this home")
    stopped = await _supervisor(hass, "POST", f"/addons/{addon['slug']}/stop", timeout=120)
    if stopped.get("status") != 200:
        return _fail(f"stop failed: {stopped.get('body')}")
    return {"ok": True, "detail": "cloudflared stopped; the public hostname is now offline"}


HANDLERS = {"tunnel_status": tunnel_status, "tunnel_setup": tunnel_setup,
            "tunnel_stop": tunnel_stop}
