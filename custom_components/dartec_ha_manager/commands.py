"""Command executor — runs server-issued commands against this HA instance.

Security model: this agent is the last line of defence, because the threat it
guards against is *our own cloud being compromised*. A guardrail that trusts
the server for anything is therefore worth nothing.

Two gates, both enforced here:

* Every ``call_service`` is checked as a ``domain.service`` pair against
  ``service_policy.py`` — default deny, with a permanently blocked tier that
  no consent flow can unlock.
* Sensitive operations (that tier, plus the account and infrastructure actions
  in ``SENSITIVE_ACTIONS``) additionally need a maintenance window the
  homeowner opened locally. See ``maintenance.py``.

Everything that runs, and everything refused, is written to this instance's
own logbook, so the house keeps its own record independent of ours.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

from . import maintenance
from .service_policy import SENSITIVE_ACTIONS, check_call_service

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"

_ADDON_ACTIONS = {
    "addon_restart": "restart",
    "addon_start": "start",
    "addon_stop": "stop",
}


def _refuse(hass: HomeAssistant, what: str, why: str) -> dict:
    """Deny a command, and make sure the house knows it was attempted."""
    _LOGGER.warning("Refused %s: %s", what, why)
    maintenance.logbook(hass, f"Refused remote command '{what}': {why}")
    return {"ok": False, "refused": True, "detail": why}


def _describe(cmd: dict[str, Any]) -> str:
    action = cmd.get("action")
    if action == "call_service":
        target = cmd.get("service_data", {}) or {}
        entity = target.get("entity_id") if isinstance(target, dict) else None
        suffix = f" on {entity}" if entity else ""
        return f"{cmd.get('domain')}.{cmd.get('service')}{suffix}"
    return str(action)


async def execute_command(hass: HomeAssistant, cmd: dict[str, Any]) -> dict[str, Any]:
    from .backup_cmds import HANDLERS as BACKUP_HANDLERS
    from .hacs_cmds import HANDLERS as HACS_HANDLERS
    from .home_cmds import HANDLERS as HOME_HANDLERS
    from .lovelace_cmds import HANDLERS as LOVELACE_HANDLERS
    from .registry_cmds import HANDLERS as REGISTRY_HANDLERS
    from .tunnel_cmds import HANDLERS as TUNNEL_HANDLERS
    from .user_cmds import HANDLERS as USER_HANDLERS

    action = cmd.get("action")
    try:
        # Window management is always reachable — the cloud may ask, and may
        # read the state, but neither grants it anything.
        if action == "maintenance_status":
            return {"ok": True, **maintenance.status(hass)}
        if action == "maintenance_request":
            return maintenance.request_window(hass, str(cmd.get("reason") or ""))

        window_open = maintenance.is_open(hass)

        if action == "call_service":
            refusal = check_call_service(cmd, maintenance_open=window_open)
            if refusal:
                return _refuse(hass, _describe(cmd), refusal)
            result = await _call_service(hass, cmd)
            maintenance.logbook(hass, f"Dartec called {_describe(cmd)}")
            return result

        if action in SENSITIVE_ACTIONS and not window_open:
            return _refuse(hass, str(action),
                           f"'{action}' needs an open maintenance window. Ask the "
                           "homeowner to run 'Dartec: allow maintenance'.")

        if action in _ADDON_ACTIONS:
            result = await _addon_action(hass, cmd.get("addon_slug", ""),
                                         _ADDON_ACTIONS[action])
        elif action in LOVELACE_HANDLERS:
            result = await LOVELACE_HANDLERS[action](hass, cmd)
        elif action in HACS_HANDLERS:
            result = await HACS_HANDLERS[action](hass, cmd)
        elif action in HOME_HANDLERS:
            result = await HOME_HANDLERS[action](hass, cmd)
        elif action in REGISTRY_HANDLERS:
            result = await REGISTRY_HANDLERS[action](hass, cmd)
        elif action in USER_HANDLERS:
            result = await USER_HANDLERS[action](hass, cmd)
        elif action in TUNNEL_HANDLERS:
            result = await TUNNEL_HANDLERS[action](hass, cmd)
        elif action in BACKUP_HANDLERS:
            result = await BACKUP_HANDLERS[action](hass, cmd)
        else:
            return {"ok": False, "detail": f"unsupported action '{action}'"}

        if action in SENSITIVE_ACTIONS:
            maintenance.logbook(hass, f"Dartec ran '{action}' under an open "
                                      "maintenance window")
        return result
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
    """Execute a call already cleared by ``service_policy.check_call_service``.

    Never call this directly — the policy check is the only thing standing
    between the cloud and every service in the house.
    """
    await hass.services.async_call(
        cmd["domain"], cmd["service"], cmd.get("service_data") or {}, blocking=True
    )
    return {"ok": True, "detail": f"called {cmd['domain']}.{cmd['service']}"}
