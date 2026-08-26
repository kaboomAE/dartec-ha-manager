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


HANDLERS = {"theme_set": theme_set, "automation_create": automation_create,
            "branding_set": branding_set}
