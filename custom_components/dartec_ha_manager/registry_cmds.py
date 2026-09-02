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

from .registry_paging import DEFAULT_PAGE, paginate_rows
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



# --- reading the registry, a page at a time ----------------------------------
# The snapshot caps devices at 600 and entities at 2500 because it is sent
# every 60 seconds. That cap is right for a heartbeat and wrong for browsing:
# a home with 4000 entities had 1500 of them simply unreachable from the
# manager, and the UI could not even say so, because it only knew about the
# rows it had been handed.
#
# This serves the live registry instead — filtered, counted and sliced here,
# where the whole thing is in memory anyway, so the manager pages through the
# real list rather than through a truncated copy of it.

async def registry_query(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """One page of the live device or entity registry.

    A read, so it is routine under the service policy: no maintenance window,
    and it stays available while a home is change-frozen, because looking is
    not changing.
    """
    from .collector import device_row, entity_row, registry_context

    kind = str(cmd.get("kind") or "entities")
    if kind not in ("entities", "devices"):
        return _fail(f"unknown registry kind '{kind}'")

    ctx = registry_context(hass)
    if kind == "entities":
        rows = [entity_row(reg, ctx, hass) for reg in ctx["entities"].entities.values()]
    else:
        rows = [device_row(device, ctx) for device in ctx["devices"].devices.values()]

    return paginate_rows(
        rows, kind,
        query=cmd.get("query") or "", domain=cmd.get("domain") or "",
        status=cmd.get("status") or "all", area=cmd.get("area") or "",
        offset=cmd.get("offset") or 0, limit=cmd.get("limit") or DEFAULT_PAGE)



async def signal_enable(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Switch on the signal-strength entities an integration disabled.

    ZHA creates RSSI sensors disabled by default, so on a typical home most of
    them are off and the manager has no idea how well anything is heard. This
    is a commissioning step rather than something to run repeatedly: enabling
    an entity makes Home Assistant start recording it, which costs a little
    database and is worth it once.

    Per-entity fault tolerant, like the assignment commands: one entity that
    refuses reports itself and the rest still apply, because a half-finished
    run that aborted silently would be worse than one that says which failed.
    """
    from .signal_health import disabled_signal_entities

    wanted = cmd.get("entity_ids")
    if not wanted:
        wanted = disabled_signal_entities(hass)
    wanted = list(wanted)[:400]
    if not wanted:
        return {"ok": True, "enabled": 0,
                "detail": "every signal sensor on this home is already on"}

    done, failed = 0, []
    for entity_id in wanted:
        result = await call_own_ws(hass, {
            "type": "config/entity_registry/update",
            "entity_id": entity_id, "disabled_by": None})
        if result.get("success"):
            done += 1
        else:
            failed.append(f"{entity_id}: {(result.get('error') or {}).get('message', 'failed')}")

    detail = f"enabled {done} signal sensor(s)"
    if failed:
        detail += f"; {len(failed)} failed ({'; '.join(failed[:3])})"
    # Home Assistant only starts polling a newly enabled entity after a
    # restart of its config entry, so the first readings can be a few minutes
    # away. Said here rather than leaving an operator wondering.
    detail += ". Readings appear once each integration reloads."
    return {"ok": done > 0 or not failed, "enabled": done,
            "failed": len(failed), "detail": detail}



async def mesh_map(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """The Zigbee mesh's shape: which node routes through which, and how well.

    Deliberately a command rather than part of the 60-second snapshot. A
    Zigbee2MQTT network scan asks every router for its routing tables, which
    floods the mesh with requests — it is the kind of measurement that degrades
    what it is measuring, so it runs when someone asks and not on a timer.
    """
    from .mesh import collect_mesh

    return await collect_mesh(hass, cmd.get("stacks"))


HANDLERS = {
    "floor_upsert": floor_upsert,
    "floor_delete": floor_delete,
    "area_upsert": area_upsert,
    "area_delete": area_delete,
    "devices_assign": devices_assign,
    "entities_assign": entities_assign,
    "registry_query": registry_query,
    "signal_enable": signal_enable,
    "mesh_map": mesh_map,
}
