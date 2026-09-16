"""Telling an enrolment code from a pairing token, and reading a refusal.

An enrolment code is what the Dartec admin panel hands out now: sixteen
symbols, usually shown as four groups (``W8DZ-HF3Q-PDTV-4RAQ``), good once and
for about an hour. The config flow trades it at ``/api/agent/enrol`` for this
home's pairing token, which is what the agent keeps and authenticates with,
exactly as before. A pairing token pasted in directly still works, for homes
paired the old way.

Both go in the one field, so this module decides which was typed. The rules
mirror the manager's ``server/app/enrolment.py`` (ALPHABET, CODE_LENGTH and
the refusal reasons); change them together.

Kept free of Home Assistant imports so it can be tested on its own.
"""
from __future__ import annotations

from typing import Any

ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
CODE_LENGTH = 16

# The manager's refusal reasons, each of which is also an error key in
# strings.json. Anything else falls back to a status-based guess.
KNOWN_REASONS = frozenset({"invalid_code", "code_expired", "code_used", "code_revoked",
                           "wrong_instance", "rate_limited"})


def normalize(value: str | None) -> str | None:
    """The canonical 16 symbols if `value` is an enrolment code, else None.

    Case, dashes and spaces are forgiven, because people type these from a
    screen or a phone call. A pairing token is 43 characters and never
    normalizes to a code.
    """
    cleaned = "".join(ch for ch in str(value or "").upper() if ch not in "- \t")
    if len(cleaned) != CODE_LENGTH or any(ch not in ALPHABET for ch in cleaned):
        return None
    return cleaned


def error_key(status: int, payload: Any) -> str:
    """The config-flow error key for a refused redemption."""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    reason = detail.get("reason") if isinstance(detail, dict) else None
    if reason in KNOWN_REASONS:
        return reason
    if status == 404:
        # A manager from before enrolment codes existed.
        return "enrolment_unsupported"
    if status == 429:
        return "rate_limited"
    if status == 401:
        return "invalid_code"
    return "cannot_connect"
