"""Home Assistant user management on the customer's home.

Creating a login is two HA operations — a user record, then a password
credential for it — and a half-done create leaves a user nobody can log in
as. So creation rolls back the user if the credential step fails.

Guardrails, because this is the most sensitive surface in the product:
- The owner account can never be deleted or deactivated from here. Locking
  an installer out of their own customer's home is unrecoverable remotely.
- Passwords are never logged, never echoed back, and never stored by the
  manager — they exist in the command payload and in HA's own credential
  store, nowhere else.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .ws_bridge import call_own_ws

_LOGGER = logging.getLogger(__name__)

GROUP_ADMIN = "system-admin"
GROUP_USER = "system-users"
GROUP_READONLY = "system-read-only"
VALID_GROUPS = {GROUP_ADMIN, GROUP_USER, GROUP_READONLY}

MIN_PASSWORD = 8


def _fail(msg: str) -> dict:
    return {"ok": False, "detail": msg}


def _error_text(result: dict, what: str) -> str:
    error = result.get("error") or {}
    return f"{what} failed: {error.get('message') or error.get('code') or 'unknown error'}"


async def users_list(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    result = await call_own_ws(hass, {"type": "config/auth/list"})
    if not result.get("success"):
        return _fail(_error_text(result, "user list"))
    users = []
    for user in result.get("result") or []:
        groups = [g["id"] if isinstance(g, dict) else g for g in (user.get("group_ids") or [])]
        users.append({
            "id": user.get("id"),
            "name": user.get("name"),
            "username": user.get("username"),
            "is_owner": user.get("is_owner", False),
            "is_active": user.get("is_active", True),
            "system_generated": user.get("system_generated", False),
            "local_only": user.get("local_only", False),
            "group_ids": groups,
            "role": ("owner" if user.get("is_owner") else
                     "admin" if GROUP_ADMIN in groups else
                     "read-only" if GROUP_READONLY in groups else "user"),
        })
    # System-generated accounts (Supervisor, cloud) are HA's plumbing, not
    # people — showing them invites someone to "tidy up" and break the home.
    return {"ok": True, "users": [u for u in users if not u["system_generated"]],
            "detail": f"{len(users)} account(s)"}


async def user_create(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    name = (cmd.get("name") or "").strip()
    username = (cmd.get("username") or "").strip().lower()
    password = cmd.get("password") or ""
    group = cmd.get("group") or GROUP_USER
    if not name or not username:
        return _fail("name and username required")
    if len(password) < MIN_PASSWORD:
        return _fail(f"password must be at least {MIN_PASSWORD} characters")
    if group not in VALID_GROUPS:
        return _fail(f"group must be one of {sorted(VALID_GROUPS)}")

    created = await call_own_ws(hass, {"type": "config/auth/create", "name": name,
                                       "group_ids": [group],
                                       "local_only": bool(cmd.get("local_only", False))})
    if not created.get("success"):
        return _fail(_error_text(created, "user create"))
    user_id = ((created.get("result") or {}).get("user") or {}).get("id")
    if not user_id:
        return _fail("HA did not return a user id")

    cred = await call_own_ws(hass, {"type": "config/auth_provider/homeassistant/create",
                                    "user_id": user_id, "username": username,
                                    "password": password})
    if not cred.get("success"):
        # Roll back: a user with no credential can never log in and would just
        # sit in the customer's account list confusing everyone.
        await call_own_ws(hass, {"type": "config/auth/delete", "user_id": user_id})
        return _fail(_error_text(cred, "login credential") + " (user rolled back)")

    return {"ok": True, "user_id": user_id,
            "detail": f"created {group.replace('system-', '')} account '{username}' for {name}"}


async def user_update(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    user_id = cmd.get("user_id")
    if not user_id:
        return _fail("user_id required")

    listing = await call_own_ws(hass, {"type": "config/auth/list"})
    target = next((u for u in (listing.get("result") or []) if u.get("id") == user_id), None)
    if target is None:
        return _fail("user not found")
    if target.get("is_owner") and (cmd.get("is_active") is False or cmd.get("group")):
        return _fail("the owner account cannot be deactivated or demoted remotely")

    payload: dict[str, Any] = {"type": "config/auth/update", "user_id": user_id}
    if cmd.get("name"):
        payload["name"] = cmd["name"].strip()
    if cmd.get("group"):
        if cmd["group"] not in VALID_GROUPS:
            return _fail(f"group must be one of {sorted(VALID_GROUPS)}")
        payload["group_ids"] = [cmd["group"]]
    if "is_active" in cmd:
        payload["is_active"] = bool(cmd["is_active"])
    if "local_only" in cmd:
        payload["local_only"] = bool(cmd["local_only"])

    if len(payload) == 2:
        return _fail("nothing to change")
    result = await call_own_ws(hass, payload)
    if not result.get("success"):
        return _fail(_error_text(result, "user update"))
    return {"ok": True, "detail": f"updated account '{target.get('name')}'"}


async def user_set_password(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    user_id = cmd.get("user_id")
    password = cmd.get("password") or ""
    if not user_id:
        return _fail("user_id required")
    if len(password) < MIN_PASSWORD:
        return _fail(f"password must be at least {MIN_PASSWORD} characters")
    result = await call_own_ws(hass, {
        "type": "config/auth_provider/homeassistant/admin_change_password",
        "user_id": user_id, "password": password})
    if not result.get("success"):
        return _fail(_error_text(result, "password change"))
    return {"ok": True, "detail": "password changed"}


async def user_delete(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    user_id = cmd.get("user_id")
    if not user_id:
        return _fail("user_id required")

    listing = await call_own_ws(hass, {"type": "config/auth/list"})
    target = next((u for u in (listing.get("result") or []) if u.get("id") == user_id), None)
    if target is None:
        return _fail("user not found")
    if target.get("is_owner"):
        return _fail("the owner account cannot be deleted remotely")
    if target.get("system_generated"):
        return _fail("system accounts belong to Home Assistant and cannot be deleted")

    result = await call_own_ws(hass, {"type": "config/auth/delete", "user_id": user_id})
    if not result.get("success"):
        return _fail(_error_text(result, "user delete"))
    return {"ok": True, "detail": f"deleted account '{target.get('name')}'"}


HANDLERS = {
    "users_list": users_list,
    "user_create": user_create,
    "user_update": user_update,
    "user_set_password": user_set_password,
    "user_delete": user_delete,
}
