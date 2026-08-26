"""Theme and automation commands.

theme_set — verify the theme actually exists on this home (a typo'd or
not-yet-loaded theme fails loudly here instead of silently doing nothing),
then set it as the frontend default.

automation_create — write an automation through HA's own storage config API
(loopback REST; this API has no websocket equivalent), so it lands in the UI
automation editor exactly as if created there, and HA reloads automations
itself. The manager's UI requires a human to review AI-generated automations
before this command is ever sent.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from homeassistant.core import HomeAssistant

from .ws_bridge import call_own_rest, call_own_ws

_LOGGER = logging.getLogger(__name__)

AUTOMATION_KEYS = {"alias", "description", "triggers", "conditions", "actions",
                   "trigger", "condition", "action", "mode", "max", "max_exceeded",
                   "variables", "trace", "initial_state"}


async def theme_set(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    name = (cmd.get("theme") or "").strip()
    if not name:
        return {"ok": False, "detail": "theme name required"}

    themes_msg = await call_own_ws(hass, {"type": "frontend/get_themes"})
    available = (themes_msg.get("result") or {}).get("themes") or {}
    if name not in available:
        return {"ok": False,
                "detail": f"theme '{name}' is not loaded on this home. "
                          f"Available: {sorted(available) or 'none'}. If it was just "
                          "installed via HACS, configuration.yaml needs "
                          "`frontend: themes: !include_dir_merge_named themes` "
                          "and a Home Assistant restart."}

    data: dict[str, Any] = {"name": name}
    if cmd.get("mode") in ("light", "dark"):
        data["mode"] = cmd["mode"]
    await hass.services.async_call("frontend", "set_theme", data, blocking=True)
    return {"ok": True, "detail": f"theme '{name}' set as frontend default"
                                  + (f" ({cmd['mode']} mode)" if data.get("mode") else "")}


async def automation_create(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    config = cmd.get("config")
    if not isinstance(config, dict):
        return {"ok": False, "detail": "config (object) required"}
    if not (config.get("alias") and (config.get("triggers") or config.get("trigger"))
            and (config.get("actions") or config.get("action"))):
        return {"ok": False, "detail": "automation needs alias, triggers and actions"}

    unknown = set(config) - AUTOMATION_KEYS - {"id"}
    if unknown:
        return {"ok": False, "detail": f"unknown automation keys: {sorted(unknown)}"}

    automation_id = str(config.pop("id", "") or "").strip() or f"dartec_{uuid.uuid4().hex[:12]}"
    result = await call_own_rest(hass, "POST",
                                 f"/api/config/automation/config/{automation_id}", config)
    if not result.get("ok"):
        return {"ok": False,
                "detail": f"HA rejected the automation (HTTP {result.get('status')}): "
                          f"{result.get('body')}"}
    return {"ok": True, "automation_id": automation_id,
            "detail": f"created automation '{config.get('alias')}' ({automation_id})"}


async def branding_set(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    from .branding import branding_set as _apply

    return await _apply(hass, cmd)


AGENT_REPO = "kaboomAE/dartec-ha-manager"


async def agent_update(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Update this integration to the latest release, then restart HA so the
    new code loads.

    The restart is what makes this genuinely 'forced' — HACS only downloads
    files; a Python integration keeps running the version already imported
    until the process restarts. The reply is sent BEFORE restarting, because
    a restart drops this websocket and the manager would otherwise record a
    timeout for a command that actually succeeded.
    """
    from .hacs_cmds import hacs_install

    installed = await hacs_install(hass, {"repo": AGENT_REPO, "category": "integration",
                                          "only_if_missing": False})
    if not installed.get("ok"):
        return {"ok": False, "detail": f"update failed: {installed.get('detail')}"}

    if cmd.get("restart", True):
        async def _restart_soon() -> None:
            import asyncio
            await asyncio.sleep(2)          # let the command_result reach the manager
            await hass.services.async_call("homeassistant", "restart", {}, blocking=False)

        hass.async_create_background_task(_restart_soon(), name="dartec_agent_restart")
        return {"ok": True, "detail": f"{installed.get('detail')}; restarting Home Assistant now",
                "restarting": True}
    return {"ok": True, "detail": f"{installed.get('detail')}; restart required to load it"}


async def ha_restart(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Restart Home Assistant Core. Config is checked first — restarting into
    a broken configuration is the one way this leaves a customer offline."""
    check = await call_own_rest(hass, "POST", "/api/config/core/check_config", {})
    body = check.get("body") or {}
    if isinstance(body, dict) and body.get("result") == "invalid":
        return {"ok": False,
                "detail": f"refusing to restart: configuration is invalid — {body.get('errors')}"}

    async def _restart_soon() -> None:
        import asyncio
        await asyncio.sleep(2)
        await hass.services.async_call("homeassistant", "restart", {}, blocking=False)

    hass.async_create_background_task(_restart_soon(), name="dartec_ha_restart")
    return {"ok": True, "detail": "configuration valid; restarting Home Assistant",
            "restarting": True}


HANDLERS = {"theme_set": theme_set, "automation_create": automation_create,
            "branding_set": branding_set, "agent_update": agent_update,
            "ha_restart": ha_restart}
