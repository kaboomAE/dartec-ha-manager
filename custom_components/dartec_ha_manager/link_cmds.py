"""Join this home to the Dartec mesh, via the Dartec Link add-on.

HA OS / Supervised only. Named `link_*` rather than `mesh_*` because
`mesh` already means the Zigbee mesh throughout this integration.

The replacement for `tunnel_cmds.py`. That gave the house a public hostname
through Cloudflare and left a Home Assistant login page on the open internet,
defended only by the household's own password. This puts the house on a
private WireGuard network instead, where it has no public address at all.

Same shape as the module it replaces -- install an add-on through the
Supervisor API, configure it with what the manager sent, start it -- because
the mechanism was never the problem.

Deliberately does NOT support Container/Core installs, for the same reason as
before: no Supervisor means no add-on, and quietly doing something different
there would be worse than saying no.

One asymmetry worth knowing. With Cloudflare, the manager had to ask the home
whether the tunnel was up, because only the home could see. Here the manager
can ask Headscale directly, and Headscale is authoritative about whether a
node is registered and online. So `mesh_status` here reports only what the
Supervisor knows -- is the add-on installed, is it running -- and the manager
does not rely on it to answer "is this home reachable".
"""
from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"

# Public of necessity: the Supervisor installs an add-on by cloning a git
# repository and has no credentials for a private one.
DARTEC_ADDON_REPO = "https://github.com/kaboomAE/dartec-addons"

# The Supervisor prefixes add-on slugs with a hash of the repository they came
# from, so the full slug is not knowable here -- match on the suffix, exactly
# as tunnel_cmds.py does for cloudflared.
DARTEC_LINK_SLUG_SUFFIX = "_dartec_link"


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
                 if str(a.get("slug", "")).endswith(DARTEC_LINK_SLUG_SUFFIX)), None)


async def link_status(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return {"ok": True, "supported": False,
                "detail": "Container/Core install — no Supervisor, so no add-on. "
                          "This home cannot join the mesh this way."}
    addon = await _find_addon(hass)
    if addon is None:
        return {"ok": True, "supported": True, "installed": False,
                "detail": "Dartec Link is not installed"}
    info = await _supervisor(hass, "GET", f"/addons/{addon['slug']}/info")
    options = ((info.get("body") or {}).get("data") or {}).get("options") or {}
    return {"ok": True, "supported": True, "installed": True,
            "slug": addon["slug"], "state": addon.get("state"),
            "version": addon.get("version"),
            "node_name": options.get("hostname") or "",
            "login_server": options.get("login_server") or "",
            "detail": f"Dartec Link {addon.get('state')}"}


async def link_setup(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Install (if needed), configure with the manager-supplied auth key, start.

    The key is single-use and expires within the hour, so this is not a retry
    loop: if it fails, the manager mints a new one and calls again.
    """
    auth_key = (cmd.get("auth_key") or "").strip()
    login_server = (cmd.get("login_server") or "").strip()
    node_name = (cmd.get("node_name") or "").strip().lower()
    if not auth_key or not login_server:
        return _fail("auth_key and login_server required")
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return _fail("this home has no Supervisor (Container/Core install), "
                     "so the Dartec Link add-on cannot be installed")

    addon = await _find_addon(hass)
    if addon is None:
        added = await _supervisor(hass, "POST", "/store/repositories",
                                  {"repository": DARTEC_ADDON_REPO})
        if added.get("status") not in (200, 400):   # 400 = already added
            return _fail(f"could not add the Dartec add-on repository: {added.get('body')}")
        await _supervisor(hass, "POST", "/store/reload", timeout=120)
        addon = await _find_addon(hass)
        if addon is None:
            return _fail("Dartec Link not found after adding its repository")

    slug = addon["slug"]
    if not addon.get("version"):        # not installed yet
        # No image is published, so the Supervisor BUILDS this on the home's
        # own hardware. On a Raspberry Pi that is minutes, not seconds, which
        # is why the timeout here is generous rather than optimistic.
        install = await _supervisor(hass, "POST", f"/store/addons/{slug}/install", timeout=900)
        if install.get("status") != 200:
            return _fail(f"add-on install failed: {install.get('body')}")

    options: dict[str, Any] = {"login_server": login_server, "auth_key": auth_key}
    if node_name:
        options["hostname"] = node_name
    configured = await _supervisor(hass, "POST", f"/addons/{slug}/options",
                                   {"options": options})
    if configured.get("status") != 200:
        return _fail(f"add-on configuration failed: {configured.get('body')}")

    action = "restart" if addon.get("state") == "started" else "start"
    started = await _supervisor(hass, "POST", f"/addons/{slug}/{action}", timeout=300)
    if started.get("status") != 200:
        return _fail(f"add-on {action} failed: {started.get('body')}")

    return {"ok": True, "node_name": node_name,
            "detail": f"Dartec Link configured for {login_server} and {action}ed"}


async def link_stop(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Stop the add-on here.

    This is NOT what revokes access -- removing the node from Headscale is,
    and the manager does that separately precisely so a home that is offline
    or unreachable can still be cut off.
    """
    addon = await _find_addon(hass)
    if addon is None:
        return _fail("Dartec Link is not installed on this home")
    stopped = await _supervisor(hass, "POST", f"/addons/{addon['slug']}/stop", timeout=120)
    if stopped.get("status") != 200:
        return _fail(f"stop failed: {stopped.get('body')}")
    return {"ok": True, "detail": "Dartec Link stopped; this home has left the mesh"}


HANDLERS = {"link_status": link_status, "link_setup": link_setup,
            "link_stop": link_stop}
