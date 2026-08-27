"""Comparing version strings.

Kept free of Home Assistant imports so it can be tested on its own, and
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
