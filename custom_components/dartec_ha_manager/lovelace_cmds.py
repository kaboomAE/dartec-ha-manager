"""Remote Lovelace dashboard commands.

Uses the lovelace component's in-process objects — the same code paths the
frontend's websocket commands (lovelace/config, lovelace/config/save,
lovelace/dashboards/create) call. Storage-mode dashboards only: YAML-mode
configs are read-only and save attempts return a clean error.

BEST-EFFORT: hass.data["lovelace"] internals are not public API; every access
is defensive and failures come back as {"ok": False, "detail": ...}.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .ws_bridge import call_own_ws

_LOGGER = logging.getLogger(__name__)


def _lovelace_data(hass: HomeAssistant):
    data = hass.data.get("lovelace")
    if data is None:
        raise HomeAssistantLovelaceError("lovelace component not loaded")
    return data


class HomeAssistantLovelaceError(Exception):
    pass


def _get_dashboard(hass: HomeAssistant, url_path: str | None):
    dashboards = getattr(_lovelace_data(hass), "dashboards", None) or {}
    dashboard = dashboards.get(url_path or None)
    if dashboard is None:
        raise HomeAssistantLovelaceError(f"dashboard '{url_path or '(default)'}' not found")
    return dashboard


async def lovelace_get(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    dashboard = _get_dashboard(hass, cmd.get("url_path"))
    try:
        config = await dashboard.async_load(False)
    except Exception as err:  # noqa: BLE001 — surfaces config_not_found for never-saved dashboards
        return {"ok": True, "config": None, "mode": getattr(dashboard, "mode", "storage"),
                "detail": f"no stored config yet ({err})"}
    return {"ok": True, "config": config, "mode": getattr(dashboard, "mode", "storage")}


async def lovelace_save(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    config = cmd.get("config")
    if not isinstance(config, dict):
        return {"ok": False, "detail": "config (object) required"}
    dashboard = _get_dashboard(hass, cmd.get("url_path"))
    if getattr(dashboard, "mode", "storage") != "storage":
        return {"ok": False, "detail": "YAML-mode dashboard is read-only via API"}
    await dashboard.async_save(config)
    return {"ok": True, "detail": f"saved dashboard '{cmd.get('url_path') or '(default)'}'"}


async def lovelace_create(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    url_path = (cmd.get("url_path") or "").strip()
    if "-" not in url_path:
        return {"ok": False, "detail": "url_path must contain a hyphen (HA requirement)"}
    if url_path in (getattr(_lovelace_data(hass), "dashboards", None) or {}):
        return {"ok": False, "detail": f"dashboard '{url_path}' already exists"}

    # The live DashboardsCollection is a local variable inside lovelace's setup
    # and unreachable in-process; creating through a parallel collection would
    # desync it. Use HA's own websocket command instead.
    result_msg = await call_own_ws(hass, {
        "type": "lovelace/dashboards/create",
        "url_path": url_path,
        "title": cmd.get("title") or url_path,
        "icon": cmd.get("icon") or "mdi:view-dashboard",
        "show_in_sidebar": cmd.get("show_in_sidebar", True),
        "require_admin": False,
    })
    if not result_msg.get("success"):
        error = result_msg.get("error") or {}
        return {"ok": False, "detail": f"create failed: {error.get('message', error)}"}

    result: dict[str, Any] = {"ok": True, "detail": f"created dashboard '{url_path}'"}
    if isinstance(cmd.get("config"), dict):
        try:
            dashboard = _get_dashboard(hass, url_path)
            await dashboard.async_save(cmd["config"])
            result["detail"] += " with initial config"
        except Exception as err:  # noqa: BLE001
            result["detail"] += f" (initial config save failed: {err})"
    return result


async def lovelace_update(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Rename a storage dashboard (its sidebar title, and optionally icon).

    Creating a dashboard is the only other place a title is set, so without
    this a title given at rollout could never be corrected: that is how a
    home came to read "DarTec Dashboard" (dartec-ha-manager#25). Goes through
    Home Assistant's own websocket commands, like create, because the live
    dashboards collection is not reachable in-process.
    """
    url_path = (cmd.get("url_path") or "").strip()
    title = str(cmd.get("title") or "").strip()[:100]
    if not url_path or not title:
        return {"ok": False, "detail": "url_path and title required"}
    listed = await call_own_ws(hass, {"type": "lovelace/dashboards/list"})
    if not listed.get("success"):
        return {"ok": False, "detail": f"could not list dashboards: {listed.get('error')}"}
    # The collection's id is derived from the url_path but is not always
    # equal to it, so it is looked up rather than assumed.
    item = next((d for d in listed.get("result") or [] if d.get("url_path") == url_path), None)
    if item is None:
        return {"ok": False,
                "detail": f"dashboard '{url_path}' not found, or not a storage dashboard"}
    update: dict[str, Any] = {"type": "lovelace/dashboards/update",
                              "dashboard_id": item["id"], "title": title}
    if cmd.get("icon"):
        update["icon"] = str(cmd["icon"])
    result = await call_own_ws(hass, update)
    if not result.get("success"):
        error = result.get("error") or {}
        return {"ok": False, "detail": f"rename failed: {error.get('message', error)}",
                "previous_title": item.get("title")}
    return {"ok": True, "previous_title": item.get("title"),
            "detail": f"renamed dashboard '{url_path}' from '{item.get('title')}' to '{title}'"}


HANDLERS = {
    "lovelace_get": lovelace_get,
    "lovelace_save": lovelace_save,
    "lovelace_create": lovelace_create,
    "lovelace_update": lovelace_update,
}
