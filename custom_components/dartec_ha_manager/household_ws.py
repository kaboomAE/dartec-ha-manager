""""My Home": the household panel's registration and its websocket commands.

The rules live in `household.py`; this module applies them to Home
Assistant. Read that module's docstring first for why the panel exists in the
home rather than in Dartec's cloud.

**Who can call these.** Every command is `require_admin`, checked by Home
Assistant before any of this runs, so only a signed-in administrator's own
session reaches them, and each change is made in their name. The panel itself
is registered `require_admin` too, so a regular user never sees it in the
sidebar. Two more refusals are added here:

* the agent's own loopback credential (`ws_bridge`, a five-minute owner token
  named "Dartec task ...") is refused outright. Nothing in the agent calls
  these commands, and this keeps it that way: if a future change ever routed
  a cloud command through the loopback, it would reach Home Assistant's own
  user commands, which are behind the consent gate, and never these;
* Dartec's support account is refused by the rules (`check_actor`).

**The APIs.** Each change is the same Home Assistant call its own user
screens make, done in-process as the signed-in administrator rather than over
a second connection: `hass.auth` for the user (what `config/auth/*` calls),
the `homeassistant` auth provider for the login (what
`config/auth_provider/homeassistant/*` calls), the person collection for the
link that makes presence work (what `person/*` calls), and the frontend's
per-user store for the first dashboard (what `frontend/set_user_data` writes
for the signed-in user; here it is written for the person being set up).
`tests/live/run_live_household.py` checks every one of them against Home
Assistant 2026.8.3 and 2026.9.2 through Home Assistant's own commands.

**The record.** Every change is written to the home's logbook as "done by"
the person who did it, with their user attached as the entry's context, and
kept in a short local activity list the panel shows under "Recent changes".
Neither leaves the house.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import household as rules
from . import user_prefs
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "dartec-household"
PANEL_ELEMENT = "dartec-household-panel"
STATIC_URL = "/dartec_household"
PANEL_ICON = "mdi:home-account"
# The sidebar title is one string for everyone, so it follows the home's own
# language. Everything inside the panel follows each person's.
PANEL_TITLES = {"ar": "بيتي"}
PANEL_TITLE_DEFAULT = "My Home"

STORE_KEY = f"{DOMAIN}.household"
STORE_VERSION = 1
ACTIVITY_KEEP = 100
ACTIVITY_SHOWN = 20
# What ws_bridge names the token the agent mints for itself.
LOOPBACK_CLIENT_PREFIX = "Dartec task"
# Dashboards a person can be sent to first: the home's own dashboards and
# Home Assistant's built-in home page. Not the map, energy or settings pages.
DASHBOARD_COMPONENTS = ("lovelace", "home")
DARTEC_DASHBOARD_PREFIX = "dartec-"
# One room's dashboard, made for that room's wall panel (panels.py). Hidden
# from the sidebar already; left out here even if someone shows it there.
ROOM_DASHBOARD_PREFIX = "dartec-room-"

_DATA = "_household"
_WS_REGISTERED = "_household_ws_registered"
_STATIC_REGISTERED = "_household_static_registered"


class HouseholdStore:
    """Who is a guest, and the recent changes. One small file in `.storage`."""

    def __init__(self, hass: HomeAssistant) -> None:
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, STORE_VERSION, STORE_KEY)
        self.guests: set[str] = set()
        self.activity: list[dict] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.guests = set(data.get("guests") or [])
        self.activity = list(data.get("activity") or [])[-ACTIVITY_KEEP:]

    async def async_save(self) -> None:
        await self._store.async_save({"guests": sorted(self.guests),
                                      "activity": self.activity[-ACTIVITY_KEEP:]})


def _data(hass: HomeAssistant) -> HouseholdStore | None:
    return hass.data.get(DOMAIN, {}).get(_DATA)


async def async_setup(hass: HomeAssistant) -> None:
    """Register the panel, its files and its commands. Safe to call again:
    each part is registered once per Home Assistant run."""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get(_DATA) is None:
        data = HouseholdStore(hass)
        await data.async_load()
        store[_DATA] = data

    www = Path(__file__).parent / "www" / "household"
    if not store.get(_STATIC_REGISTERED):
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(www), True)])
        store[_STATIC_REGISTERED] = True

    if not store.get(_WS_REGISTERED):
        for command in (ws_list, ws_create, ws_update, ws_set_password, ws_remove,
                        ws_set_dashboard, ws_set_language):
            websocket_api.async_register_command(hass, command)
        store[_WS_REGISTERED] = True

    # A content hash as the cache buster, so a new agent version reaches
    # browsers and the Companion app without anyone clearing a cache.
    stamp = await hass.async_add_executor_job(_stamp, www)
    from homeassistant.components.frontend import async_register_built_in_panel

    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLES.get((hass.config.language or "")[:2], PANEL_TITLE_DEFAULT),
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        require_admin=True,
        # The shape panel_custom gives a custom panel: the element's own
        # settings beside `_panel_custom`. The panel fetches its strings with
        # the same stamp.
        config={"stamp": stamp, "static": STATIC_URL,
                "_panel_custom": {
                    "name": PANEL_ELEMENT,
                    "module_url": f"{STATIC_URL}/household-panel.js?v={stamp}",
                    "embed_iframe": False,
                    "trust_external": False,
                }},
        update=True,
    )


@callback
def async_unload(hass: HomeAssistant) -> None:
    """Take the panel out of the sidebar. The commands stay registered until
    Home Assistant restarts (it has no way to unregister one), and refuse to
    run while the integration is not loaded."""
    from homeassistant.components.frontend import async_remove_panel

    try:
        async_remove_panel(hass, PANEL_URL_PATH)
    except Exception as err:  # noqa: BLE001 - already gone is fine
        _LOGGER.debug("household panel removal: %s", err)


def _stamp(www: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(www.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


# ── Reading Home Assistant ──────────────────────────────────────────────────

def user_dict(user) -> dict:
    """A Home Assistant user as `config/auth/list` describes it."""
    username = next((cred.data.get("username") for cred in user.credentials
                     if cred.auth_provider_type == "homeassistant"), None)
    return {
        "id": user.id,
        "name": user.name,
        "username": username,
        "is_owner": user.is_owner,
        "is_active": user.is_active,
        "local_only": user.local_only,
        "system_generated": user.system_generated,
        "group_ids": [group.id for group in user.groups],
    }


async def _users(hass: HomeAssistant) -> list[dict]:
    return [user_dict(u) for u in await hass.auth.async_get_users()]


def _person_collection(hass: HomeAssistant):
    """Home Assistant's storage collection of people, or None when the person
    integration is not loaded. `hass.data["person"]` has been
    (yaml, storage, entities) since 2021; checked by the live test."""
    data = hass.data.get("person")
    if isinstance(data, tuple) and len(data) >= 2:
        return data[1]
    return None


def _persons_of(hass: HomeAssistant, user_id: str) -> list[dict]:
    coll = _person_collection(hass)
    if coll is None:
        return []
    return [p for p in coll.async_items() if p.get("user_id") == user_id]


def dashboards(hass: HomeAssistant) -> list[dict]:
    """Every dashboard a regular user can open, as the sidebar knows it."""
    from homeassistant.components.frontend import DATA_PANELS

    out = []
    for url_path, panel in (hass.data.get(DATA_PANELS) or {}).items():
        if panel.component_name not in DASHBOARD_COMPONENTS or panel.require_admin:
            continue
        # What the sidebar shows is what the person will recognise. In 2026.9
        # the built-in "home" page is hidden there, and "Your home's usual
        # start page" in the panel already stands for it.
        if not getattr(panel, "show_in_sidebar", True):
            continue
        if url_path.startswith(ROOM_DASHBOARD_PREFIX):
            continue
        out.append({"url_path": url_path,
                    "title": panel.sidebar_title,
                    "icon": panel.sidebar_icon,
                    "builtin": panel.component_name == "home" or url_path == "lovelace",
                    "from_dartec": url_path.startswith(DARTEC_DASHBOARD_PREFIX)})
    return sorted(out, key=lambda d: (not d["builtin"], str(d["title"] or d["url_path"]).casefold()))


async def _user_store(hass: HomeAssistant, user_id: str):
    from homeassistant.components.frontend.storage import async_user_store

    return await async_user_store(hass, user_id)


async def first_dashboard(hass: HomeAssistant, user_id: str) -> str | None:
    """The dashboard someone sees first, from their own frontend settings:
    `core.default_panel`, which is what the profile page's "Dashboard" picker
    writes in Home Assistant 2026.8 and 2026.9. Unset means the home's
    default."""
    store = await _user_store(hass, user_id)
    core = store.data.get("core")
    return core.get("default_panel") if isinstance(core, dict) else None


async def set_first_dashboard(hass: HomeAssistant, user_id: str,
                              url_path: str | None) -> None:
    store = await _user_store(hass, user_id)
    core = dict(store.data.get("core") or {})
    if url_path:
        core["default_panel"] = url_path
    else:
        core.pop("default_panel", None)
    # async_set_item also tells any of their open sessions, which pick the
    # change up the way they would from their own profile page.
    await store.async_set_item("core", core)


async def person_language(hass: HomeAssistant, user_id: str) -> str | None:
    """The language Home Assistant shows someone in, from their own frontend
    settings (`user_prefs.py`), or None when it follows their browser."""
    store = await _user_store(hass, user_id)
    return user_prefs.language_of(store.data.get("language"))


async def set_language(hass: HomeAssistant, user_id: str, language: str | None) -> None:
    """Write someone's language as their profile page would, keeping the
    number, time and date formats they chose. Their open sessions switch
    straight away (async_set_item tells them), right to left for Arabic."""
    store = await _user_store(hass, user_id)
    await store.async_set_item(
        "language", user_prefs.language_value(store.data.get("language"), language))


def household_counts(hass: HomeAssistant, users: list[dict]) -> dict[str, int]:
    """For the snapshot: numbers only."""
    data = _data(hass)
    return rules.counts(users, data.guests if data else ())


def _forget_departed(data: HouseholdStore, users: list[dict]) -> None:
    """Someone removed in Home Assistant's own settings stops being a guest
    here too. Saved with the next change; a stale id is harmless meanwhile."""
    data.guests &= {u["id"] for u in users}


# ── Answering ───────────────────────────────────────────────────────────────

def _refuse(connection, msg: dict, code: str, message: str) -> None:
    connection.send_error(msg["id"], code, message)


def _gate(hass: HomeAssistant, connection, msg: dict) -> HouseholdStore | None:
    """Refusals that come before any rule: the integration not loaded, and
    the agent's own loopback credential."""
    data = _data(hass)
    from homeassistant.config_entries import ConfigEntryState

    loaded = any(entry.state is ConfigEntryState.LOADED
                 for entry in hass.config_entries.async_entries(DOMAIN))
    if data is None or not loaded:
        _refuse(connection, msg, "not_loaded", "The Dartec integration is not running.")
        return None
    token = hass.auth.async_get_refresh_token(connection.refresh_token_id) \
        if connection.refresh_token_id else None
    if token is not None and (token.client_name or "").startswith(LOOPBACK_CLIENT_PREFIX):
        _refuse(connection, msg, "loopback",
                "The household can only be managed by someone signed in to this home.")
        return None
    return data


def _actor_name(actor: dict) -> str:
    return actor.get("name") or actor.get("username") or "Someone"


async def _record(hass: HomeAssistant, connection, msg: dict, data: HouseholdStore,
                  actor: dict, action: str, target: dict, detail: dict | None = None) -> None:
    """The logbook line and the activity entry. Never a password."""
    detail = {k: v for k, v in (detail or {}).items() if k != "password"}
    line = rules.summary(action, _actor_name(actor), target.get("name") or "", detail)
    try:
        hass.bus.async_fire("logbook_entry", {"name": "My Home", "message": line,
                                              "domain": DOMAIN},
                            context=connection.context(msg))
    except Exception as err:  # noqa: BLE001 - auditing never breaks the change
        _LOGGER.debug("household logbook entry failed: %s", err)
    _LOGGER.info("My Home: %s", line)
    data.activity.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "by": _actor_name(actor), "by_id": actor.get("id"),
                          "action": action, "target": target.get("name"),
                          "detail": detail})
    await data.async_save()


async def _answer(hass: HomeAssistant, connection, msg: dict, data: HouseholdStore) -> None:
    """The whole panel's state, which every command answers with, so the
    panel never shows a list older than the change it just made."""
    actor = user_dict(connection.user)
    users = await _users(hass)
    _forget_departed(data, users)
    people = []
    for person in rules.household(users, data.guests):
        people.append({
            "id": person["id"], "name": person["name"], "username": person["username"],
            "role": person["role"], "kind": person["kind"],
            "is_active": person["is_active"], "local_only": person["local_only"],
            "is_me": person["id"] == actor["id"],
            "has_login": bool(person["username"]),
            "dashboard": await first_dashboard(hass, person["id"]),
            "language": await person_language(hass, person["id"]),
            "linked_person": bool(_persons_of(hass, person["id"])),
        })
    connection.send_result(msg["id"], {
        "me": {"id": actor["id"], "name": actor["name"], "is_owner": actor["is_owner"],
               "can_manage": rules.kind(actor) != rules.KIND_MAINTENANCE},
        "people": people,
        "dashboards": dashboards(hass),
        "maintenance_account": any(rules.kind(u) == rules.KIND_MAINTENANCE for u in users),
        "panel_accounts": rules.panel_count(users),
        "activity": list(reversed(data.activity[-ACTIVITY_SHOWN:])),
        "limits": {"min_password": rules.MIN_PASSWORD},
    })


def _refused(connection, msg: dict, err: rules.Refused) -> None:
    connection.send_error(msg["id"], err.code, err.message)


# ── Commands ────────────────────────────────────────────────────────────────

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/household/list"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_list(hass: HomeAssistant, connection, msg: dict) -> None:
    data = _gate(hass, connection, msg)
    if data is not None:
        await _answer(hass, connection, msg, data)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/household/create",
    vol.Required("name"): str,
    vol.Required("username"): str,
    vol.Required("password"): str,
    vol.Required("role"): str,
    vol.Optional("local_only", default=False): bool,
    vol.Optional("dashboard"): vol.Any(None, str),
    vol.Optional("language"): vol.Any(None, str),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_create(hass: HomeAssistant, connection, msg: dict) -> None:
    data = _gate(hass, connection, msg)
    if data is None:
        return
    actor = user_dict(connection.user)
    users = await _users(hass)
    try:
        clean = rules.check_create(actor, users, msg)
        first = rules.clean_dashboard(msg.get("dashboard"),
                                      [d["url_path"] for d in dashboards(hass)])
    except rules.Refused as err:
        _refused(connection, msg, err)
        return

    from homeassistant.auth.providers import homeassistant as auth_ha

    user = await hass.auth.async_create_user(
        clean["name"], group_ids=[rules.ROLE_GROUP[clean["role"]]],
        local_only=clean["local_only"])
    provider = auth_ha.async_get_provider(hass)
    added_auth = False
    try:
        await provider.async_add_auth(clean["username"], clean["password"])
        added_auth = True
        credentials = await provider.async_get_or_create_credentials(
            {"username": clean["username"]})
        await hass.auth.async_link_user(user, credentials)
    except Exception as err:  # noqa: BLE001 - roll back, then say why
        # A user with no login can never sign in, and would sit in the list
        # confusing everyone. Take both halves back out.
        if added_auth:
            try:
                await provider.async_remove_auth(clean["username"])
            except Exception:  # noqa: BLE001
                pass
        await hass.auth.async_remove_user(user)
        taken = isinstance(err, auth_ha.InvalidUser)
        _refuse(connection, msg, "username_taken" if taken else "create_failed",
                "Someone on this home already has that username." if taken
                else f"Home Assistant could not add them: {err}")
        return

    if clean["role"] == rules.ROLE_GUEST:
        data.guests.add(user.id)
    linked = await _link_person(hass, user.id, clean["name"])
    if first:
        await set_first_dashboard(hass, user.id, first)
    if clean["language"]:
        await set_language(hass, user.id, clean["language"])
    await _record(hass, connection, msg, data, actor, "create",
                  {"name": clean["name"]},
                  {"role": clean["role"], "local_only": clean["local_only"],
                   "person_linked": linked, "language": clean["language"]})
    await _answer(hass, connection, msg, data)


async def _link_person(hass: HomeAssistant, user_id: str, name: str) -> bool:
    """Give the new user a person, which is what presence hangs off. If the
    installer already made a person with this exact name and no login, link
    that one rather than making a second."""
    coll = _person_collection(hass)
    if coll is None:
        return False
    try:
        spare = [p for p in coll.async_items()
                 if not p.get("user_id") and (p.get("name") or "").casefold() == name.casefold()]
        if len(spare) == 1:
            await coll.async_update_item(spare[0]["id"], {"user_id": user_id})
        else:
            from homeassistant.components.person import async_create_person

            await async_create_person(hass, name, user_id=user_id)
        return True
    except Exception as err:  # noqa: BLE001 - the login works without it
        _LOGGER.warning("Could not link a person to the new user: %s", err)
        return False


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/household/update",
    vol.Required("user_id"): str,
    vol.Optional("name"): str,
    vol.Optional("role"): str,
    vol.Optional("local_only"): bool,
    vol.Optional("is_active"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_update(hass: HomeAssistant, connection, msg: dict) -> None:
    data = _gate(hass, connection, msg)
    if data is None:
        return
    actor = user_dict(connection.user)
    users = await _users(hass)
    changes = {k: msg[k] for k in ("name", "role", "local_only", "is_active") if k in msg}
    try:
        clean = rules.check_update(actor, users, msg["user_id"], changes, data.guests)
    except rules.Refused as err:
        _refused(connection, msg, err)
        return

    user = await hass.auth.async_get_user(msg["user_id"])
    before = user_dict(user)
    kwargs: dict[str, Any] = {}
    if "name" in clean:
        kwargs["name"] = clean["name"]
    if "role" in clean:
        kwargs["group_ids"] = [rules.ROLE_GROUP[clean["role"]]]
    if "local_only" in clean:
        kwargs["local_only"] = clean["local_only"]
    if "is_active" in clean:
        # Pausing also signs them out everywhere: Home Assistant removes a
        # deactivated user's sessions itself.
        kwargs["is_active"] = clean["is_active"]
    await hass.auth.async_update_user(user, **kwargs)

    if "role" in clean:
        if clean["role"] == rules.ROLE_GUEST:
            data.guests.add(user.id)
        else:
            data.guests.discard(user.id)
    if "name" in clean:
        coll = _person_collection(hass)
        for person in _persons_of(hass, user.id):
            # Only a person still called what the user was called: someone
            # who named the person differently on purpose keeps their name.
            if coll is not None and person.get("name") == before["name"]:
                await coll.async_update_item(person["id"], {"name": clean["name"]})
    await _record(hass, connection, msg, data, actor, "update",
                  {"name": clean.get("name", before["name"])},
                  {**clean, "old_name": before["name"]} if "name" in clean else clean)
    await _answer(hass, connection, msg, data)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/household/set_password",
    vol.Required("user_id"): str,
    vol.Required("password"): str,
    vol.Optional("sign_out", default=False): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_password(hass: HomeAssistant, connection, msg: dict) -> None:
    data = _gate(hass, connection, msg)
    if data is None:
        return
    actor = user_dict(connection.user)
    users = await _users(hass)
    try:
        target = rules.check_reset_password(actor, users, msg["user_id"], msg["password"])
    except rules.Refused as err:
        _refused(connection, msg, err)
        return

    from homeassistant.auth.providers import homeassistant as auth_ha

    await auth_ha.async_get_provider(hass).async_change_password(
        target["username"], msg["password"])
    signed_out = False
    if msg.get("sign_out"):
        # Home Assistant keeps existing sessions on a password change. When
        # the reason for the new password is that the old one got out, they
        # should go too; the panel asks.
        user = await hass.auth.async_get_user(target["id"])
        for token in list(user.refresh_tokens.values()):
            hass.auth.async_remove_refresh_token(token)
        signed_out = True
    await _record(hass, connection, msg, data, actor, "password", target,
                  {"signed_out": signed_out})
    await _answer(hass, connection, msg, data)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/household/remove",
    vol.Required("user_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_remove(hass: HomeAssistant, connection, msg: dict) -> None:
    data = _gate(hass, connection, msg)
    if data is None:
        return
    actor = user_dict(connection.user)
    users = await _users(hass)
    try:
        target = rules.check_remove(actor, users, msg["user_id"])
    except rules.Refused as err:
        _refused(connection, msg, err)
        return

    # The person first: once the user is gone, Home Assistant unlinks it on
    # its own and there is no longer anything tying it to who was removed.
    coll = _person_collection(hass)
    for person in _persons_of(hass, target["id"]):
        try:
            await coll.async_delete_item(person["id"])
        except Exception as err:  # noqa: BLE001 - the login still goes
            _LOGGER.warning("Could not remove the person for a removed user: %s", err)
    user = await hass.auth.async_get_user(target["id"])
    await hass.auth.async_remove_user(user)
    data.guests.discard(target["id"])
    await _record(hass, connection, msg, data, actor, "remove", target)
    await _answer(hass, connection, msg, data)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/household/set_dashboard",
    vol.Required("user_id"): str,
    vol.Required("url_path"): vol.Any(None, str),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_dashboard(hass: HomeAssistant, connection, msg: dict) -> None:
    data = _gate(hass, connection, msg)
    if data is None:
        return
    actor = user_dict(connection.user)
    users = await _users(hass)
    boards = dashboards(hass)
    try:
        target, url_path = rules.check_dashboard(
            actor, users, msg["user_id"], msg["url_path"], [d["url_path"] for d in boards])
    except rules.Refused as err:
        _refused(connection, msg, err)
        return
    await set_first_dashboard(hass, target["id"], url_path)
    title = next((d["title"] or d["url_path"] for d in boards if d["url_path"] == url_path), None)
    await _record(hass, connection, msg, data, actor, "dashboard", target,
                  {"url_path": url_path, "title": title})
    await _answer(hass, connection, msg, data)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/household/set_language",
    vol.Required("user_id"): str,
    vol.Required("language"): vol.Any(None, str),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_language(hass: HomeAssistant, connection, msg: dict) -> None:
    """The language Home Assistant shows someone in: English, Arabic, or
    their phone's or browser's (dartec-ha-manager#49). Like the first
    dashboard, a convenience and not a security boundary: the person can
    change it on their own profile."""
    data = _gate(hass, connection, msg)
    if data is None:
        return
    actor = user_dict(connection.user)
    users = await _users(hass)
    try:
        target, language = rules.check_language(actor, users, msg["user_id"],
                                                 msg["language"])
    except rules.Refused as err:
        _refused(connection, msg, err)
        return
    await set_language(hass, target["id"], language)
    await _record(hass, connection, msg, data, actor, "language", target,
                  {"language": language})
    await _answer(hass, connection, msg, data)
