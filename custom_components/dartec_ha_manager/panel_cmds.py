"""Room panel accounts: the manager's four commands.

The rules (who is a panel account, what a panel's sidebar hides, what a
status row says) live in `panels.py`, without Home Assistant; read its
docstring first, in particular the part about this **not** being a security
boundary. This module applies them to Home Assistant.

* `panel_setup` creates the panel account, or updates the one with that
  username: a non-administrator (`system-users`), `local_only`, with a Home
  Assistant login, its first dashboard set to the room and every other
  sidebar entry hidden. **Sensitive**: it creates standing access to the
  home, so it needs the home's consent like `user_create`
  (`service_policy.SENSITIVE_ACTIONS`).
* `panel_update` points a panel at another room, or renames it; `panel_remove`
  deletes one; `panel_status` reports them. **Routine**, and only ever for a
  panel account; see `service_policy.py` for why.
* Setup and update also take an optional `language` (`en` / `ar`) and
  `theme` (a theme loaded on the home), written to the panel account's own
  `language` and `theme` user data, as the profile page would
  (`user_prefs.py`; dartec-ha-manager#49). Left out, each is left as it is;
  `null` clears it, so the panel follows the browser or the system default.

**The APIs.** The account itself goes through Home Assistant's own
websocket commands over the loopback (`config/auth/*` and
`config/auth_provider/homeassistant/*`), as `user_cmds.py` does, so Home
Assistant applies its own checks and events. The frontend settings go
through the frontend's per-user store in process, as My Home's first
dashboard does (`household_ws.set_first_dashboard`): that is what
`frontend/set_user_data` writes when the person does it themselves, and
there is no command to write another user's. Reading users and their
sign-ins is in process too (`hass.auth`), because `config/auth/list` does
not say when a login was last used.

**The password** exists in the command, in Home Assistant's credential
store, and nowhere else: it is never logged, never in an answer, never in a
logbook line, and an error Home Assistant returns is scrubbed of it before
it is relayed. Failures are reported by type, not message, for the same
reason.

`tests/live/run_live_panels.py` checks all of it inside real Home Assistant,
signing in as the panel account itself.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import panels as rules
from . import user_prefs as prefs
from .ws_bridge import call_own_ws

_LOGGER = logging.getLogger(__name__)


def _refused(err: rules.Refused) -> dict:
    return {"ok": False, "code": err.code, "detail": err.message}


def _fail(detail: str, code: str = "failed") -> dict:
    return {"ok": False, "code": code, "detail": detail}


def _ws_error(result: dict, what: str, password: Any = None) -> str:
    error = result.get("error") or {}
    text = error.get("message") or error.get("code") or "unknown error"
    return rules.scrub(f"{what} failed: {text}", password)


# ── Reading Home Assistant ──────────────────────────────────────────────────

async def _users(hass: HomeAssistant) -> list[dict]:
    from .household_ws import user_dict

    return [user_dict(u) for u in await hass.auth.async_get_users()]


def _panel_settings(hass: HomeAssistant) -> dict[str, dict]:
    """Every registered panel, with `require_admin` as it is in effect.

    An administrator can override a panel's `require_admin` and
    `show_in_sidebar` from the sidebar settings; Home Assistant keeps those
    beside the panel (`DATA_PANELS_CONFIG`, in 2026.8 and 2026.9) and applies
    them when it answers `get_panels`. So does this, and where that key does
    not exist it falls back to the panel's own setting."""
    from homeassistant.components import frontend

    key = getattr(frontend, "DATA_PANELS_CONFIG", None)
    overrides = (hass.data.get(key) if key is not None else None) or {}
    out = {}
    for url_path, panel in (hass.data.get(frontend.DATA_PANELS) or {}).items():
        override = overrides.get(url_path) or {}
        out[url_path] = {"require_admin": bool(override.get("require_admin",
                                                             panel.require_admin))}
    return out


def _dashboards(hass: HomeAssistant, panels: dict[str, dict]) -> dict[str, dict]:
    """The home's own Lovelace dashboards (not the built-in default one,
    which has no URL path of its own), each with its panel's settings."""
    lovelace = hass.data.get("lovelace")
    boards = getattr(lovelace, "dashboards", None) or {}
    return {url_path: panels[url_path] for url_path in boards
            if url_path and url_path in panels}


async def _default_panel(hass: HomeAssistant, user_id: str) -> str | None:
    from .household_ws import first_dashboard

    return await first_dashboard(hass, user_id)


def _themes(hass: HomeAssistant) -> list[str]:
    """The themes loaded on this home, as `frontend/get_themes` lists them."""
    from homeassistant.components import frontend

    return list(hass.data.get(getattr(frontend, "DATA_THEMES", "frontend_themes")) or {})


def _clean_prefs(hass: HomeAssistant, cmd: dict[str, Any]) -> dict[str, str | None]:
    """The language and theme the command asks for: only the keys it sends,
    `None` for one it clears. Refuses before anything is changed."""
    out: dict[str, str | None] = {}
    try:
        if "language" in cmd:
            out["language"] = prefs.clean_language(cmd["language"])
        if "theme" in cmd:
            out["theme"] = prefs.clean_theme(cmd["theme"], _themes(hass))
    except prefs.Invalid as err:
        raise rules.Refused("no_theme" if err.code == "no_theme" else "invalid",
                            err.message) from err
    return out


async def _write_prefs(hass: HomeAssistant, user_id: str,
                       wanted: dict[str, str | None]) -> dict[str, str | None]:
    """Apply `wanted` to the account's own frontend data, and return its
    language and theme as they now are."""
    from .household_ws import _user_store

    store = await _user_store(hass, user_id)
    if "language" in wanted:
        await store.async_set_item(
            "language", prefs.language_value(store.data.get("language"), wanted["language"]))
    if "theme" in wanted:
        await store.async_set_item(
            "theme", prefs.theme_value(store.data.get("theme"), wanted["theme"]))
    return {"language": prefs.language_of(store.data.get("language")),
            "theme": prefs.theme_of(store.data.get("theme"))}


async def _read_prefs(hass: HomeAssistant, user_id: str) -> dict[str, str | None]:
    return await _write_prefs(hass, user_id, {})


def _prefs_text(applied: dict[str, str | None], wanted: dict) -> str:
    parts = []
    if "language" in wanted:
        parts.append(f"language {applied['language'] or 'as the browser'}")
    if "theme" in wanted:
        parts.append(f"theme {applied['theme'] or 'the system default'}")
    return (", " + ", ".join(parts)) if parts else ""


async def _write_frontend(hass: HomeAssistant, user_id: str, url_path: str,
                          panels: dict[str, dict]) -> int:
    """The room as the first dashboard, and every other entry hidden. Returns
    how many were hidden."""
    from .household_ws import _user_store

    store = await _user_store(hass, user_id)
    await store.async_set_item("core", rules.core_value(store.data.get("core"), url_path))
    sidebar = rules.sidebar_value(panels, url_path)
    await store.async_set_item("sidebar", sidebar)
    return len(sidebar["hiddenPanels"])


async def panel_rows(hass: HomeAssistant, user_id: str | None = None) -> list[dict]:
    """Every panel account, as `panel_status` and the snapshot report them."""
    from .household_ws import user_dict

    rows = []
    for user in await hass.auth.async_get_users():
        described = user_dict(user)
        if not rules.is_panel_account(described):
            continue
        if user_id is not None and described["id"] != user_id:
            continue
        tokens = [{"token_type": t.token_type, "last_used_at": t.last_used_at}
                  for t in user.refresh_tokens.values()]
        own = await _read_prefs(hass, described["id"])
        rows.append(rules.status_row(described, tokens,
                                     await _default_panel(hass, described["id"]),
                                     own["language"], own["theme"]))
    return sorted(rows, key=lambda r: r["username"] or "")


# ── Commands ────────────────────────────────────────────────────────────────

async def panel_setup(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    password = cmd.get("password")
    try:
        name = rules.clean_name(cmd.get("name"))
        username = rules.clean_username(cmd.get("username"))
        rules.check_password(password)
        url_path = rules.clean_url_path(cmd.get("url_path"))
        panels = _panel_settings(hass)
        rules.check_dashboard(url_path, _dashboards(hass, panels))
        wanted = _clean_prefs(hass, cmd)
        existing = rules.plan_setup(await _users(hass), username)
    except rules.Refused as err:
        return _refused(err)

    try:
        if existing is None:
            user_id = await _create(hass, name, username, password)
            if isinstance(user_id, dict):
                return user_id
            created = True
        else:
            user_id = existing["id"]
            refused = await _refresh(hass, user_id, name, password)
            if refused:
                return refused
            created = False
        hidden = await _write_frontend(hass, user_id, url_path, panels)
        applied = await _write_prefs(hass, user_id, wanted)
    except Exception as err:  # noqa: BLE001 - by type only: never risk echoing the password
        _LOGGER.warning("panel_setup for %s failed: %s", username, type(err).__name__)
        return _fail(f"panel setup failed ({type(err).__name__})")

    verb = "created" if created else "updated"
    return {"ok": True, "user_id": user_id, "username": username, "created": created,
            "url_path": url_path, "hidden_panels": hidden, **applied,
            "detail": f"{verb} panel account '{username}' ({name}), first dashboard "
                      f"'{url_path}', {hidden} other sidebar entries hidden"
                      + _prefs_text(applied, wanted)}


async def _create(hass: HomeAssistant, name: str, username: str, password: str):
    """A new panel account and its login, or a failure answer. A user left
    without a login could never sign in, so it is taken back out if the
    login cannot be made, as `user_create` does."""
    made = await call_own_ws(hass, {"type": "config/auth/create", "name": name,
                                    "group_ids": [rules.GROUP_USER], "local_only": True})
    if not made.get("success"):
        return _fail(_ws_error(made, "user create", password))
    user_id = ((made.get("result") or {}).get("user") or {}).get("id")
    if not user_id:
        return _fail("Home Assistant did not return a user id")
    login = await call_own_ws(hass, {"type": "config/auth_provider/homeassistant/create",
                                     "user_id": user_id, "username": username,
                                     "password": password})
    if not login.get("success"):
        await call_own_ws(hass, {"type": "config/auth/delete", "user_id": user_id})
        # A login left behind by an account deleted outside Home Assistant's
        # own screens holds the username without any user: still taken.
        # Home Assistant answers that with a general error whose message says
        # the username already exists.
        error = login.get("error") or {}
        taken = "exist" in f"{error.get('code')} {error.get('message')}".casefold()
        code = "username_taken" if taken else "failed"
        return _fail(_ws_error(login, "login credential", password) + " (user rolled back)",
                     code)
    return user_id


async def _refresh(hass: HomeAssistant, user_id: str, name: str, password: str):
    """Put an existing panel account back into its shape, with the new
    password. Returns a failure answer, or None.

    `admin_change_password` is the owner's command in Home Assistant, and the
    loopback token is the owner's (`ws_bridge`), so this works on any home
    with an active owner; on one without, Home Assistant refuses it and the
    answer says so."""
    updated = await call_own_ws(hass, {"type": "config/auth/update", "user_id": user_id,
                                       "name": name, "group_ids": [rules.GROUP_USER],
                                       "is_active": True, "local_only": True})
    if not updated.get("success"):
        return _fail(_ws_error(updated, "user update", password))
    changed = await call_own_ws(hass, {
        "type": "config/auth_provider/homeassistant/admin_change_password",
        "user_id": user_id, "password": password})
    if not changed.get("success"):
        return _fail(_ws_error(changed, "password change", password))
    return None


async def panel_update(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Another room, another name, language or theme. With none, the panel's
    sidebar is worked out again, which is how a dashboard added to the home
    after the panel was set up gets hidden from it too."""
    try:
        target = rules.find_panel(await _users(hass), cmd.get("user_id"))
        if target is None:
            return _fail("panel account not found", "not_found")
        name = rules.clean_name(cmd["name"]) if cmd.get("name") is not None else None
        panels = _panel_settings(hass)
        url_path = cmd.get("url_path")
        if url_path is None:
            url_path = await _default_panel(hass, target["id"])
        url_path = rules.clean_url_path(url_path)
        rules.check_dashboard(url_path, _dashboards(hass, panels))
        wanted = _clean_prefs(hass, cmd)
    except rules.Refused as err:
        return _refused(err)

    if name and name != target.get("name"):
        result = await call_own_ws(hass, {"type": "config/auth/update",
                                          "user_id": target["id"], "name": name})
        if not result.get("success"):
            return _fail(_ws_error(result, "rename"))
    hidden = await _write_frontend(hass, target["id"], url_path, panels)
    applied = await _write_prefs(hass, target["id"], wanted)
    return {"ok": True, "hidden_panels": hidden, "url_path": url_path, **applied,
            "detail": f"set panel account '{target.get('username')}' to dashboard "
                      f"'{url_path}'" + (f", named {name}" if name else "")
                      + _prefs_text(applied, wanted)}


async def panel_remove(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    try:
        target = rules.find_panel(await _users(hass), cmd.get("user_id"))
    except rules.Refused as err:
        return _refused(err)
    if target is None:
        # Removing what is already gone is done: the manager may be retrying
        # a removal whose answer it never received.
        return {"ok": True, "gone": True, "detail": "already gone"}
    result = await call_own_ws(hass, {"type": "config/auth/delete", "user_id": target["id"]})
    if not result.get("success"):
        return _fail(_ws_error(result, "delete"))
    return {"ok": True, "detail": f"deleted panel account '{target.get('username')}'"}


async def panel_status(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    user_id = cmd.get("user_id")
    return {"ok": True, "panels": await panel_rows(hass, str(user_id) if user_id else None)}


HANDLERS = {
    "panel_setup": panel_setup,
    "panel_update": panel_update,
    "panel_remove": panel_remove,
    "panel_status": panel_status,
}
