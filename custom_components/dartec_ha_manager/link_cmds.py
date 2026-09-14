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

import asyncio
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


async def _list_addons(hass: HomeAssistant) -> list[dict]:
    listing = await _supervisor(hass, "GET", "/addons")
    if listing.get("_no_supervisor"):
        return []
    return ((listing.get("body") or {}).get("data") or {}).get("addons") or []


async def _find_addon(hass: HomeAssistant, *, tries: int = 1,
                      delay: float = 4.0) -> dict | None:
    """The Dartec Link add-on, or None.

    `tries` exists because adding a repository is not synchronous with the
    add-on appearing in it: the Supervisor clones and re-reads the store in
    the background, and looking immediately afterwards finds nothing. The
    first version of this asked once and reported "not found after adding its
    repository", which is indistinguishable from a genuinely broken
    repository and sent us hunting a config bug that was not there.
    """
    for attempt in range(max(1, tries)):
        for addon in await _list_addons(hass):
            if str(addon.get("slug", "")).endswith(DARTEC_LINK_SLUG_SUFFIX):
                return addon
        if attempt + 1 < tries:
            await asyncio.sleep(delay)
    return None


async def _store_diagnostics(hass: HomeAssistant) -> str:
    """What the Supervisor actually has, for when the add-on is missing.

    A failure that says only "not found" forces the next person to guess.
    This turns it into evidence: which repositories are registered, and which
    add-on slugs exist. If our repository is absent the problem is the add;
    if it is present but has no add-ons the problem is the repository's
    contents.
    """
    try:
        repos = await _supervisor(hass, "GET", "/store/repositories")
        repo_list = ((repos.get("body") or {}).get("data") or [])
        if isinstance(repo_list, dict):
            repo_list = repo_list.get("repositories") or []
        sources = [str(r.get("source") or r.get("slug") or "?") for r in repo_list
                   if isinstance(r, dict)]
    except Exception:  # noqa: BLE001
        sources = ["<could not list repositories>"]

    slugs = [str(a.get("slug", "")) for a in await _list_addons(hass)]
    ours = "yes" if any("dartec" in x.lower() for x in sources) else "NO"
    return (f"repositories known to the Supervisor: {sources or 'none'}; "
            f"Dartec repo present: {ours}; "
            f"add-on slugs available: {slugs or 'none'}")


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
        status = added.get("status")
        raw = added.get("body")
        body = raw if isinstance(raw, dict) else {}
        text = str(raw or "")

        # The Supervisor names its failures. Match on error_key, not on the
        # status code and not on the prose.
        #
        # Both of my previous attempts at this were wrong in the same way.
        # First every 400 was assumed to mean "already added", which swallowed
        # genuine rejections. Then the prose was searched for "exist" -- and
        # the actual message is "already in the store", which contains no such
        # word, so a repository that was present and fine got reported as a
        # hard failure. The status was not 400 either. error_key is the one
        # part of that response designed to be matched on.
        already_added = (body.get("error_key") == "store_repository_already_added_error"
                         or "already in the store" in text.lower())

        if status != 200 and not already_added:
            return _fail(f"the Supervisor rejected the add-on repository: "
                         f"{body.get('message') or text}")

        await _supervisor(hass, "POST", "/store/reload", timeout=180)
        # The clone and re-read finish in the background, so poll rather than
        # asking once. Roughly a minute in total, which is generous for a
        # small repository on a slow line and still bounded.
        addon = await _find_addon(hass, tries=12, delay=5.0)
        if addon is None:
            detail = await _store_diagnostics(hass)
            return _fail(f"Dartec Link did not appear after adding its "
                         f"repository. {detail}")

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
