"""Theme and automation commands.

theme_set — verify the theme actually exists on this home (a typo'd or
not-yet-loaded theme fails loudly here instead of silently doing nothing),
then set it as the frontend default.

automation_create — write an automation through HA's own storage config API
(loopback REST; this API has no websocket equivalent), so it lands in the UI
automation editor exactly as if created there, and HA reloads automations
itself. The manager's UI requires a human to review AI-generated automations
before this command is ever sent.

It takes two shapes: an automation written out in full, and a *blueprint
instance* — `{alias, use_blueprint: {path, input}}` — whose behaviour comes
from a blueprint staged by `blueprint_cmds.py`. HA substitutes the inputs and
validates the result against the automation schema before storing it, so a
bad input is refused here rather than becoming an automation that silently
never fires.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # keeps the validation below importable
    from homeassistant.core import HomeAssistant   # without HA, so CI can test it

_LOGGER = logging.getLogger(__name__)

AUTOMATION_KEYS = {"alias", "description", "triggers", "conditions", "actions",
                   "trigger", "condition", "action", "mode", "max", "max_exceeded",
                   "variables", "trace", "initial_state"}

# A blueprint instance is a different shape entirely: it has no triggers or
# actions of its own, because those come from the blueprint. Its own key set
# rather than a widened AUTOMATION_KEYS, so a config that tries to be both is
# refused here with a clear reason instead of by HA with a schema error.
BLUEPRINT_AUTOMATION_KEYS = {"alias", "description", "use_blueprint"}


async def theme_set(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    name = (cmd.get("theme") or "").strip()
    if not name:
        return {"ok": False, "detail": "theme name required"}

    from .ws_bridge import call_own_ws

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


def _validate_automation(config: dict[str, Any]) -> str | None:
    """Refusal reason for an automation config, or None if it may be written.

    Two accepted shapes. A **written** automation carries its own triggers and
    actions, and is model output — the reason `automation_create` sits in
    `SENSITIVE_ACTIONS` at all. A **blueprint instance** carries neither: its
    behaviour comes from a YAML file already staged on this home, so the
    executable part was written and reviewed by us rather than generated per
    house. Both still need the maintenance window; only the review burden
    differs.
    """
    if not isinstance(config, dict):
        return "config (object) required"
    if not config.get("alias"):
        return "automation needs an alias"

    if "use_blueprint" in config:
        used = config["use_blueprint"]
        if not isinstance(used, dict) or not str(used.get("path") or "").strip():
            return "use_blueprint needs a path"
        if "input" in used and not isinstance(used["input"], dict):
            return "use_blueprint input must be an object"
        unknown = set(config) - BLUEPRINT_AUTOMATION_KEYS - {"id"}
        if unknown:
            return (f"a blueprint automation cannot also set {sorted(unknown)} — "
                    "triggers, actions and mode come from the blueprint")
        return None

    if not ((config.get("triggers") or config.get("trigger"))
            and (config.get("actions") or config.get("action"))):
        return "automation needs alias, triggers and actions"
    unknown = set(config) - AUTOMATION_KEYS - {"id"}
    if unknown:
        return f"unknown automation keys: {sorted(unknown)}"
    return None


async def automation_create(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    config = cmd.get("config")
    refusal = _validate_automation(config)
    if refusal:
        return {"ok": False, "detail": refusal}

    from .ws_bridge import call_own_rest

    automation_id = str(config.pop("id", "") or "").strip() or f"dartec_{uuid.uuid4().hex[:12]}"
    result = await call_own_rest(hass, "POST",
                                 f"/api/config/automation/config/{automation_id}", config)
    if not result.get("ok"):
        return {"ok": False,
                "detail": f"HA rejected the automation (HTTP {result.get('status')}): "
                          f"{result.get('body')}"}
    used = (config.get("use_blueprint") or {}).get("path")
    return {"ok": True, "automation_id": automation_id, "blueprint": used,
            "detail": f"created automation '{config.get('alias')}' ({automation_id})"
                      + (f" from blueprint {used}" if used else "")}


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
    from .collector import _agent_version
    from .hacs_cmds import hacs_install

    # Pass our own on-disk version: when the agent was installed by hand, HACS
    # has no record of it and would otherwise treat any release as an upgrade.
    installed = await hacs_install(hass, {"repo": AGENT_REPO, "category": "integration",
                                          "only_if_missing": False,
                                          "current_version": _agent_version(),
                                          "allow_downgrade": cmd.get("allow_downgrade", False)})
    if not installed.get("ok"):
        return {"ok": False, "detail": f"update failed: {installed.get('detail')}"}

    # Nothing was downloaded, so there is nothing new to load. Restarting here
    # would take a customer's home offline for a minute to run the same code it
    # was already running.
    if installed.get("changed") is False:
        return {"ok": True, "detail": installed.get("detail"), "restarting": False}

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
    from .ws_bridge import call_own_rest

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
