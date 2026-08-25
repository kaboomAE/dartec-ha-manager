"""Command executor — runs server-issued commands against this HA instance.

Security model: the allowlist here is the agent's own guardrail. Even a
compromised cloud can only invoke these specific operations; nothing here
evaluates arbitrary payloads, touches files, or shells out.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"

_ADDON_ACTIONS = {
    "addon_restart": "restart",
    "addon_start": "start",
    "addon_stop": "stop",
}


async def execute_command(hass: HomeAssistant, cmd: dict[str, Any]) -> dict[str, Any]:
    from .lovelace_cmds import HANDLERS as LOVELACE_HANDLERS

    action = cmd.get("action")
    try:
        if action in _ADDON_ACTIONS:
            return await _addon_action(hass, cmd.get("addon_slug", ""), _ADDON_ACTIONS[action])
        if action == "call_service":
            return await _call_service(hass, cmd)
        if action in LOVELACE_HANDLERS:
            return await LOVELACE_HANDLERS[action](hass, cmd)
        return {"ok": False, "detail": f"unsupported action '{action}'"}
    except Exception as err:  # noqa: BLE001 — always answer the cloud, never raise
        _LOGGER.warning("Command %s failed: %s", action, err)
        return {"ok": False, "detail": str(err)}


async def _addon_action(hass: HomeAssistant, slug: str, verb: str) -> dict:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {"ok": False, "detail": "No Supervisor on this install (Container/Core)"}
    if not slug:
        return {"ok": False, "detail": "addon_slug missing"}
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    async with session.post(f"{SUPERVISOR_URL}/addons/{slug}/{verb}",
                            headers={"Authorization": f"Bearer {token}"}, timeout=90) as resp:
        if resp.status == 200:
            return {"ok": True, "detail": f"{verb} {slug} succeeded"}
        body = await resp.text()
        return {"ok": False, "detail": f"Supervisor returned {resp.status}: {body[:200]}"}


async def _call_service(hass: HomeAssistant, cmd: dict) -> dict:
    await hass.services.async_call(
        cmd["domain"], cmd["service"], cmd.get("service_data") or {}, blocking=True
    )
    return {"ok": True, "detail": f"called {cmd['domain']}.{cmd['service']}"}
