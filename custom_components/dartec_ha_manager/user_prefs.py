"""A person's own language and theme in Home Assistant. No Home Assistant imports.

Home Assistant keeps both **per account, on the server**, in the frontend's
per-user store, which is what the profile page writes when someone chooses
them for themselves (`frontend/set_user_data`):

* `language` - since 2025, the whole locale: the language plus how numbers,
  times, dates and the first weekday follow it. `null` means "follow the
  browser". The bench wrote exactly this shape to switch an account to Arabic
  (docs/dashboards/prototypes/bench-log.jsonl), and the frontend then mirrors
  the whole interface right to left.
* `theme` - since 2026.2, the theme that person sees, overriding the
  system default (`frontend.set_theme`) on every device they sign in on. The
  frontend stores `{"theme": name}` (its `selectedTheme`, the key read from
  the 2026.9 frontend bundle). `null`, or no theme, means "the system
  default".

Room panels (`panel_cmds.py`) and My Home (`household_ws.py`) both set these
for someone else, so the rules are here once, as plain functions over plain
values, and unit-tested in tests/test_user_prefs.py.

**Neither is a security boundary.** A language or a theme is how a screen
looks, nothing more; the person can change either on their own profile.
"""
from __future__ import annotations

from typing import Any

# What Dartec's homes are set up in. Home Assistant has many more; a Dartec
# account is set to one of these or left to the browser.
LANGUAGES = ("en", "ar")

# The frontend's own defaults for the rest of the locale: "follow the
# language", which is what the profile page writes the first time someone
# picks a language.
LOCALE_DEFAULTS = {"number_format": "language", "time_format": "language",
                   "date_format": "language", "time_zone": "local",
                   "first_weekday": "language"}

# Home Assistant's built-in theme, always there even with no themes loaded.
BUILTIN_THEME = "default"
MAX_THEME = 64


class Invalid(ValueError):
    """A value these rules do not accept. `code` is for the caller to map."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def clean_language(value: Any) -> str | None:
    """`en`, `ar`, or None for "follow the browser"."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value.strip().lower() not in LANGUAGES:
        raise Invalid("language", "language must be 'en' or 'ar', or null to follow "
                                  "the browser")
    return value.strip().lower()


def language_value(current: Any, language: str | None) -> dict | None:
    """The `language` user data for `language`, keeping any number, time or
    date format the person already chose. None clears it."""
    if language is None:
        return None
    out = dict(LOCALE_DEFAULTS)
    if isinstance(current, dict):
        out.update({k: v for k, v in current.items() if k != "language"})
    out["language"] = language
    return out


def language_of(stored: Any) -> str | None:
    """The language an account's stored `language` user data sets, or None."""
    if isinstance(stored, dict) and isinstance(stored.get("language"), str):
        return stored["language"]
    return None


def clean_theme(value: Any, available: Any) -> str | None:
    """A theme loaded on this home (or Home Assistant's own `default`), or
    None for "the system default". A theme that is not loaded would silently
    fall back to Home Assistant's default on the person's screens, so it is
    refused instead."""
    if value is None or value == "":
        return None
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > MAX_THEME:
        raise Invalid("theme", "theme must be a theme name, or null for the system default")
    if name != BUILTIN_THEME and name not in set(available or ()):
        raise Invalid("no_theme", f"There is no theme '{name}' on this home.")
    return name


def theme_value(current: Any, theme: str | None) -> dict | None:
    """The `theme` user data for `theme`, keeping whatever else the person
    chose (light or dark, their own colours), as the profile page does: it
    merges the new name into the stored value. None clears it (the system
    default again)."""
    if theme is None:
        return None
    out = dict(current) if isinstance(current, dict) else {}
    out["theme"] = theme
    return out


def theme_of(stored: Any) -> str | None:
    if isinstance(stored, dict) and isinstance(stored.get("theme"), str) and stored["theme"]:
        return stored["theme"]
    return None
