"""Comparing version strings, and knowing our own.

Kept free of module-level Home Assistant imports so it can be tested on its
own, and
deliberately not done with string comparison: "0.10.4" < "0.9.0" as text,
which would make a downgrade look like an upgrade for every release past .9.
That is not hypothetical — it is the mistake that motivated this module.
"""
from __future__ import annotations

import re

_LEADING_NUMBERS = re.compile(r"v?(\d+(?:\.\d+)*)")


def parse(value: str | None) -> tuple[int, ...] | None:
    """Leading dotted numbers as a tuple, or None if there are none.

    Tolerates a "v" prefix and ignores any pre-release suffix, so "v1.2.3-beta1"
    parses as (1, 2, 3). That means a beta compares equal to its release; for
    this integration's purposes an unknown-or-equal comparison is treated as
    "do not act", which is the safe direction.
    """
    match = _LEADING_NUMBERS.match(str(value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_older(candidate: str | None, than: str | None) -> bool:
    """True only when both parse and `candidate` is genuinely the lower version.

    Returns False when either side is unparseable: refusing to act on a version
    we could not read would block legitimate updates, and the caller treats
    False as "no objection".
    """
    left, right = parse(candidate), parse(than)
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) < right + (0,) * (width - len(right))


# The version this process is running. Resolved once and kept: it cannot change
# until Home Assistant restarts, because an integration's code is imported once.
_running_version: str | None = None


async def async_agent_version(hass, domain: str) -> str | None:
    """Our own version, as Home Assistant loaded it.

    This used to read manifest.json off disk on every snapshot, which is
    blocking file I/O in the event loop and which Home Assistant logs as such.
    The loader has already parsed that manifest at startup and caches the
    result, so asking it costs no I/O. It is also the more truthful answer:
    after a HACS download and before the restart, the file on disk describes
    the new release while this process is still running the old one.

    Home Assistant is imported here rather than at the top so the rest of this
    module stays testable without it. A failure is not cached, so the next
    snapshot asks again instead of reporting "unknown" until a restart.
    """
    global _running_version  # noqa: PLW0603 — one value per process, by design
    if _running_version is None:
        try:
            from homeassistant.loader import async_get_integration

            integration = await async_get_integration(hass, domain)
            if integration.version:
                _running_version = str(integration.version)
        except Exception:  # noqa: BLE001 — a missing version must not cost a snapshot
            return None
    return _running_version
