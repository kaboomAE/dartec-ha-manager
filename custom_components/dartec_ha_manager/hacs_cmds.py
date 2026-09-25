"""Remote HACS operations.

Lets the manager install or update a HACS repository on a home — the
prerequisite for deploying any community dashboard (Dwains Dashboard Next,
Bubble Card, Mushroom…) across a fleet without visiting each house.

Goes through HACS's own websocket commands rather than its internals, so HACS
stays the source of truth for its repository index and, for dashboard/plugin
repositories, registers the Lovelace resource itself.

**Every install names its version** (dartec-ha-manager#54, owner decision
2026-09-26). A repository's latest release is whatever its maintainer
published last, and for a dashboard or a card that is JavaScript running in
the family's browser: taking "latest" at each rollout sends code nobody at
Dartec has tested to every home that rollout reaches. So `hacs_install`
downloads only an exact `version`, the one the manager's catalogue records as
approved and passes to HACS's own `hacs/repository/download`, and refuses
(`code: "version_required"`, nothing added, nothing downloaded) a command
that would download without one. Asking for something already installed with
`only_if_missing` still needs no version, because nothing is downloaded.

**The one exception** is this integration's own repository, updated by
`agent_update` (home_cmds.py), which takes its latest release. That is the
owner's decision of 2026-09-18 on guarded agent updates: Dartec cuts those
releases itself, and the manager stages them bench, pilot, then the rest.
It is reachable only from `agent_update`, as a keyword the remote command
cannot set; a remote `hacs_install` naming this repository needs a version
like any other. `LATEST_ALLOWED` below is the whole list, each entry with who
decided it and when.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from .hacs_token import hacs_token_set
from .version import is_older, parse
from .ws_bridge import call_own_ws

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Repositories that may be installed at their latest release, and why. Keyed
# by lower-case `owner/name`. Adding one is an owner decision: record it here.
LATEST_ALLOWED: dict[str, str] = {
    "kaboomae/dartec-ha-manager":
        "agent_update: guarded agent updates take the latest release of this "
        "integration (owner decision 2026-09-18); Dartec cuts those releases",
}

# A release tag or commit, as HACS names it: "v1.8.0", "5.0.15", "2.1.26b1".
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
# A commit, for the rare repository with no releases. A branch name ("main")
# is refused: HACS accepts one, but a branch moves, which is what pinning is
# meant to stop.
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
# Seconds between looks for a repository HACS was only just told about.
ADD_POLL_S = 3

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


def check_version(cmd: dict[str, Any], *, latest_ok: bool = False) -> dict | None:
    """The refusal for a command's `version`, or None when it may go ahead.

    `version` must be a release tag as HACS names it ("v1.8.0", "5.0.15"),
    or a commit hash; never a branch, which moves. Without one,
    only two things may proceed: `only_if_missing` (which downloads nothing
    if the repository is already there; `_hacs_install` refuses it later if
    it is not), and a repository in `LATEST_ALLOWED` when the caller is the
    code path that entry names (`latest_ok`, never set from a command).
    """
    version = cmd.get("version")
    if version is not None:
        text = version.strip() if isinstance(version, str) else ""
        if not VERSION_RE.match(text) or (parse(text) is None and not COMMIT_RE.match(text)):
            return {"ok": False, "changed": False, "code": "invalid",
                    "detail": "version must be a release tag such as 'v1.2.3', or a "
                              "commit; not a branch"}
        return None
    repo = str(cmd.get("repo") or "").strip().lower()
    if latest_ok and repo in LATEST_ALLOWED:
        return None
    if cmd.get("only_if_missing"):
        return None
    return _version_required(repo)


def _version_required(repo: str) -> dict:
    return {"ok": False, "changed": False, "code": "version_required",
            "detail": f"refusing to install {repo or 'a repository'} without an exact "
                      "version: every install names the approved release "
                      "(dartec-ha-manager#54)"}


async def hacs_install(hass: HomeAssistant, cmd: dict[str, Any], *,
                       latest_ok: bool = False) -> dict:
    """Install a HACS repository at an exact version, and report what a
    restart would be for.

    `version` is required; see the module docstring for the rule and its one
    exception, which `latest_ok` selects and only `agent_update` passes.

    The manager used to restart a home after every integration install,
    because HACS says "restart Home Assistant to load it". Tested live, that is
    not true of a *new* integration: a HACS-managed install refreshes HA's
    integration cache, and the integration sets up and registers its services
    with no restart. A restart is needed only when code that is **already
    imported** has changed on disk, because Python keeps the old module in
    memory until the process exits.

    Whether the domain was loaded has to be read *before* the download — after
    it, the answer describes the new state, not the one that decides. So this
    reports the facts and the manager decides: `was_loaded` (the domain was set
    up before we touched it) and `previous_version`.
    """
    refused = check_version(cmd, latest_ok=latest_ok)
    if refused:
        return refused
    domain = (cmd.get("domain") or "").strip()
    was_loaded = bool(domain) and domain in hass.config.components
    previous = None
    if hass.data.get("hacs") is not None and "/" in (cmd.get("repo") or ""):
        before = await _repo_entry(hass, cmd["repo"].strip())
        previous = (before or {}).get("installed_version")

    result = await _hacs_install(hass, cmd)
    return {**result, "domain": domain or None, "was_loaded": was_loaded,
            "previous_version": previous}


async def _hacs_install(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Add (if needed) and download a HACS repository. Idempotent: an already
    installed repo at the requested version is reported as such, not re-fetched.

    `check_version` has already run. With a `version` this downloads exactly
    that release; without one it either downloads nothing (`only_if_missing`
    and already installed) or takes the latest release of a repository in
    `LATEST_ALLOWED`."""
    repo = (cmd.get("repo") or "").strip()
    category = (cmd.get("category") or "plugin").strip()
    pinned = (cmd.get("version") or "").strip() or None
    if "/" not in repo:
        return {"ok": False, "detail": "repo must be 'owner/name'"}
    if category not in CATEGORIES:
        return {"ok": False, "detail": f"unknown HACS category '{category}'"}

    if hass.data.get("hacs") is None:
        return {"ok": False, "detail": "HACS is not installed on this home"}

    latest = pinned is None and repo.lower() in LATEST_ALLOWED
    entry = await _repo_entry(hass, repo)
    if pinned is None and not latest and not (entry or {}).get("installed_version"):
        # only_if_missing, and it is missing: installing it would take the
        # latest release. Refused before HACS is even told about the
        # repository, so a refusal leaves nothing behind.
        return _version_required(repo)
    if entry is None:
        added = await call_own_ws(hass, {"type": "hacs/repositories/add",
                                         "repository": repo, "category": category})
        if not added.get("success"):
            error = added.get("error") or {}
            return {"ok": False, "detail": f"could not add {repo}: {error.get('message', error)}"}
        # HACS fetches metadata asynchronously; wait for it to appear.
        for _ in range(10):
            await asyncio.sleep(ADD_POLL_S)
            entry = await _repo_entry(hass, repo)
            # Wait for the version too: an entry that exists but has no
            # available_version yet is the state in which the downgrade guard
            # below has nothing to compare against. A pinned install compares
            # against its own version, so the entry alone is enough.
            if entry and (pinned or entry.get("available_version")):
                break
        if entry is None:
            return {"ok": False, "detail": f"{repo} added but did not appear in HACS in time"}

    installed = entry.get("installed_version")
    if cmd.get("only_if_missing") and installed:
        return {"ok": True, "changed": False, "installed_version": installed,
                "detail": f"{repo} already installed ({installed})"}

    if not pinned:
        # Ask HACS to re-read the repository from GitHub before deciding
        # there is nothing to do. available_version otherwise comes from
        # HACS's cached index, which lags behind a release by up to its
        # polling interval — so a forced update would report "already up to
        # date" and change nothing. A pinned download needs no index: HACS
        # fetches the named release itself.
        refreshed = await call_own_ws(hass, {"type": "hacs/repository/refresh",
                                             "repository": entry.get("id")}, timeout=120)
        if refreshed.get("success"):
            entry = await _repo_entry(hass, repo) or entry
            installed = entry.get("installed_version")
        else:
            _LOGGER.debug("HACS refresh for %s failed; using cached index", repo)

    target = pinned or entry.get("available_version")

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
        if parse(target) is None and target != floor:
            # Latest: HACS populates release metadata asynchronously after a
            # repository is added, so "no version yet" is a normal transient
            # state. Pinned to a commit: there is no telling whether it is
            # older. Either way, acting would mean downloading an unknown
            # version over a known one.
            detail = (f"cannot tell whether {target} is older than {floor}, which this "
                      "home runs; send allow_downgrade to replace it anyway" if pinned
                      else f"HACS has not reported a version for {repo} yet; this home "
                           f"runs {floor}. Try again in a moment.")
            return {"ok": False, "changed": False, "installed_version": floor,
                    "detail": detail}
        # Never quietly replace a home's code with something older. HACS offers
        # the latest *release*, which can trail a hand-installed build, and a
        # catalogue can hold a version older than one a home was given by
        # hand — and for an integration this command also restarts Home
        # Assistant, so an accidental downgrade costs an outage and silently
        # removes fixes.
        if is_older(target, floor):
            asked = "the version asked for" if pinned else "the newest release"
            return {"ok": False, "changed": False, "installed_version": floor,
                    "code": "downgrade",
                    "detail": f"refusing to downgrade {repo}: this home runs {floor} "
                              f"and {asked} is {target}"}

    # Compare by value so "v0.10.3" and "0.10.3" are one version, not two.
    if floor and target and (parse(floor) == parse(target) if parse(target)
                             else floor == target):
        return {"ok": True, "changed": False, "installed_version": floor,
                "detail": f"{repo} already up to date ({floor})"}

    download: dict[str, Any] = {"type": "hacs/repository/download",
                                "repository": entry.get("id")}
    if pinned:
        download["version"] = pinned
    downloaded = await call_own_ws(hass, download, timeout=180)
    if not downloaded.get("success"):
        error = downloaded.get("error") or {}
        return {"ok": False, "detail": f"download failed: {error.get('message', error)}"}

    entry = await _repo_entry(hass, repo) or entry
    version = entry.get("installed_version") or target
    if pinned and parse(pinned) is not None and parse(version) != parse(pinned):
        # HACS says it downloaded but records another release. Files did
        # change, so this is not "nothing happened"; the manager is told
        # plainly rather than shown a success for the wrong version.
        return {"ok": False, "changed": True, "installed_version": version,
                "code": "version_mismatch",
                "detail": f"asked HACS for {repo} {pinned}; it reports {version}"}
    return {"ok": True, "changed": True, "installed_version": version,
            "pinned": bool(pinned),
            # No blanket "restart to load it": for a new integration that is
            # not true, and repeating HACS's conservative advice here is how
            # the manager came to restart homes for nothing.
            "detail": f"installed {repo} {version}"}


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


HANDLERS = {"hacs_install": hacs_install, "hacs_list": hacs_list,
            "hacs_token_set": hacs_token_set}
