"""Join this home to the Dartec mesh, via the Dartec Link add-on.

HA OS / Supervised only. Named `link_*` rather than `mesh_*` because `mesh`
already means the Zigbee mesh throughout this integration.

The tunnel gave each house a public hostname and left a Home Assistant login
page on the open internet, defended only by the household's own password. The
mesh gives it a private address and no public presence at all.

Deliberately does NOT support Container/Core installs: no Supervisor means no
add-on, and quietly doing something different there would be worse than saying
no.

THE TWO SUPERVISOR ENDPOINTS ARE NOT INTERCHANGEABLE, and confusing them cost
a day:

    GET /addons        -> apps that are INSTALLED on this home
    GET /store/addons  -> apps AVAILABLE from the store, each with `installed`

An add-on that has been published but never installed appears only in the
second. Looking for it in the first means never finding it, never reaching the
install call, and reporting "did not appear after adding its repository" about
a repository that cloned perfectly and an add-on sitting visible in the store.
The Supervisor log for that failure contains no install line at all, because
none was ever requested.

One asymmetry worth knowing: with Cloudflare the manager had to ask the home
whether the tunnel was up. Headscale knows whether a node is registered and
online without the house answering, so `link_status` here reports only what
the Supervisor knows, and the manager does not rely on it for "is this home
reachable" -- the question that matters precisely when the house cannot answer.
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


def _data(response: dict) -> dict:
    return (response.get("body") or {}).get("data") or {}


async def _store_apps(hass: HomeAssistant) -> list[dict]:
    """Everything the store offers, installed or not.

    `/store/addons`, NOT `/addons`. See the module docstring -- this is the
    distinction the whole thing turned on.
    """
    listing = await _supervisor(hass, "GET", "/store/addons")
    if listing.get("_no_supervisor"):
        return []
    data = _data(listing)
    # v1 answers with "addons"; newer builds also expose "apps".
    return data.get("addons") or data.get("apps") or []


async def _find_addon(hass: HomeAssistant, *, tries: int = 1,
                      delay: float = 4.0) -> dict | None:
    """The Dartec Link entry from the store, or None.

    `tries` because adding a repository is not synchronous with its add-ons
    appearing: the Supervisor clones and re-reads the store in the background.
    """
    for attempt in range(max(1, tries)):
        for app in await _store_apps(hass):
            if str(app.get("slug", "")).endswith(DARTEC_LINK_SLUG_SUFFIX):
                return app
        if attempt + 1 < tries:
            await asyncio.sleep(delay)
    return None


async def _store_diagnostics(hass: HomeAssistant) -> str:
    """What the Supervisor actually has, for when the add-on is missing.

    A failure that says only "not found" forces the next person to guess. If
    our repository is absent the problem is the add; if it is present but
    offers no Dartec add-on, the problem is the repository's contents.
    """
    try:
        repos = await _supervisor(hass, "GET", "/store/repositories")
        repo_list = _data(repos)
        if isinstance(repo_list, dict):
            repo_list = repo_list.get("repositories") or []
        if not isinstance(repo_list, list):
            repo_list = []
        sources = [str(r.get("source") or r.get("slug") or "?") for r in repo_list
                   if isinstance(r, dict)]
    except Exception:  # noqa: BLE001
        sources = ["<could not list repositories>"]

    slugs = [str(a.get("slug", "")) for a in await _store_apps(hass)]
    ours = "yes" if any("dartec" in x.lower() for x in sources) else "NO"
    return (f"repositories known to the Supervisor: {sources or 'none'}; "
            f"Dartec repo present: {ours}; "
            f"store offers {len(slugs)} add-on(s)")


async def link_status(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return {"ok": True, "supported": False,
                "detail": "Container/Core install — no Supervisor, so no add-on. "
                          "This home cannot join the mesh this way."}
    addon = await _find_addon(hass)
    if addon is None:
        return {"ok": True, "supported": True, "installed": False,
                "detail": "Dartec Link is not available in the store here"}
    if not addon.get("installed"):
        return {"ok": True, "supported": True, "installed": False,
                "slug": addon.get("slug"),
                "detail": "Dartec Link is in the store but not installed"}

    info = await _supervisor(hass, "GET", f"/addons/{addon['slug']}/info")
    info_data = _data(info)
    options = info_data.get("options") or {}
    return {"ok": True, "supported": True, "installed": True,
            "slug": addon["slug"], "state": info_data.get("state"),
            "version": info_data.get("version"),
            "node_name": options.get("hostname") or "",
            "login_server": options.get("login_server") or "",
            "detail": f"Dartec Link {info_data.get('state')}"}


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
        # status code and not on the prose -- "already in the store" is a
        # success for our purposes and contains none of the words a guess
        # would look for.
        already_added = (body.get("error_key") == "store_repository_already_added_error"
                         or "already in the store" in text.lower())
        if status != 200 and not already_added:
            return _fail(f"the Supervisor rejected the add-on repository: "
                         f"{body.get('message') or text}")

        await _supervisor(hass, "POST", "/store/reload", timeout=180)
        addon = await _find_addon(hass, tries=12, delay=5.0)
        if addon is None:
            detail = await _store_diagnostics(hass)
            return _fail(f"Dartec Link did not appear in the store after adding "
                         f"its repository. {detail}")

    slug = addon["slug"]

    # An add-on already installed here keeps whatever version it first got,
    # forever, unless something asks for an update. That means a fix published
    # to the add-on can never reach a home that already has the broken one --
    # which is exactly the position this code was in when the schema bug
    # landed. Not fatal if it fails: an older but working add-on beats
    # refusing to proceed.
    if addon.get("installed") and addon.get("update_available"):
        updated = await _supervisor(hass, "POST", f"/store/addons/{slug}/update",
                                    timeout=900)
        if updated.get("status") != 200:
            _LOGGER.warning("Dartec Link update failed, continuing on the "
                            "installed version: %s", updated.get("body"))

    if not addon.get("installed"):
        # An image is published for every supported architecture, so this is a
        # pull rather than a build. It was a build once, on the home's own
        # hardware, which is why the timeout is still generous.
        install = await _supervisor(hass, "POST", f"/store/addons/{slug}/install",
                                    timeout=900)
        if install.get("status") != 200:
            detail = (install.get("body") or {})
            return _fail(f"add-on install failed: "
                         f"{detail.get('message') if isinstance(detail, dict) else detail}")

    # EVERY key the add-on's schema declares, not just the ones being changed.
    # The Supervisor replaces the whole options object rather than merging, so
    # anything omitted is "missing" -- and a list option is mandatory unless
    # its inner type is marked optional, which this one was not until add-on
    # 0.2.1. Sending the full set works against both versions, which matters
    # because a home already running the older add-on cannot be fixed by
    # changing the newer one.
    options: dict[str, Any] = {
        "login_server": login_server,
        "auth_key": auth_key,
        "hostname": node_name or "",
        # Home Assistant resolves names for the whole house; handing DNS to the
        # mesh breaks local integrations in ways that look nothing like a DNS
        # change. Off unless someone deliberately turns it on.
        "accept_dns": False,
        # No subnet routing: this node is a leaf. Advertising the house LAN
        # would put every device behind it on the mesh, which is a much larger
        # promise than "Dartec can reach Home Assistant".
        "advertise_routes": [],
    }
    configured = await _supervisor(hass, "POST", f"/addons/{slug}/options",
                                   {"options": options})
    if configured.get("status") != 200:
        return _fail(f"add-on configuration failed: {configured.get('body')}")

    info = _data(await _supervisor(hass, "GET", f"/addons/{slug}/info"))
    action = "restart" if info.get("state") == "started" else "start"
    started = await _supervisor(hass, "POST", f"/addons/{slug}/{action}", timeout=300)
    if started.get("status") != 200:
        return _fail(f"add-on {action} failed: {started.get('body')}")

    # A 200 from start means the Supervisor ACCEPTED the request, not that the
    # add-on is running. One that starts and immediately exits returns 200 all
    # the same -- which is how a crash-looping add-on was reported to the
    # manager as a successful join, leaving the home showing "on the mesh" and
    # "never joined" at once.
    #
    # So confirm it is actually up, and if it is not, hand back the add-on's
    # own log. That log said `curl: command not found` in plain words while the
    # manager was reporting success.
    for attempt in range(10):
        await asyncio.sleep(3)
        state = _data(await _supervisor(hass, "GET", f"/addons/{slug}/info")).get("state")
        if state == "started":
            break
    else:
        state = _data(await _supervisor(hass, "GET", f"/addons/{slug}/info")).get("state")

    if state != "started":
        tail = ""
        try:
            logs = await _supervisor(hass, "GET", f"/addons/{slug}/logs", timeout=30)
            raw = logs.get("body")
            text = raw.get("raw") if isinstance(raw, dict) else str(raw or "")
            # The last few lines carry the reason; the rest is s6 boilerplate.
            tail = " | ".join(
                line.strip() for line in str(text).strip().splitlines()[-6:]
                if line.strip())
        except Exception:  # noqa: BLE001
            tail = "(could not read the add-on log)"
        return _fail(f"Dartec Link did not stay running (state: {state}). "
                     f"Add-on log: {tail}")

    return {"ok": True, "node_name": node_name,
            "detail": f"Dartec Link configured for {login_server} and running"}


async def link_stop(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Stop the add-on here.

    This is NOT what revokes access -- removing the node from Headscale is,
    and the manager does that separately precisely so a home that is offline
    or unreachable can still be cut off.
    """
    addon = await _find_addon(hass)
    if addon is None or not addon.get("installed"):
        return _fail("Dartec Link is not installed on this home")
    stopped = await _supervisor(hass, "POST", f"/addons/{addon['slug']}/stop",
                                timeout=120)
    if stopped.get("status") != 200:
        return _fail(f"stop failed: {stopped.get('body')}")
    return {"ok": True, "detail": "Dartec Link stopped; this home has left the mesh"}


HANDLERS = {"link_status": link_status, "link_setup": link_setup,
            "link_stop": link_stop}
