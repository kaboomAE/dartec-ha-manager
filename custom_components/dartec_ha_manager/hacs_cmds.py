"""Remote HACS operations.

Lets the manager install or update a HACS repository on a home — the
prerequisite for deploying any community dashboard (Dwains Dashboard Next,
Bubble Card, Mushroom…) across a fleet without visiting each house.

Goes through HACS's own websocket commands rather than its internals, so HACS
stays the source of truth for its repository index and, for dashboard/plugin
repositories, registers the Lovelace resource itself.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .version import is_older, parse
from .ws_bridge import call_own_ws

_LOGGER = logging.getLogger(__name__)

# HACS's API category for Lovelace frontend resources. HACS 2.x presents these
# as "Dashboard" in its UI, but the wire value is still "plugin".
CATEGORIES = {"integration", "plugin", "theme", "template", "python_script",
              "appdaemon", "netdaemon"}


async def _repo_entry(hass: HomeAssistant, repo: str) -> dict | None:
    listing = await call_own_ws(hass, {"type": "hacs/repositories/list"})
    if not listing.get("success"):
        return None
    for item in listing.get("result") or []:
        if (item.get("full_name") or "").lower() == repo.lower():
            return item
    return None


async def hacs_install(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Add (if needed) and download a HACS repository. Idempotent: an already
    installed repo at the requested version is reported as such, not re-fetched."""
    repo = (cmd.get("repo") or "").strip()
    category = (cmd.get("category") or "plugin").strip()
    if "/" not in repo:
        return {"ok": False, "detail": "repo must be 'owner/name'"}
    if category not in CATEGORIES:
        return {"ok": False, "detail": f"unknown HACS category '{category}'"}

    if hass.data.get("hacs") is None:
        return {"ok": False, "detail": "HACS is not installed on this home"}

    entry = await _repo_entry(hass, repo)
    if entry is None:
        added = await call_own_ws(hass, {"type": "hacs/repositories/add",
                                         "repository": repo, "category": category})
        if not added.get("success"):
            error = added.get("error") or {}
            return {"ok": False, "detail": f"could not add {repo}: {error.get('message', error)}"}
        # HACS fetches metadata asynchronously; wait for it to appear.
        for _ in range(10):
            await asyncio.sleep(3)
            entry = await _repo_entry(hass, repo)
            # Wait for the version too: an entry that exists but has no
            # available_version yet is the state in which the downgrade guard
            # below has nothing to compare against.
            if entry and entry.get("available_version"):
                break
        if entry is None:
            return {"ok": False, "detail": f"{repo} added but did not appear in HACS in time"}

    installed = entry.get("installed_version")
    if cmd.get("only_if_missing") and installed:
        return {"ok": True, "detail": f"{repo} already installed ({installed})",
                "installed_version": installed}

    # Ask HACS to re-read the repository from GitHub before deciding there is
    # nothing to do. available_version otherwise comes from HACS's cached
    # index, which lags behind a release by up to its polling interval — so a
    # forced update would report "already up to date" and change nothing.
    refreshed = await call_own_ws(hass, {"type": "hacs/repository/refresh",
                                         "repository": entry.get("id")}, timeout=120)
    if refreshed.get("success"):
        entry = await _repo_entry(hass, repo) or entry
        installed = entry.get("installed_version")
    else:
        _LOGGER.debug("HACS refresh for %s failed; using cached index", repo)

    available = entry.get("available_version")

    # What is really on this home is the NEWER of what HACS recorded and what
    # the caller read off disk. Neither alone is trustworthy: a repository HACS
    # was only just told about reports installed_version None though the files
    # are already there, and once it has downloaded once its record still
    # trails anything copied in by hand afterwards. Establish this BEFORE
    # deciding anything — comparing HACS's record alone reports a home as "up
    # to date" at a version it is not running.
    floor = installed
    claimed = cmd.get("current_version")
    if claimed and (floor is None or is_older(floor, claimed)):
        floor = claimed

    if floor and not cmd.get("allow_downgrade"):
        if parse(available) is None:
            # HACS populates release metadata asynchronously after a repository
            # is added, so "no version yet" is a normal transient state. Acting
            # on it would mean downloading an unknown version over a known one.
            return {"ok": False, "changed": False, "installed_version": floor,
                    "detail": f"HACS has not reported a version for {repo} yet; "
                              f"this home runs {floor}. Try again in a moment."}
        # Never quietly replace a home's code with something older. HACS offers
        # the latest *release*, which can trail a hand-installed build — and
        # for an integration this command also restarts Home Assistant, so an
        # accidental downgrade costs an outage and silently removes fixes.
        if is_older(available, floor):
            return {"ok": False, "changed": False, "installed_version": floor,
                    "detail": f"refusing to downgrade {repo}: this home runs {floor} "
                              f"and the newest release is {available}"}

    # Compare by value so "v0.10.3" and "0.10.3" are one version, not two.
    if floor and available and parse(floor) == parse(available):
        return {"ok": True, "changed": False, "installed_version": floor,
                "detail": f"{repo} already up to date ({floor})"}

    downloaded = await call_own_ws(hass, {"type": "hacs/repository/download",
                                          "repository": entry.get("id")}, timeout=180)
    if not downloaded.get("success"):
        error = downloaded.get("error") or {}
        return {"ok": False, "detail": f"download failed: {error.get('message', error)}"}

    entry = await _repo_entry(hass, repo) or entry
    version = entry.get("installed_version") or available
    return {"ok": True, "changed": True, "installed_version": version,
            "detail": f"installed {repo} {version}"
                      + (" — restart Home Assistant to load it" if category == "integration" else "")}


async def hacs_list(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    if hass.data.get("hacs") is None:
        return {"ok": False, "detail": "HACS is not installed on this home"}
    listing = await call_own_ws(hass, {"type": "hacs/repositories/list"})
    if not listing.get("success"):
        return {"ok": False, "detail": "could not list HACS repositories"}
    repos = [{"name": r.get("full_name"), "category": r.get("category"),
              "installed_version": r.get("installed_version"),
              "available_version": r.get("available_version")}
             for r in (listing.get("result") or []) if r.get("installed")]
    return {"ok": True, "repositories": repos, "detail": f"{len(repos)} installed"}


HANDLERS = {"hacs_install": hacs_install, "hacs_list": hacs_list}
