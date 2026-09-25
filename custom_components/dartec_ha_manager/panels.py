"""The rules behind room panels. No Home Assistant imports.

A **room panel** is a wall tablet (an Android tablet, a Sonoff NSPanel Pro)
that shows one room's dashboard and nothing else. It signs in to Home
Assistant as its own account, a **panel account**, which the agent creates on
the manager's behalf (`panel_cmds.py`) with three things set:

* its **first dashboard** (`core.default_panel` in the account's own frontend
  settings) is the room's dashboard, so that is where the tablet lands;
* its **sidebar** (`sidebar.hiddenPanels`) hides every other entry the account
  could see, so there is nothing to wander off to;
* it is **not an administrator** and can **only sign in on the home network**
  (`local_only`), so its password is worth nothing outside the house.

The tablet itself is also told, once, by the technician, to "Always hide the
sidebar": that is a setting of the browser on that device (Home Assistant
keeps it in the browser's local storage, not per user), so no command can set
it and it is not in here.

**What this is not.** It is not a security boundary, and nothing in this
module pretends otherwise. Home Assistant has no per-user device permissions:
any signed-in account can call any service through Home Assistant's API, so a
panel account can still control every device in the home. What the rules
here buy is a screen that stays on its room, not an account that can only
reach its room. `docs/my-home/README.md` says the same to the homeowner.

**Who is a panel account.** Its username starts with `panel-` **and** it is
not the owner, not one of Home Assistant's system accounts, and not an
administrator. The prefix alone is not enough, deliberately: the commands in
`panel_cmds.py` may update or delete a panel account without the home's
consent (see `service_policy.py`), so a person who happens to be called
`panel-something` must never read as one. An administrator can never be a
panel, and the setup refuses the username rather than take the account over.
My Home (`household.py`) uses the same test, keeps panel accounts out of the
household, and reserves the prefix so a new person cannot be given it.

Plain functions over plain dicts, the shapes `household_ws.user_dict` and
`config/auth/list` give, so all of it is unit-tested without Home Assistant
(tests/test_panels.py).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

PANEL_USERNAME_PREFIX = "panel-"
# `panel-` and a slug of 1 to 26 characters: 32 at most, Home Assistant's own
# practical limit and the one My Home keeps. Lowercase letters, digits and
# dashes, because it is typed once on a tablet's on-screen keyboard.
USERNAME_RE = re.compile(r"^panel-[a-z0-9][a-z0-9-]{0,25}$")
# Dashboards are identified by their URL path, as Home Assistant stores them.
URL_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

GROUP_ADMIN = "system-admin"
GROUP_USER = "system-users"

# Longer than My Home's floor of 8: nobody chooses this password, the
# technician's phone generates it (16 characters in four groups), and it is
# typed once. A short one here could only be a mistake in the manager.
MIN_PASSWORD = 12
MAX_PASSWORD = 128
MAX_NAME = 50

TOKEN_TYPE_NORMAL = "normal"


class Refused(Exception):
    """A panel command the rules do not allow. `code` is what the manager
    acts on; the message is for people. Neither ever carries a password."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _groups(user: dict) -> list[str]:
    return [g["id"] if isinstance(g, dict) else g for g in (user.get("group_ids") or [])]


def has_panel_username(username: Any) -> bool:
    return str(username or "").strip().casefold().startswith(PANEL_USERNAME_PREFIX)


def is_panel_account(user: dict) -> bool:
    """A room panel's account. See the module docstring for why every part of
    this test is needed."""
    return (has_panel_username(user.get("username"))
            and not user.get("is_owner")
            and not user.get("system_generated")
            and GROUP_ADMIN not in _groups(user))


def clean_name(name: Any) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise Refused("invalid", "A name for the panel is needed.")
    if len(name) > MAX_NAME:
        raise Refused("invalid", f"Panel names are at most {MAX_NAME} characters.")
    return name


def clean_username(username: Any) -> str:
    """Home Assistant stores usernames stripped and case-folded, and refuses
    one that is not already in that form, so it is normalised first."""
    username = str(username or "").strip().casefold()
    if not USERNAME_RE.match(username):
        raise Refused("invalid", "A panel's username is 'panel-' followed by 1 to 26 "
                                 "lowercase letters, digits or dashes, starting with a "
                                 "letter or digit.")
    return username


def check_password(password: Any) -> None:
    """The floor only. Never echoes the value, not even its length."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise Refused("invalid", f"A panel's password needs at least {MIN_PASSWORD} "
                                 "characters.")
    if len(password) > MAX_PASSWORD:
        raise Refused("invalid", f"A panel's password is at most {MAX_PASSWORD} characters.")


def clean_url_path(url_path: Any) -> str:
    if not isinstance(url_path, str) or not URL_PATH_RE.match(url_path.strip()):
        raise Refused("no_dashboard", "url_path must name a dashboard on this home.")
    return url_path.strip()


def check_dashboard(url_path: str, dashboards: dict[str, dict]) -> None:
    """`dashboards` maps each Lovelace dashboard's URL path to
    `{"require_admin": bool}` as it is in effect (a sidebar override
    included). A panel account is never an administrator, so an admin-only
    dashboard would open to an error on the wall."""
    board = dashboards.get(url_path)
    if board is None:
        raise Refused("no_dashboard", f"There is no dashboard '{url_path}' on this home.")
    if board.get("require_admin"):
        raise Refused("no_dashboard", f"The dashboard '{url_path}' is for administrators "
                                      "only, and a panel is not one.")


def plan_setup(users: Iterable[dict], username: str) -> dict | None:
    """The panel account to update, or None to create one.

    Setup is idempotent on the username, which is what makes it retry-safe
    for the manager: the same username again updates the same account. The
    username held by anyone who is not a panel account is refused, never
    taken over."""
    holder = next((u for u in users
                   if (u.get("username") or "").casefold() == username), None)
    if holder is None:
        return None
    if not is_panel_account(holder):
        raise Refused("username_taken", f"The username '{username}' belongs to an account "
                                        "that is not a room panel.")
    return holder


def find_panel(users: Iterable[dict], user_id: Any) -> dict | None:
    """The panel account with this id, None when there is no such user, and
    a refusal when the user exists but is not a panel account: the routine
    commands may only ever touch panels."""
    target = next((u for u in users if u.get("id") == user_id), None)
    if target is None:
        return None
    if not is_panel_account(target):
        raise Refused("not_panel", "That account is not a room panel, and only room "
                                   "panels are changed by this command.")
    return target


def hidden_panels(panels: dict[str, dict], keep: str) -> list[str]:
    """Every sidebar entry a non-administrator could be shown, except the
    room's own dashboard.

    `panels` maps each registered panel's URL path to `{"require_admin":
    bool}`, in effect. Admin-only panels are left out because a non-admin is
    never sent them. Panels that are not shown in the sidebar today (another
    room's dashboard, Home Assistant's built-in pages) are hidden too: a
    sidebar override can switch one on later, and hiding an entry that is
    not shown costs nothing. The frontend always shows the default dashboard
    whatever the list says, which is one more reason `keep` is left out.
    """
    return sorted(path for path, panel in panels.items()
                  if path != keep and not panel.get("require_admin"))


def sidebar_value(panels: dict[str, dict], url_path: str) -> dict:
    """The `sidebar` frontend setting for a panel account."""
    return {"hiddenPanels": hidden_panels(panels, url_path), "panelOrder": [url_path]}


def core_value(current: Any, url_path: str) -> dict:
    """The `core` frontend setting with the room as its first dashboard.
    Any other key already there is kept, as the profile page would."""
    core = dict(current) if isinstance(current, dict) else {}
    core["default_panel"] = url_path
    return core


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def status_row(user: dict, tokens: Iterable[dict], default_panel: str | None,
               language: str | None = None, theme: str | None = None) -> dict:
    """What `panel_status` and the snapshot report for one panel account.

    `language` and `theme` are the account's own (`user_prefs.py`), None
    when it follows the browser or the system default.

    `tokens` are its refresh tokens as `{"token_type", "last_used_at"}`. Only
    sign-ins count: a `normal` token is what a login makes, and it records
    when it was last used to get an access token. Long-lived tokens and
    system tokens are not a tablet signing in. Addresses are never read."""
    signins = [t for t in tokens if t.get("token_type") == TOKEN_TYPE_NORMAL]
    used = [t["last_used_at"] for t in signins if t.get("last_used_at")]
    return {"user_id": user.get("id"), "username": user.get("username"),
            "name": user.get("name"), "is_active": bool(user.get("is_active", True)),
            "local_only": bool(user.get("local_only")), "default_panel": default_panel,
            "language": language, "theme": theme, "signed_in": bool(signins), "last_used_at": _iso(max(used)) if used else None}


def scrub(text: Any, secret: Any) -> str:
    """An error text safe to relay: a password that somehow made its way into
    Home Assistant's answer is cut out before it goes anywhere."""
    text = str(text or "")
    if isinstance(secret, str) and len(secret) >= 4:
        text = text.replace(secret, "***")
    return text
