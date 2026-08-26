"""Remote room/floor setup and device assignment.

Everything here goes through Home Assistant's own registry websocket
commands over loopback, so the result is identical to doing it by hand in
Settings → Areas — no direct registry mutation, no storage races, and the
frontend updates live.

Why this matters operationally: on a real 253-device home only 47 devices
had an area, which is what makes room-based dashboards look empty. Assigning
in bulk from the manager is far faster than tapping through the HA UI device
by device, and it is the prerequisite for the blueprint compiler producing
good Rooms views.

Bulk operations are per-item fault tolerant: one bad id reports itself and
the rest still apply, because a half-finished assignment run that silently
aborted would be worse than one that tells you which items failed.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .ws_bridge import call_own_ws

_LOGGER = logging.getLogger(__name__)

# Area fields we let the manager set. temperature/humidity_entity_id are the
# entities HA itself uses for an area's climate readout (2025.2+).
AREA_FIELDS = ("name", "floor_id", "icon", "aliases",
               "temperature_entity_id", "humidity_entity_id")
FLOOR_FIELDS = ("name", "icon", "level", "aliases")


def _fail(msg: str) -> dict:
    return {"ok": False, "detail": msg}


def _ws_error(result: dict, what: str) -> str:
    error = result.get("error") or {}
    return f"{what} failed: {error.get('message') or error.get('code') or result}"


async def floor_upsert(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Create a floor, or update it when floor_id is given."""
    payload = {k: cmd[k] for k in FLOOR_FIELDS if k in cmd and cmd[k] is not None}
    if cmd.get("floor_id"):
        result = await call_own_ws(hass, {"type": "config/floor_registry/update",
                                          "floor_id": cmd["floor_id"], **payload})
        verb = "updated"
    else:
        if not payload.get("name"):
            return _fail("name required to create a floor")
        result = await call_own_ws(hass, {"type": "config/floor_registry/create", **payload})
        verb = "created"
    if not result.get("success"):
        return _fail(_ws_error(result, "floor"))
    entry = result.get("result") or {}
    return {"ok": True, "floor_id": entry.get("floor_id"),
            "detail": f"floor '{entry.get('name')}' {verb}"}


async def floor_delete(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    if not cmd.get("floor_id"):
        return _fail("floor_id required")
    result = await call_own_ws(hass, {"type": "config/floor_registry/delete",
                                      "floor_id": cmd["floor_id"]})
    if not result.get("success"):
        return _fail(_ws_error(result, "floor delete"))
    return {"ok": True, "detail": "floor deleted (its areas keep existing, unassigned)"}


async def area_upsert(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Create an area, or update it when area_id is given."""
    payload = {k: cmd[k] for k in AREA_FIELDS if k in cmd}
    # HA accepts null to clear these, so only drop keys that were not sent.
    payload = {k: v for k, v in payload.items()
               if v is not None or k in ("floor_id", "icon",
                                         "temperature_entity_id", "humidity_entity_id")}
    if cmd.get("area_id"):
        result = await call_own_ws(hass, {"type": "config/area_registry/update",
                                          "area_id": cmd["area_id"], **payload})
        verb = "updated"
    else:
        if not payload.get("name"):
            return _fail("name required to create an area")
        result = await call_own_ws(hass, {"type": "config/area_registry/create", **payload})
        verb = "created"
    if not result.get("success"):
        return _fail(_ws_error(result, "area"))
    entry = result.get("result") or {}
    return {"ok": True, "area_id": entry.get("area_id"),
            "detail": f"area '{entry.get('name')}' {verb}"}


async def area_delete(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    if not cmd.get("area_id"):
        return _fail("area_id required")
    result = await call_own_ws(hass, {"type": "config/area_registry/delete",
                                      "area_id": cmd["area_id"]})
    if not result.get("success"):
        return _fail(_ws_error(result, "area delete"))
    return {"ok": True, "detail": "area deleted; its devices are now unassigned"}


async def devices_assign(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Bulk-assign devices to an area (area_id null clears the assignment)."""
    assignments = cmd.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        return _fail("assignments (list of {device_id, area_id}) required")

    done, failed = 0, []
    for item in assignments[:500]:
        device_id = (item or {}).get("device_id")
        if not device_id:
            failed.append("missing device_id")
            continue
        result = await call_own_ws(hass, {"type": "config/device_registry/update",
                                          "device_id": device_id,
                                          "area_id": item.get("area_id")})
        if result.get("success"):
            done += 1
        else:
            failed.append(f"{device_id}: {(result.get('error') or {}).get('message', 'failed')}")

    detail = f"assigned {done} device(s)"
    if failed:
        detail += f"; {len(failed)} failed ({'; '.join(failed[:3])})"
    return {"ok": done > 0 or not failed, "assigned": done,
            "failed": len(failed), "detail": detail}


async def entities_assign(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Bulk-assign entities to an area — for entities whose device sits
    elsewhere (a multi-room hub) or which have no device at all."""
    assignments = cmd.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        return _fail("assignments (list of {entity_id, area_id}) required")

    done, failed = 0, []
    for item in assignments[:500]:
        entity_id = (item or {}).get("entity_id")
        if not entity_id:
            failed.append("missing entity_id")
            continue
        result = await call_own_ws(hass, {"type": "config/entity_registry/update",
                                          "entity_id": entity_id,
                                          "area_id": item.get("area_id")})
        if result.get("success"):
            done += 1
        else:
            failed.append(f"{entity_id}: {(result.get('error') or {}).get('message', 'failed')}")

    detail = f"assigned {done} entity(ies)"
    if failed:
        detail += f"; {len(failed)} failed ({'; '.join(failed[:3])})"
    return {"ok": done > 0 or not failed, "assigned": done,
            "failed": len(failed), "detail": detail}


HANDLERS = {
    "floor_upsert": floor_upsert,
    "floor_delete": floor_delete,
    "area_upsert": area_upsert,
    "area_delete": area_delete,
    "devices_assign": devices_assign,
    "entities_assign": entities_assign,
}
