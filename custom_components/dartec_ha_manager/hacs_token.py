"""The GitHub token in this home's HACS config entry, and replacing it.

HACS asks for a GitHub token when it is set up, and keeps it in its own config
entry. On a Dartec home that token is a fine-grained personal access token on a
Dartec bot account: public repositories, read-only, no permissions. It only
exists so HACS is not rate-limited by GitHub. It has to be rotated, and
rotating it by visiting every house is how it would never get rotated.

So the manager holds the current token, the snapshot reports which one each
home has, and `hacs_token_set` replaces it where they differ. Two rules shape
everything here:

* **The token never leaves the entry except into the entry.** The snapshot
  carries a fingerprint — the first 16 hex characters of its SHA-256 — which is
  enough to tell "same token" from "different token" and useless for anything
  else. No response, log line or logbook entry carries the token itself.
* **Only the token changes, and only in a HACS entry that already exists.**
  This never creates a HACS entry: setting HACS up is `integration_setup`,
  which is behind consent. Replacing one read-only token with another cannot
  add a repository, download anything, or change what HACS is allowed to do.

Why this needs no maintenance window is argued in ``service_policy.py``.

No Home Assistant imports, so the whole module is testable without one: the
only HA surface it touches is ``hass.config_entries``, reached through the
object it is handed.
"""
from __future__ import annotations

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


def _refuse(reason: str, detail: str) -> dict:
    return {"ok": False, "reason": reason, "detail": detail}


async def hacs_token_set(hass, cmd: dict[str, Any]) -> dict:
    """Replace the GitHub token in the existing HACS entry.

    The command carries the token and its fingerprint. The fingerprint is
    checked against the token rather than trusted: a token damaged in transit
    or pasted wrongly on the manager is refused here instead of being written
    into a working HACS entry and breaking it.
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

    if entry.data.get("token") == token:
        return {"ok": True, "changed": False, "fingerprint": fp,
                "detail": f"HACS already uses token {fp}"}

    try:
        # HA replaces `data` wholesale, so copy every other key across: only
        # the token is ours to change.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "token": token})
    except Exception as err:  # noqa: BLE001 — the message could echo the data
        _LOGGER.warning("Could not update the HACS entry (%s)", type(err).__name__)
        return _refuse("update_failed",
                       f"Home Assistant refused the update ({type(err).__name__})")

    try:
        # HACS reads the token once, at setup; the new one is in use only
        # after the entry reloads.
        await hass.config_entries.async_reload(entry.entry_id)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("HACS did not reload after its token changed (%s)",
                        type(err).__name__)
        return {"ok": False, "reason": "reload_failed", "changed": True,
                "fingerprint": fp,
                "detail": "token saved, but HACS did not reload; it takes "
                          "effect when HACS next loads"}

    return {"ok": True, "changed": True, "fingerprint": fp,
            "detail": f"HACS now uses token {fp}"}


def logbook_line(fp: str) -> str:
    """The homeowner's record of a change. The fingerprint, never the token."""
    return f"Dartec updated the HACS GitHub token (fingerprint {fp})"


HANDLERS = {"hacs_token_set": hacs_token_set}
