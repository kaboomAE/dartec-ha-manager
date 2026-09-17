"""The GitHub token in this home's HACS config entry, and replacing it.

HACS asks for a GitHub token when it is set up, and keeps it in its own config
entry. On a Dartec home that token is a fine-grained personal access token on a
Dartec bot account: public repositories, read-only, no permissions. It only
exists so HACS is not rate-limited by GitHub. It has to be rotated, and
rotating it by visiting every house is how it would never get rotated.

So the manager holds the current token, the snapshot reports which one each
home has, and `hacs_token_set` replaces it where they differ. Three rules shape
everything here:

* **The token never leaves the entry except into the entry.** The snapshot
  carries a fingerprint — the first 16 hex characters of its SHA-256 — which is
  enough to tell "same token" from "different token" and useless for anything
  else. No response, log line or logbook entry carries the token itself.
* **Only the token changes, and only in a HACS entry that already exists.**
  This never creates a HACS entry: setting HACS up is `integration_setup`,
  which is behind consent. Replacing one read-only token with another cannot
  add a repository, download anything, or change what HACS is allowed to do.
* **Never a worse token than the home had.** The new token is checked against
  GitHub before the entry is touched, and a swap after which HACS does not
  load is undone with the previous data, kept in memory for exactly that.

Why this needs no maintenance window is argued in ``service_policy.py``.

No module-level Home Assistant imports, so it is testable without one: it
reaches ``hass.config_entries`` through the object it is handed, and HA's
shared HTTP session through ``_session``, imported only when used.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

HACS_DOMAIN = "hacs"

# The snapshot key. A top-level key of its own rather than a field inside
# `snapshot["hacs"]`, because that one is already the *list* of downloaded
# repositories, and the manager reads it as a list.
SNAPSHOT_KEY = "hacs_token"

FINGERPRINT_LENGTH = 16

# Classic (`ghp_`, 40 characters) and fine-grained (`github_pat_`, 93) tokens.
# The bounds are generous on purpose — GitHub has changed token lengths before —
# and exist only to refuse something that is plainly not a token.
_PREFIXES = ("github_pat_", "ghp_")
_MIN_LENGTH = 20
_MAX_LENGTH = 255
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9_]+")

# Public, and ours: any token that can read public repositories can read it,
# so a 200 here means the token authenticates and HACS will be able to use it.
VERIFY_URL = "https://api.github.com/repos/kaboomAE/dartec-ha-manager"
VERIFY_TIMEOUT_S = 15

# After a reload, how long HACS gets to reach LOADED, and how often to look.
LOAD_TIMEOUT_S = 60
LOAD_POLL_S = 1.0

VERIFIED = "verified"
REJECTED = "token_rejected"
UNREACHABLE = "github_unreachable"


def fingerprint(token: str) -> str:
    """First 16 hex characters of the token's SHA-256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def valid_token(token: Any) -> bool:
    """Plainly a GitHub token: a prefix GitHub issues, a sane length, and
    nothing a paste could have dragged along with it (whitespace, quotes)."""
    return (isinstance(token, str)
            and token.startswith(_PREFIXES)
            and _MIN_LENGTH <= len(token) <= _MAX_LENGTH
            and _TOKEN_CHARS.fullmatch(token) is not None)


def hacs_entry(hass) -> Any | None:
    """HACS's config entry, or None. HACS allows a single instance."""
    entries = hass.config_entries.async_entries(HACS_DOMAIN)
    return entries[0] if entries else None


def current_fingerprint(hass) -> str | None:
    """Fingerprint of the token HACS holds now, or None with no entry or token."""
    entry = hacs_entry(hass)
    token = (getattr(entry, "data", None) or {}).get("token") if entry else None
    if not isinstance(token, str) or not token:
        return None
    return fingerprint(token)


def snapshot_section(hass) -> dict:
    """What the snapshot reports: which token, never the token."""
    return {"token_fingerprint": current_fingerprint(hass)}


# --- Verifying a token before it goes anywhere near the entry ------------------

def _session(hass):
    """Home Assistant's shared aiohttp session."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return async_get_clientsession(hass)


def classify_github_response(status: int, headers: Any, body: str) -> str:
    """What one answer from GitHub says about the token.

    Only a rejection *of the token* is `token_rejected`. Rate limiting and
    GitHub's own trouble are `github_unreachable`: the token may be fine, and
    the manager tries again later. `body` is inspected, never returned or
    logged.
    """
    if status == 200:
        return VERIFIED
    if status == 429 or status >= 500:
        return UNREACHABLE
    if status == 403:
        headers = headers or {}
        remaining = (headers.get("X-RateLimit-Remaining")
                     or headers.get("x-ratelimit-remaining"))
        if remaining == "0" or "rate limit" in (body or "").lower():
            return UNREACHABLE
    # 401 (bad credentials), any other 403, 404 and the rest: a token that
    # cannot read a public repository is no use to HACS, whatever the reason.
    return REJECTED


async def verify_token(hass, token: str) -> str:
    """One authenticated request to GitHub with the new token. Of the request
    and its answer, only the verdict is ever logged."""
    async def _ask() -> str:
        session = _session(hass)
        async with session.get(
                VERIFY_URL,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                timeout=VERIFY_TIMEOUT_S) as resp:
            body = await resp.text() if resp.status == 403 else ""
            return classify_github_response(resp.status, resp.headers, body)

    try:
        # Belt and braces over the session's own timeout: a hung check must
        # not hold the command, and a timeout changes nothing.
        verdict = await asyncio.wait_for(_ask(), VERIFY_TIMEOUT_S + 5)
    except Exception as err:  # noqa: BLE001 — network trouble is not a verdict
        _LOGGER.info("Could not reach GitHub to check the new HACS token (%s)",
                     type(err).__name__)
        return UNREACHABLE
    if verdict != VERIFIED:
        _LOGGER.info("GitHub did not accept the new HACS token (%s)", verdict)
    return verdict


# --- Swapping, with a way back -------------------------------------------------

def _is_loaded(entry) -> bool:
    """ConfigEntryState.LOADED, compared by value so no HA import is needed."""
    state = getattr(entry, "state", None)
    return getattr(state, "value", state) == "loaded"


async def _wait_loaded(entry) -> bool:
    waited = 0.0
    while not _is_loaded(entry):
        if waited >= LOAD_TIMEOUT_S:
            return False
        await asyncio.sleep(LOAD_POLL_S)
        waited += LOAD_POLL_S
    return True


async def _apply(hass, entry, data: dict) -> str | None:
    """Write `data`, reload, and wait for HACS to load. None on success, or
    which step failed — by exception type only, since a message can echo the
    data it was given, and the data holds a token."""
    try:
        # HA replaces `data` wholesale, so the caller passes every key.
        hass.config_entries.async_update_entry(entry, data=data)
    except Exception as err:  # noqa: BLE001
        return f"the entry update failed ({type(err).__name__})"
    try:
        # HACS reads the token once, at setup; a new one is in use only after
        # the entry reloads.
        await hass.config_entries.async_reload(entry.entry_id)
    except Exception as err:  # noqa: BLE001
        return f"the reload failed ({type(err).__name__})"
    if not await _wait_loaded(entry):
        return f"HACS did not load within {LOAD_TIMEOUT_S}s"
    return None


def _refuse(reason: str, detail: str, **extra: Any) -> dict:
    return {"ok": False, "reason": reason, "detail": detail, **extra}


async def hacs_token_set(hass, cmd: dict[str, Any]) -> dict:
    """Replace the GitHub token in the existing HACS entry, never with a worse one.

    In order, stopping at the first thing that is not right:

    1. It is a GitHub token and matches its fingerprint (`invalid_token`).
    2. HACS is set up on this home; this never creates it (`no_hacs_entry`).
    3. GitHub accepts the token (`token_rejected`), or cannot be asked right
       now (`github_unreachable`). Either way the entry is untouched.
    4. Only the token changes, HACS reloads, and it reaches LOADED. If any of
       that fails, the previous data — held in memory, written nowhere else —
       goes back and HACS reloads again (`rolled_back`). Only when that fails
       too is the answer `reload_failed`.
    """
    token = cmd.get("token")
    claimed = cmd.get("fingerprint")
    if not valid_token(token) or not isinstance(claimed, str) \
            or claimed != fingerprint(token):
        return _refuse("invalid_token",
                       "not a GitHub token, or its fingerprint does not match")
    fp = fingerprint(token)

    entry = hacs_entry(hass)
    if entry is None:
        # Never create one. Setting HACS up is `integration_setup`, which needs
        # consent; this command only maintains what is already there.
        return _refuse("no_hacs_entry", "HACS is not set up on this home")

    previous = dict(entry.data)
    if previous.get("token") == token:
        return {"ok": True, "changed": False, "fingerprint": fp,
                "detail": f"HACS already uses token {fp}"}
    old = previous.get("token")
    old_fp = fingerprint(old) if isinstance(old, str) and old else None

    verdict = await verify_token(hass, token)
    if verdict == REJECTED:
        return _refuse(REJECTED, "GitHub rejected the new token; HACS keeps "
                                 "its current one", changed=False, fingerprint=fp)
    if verdict != VERIFIED:
        return _refuse(UNREACHABLE, "could not check the new token with GitHub; "
                                    "nothing was changed",
                       changed=False, fingerprint=fp)

    failure = await _apply(hass, entry, {**previous, "token": token})
    if failure is None:
        return {"ok": True, "changed": True, "fingerprint": fp,
                "previous_fingerprint": old_fp,
                "detail": f"HACS now uses token {fp}"}

    _LOGGER.warning("HACS token swap failed: %s; restoring the previous token",
                    failure)
    rollback = await _apply(hass, entry, previous)
    if rollback is None:
        return _refuse("rolled_back",
                       f"{failure}; the previous token was restored and HACS "
                       "is loaded",
                       changed=False, fingerprint=fp, previous_fingerprint=old_fp)

    _LOGGER.warning("Restoring the previous HACS token failed too: %s", rollback)
    return _refuse("reload_failed",
                   f"{failure}; restoring the previous token also failed: "
                   f"{rollback}",
                   fingerprint=fp, previous_fingerprint=old_fp)


def logbook_line(result: dict) -> str | None:
    """The homeowner's record of an attempt that touched the entry. Fingerprints
    only, never a token; None when the entry was never touched."""
    fp = result.get("fingerprint")
    old = result.get("previous_fingerprint") or "none"
    reason = result.get("reason")
    if result.get("ok") and result.get("changed"):
        return (f"Dartec updated the HACS GitHub token (fingerprint {fp}, "
                f"replacing {old})")
    if reason == "rolled_back":
        return (f"Dartec tried to update the HACS GitHub token (fingerprint "
                f"{fp}), but HACS did not load with it, so the previous token "
                f"(fingerprint {old}) was restored")
    if reason == "reload_failed":
        return (f"Dartec tried to update the HACS GitHub token (fingerprint "
                f"{fp}); HACS did not load, and restoring the previous token "
                f"(fingerprint {old}) did not bring it back. HACS needs "
                "checking")
    return None


HANDLERS = {"hacs_token_set": hacs_token_set}
