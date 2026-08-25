"""Snapshot collector — gathers the DarTec health payload from a running
Home Assistant instance every cycle.

Design rule: every section is wrapped in its own try/except so a single
misbehaving source (e.g. HACS absent, no Supervisor) degrades to null for
that section instead of killing the snapshot. Sections marked BEST-EFFORT
touch HA internals that are not public API and must be re-validated against
each HA release in CI.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

from .hardware import async_collect_hardware

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"


async def collect_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}

    snapshot["core"] = _collect_core(hass)
    snapshot["integrations"] = _collect_integrations(hass)
    snapshot["automations"] = _collect_automations(hass)
    snapshot["dashboards"] = _collect_dashboards(hass)
    snapshot["logs"] = _collect_logs(hass)
    snapshot["hacs"] = _collect_hacs(hass)
    snapshot["entity_count"] = len(hass.states.async_entity_ids())
    snapshot.update(_collect_registries(hass))

    # Host metrics: psutil reads /proc, which is host-wide even inside the HA
    # container — so CPU/memory/uptime are true SYSTEM usage on every install
    # type. (Supervisor core_stats only measures the Core container; wrong for
    # "how loaded is this machine".) Disk prefers the Supervisor's host view
    # (the HAOS data disk) and falls back to psutil.
    # Runs in an executor: psutil hits the filesystem and must not block the loop.
    snapshot["host"] = await hass.async_add_executor_job(_collect_host_psutil)

    supervisor = await _collect_supervisor(hass)
    snapshot["hardware"] = await async_collect_hardware(
        hass, supervisor.get("platform") if supervisor else None)
    if supervisor:
        snapshot["addons"] = supervisor.get("addons")
        host_disk = supervisor.get("host_disk") or {}
        if host_disk.get("disk_total_gb"):
            snapshot["host"]["disk_used_gb"] = host_disk.get("disk_used_gb")
            snapshot["host"]["disk_total_gb"] = host_disk.get("disk_total_gb")
        if supervisor.get("core_update_available") is not None:
            snapshot["core"]["update_available"] = supervisor["core_update_available"]
            snapshot["core"]["latest_version"] = supervisor.get("core_latest_version")
    else:
        snapshot["addons"] = []

    return snapshot


def _collect_core(hass: HomeAssistant) -> dict:
    try:
        from homeassistant.const import __version__ as ha_version

        return {
            "version": ha_version,
            "location_name": hass.config.location_name,
            "installation_type": "Home Assistant OS" if os.environ.get("SUPERVISOR_TOKEN")
                                 else "Container/Core",
            "update_available": False,  # refined by Supervisor data when available
        }
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("core collect failed: %s", err)
        return {}


def _collect_integrations(hass: HomeAssistant) -> list[dict]:
    entries = []
    try:
        entity_counts: dict[str, int] = {}
        try:
            from homeassistant.helpers import entity_registry as er

            for reg_entry in er.async_get(hass).entities.values():
                if reg_entry.config_entry_id:
                    entity_counts[reg_entry.config_entry_id] = entity_counts.get(reg_entry.config_entry_id, 0) + 1
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("entity registry counts failed: %s", err)

        for entry in hass.config_entries.async_entries():
            entries.append({
                "domain": entry.domain,
                "title": entry.title,
                "state": entry.state.value if hasattr(entry.state, "value") else str(entry.state),
                # entry.reason carries the setup-error message on failed entries (BEST-EFFORT)
                "reason": getattr(entry, "reason", None),
                "entity_count": entity_counts.get(entry.entry_id, 0),
            })
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("integrations collect failed: %s", err)
    return entries


MAX_DEVICES = 600
MAX_ENTITIES = 2500


def _collect_registries(hass: HomeAssistant) -> dict:
    """Devices, entities and areas from the registries — powers the Devices and
    Entities tabs and (later) per-home dashboard generation. Capped so a very
    large home cannot produce an unbounded snapshot."""
    out: dict[str, Any] = {"devices": [], "entities": [], "areas": []}
    try:
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        areas = ar.async_get(hass)
        area_names = {area.id: area.name for area in areas.async_list_areas()}
        out["areas"] = [{"id": area.id, "name": area.name,
                         "floor_id": getattr(area, "floor_id", None)}
                        for area in areas.async_list_areas()]

        devices = dr.async_get(hass)
        entities = er.async_get(hass)

        entities_per_device: dict[str, int] = {}
        for reg in entities.entities.values():
            if reg.device_id:
                entities_per_device[reg.device_id] = entities_per_device.get(reg.device_id, 0) + 1

        device_list = list(devices.devices.values())
        out["device_count"] = len(device_list)
        for device in device_list[:MAX_DEVICES]:
            out["devices"].append({
                "id": device.id,
                "name": device.name_by_user or device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "sw_version": device.sw_version,
                "hw_version": device.hw_version,
                "area": area_names.get(device.area_id) if device.area_id else None,
                "via_device": bool(device.via_device_id),
                "disabled": device.disabled_by is not None,
                "entry_type": str(device.entry_type) if device.entry_type else None,
                "connections": sorted({conn_type for conn_type, _ in (device.connections or set())}),
                "integrations": sorted(device.identifiers and {ident[0] for ident in device.identifiers} or set()),
                "entity_count": entities_per_device.get(device.id, 0),
            })

        device_area_id = {device.id: device.area_id for device in device_list}

        entity_list = list(entities.entities.values())
        out["entity_registry_count"] = len(entity_list)
        for reg in entity_list[:MAX_ENTITIES]:
            state = hass.states.get(reg.entity_id)
            # HA semantics: an entity belongs to its own area if set, otherwise
            # to its device's area. Reading only entity.area_id under-reports
            # badly — on a real 253-device home it missed 1265 entities.
            area_id = reg.area_id or device_area_id.get(reg.device_id)
            out["entities"].append({
                "entity_id": reg.entity_id,
                "name": reg.name or reg.original_name,
                "domain": reg.domain,
                "platform": reg.platform,
                "device_class": reg.device_class or reg.original_device_class,
                "area": area_names.get(area_id) if area_id else None,
                "device_id": reg.device_id,
                # "config"/"diagnostic" entities are plumbing, not things a
                # resident wants on a dashboard — the compiler filters on this.
                "entity_category": str(reg.entity_category.value)
                                   if getattr(reg.entity_category, "value", None)
                                   else (str(reg.entity_category) if reg.entity_category else None),
                "disabled": reg.disabled_by is not None,
                "hidden": reg.hidden_by is not None,
                "state": state.state if state else None,
            })
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("registry collect failed: %s", err)
    return out


def _collect_automations(hass: HomeAssistant) -> dict:
    try:
        states = [hass.states.get(eid) for eid in hass.states.async_entity_ids("automation")]
        states = [s for s in states if s is not None]

        def _last_triggered(s):
            lt = s.attributes.get("last_triggered")
            return lt.isoformat() if hasattr(lt, "isoformat") else lt

        recent = sorted(
            (s for s in states if s.attributes.get("last_triggered")),
            key=lambda s: s.attributes["last_triggered"], reverse=True)[:10]
        return {
            "total": len(states),
            "enabled": sum(1 for s in states if s.state == "on"),
            "disabled": sum(1 for s in states if s.state == "off"),
            "unavailable": [s.entity_id for s in states if s.state == "unavailable"],
            "recent": [{"entity_id": s.entity_id,
                        "name": s.attributes.get("friendly_name") or s.entity_id,
                        "last_triggered": _last_triggered(s)} for s in recent],
        }
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("automations collect failed: %s", err)
        return {}


def _collect_dashboards(hass: HomeAssistant) -> list[dict]:
    """BEST-EFFORT: reads the lovelace component's in-memory dashboard map."""
    dashboards = []
    try:
        lovelace = hass.data.get("lovelace")
        dashboard_map = getattr(lovelace, "dashboards", None) or {}
        for url_path, config in dashboard_map.items():
            lovelace_config = getattr(config, "config", None) or {}
            dashboards.append({
                "url_path": url_path,
                "title": lovelace_config.get("title") or (url_path or "Overview"),
                "mode": getattr(config, "mode", "storage"),
            })
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("dashboards collect failed: %s", err)
    return dashboards


def _collect_logs(hass: HomeAssistant) -> list[dict]:
    """BEST-EFFORT: reads the system_log component's deduplicated record store."""
    records = []
    try:
        handler = hass.data.get("system_log")
        store = getattr(handler, "records", None)
        if store:
            for entry in list(store.values())[-50:]:
                as_dict = entry.to_dict() if hasattr(entry, "to_dict") else {}
                records.append({
                    "level": as_dict.get("level"),
                    "name": as_dict.get("name"),
                    "message": (as_dict.get("message") or [""])[0]
                               if isinstance(as_dict.get("message"), list) else as_dict.get("message"),
                    "count": as_dict.get("count", 1),
                })
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("logs collect failed: %s", err)
    return records


def _collect_hacs(hass: HomeAssistant) -> list[dict]:
    """BEST-EFFORT: reads HACS's in-memory repository list when HACS is installed."""
    repos = []
    try:
        hacs = hass.data.get("hacs")
        repositories = getattr(getattr(hacs, "repositories", None), "list_downloaded", None)
        for repo in repositories or []:
            data = getattr(repo, "data", None)
            repos.append({
                "name": getattr(data, "full_name", None) or getattr(repo, "display_name", "?"),
                "category": str(getattr(data, "category", "")),
                "installed_version": getattr(data, "installed_version", None),
                "available_version": getattr(data, "last_version", None),
            })
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("hacs collect failed: %s", err)
    return repos


async def _collect_supervisor(hass: HomeAssistant) -> dict | None:
    """Query the Supervisor REST API directly (HA OS / Supervised only)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        headers = {"Authorization": f"Bearer {token}"}

        async def get(path: str) -> dict:
            async with session.get(f"{SUPERVISOR_URL}{path}", headers=headers, timeout=15) as resp:
                body = await resp.json()
                return body.get("data") or {}

        addons_raw = await get("/addons")
        host_info = await get("/host/info")
        core_info = await get("/core/info")
        os_info = await get("/os/info")
        supervisor_info = await get("/supervisor/info")

        addons = [{
            "slug": addon.get("slug"),
            "name": addon.get("name"),
            "version": addon.get("version"),
            "version_latest": addon.get("version_latest"),
            "state": addon.get("state"),
            "boot": addon.get("boot"),
            "update_available": addon.get("update_available", False),
        } for addon in addons_raw.get("addons") or []]

        return {
            "addons": addons,
            "host_disk": {
                "disk_used_gb": host_info.get("disk_used"),
                "disk_total_gb": host_info.get("disk_total"),
            },
            "platform": {
                "board": os_info.get("board"),
                "os_version": os_info.get("version"),
                "operating_system": host_info.get("operating_system"),
                "kernel": host_info.get("kernel"),
                "disk_life_time": host_info.get("disk_life_time"),
                "supervisor_version": supervisor_info.get("version"),
            },
            "core_update_available": core_info.get("update_available"),
            "core_latest_version": core_info.get("version_latest"),
        }
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("supervisor collect failed: %s", err)
        return None


def _collect_host_psutil() -> dict:
    """Host-wide metrics via psutil (/proc is host-scoped even in containers).
    Ships with HA Core (psutil-home-assistant), so present on all install types.

    BLOCKING — reads /proc and calls statvfs. Executor only; unlike the static
    hardware facts these must be re-read every cycle, so they cannot be cached."""
    try:
        import time

        import psutil  # noqa: PLC0415

        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/config") if os.path.isdir("/config") else psutil.disk_usage("/")
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_used_mb": vm.used / 1024 / 1024,
            "memory_total_mb": vm.total / 1024 / 1024,
            "disk_used_gb": disk.used / 1024 / 1024 / 1024,
            "disk_total_gb": disk.total / 1024 / 1024 / 1024,
            "uptime_s": time.time() - psutil.boot_time(),
        }
    except Exception:  # noqa: BLE001
        return {}
