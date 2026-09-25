"""The rules behind "My Home", the household panel. No Home Assistant imports.

"My Home" lets the homeowner manage the people who can sign in to their home
without knowing Home Assistant: add a person, change what they may do, reset
a forgotten password, pause them, remove them, and pick the dashboard they
see first. It lives **inside the home**, deliberately:

* The agent's consent model assumes Dartec's own cloud may be compromised, so
  account changes from the cloud (`user_create` and the rest) need consent
  given on the home. A cloud portal where "the customer" managed accounts
  would have turned the manager into an account-creation path into every
  home, and nothing on the home can tell a genuine request from the
  homeowner apart from a forged one.
* Here the homeowner is authenticated by Home Assistant itself, the panel is
  admin-only, and every change runs in this process under that person's own
  session. The cloud gains nothing: no command reaches this module, and the
  snapshot carries only a count of people by role.

This module holds everything that decides *whether* a change may happen, as
plain functions over plain dicts, so the guards are unit-tested without Home
Assistant (tests/test_household.py). `household_ws.py` applies them.

An account is one of five kinds, and only a **person** can be changed here:

* **owner** - shown, labelled, never changed from the panel. Home Assistant
  itself refuses to deactivate an owner; the owner's own password is changed
  from their profile, like anyone's.
* **maintenance** - Dartec's own support account (username ``dartec``,
  created by the onboarding app). Not listed as a member of the household,
  never changed here. The panel says it exists, so nobody is surprised to
  find it in Home Assistant's own settings.
* **panel** - a room panel's account (`panel-kitchen`): a wall tablet
  showing one room, set up by the installer through the manager (see
  `panels.py`). Not a member of the household, never changed here, not
  counted; the panel says how many there are, as it does for Dartec's
  account. The `panel-` prefix is reserved, so a new person cannot be given
  it and then be mistaken for one.
* **system** - Home Assistant's plumbing: the Supervisor, add-ons, Home
  Assistant Cloud. Never shown, never changed.
* **person** - everyone else.

Roles, in the words the panel uses:

* **Family (can manage the home)** - Home Assistant's administrator group.
* **Family** - a regular user.
* **Guest** - a regular user who can only sign in on the home network, and
  whom this integration remembers as a guest. That is all a guest is; see
  `GUEST_LIMITS` for what it does and does not restrict.
* **View only** - Home Assistant's read-only group. Never offered, only shown
  when an installer set it up, and it can be changed to one of the others.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

try:
    from . import user_prefs
    from .panels import PANEL_USERNAME_PREFIX, is_panel_account
except ImportError:  # imported on its own, by the unit tests
    import user_prefs
    from panels import PANEL_USERNAME_PREFIX, is_panel_account

GROUP_ADMIN = "system-admin"
GROUP_USER = "system-users"
GROUP_READONLY = "system-read-only"

MAINTENANCE_USERNAME = "dartec"

KIND_OWNER = "owner"
KIND_MAINTENANCE = "maintenance"
KIND_SYSTEM = "system"
KIND_PANEL = "panel"
KIND_PERSON = "person"

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_FAMILY = "family"
ROLE_GUEST = "guest"
ROLE_VIEW_ONLY = "view_only"
# What someone may choose for a person. The owner is not a role you give.
ASSIGNABLE_ROLES = (ROLE_ADMIN, ROLE_FAMILY, ROLE_GUEST)

ROLE_GROUP = {ROLE_ADMIN: GROUP_ADMIN, ROLE_FAMILY: GROUP_USER, ROLE_GUEST: GROUP_USER}
ROLE_LABEL = {ROLE_OWNER: "Owner", ROLE_ADMIN: "Family (can manage the home)",
              ROLE_FAMILY: "Family", ROLE_GUEST: "Guest", ROLE_VIEW_ONLY: "View only"}

# What "Guest" means, stated once. The panel's copy and the docs say the same.
GUEST_LIMITS = {
    "restricts": [
        "cannot open Settings, add or remove devices, or manage people "
        "(a regular user, not an administrator)",
        "can only sign in on the home's own network, never from outside",
        "is labelled a guest, so they are easy to find and pause when they leave",
    ],
    "does_not_restrict": [
        "can see and control every device in the home while signed in "
        "(Home Assistant has no per-device permissions for users)",
        "can open every dashboard, history and camera, not just the one "
        "chosen for them",
    ],
}

MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_NAME = 50
# Letters a phone keyboard types without switching layouts. Home Assistant
# itself accepts any string, but a username has to be typed on every device
# someone signs in on, including a TV remote or a wall tablet.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
# Dashboards are identified by their URL path, as Home Assistant stores them.
URL_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class Refused(Exception):
    """A change the rules do not allow. `code` is what the panel translates;
    the message is for logs and for anyone reading the websocket answer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _groups(user: dict) -> list[str]:
    return [g["id"] if isinstance(g, dict) else g for g in (user.get("group_ids") or [])]


def is_maintenance(user: dict) -> bool:
    """Dartec's own support account. Recognised by its username, and by its
    name when it has no Home Assistant login yet: the onboarding app creates
    the user first and attaches the credential second, and a user caught in
    between is still ours."""
    username = (user.get("username") or "").strip().casefold()
    if username:
        return username == MAINTENANCE_USERNAME
    return (user.get("name") or "").strip().casefold() == MAINTENANCE_USERNAME


def kind(user: dict) -> str:
    if user.get("system_generated"):
        return KIND_SYSTEM
    if user.get("is_owner"):
        return KIND_OWNER
    if is_maintenance(user):
        return KIND_MAINTENANCE
    if is_panel_account(user):
        return KIND_PANEL
    return KIND_PERSON


def panel_count(users: Iterable[dict]) -> int:
    """How many room panel accounts the home has, for the panel's note."""
    return sum(1 for u in users if kind(u) == KIND_PANEL)


def is_admin(user: dict) -> bool:
    return bool(user.get("is_owner")) or GROUP_ADMIN in _groups(user)


def role(user: dict, guests: Iterable[str]) -> str:
    if user.get("is_owner"):
        return ROLE_OWNER
    groups = _groups(user)
    if GROUP_ADMIN in groups:
        return ROLE_ADMIN
    if GROUP_READONLY in groups and GROUP_USER not in groups:
        return ROLE_VIEW_ONLY
    if user.get("id") in set(guests):
        return ROLE_GUEST
    return ROLE_FAMILY


def household(users: Iterable[dict], guests: Iterable[str]) -> list[dict]:
    """The accounts the panel shows, owner first then by name: the owner and
    every person. Maintenance, panel and system accounts are left out."""
    guests = set(guests)
    shown = [{**u, "kind": kind(u), "role": role(u, guests)}
             for u in users if kind(u) in (KIND_OWNER, KIND_PERSON)]
    return sorted(shown, key=lambda u: (u["kind"] != KIND_OWNER,
                                        (u.get("name") or "").casefold()))


def counts(users: Iterable[dict], guests: Iterable[str]) -> dict[str, int]:
    """What the manager may know: how many people, by role, and how many are
    paused. No names, usernames or ids - those are the customer's. Room
    panels are not people and are not counted; the snapshot reports them on
    their own (`panels`)."""
    out = {ROLE_OWNER: 0, ROLE_ADMIN: 0, ROLE_FAMILY: 0, ROLE_GUEST: 0,
           ROLE_VIEW_ONLY: 0, "paused": 0, "total": 0}
    for user in household(users, guests):
        out[user["role"]] += 1
        out["total"] += 1
        if not user.get("is_active", True):
            out["paused"] += 1
    return out


def _active_admins(users: Iterable[dict]) -> set[str]:
    """People who can manage the home right now. Dartec's account is not one
    of them: a home whose only administrator is its installer's support login
    has been taken away from its owner, whatever Home Assistant thinks."""
    return {u.get("id") for u in users
            if kind(u) in (KIND_OWNER, KIND_PERSON) and u.get("is_active", True)
            and is_admin(u)}


def _find(users: Iterable[dict], user_id: Any) -> dict:
    target = next((u for u in users if u.get("id") == user_id), None)
    if target is None:
        raise Refused("not_found", "That person is no longer on this home.")
    return target


def _changeable(target: dict) -> None:
    what = kind(target)
    if what == KIND_OWNER:
        raise Refused("owner", "The owner's account cannot be changed here.")
    if what == KIND_MAINTENANCE:
        raise Refused("maintenance", "Dartec's support account is not managed here.")
    if what == KIND_PANEL:
        raise Refused("panel", "Room panels are set up by your installer and are not "
                               "managed here.")
    if what == KIND_SYSTEM:
        raise Refused("system", "This account belongs to Home Assistant itself.")


def check_actor(actor: dict) -> None:
    """Who may make changes at all. The panel is admin-only and Home
    Assistant checks that before any of this runs; this is what it adds.

    Dartec's support account may not use it. Account changes from Dartec go
    through the path that needs the home's consent, and keeping this panel
    the homeowner's alone is what lets its logbook lines mean what they say.
    Home Assistant's own Settings still let any administrator manage users,
    this account included: an integration cannot take that away, which is why
    this is a rule about this panel and not a security boundary.
    """
    if kind(actor) == KIND_MAINTENANCE:
        raise Refused("actor_maintenance",
                      "Dartec's support account cannot manage the household.")
    if kind(actor) == KIND_SYSTEM or not is_admin(actor):
        raise Refused("actor_not_admin", "Only people who can manage the home can do this.")


def clean_name(name: Any) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise Refused("name_required", "A name is needed.")
    if len(name) > MAX_NAME:
        raise Refused("name_too_long", f"Names are at most {MAX_NAME} characters.")
    return name


def clean_username(username: Any) -> str:
    """Home Assistant normalises usernames by stripping and case-folding, and
    refuses one that is not already normal - so this normalises first."""
    username = str(username or "").strip().casefold()
    if not USERNAME_RE.match(username):
        raise Refused("username_invalid",
                      "Usernames are 2 to 32 letters, numbers, dots, dashes or "
                      "underscores, starting with a letter or number.")
    if username == MAINTENANCE_USERNAME:
        raise Refused("username_reserved", "That username is reserved for Dartec.")
    if username.startswith(PANEL_USERNAME_PREFIX):
        # Otherwise a person added as a regular user could later be read as a
        # room panel, and changed or removed by the manager without consent.
        raise Refused("username_panel", "Usernames starting with 'panel-' are kept "
                                        "for room panels.")
    return username


def check_password(password: Any, *, username: str = "", name: str = "") -> str:
    """The floor, not the meter. The panel shows a strength meter and offers
    a generated password; the server only refuses what is plainly unsafe."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise Refused("password_short", f"Passwords need at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise Refused("password_long", f"Passwords are at most {MAX_PASSWORD} characters.")
    folded = password.casefold()
    for word in (username, *name.split()):
        if len(word) >= 3 and word.casefold() in folded:
            raise Refused("password_personal",
                          "The password cannot contain their name or username.")
    if len(set(password)) < 4:
        raise Refused("password_simple", "That password is too easy to guess.")
    return password


def check_role(value: Any) -> str:
    if value not in ASSIGNABLE_ROLES:
        raise Refused("role_invalid", "Choose Family (can manage the home), Family or Guest.")
    return value


def check_create(actor: dict, users: list[dict], payload: dict) -> dict:
    """Validate an "Add a person". Returns the cleaned values."""
    check_actor(actor)
    name = clean_name(payload.get("name"))
    username = clean_username(payload.get("username"))
    if any((u.get("username") or "").casefold() == username for u in users):
        raise Refused("username_taken", "Someone on this home already has that username.")
    password = check_password(payload.get("password"), username=username, name=name)
    new_role = check_role(payload.get("role"))
    # A guest can only ever sign in at home. That is part of what the word
    # means here, so it is not left to a checkbox.
    local_only = True if new_role == ROLE_GUEST else bool(payload.get("local_only"))
    return {"name": name, "username": username, "password": password,
            "role": new_role, "local_only": local_only,
            "language": clean_language(payload.get("language"))}


def check_update(actor: dict, users: list[dict], user_id: str, changes: dict,
                 guests: Iterable[str]) -> dict:
    """Validate an edit, a pause or a resume. Returns the cleaned changes,
    only those that differ from how the person is now."""
    check_actor(actor)
    target = _find(users, user_id)
    _changeable(target)
    self_edit = target.get("id") == actor.get("id")
    current_role = role(target, guests)
    out: dict[str, Any] = {}

    if "name" in changes:
        name = clean_name(changes["name"])
        if name != target.get("name"):
            out["name"] = name
    if "role" in changes:
        new_role = check_role(changes["role"])
        if new_role != current_role:
            if self_edit:
                raise Refused("own_role", "You cannot change your own role. Ask "
                                          "someone else who can manage the home.")
            out["role"] = new_role
    if "is_active" in changes:
        active = bool(changes["is_active"])
        if active != target.get("is_active", True):
            if self_edit:
                raise Refused("self_pause", "You cannot pause yourself.")
            out["is_active"] = active
    role_after = out.get("role", current_role)
    if role_after == ROLE_GUEST:
        # Guests only ever sign in at home, whatever else changes.
        if not target.get("local_only"):
            out["local_only"] = True
    elif "local_only" in changes:
        local_only = bool(changes["local_only"])
        if local_only != bool(target.get("local_only")):
            if self_edit:
                # Done from a phone outside the house, this would sign you out
                # of your own home with no way back in until you are there.
                raise Refused("self_local", "You cannot change where you yourself can sign in.")
            out["local_only"] = local_only
    if not out:
        raise Refused("no_change", "Nothing was changed.")

    after = {**target, "is_active": out.get("is_active", target.get("is_active", True)),
             "group_ids": [ROLE_GROUP[role_after]] if "role" in out else _groups(target)}
    _keep_an_admin(users, target, after)
    return out


def check_remove(actor: dict, users: list[dict], user_id: str) -> dict:
    check_actor(actor)
    target = _find(users, user_id)
    _changeable(target)
    if target.get("id") == actor.get("id"):
        raise Refused("self_remove", "You cannot remove yourself.")
    _keep_an_admin(users, target, None)
    return target


def check_reset_password(actor: dict, users: list[dict], user_id: str,
                         password: Any) -> dict:
    """Setting someone else's password.

    Only the **owner** may, because that is Home Assistant's own rule: its
    `admin_change_password` command refuses anyone but the owner, and this
    panel does not hand an administrator a power Home Assistant withholds.
    Everyone changes their own password from their profile, where Home
    Assistant asks for the current one first.
    """
    check_actor(actor)
    target = _find(users, user_id)
    # Before the owner check, so the owner is pointed at their profile rather
    # than told their account is off limits.
    if target.get("id") == actor.get("id"):
        raise Refused("own_password", "Change your own password from your profile.")
    _changeable(target)
    if not actor.get("is_owner"):
        raise Refused("owner_only", "Only the home's owner can set someone else's password.")
    if not target.get("username"):
        raise Refused("no_login", "This person signs in another way and has no password here.")
    check_password(password, username=target.get("username") or "",
                   name=target.get("name") or "")
    return target


def clean_dashboard(url_path: Any, allowed: Iterable[str]) -> str | None:
    """A dashboard to show first, or None for Home Assistant's own default.
    Only dashboards the person could open anyway are offered."""
    if url_path in (None, ""):
        return None
    if not isinstance(url_path, str) or not URL_PATH_RE.match(url_path)             or url_path not in set(allowed):
        raise Refused("dashboard_unknown", "That dashboard is not on this home.")
    return url_path


def check_dashboard(actor: dict, users: list[dict], user_id: str, url_path: Any,
                    allowed: Iterable[str]) -> tuple[dict, str | None]:
    """Choosing the dashboard someone sees first. Your own is allowed: it is
    the same setting as the one on your profile page."""
    check_actor(actor)
    target = _find(users, user_id)
    if target.get("id") != actor.get("id"):
        _changeable(target)
    return target, clean_dashboard(url_path, allowed)


def clean_language(language: Any) -> str | None:
    """English, Arabic, or None for "follow their phone or browser"."""
    try:
        return user_prefs.clean_language(language)
    except user_prefs.Invalid as err:
        raise Refused("language_invalid", "Choose English or Arabic.") from err


def check_language(actor: dict, users: list[dict], user_id: str,
                   language: Any) -> tuple[dict, str | None]:
    """Choosing the language Home Assistant shows someone in. The same rule
    as the first dashboard: your own is allowed (it is the setting on your
    profile page), anyone else's only if they are a person here. Like the
    first dashboard, it changes how the screens look and nothing they can do.
    """
    check_actor(actor)
    target = _find(users, user_id)
    if target.get("id") != actor.get("id"):
        _changeable(target)
    return target, clean_language(language)


LANGUAGE_NAMES = {"en": "English", "ar": "Arabic"}


def _keep_an_admin(users: list[dict], before: dict, after: dict | None) -> None:
    """Refuse a change that would leave no active person who can manage the
    home. `after` is the account as it would be, or None when it is removed."""
    admins = _active_admins(users)
    if before.get("id") not in admins:
        return
    remaining = admins - {before.get("id")}
    if after is not None and _active_admins([after]):
        remaining.add(before.get("id"))
    if not remaining:
        raise Refused("last_admin", "Someone has to be able to manage the home. Give "
                                    "another person that role first.")


def summary(action: str, actor_name: str, target_name: str,
            detail: dict | None = None) -> str:
    """The logbook line, in the words the homeowner would use. English,
    because the logbook stores text rather than a key to translate."""
    detail = detail or {}
    who, whom = actor_name, target_name
    if action == "create":
        where = ", signing in only at home" if detail.get("local_only") else ""
        return f"{who} added {whom} as {ROLE_LABEL.get(detail.get('role'), 'Family')}{where}"
    if action == "remove":
        return f"{who} removed {whom} from the household"
    if action == "password":
        line = f"{who} set a new password for {whom}"
        return line + " and signed them out everywhere" if detail.get("signed_out") else line
    if action == "dashboard":
        title = detail.get("title")
        if title:
            return f"{who} set the first dashboard {whom} sees to '{title}'"
        return f"{who} set the first dashboard {whom} sees back to the home's usual one"
    if action == "language":
        name = LANGUAGE_NAMES.get(detail.get("language"))
        if name:
            return f"{who} set the language {whom} sees to {name}"
        return f"{who} set the language {whom} sees back to their phone's or browser's"
    parts = []
    if "name" in detail:
        parts.append(f"renamed {detail.get('old_name') or whom} to {detail['name']}")
    if "role" in detail:
        parts.append(f"made {whom} {ROLE_LABEL.get(detail['role'])}")
    if "local_only" in detail:
        parts.append(f"let {whom} sign in only at home" if detail["local_only"]
                     else f"let {whom} sign in from anywhere")
    if "is_active" in detail:
        parts.append(f"resumed {whom}" if detail["is_active"] else f"paused {whom}")
    return f"{who} " + (", and ".join(parts) if parts else f"updated {whom}")
