"""Call Home Assistant's own websocket API against this instance.

Some things can only be done correctly through the official websocket commands
— dashboard creation owns collection state the frontend also mutates, and HACS
keeps its own in-memory repository index. Reimplementing either in-process
risks desyncing them, so the agent connects back to itself over loopback and
issues the real command, exactly as the frontend would.

Auth: a short-lived owner token minted for the call and revoked immediately
after, so no long-lived credential is created or stored.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

TOKEN_TTL_MINUTES = 5


async def mint_owner_token(hass: HomeAssistant):
    """Public alias — other modules (backup upload) need a short-lived token
    for HA's own HTTP API and must remove the refresh token afterwards."""
    return await _mint_owner_token(hass)


def base_url(hass: HomeAssistant, ws: bool = False) -> str:
    return _base_url(hass, ws)


async def _mint_owner_token(hass: HomeAssistant):
    """(refresh_token, access_token) for a short-lived owner credential, or
    (None, error_message) when no owner/admin exists."""
    from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN

    users = await hass.auth.async_get_users()
    owner = next((u for u in users if u.is_owner and u.is_active), None)
    if owner is None:
        owner = next((u for u in users if u.is_active
                      and any(g.id == "system-admin" for g in u.groups)), None)
    if owner is None:
        return None, "no active owner/admin user found"

    refresh = await hass.auth.async_create_refresh_token(
        owner, client_name=f"Dartec task {uuid.uuid4().hex[:8]}",
        token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
        access_token_expiration=timedelta(minutes=TOKEN_TTL_MINUTES))
    return refresh, hass.auth.async_create_access_token(refresh)


def _base_url(hass: HomeAssistant, ws: bool) -> str:
    api = getattr(hass.config, "api", None)
    port = getattr(api, "port", None) or 8123
    if ws:
        scheme = "wss" if getattr(api, "use_ssl", False) else "ws"
    else:
        scheme = "https" if getattr(api, "use_ssl", False) else "http"
    return f"{scheme}://127.0.0.1:{port}"


async def call_own_rest(hass: HomeAssistant, method: str, path: str,
                        json_body: dict | None = None, timeout: int = 60) -> dict:
    """Call this instance's own REST API (some config APIs — automation
    storage among them — exist only over REST, not websocket)."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    refresh, access = await _mint_owner_token(hass)
    if refresh is None:
        return {"ok": False, "status": 0, "detail": access}
    try:
        session = async_get_clientsession(hass)
        async with session.request(method, _base_url(hass, ws=False) + path,
                                   json=json_body, ssl=False, timeout=timeout,
                                   headers={"Authorization": f"Bearer {access}"}) as resp:
            body: Any = None
            try:
                body = await resp.json()
            except Exception:  # noqa: BLE001
                body = (await resp.text())[:500]
            return {"ok": resp.status < 300, "status": resp.status, "body": body}
    finally:
        hass.auth.async_remove_refresh_token(refresh)


async def call_own_ws(hass: HomeAssistant, payload: dict[str, Any],
                      timeout: int = 60) -> dict:
    """Run one websocket command against this instance. Returns the raw result
    message ({"success": bool, "result"/"error": ...})."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    refresh, access = await _mint_owner_token(hass)
    if refresh is None:
        return {"success": False, "error": {"message": access}}
    try:
        session = async_get_clientsession(hass)
        async with session.ws_connect(_base_url(hass, ws=True) + "/api/websocket",
                                      ssl=False, timeout=timeout) as ws:
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": access})
            if (await ws.receive_json()).get("type") != "auth_ok":
                return {"success": False, "error": {"message": "loopback auth failed"}}
            await ws.send_json({"id": 1, **payload})
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == 1 and msg.get("type") == "result":
                    return msg
    finally:
        hass.auth.async_remove_refresh_token(refresh)
