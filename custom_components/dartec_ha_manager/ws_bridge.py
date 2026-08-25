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


async def call_own_ws(hass: HomeAssistant, payload: dict[str, Any],
                      timeout: int = 60) -> dict:
    """Run one websocket command against this instance. Returns the raw result
    message ({"success": bool, "result"/"error": ...})."""
    from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    users = await hass.auth.async_get_users()
    owner = next((u for u in users if u.is_owner and u.is_active), None)
    if owner is None:
        owner = next((u for u in users if u.is_active
                      and any(g.id == "system-admin" for g in u.groups)), None)
    if owner is None:
        return {"success": False, "error": {"message": "no active owner/admin user found"}}

    refresh = await hass.auth.async_create_refresh_token(
        owner, client_name=f"DarTec task {uuid.uuid4().hex[:8]}",
        token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
        access_token_expiration=timedelta(minutes=TOKEN_TTL_MINUTES))
    access = hass.auth.async_create_access_token(refresh)
    try:
        api = getattr(hass.config, "api", None)
        port = getattr(api, "port", None) or 8123
        scheme = "wss" if getattr(api, "use_ssl", False) else "ws"
        session = async_get_clientsession(hass)
        async with session.ws_connect(f"{scheme}://127.0.0.1:{port}/api/websocket",
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
