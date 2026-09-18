"""Snapshot collector — gathers the Dartec health payload from a running
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

from . import device_health, hacs_token, hardware, signal_health
from .const import DOMAIN
from .hardware import async_collect_hardware
from .registry_access import all_devices
from .registry_paging import inventory_digest
from .version import async_agent_version

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"


async def collect_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}

    # Which homes run an outdated agent. From the loader, not manifest.json:
    # reading the file here was blocking I/O in the event loop every cycle.
    snapshot["core"] = _collect_core(hass, await async_agent_version(hass, DOMAIN))
    snapshot["integrations"] = _collect_integrations(hass)
    snapshot["automations"] = _collect_automations(hass)
    snapshot["dashboards"] = _collect_dashboards(hass)
    # What the household sees by default, and what the sidebar is branded:
    # both are names a customer reads, and the manager checks them for the
    # brand's spelling and for a default theme this home does not have
    # (dartec-ha-manager#25).
    snapshot["frontend_theme"] = await _collect_frontend_theme(hass)
    snapshot["branding"] = _collect_branding(hass)
    snapshot["logs"], snapshot["log_total"] = _collect_logs(hass)
    # How well each device is heard. Cheap — one pass over the states machine
    # — and it is the data that answers "is anything about to drop off?"
    # without any mesh topology at all.
    snapshot["signal"] = signal_health.collect_signal(hass)
    snapshot["signal_disabled"] = signal_health.disabled_signal_entities(hass)
    # Battery levels and devices that stopped answering, summarised here so
    # the manager is sent two short lists rather than every entity's state.
    # Thresholds and "for how long" are decided on the manager.
    snapshot.update(device_health.collect(hass))
    # Which phones this home can actually push to. Cheap (a dict lookup, no
    # I/O) and it is the difference between a Live Activity appearing on a
    # lock screen and nothing happening at all: the notify action name is
    # derived from the device name the companion app registered, which
    # nothing else in this snapshot states outright.
    snapshot["notify_targets"] = _collect_notify_targets(hass)
    snapshot["hacs"] = _collect_hacs(hass)
    # Which GitHub token HACS holds, as a fingerprint, so the manager can see
    # which homes still need the current one without asking each of them.
    # Its own key: `hacs` above is the list of repositories.
    snapshot[hacs_token.SNAPSHOT_KEY] = _collect_hacs_token(hass)
    snapshot["backup"] = await _collect_backup(hass)
    snapshot["entity_count"] = len(hass.states.async_entity_ids())
    # Whether this home is still being commissioned, and until when. The
    # manager warns about homes left in commissioning, and it can only do that
    # across the fleet if the answer arrives without asking each home.
    try:
        from . import maintenance

        snapshot["commissioning"] = maintenance.commissioning(hass)
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("commissioning collect failed: %s", err)
    snapshot.update(_collect_registries(hass))
    # How many people can sign in, by role: enough for staff to see a home
    # has been set up, and nothing more. Names, usernames and ids are the
    # customer's and stay in the house (see household.py).
    try:
        from .household_ws import household_counts, user_dict

        snapshot["household"] = household_counts(
            hass, [user_dict(u) for u in await hass.auth.async_get_users()])
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("household collect failed: %s", err)

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
    # Which machine this is and how its disk is holding up. Slow-cadence and
    # cached (see disk_health.py); the manager uses it to know which model
    # every home runs and to notice a machine changing under a home.
    try:
        identity = await hardware.async_collect_identity(hass)
        snapshot["hardware"].update({k: v for k, v in identity.items() if v is not None})
        snapshot["disk_health"] = await hardware.async_collect_disk_health(hass, identity)
        snapshot["host"].update({k: v for k, v in (
            await hardware.async_collect_temperatures(hass, identity)).items() if v is not None})
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("hardware identity/health collect failed: %s", err)
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

    try:
        from . import ha_update

        snapshot["ha_update"] = ha_update.snapshot_section(
            hass, supervisor.get("install") if supervisor else None)
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("ha_update collect failed: %s", err)

    return snapshot


def _collect_core(hass: HomeAssistant, agent_version: str | None) -> dict:
    try:
        from homeassistant.const import __version__ as ha_version

        return {
            "version": ha_version,
            "agent_version": agent_version,
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
                # The stable identity of this integration. A title can be
                # renamed by the household at any time; the entry id cannot,
                # and it is what the manager keys an incident on so a rename
                # does not read as "one fault resolved, another appeared".
                "entry_id": entry.entry_id,
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


# The snapshot is a 60-second heartbeat, not a database dump: an uncapped
# registry from a large home would be megabytes on the wire every minute. The
# caps below are therefore deliberate, and the true totals travel alongside the
# truncated lists so nothing downstream can mistake a page for the whole thing.
# Reaching past the cap is what the registry_query command is for.
MAX_DEVICES = 600
MAX_ENTITIES = 2500
MAX_UNREGISTERED = 500


def registry_context(hass: HomeAssistant) -> dict:
    """The lookups a device or entity row needs, built once.

    Shared by the snapshot collector and the on-demand registry_query command
    so the two produce identical rows — the manager renders them through the
    same table either way, and drift between them would show up as columns
    that mysteriously empty out once you page past the snapshot.
    """
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    entities_per_device: dict[str, int] = {}
    for reg in entities.entities.values():
        if reg.device_id:
            entities_per_device[reg.device_id] = entities_per_device.get(reg.device_id, 0) + 1

    return {
        "area_names": {area.id: area.name for area in ar.async_get(hass).async_list_areas()},
        "entities_per_device": entities_per_device,
        "device_area_id": {device.id: device.area_id for device in all_devices(devices)},
        "devices": devices,
        "entities": entities,
    }


def device_row(device, ctx: dict) -> dict:
    area_names = ctx["area_names"]
    return {
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
        # The addresses, not just their kinds. A Zigbee mesh reports nodes by
        # IEEE address and Home Assistant reports devices by registry id;
        # without this pair there is no way to say which node in the mesh is
        # the device a plan line called for, which is the whole point of
        # collecting topology at all.
        "connection_ids": sorted({f"{kind}:{value}" for kind, value
                                  in (device.connections or set())})[:8],
        "integrations": sorted(device.identifiers and {ident[0] for ident in device.identifiers} or set()),
        "entity_count": ctx["entities_per_device"].get(device.id, 0),
    }


def entity_row(reg, ctx: dict, hass: HomeAssistant) -> dict:
    state = hass.states.get(reg.entity_id)
    # HA semantics: an entity belongs to its own area if set, otherwise to its
    # device's area. Reading only entity.area_id under-reports badly — on a
    # real 253-device home it missed 1265 entities.
    area_id = reg.area_id or ctx["device_area_id"].get(reg.device_id)
    return {
        "entity_id": reg.entity_id,
        "name": reg.name or reg.original_name,
        "domain": reg.domain,
        "platform": reg.platform,
        "device_class": reg.device_class or reg.original_device_class,
        "area": ctx["area_names"].get(area_id) if area_id else None,
        "device_id": reg.device_id,
        # "config"/"diagnostic" entities are plumbing, not things a resident
        # wants on a dashboard — the compiler filters on this.
        "entity_category": str(reg.entity_category.value)
                           if getattr(reg.entity_category, "value", None)
                           else (str(reg.entity_category) if reg.entity_category else None),
        "disabled": reg.disabled_by is not None,
        "hidden": reg.hidden_by is not None,
        "state": state.state if state else None,
    }


def unregistered_rows(hass: HomeAssistant, registered: set[str]) -> list[dict]:
    """Entities Home Assistant runs that are not in its entity registry.

    An entity only enters the registry if its integration gives it a
    `unique_id`; YAML platforms and many template entities do not. They work
    and show in HA's UI, and until this every consumer of the snapshot was
    blind to them — 33 of 123 entities on the demo home
    (dartec-ha-manager-server#16).

    Shaped like `entity_row` so every consumer can take them unchanged, and
    marked `registered: False`: they have no device, area or registry id, so
    nothing can be renamed, moved or disabled through the registry commands,
    and their entity id changes if their YAML does. Built from the state
    machine, so the name is the friendly name and the class is the one the
    state reports.
    """
    rows = []
    for state in hass.states.async_all():
        if state.entity_id in registered:
            continue
        rows.append({
            "entity_id": state.entity_id,
            "name": state.attributes.get("friendly_name"),
            "domain": state.domain,
            "platform": None,
            "device_class": state.attributes.get("device_class"),
            "area": None,
            "device_id": None,
            "entity_category": None,
            "disabled": False,
            "hidden": False,
            "state": state.state,
            "registered": False,
        })
    return rows


def _collect_notify_targets(hass: HomeAssistant) -> list[str]:
    """Every `notify.*` action registered on this home, action name only.

    The companion app registers one per paired device, named after the device
    name chosen at setup — `notify.mobile_app_kaboom` for a phone whose
    entities are `sensor.kaboom_*`. That correspondence is conventional rather
    than guaranteed, which is exactly why this reads the real service registry
    instead of rebuilding the name from an entity id.

    Names only. A service's schema and description are large, change between
    releases, and answer nothing anyone asks here.
    """
    try:
        return sorted(hass.services.async_services().get("notify", {}))
    except Exception as err:  # noqa: BLE001 — best-effort, like every collector
        _LOGGER.debug("notify target collect failed: %s", err)
        return []


def _collect_registries(hass: HomeAssistant) -> dict:
    """Devices, entities and areas from the registries — powers the Devices and
    Entities tabs and per-home dashboard generation.

    Capped, with `device_count` / `entity_registry_count` carrying the real
    totals. The manager uses the gap between the two to tell an operator it is
    looking at a page, and offers the live query to reach the rest.
    """
    out: dict[str, Any] = {"devices": [], "entities": [], "areas": []}
    try:
        from homeassistant.helpers import area_registry as ar

        areas = ar.async_get(hass)
        out["areas"] = [{"id": area.id, "name": area.name,
                         "floor_id": getattr(area, "floor_id", None),
                         "icon": getattr(area, "icon", None),
                         # HA's own per-area climate readout (2025.2+); the
                         # manager's Rooms tab lets an installer set these.
                         "temperature_entity_id": getattr(area, "temperature_entity_id", None),
                         "humidity_entity_id": getattr(area, "humidity_entity_id", None)}
                        for area in areas.async_list_areas()]

        try:
            from homeassistant.helpers import floor_registry as fr

            out["floors"] = [{"id": floor.floor_id, "name": floor.name,
                              "level": floor.level, "icon": floor.icon}
                             for floor in fr.async_get(hass).async_list_floors()]
        except Exception as err:  # noqa: BLE001 — floors are newer than our floor
            _LOGGER.debug("floor registry collect failed: %s", err)
            out["floors"] = []

        ctx = registry_context(hass)

        device_list = all_devices(ctx["devices"])
        out["device_count"] = len(device_list)
        out["devices"] = [device_row(device, ctx) for device in device_list[:MAX_DEVICES]]

        entity_list = list(ctx["entities"].entities.values())
        out["entity_registry_count"] = len(entity_list)
        rows = [entity_row(reg, ctx, hass) for reg in entity_list]
        out["entities"] = rows[:MAX_ENTITIES]

        # Everything HA runs outside the registry, capped like the registry
        # sections. The manager's full inventory (registry_query with
        # include_unregistered) is where the rest of both lists come from.
        unregistered = unregistered_rows(hass, {reg.entity_id for reg in entity_list})
        out["unregistered_count"] = len(unregistered)
        out["unregistered_entities"] = unregistered[:MAX_UNREGISTERED]
        # Changes only when the inventory does, never with state, so the
        # manager can keep a full copy and refetch it only when this moves.
        out["entity_inventory_digest"] = inventory_digest(rows + unregistered)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("registry collect failed: %s", err)
    return out


async def _collect_backup(hass: HomeAssistant) -> dict:
    """Enough backup state for the fleet to answer 'is this home protected?'
    without asking the home a second question."""
    try:
        from .ws_bridge import call_own_ws

        info = await call_own_ws(hass, {"type": "backup/info"}, timeout=30)
        if not info.get("success"):
            return {}
        backups = (info.get("result") or {}).get("backups") or []
        latest = max(backups, key=lambda b: b.get("date") or "", default=None)

        config = await call_own_ws(hass, {"type": "backup/config/info"}, timeout=30)
        cfg = (config.get("result") or {}).get("config") or {}
        schedule = cfg.get("schedule") or {}
        return {
            "count": len(backups),
            "last_date": (latest or {}).get("date"),
            "last_name": (latest or {}).get("name"),
            "last_size": (latest or {}).get("size"),
            "automatic_configured": bool(cfg.get("automatic_backups_configured")),
            "recurrence": schedule.get("recurrence"),
            "next_automatic": schedule.get("next_automatic_backup"),
        }
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("backup collect failed: %s", err)
        return {}


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


async def _collect_frontend_theme(hass: HomeAssistant) -> dict:
    """BEST-EFFORT: the default themes as stored, as running, and the themes loaded.

    Both are needed because Home Assistant quietly swaps a stored default it
    cannot find for its own: a home set to `DarTec` before dartec-theme v1.1.0
    renamed the theme to `Dartec` runs unthemed while still storing `DarTec`,
    and the running value alone would say "default", as if chosen. The store
    is read through the frontend's own Store object (async, already cached by
    HA after the first load); the rest are the frontend's hass.data keys.
    Theme names only, never their contents.
    """
    out: dict[str, Any] = {}
    try:
        out["default"] = hass.data.get("frontend_default_theme")
        out["default_dark"] = hass.data.get("frontend_default_dark_theme")
        themes = hass.data.get("frontend_themes")
        if isinstance(themes, dict):
            out["available"] = sorted(str(name) for name in themes)[:50]
        store = hass.data.get("frontend_themes_store")
        stored = await store.async_load() if store is not None else None
        if isinstance(stored, dict):
            out["stored_default"] = stored.get("frontend_default_theme")
            out["stored_default_dark"] = stored.get("frontend_default_dark_theme")
        else:
            # Never saved: nothing was ever chosen, so the running value is it.
            out["stored_default"] = out["default"]
            out["stored_default_dark"] = out["default_dark"]
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("frontend theme collect failed: %s", err)
    return out


def _collect_branding(hass: HomeAssistant) -> dict:
    """The sidebar branding in force: whether it is on, and the name it shows."""
    try:
        from . import branding

        config = branding._config(hass)
        return {"enabled": bool(config.get("enabled")), "title": config.get("title")}
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("branding collect failed: %s", err)
        return {}


MAX_LOG_RECORDS = 50


def _collect_logs(hass: HomeAssistant) -> tuple[list[dict], int]:
    """BEST-EFFORT: reads the system_log component's deduplicated record store.

    Returns the newest records and the true count, because "50 errors" and
    "the last 50 of 214 errors" are very different things to read on a
    dashboard.
    """
    records = []
    out_total = 0
    try:
        handler = hass.data.get("system_log")
        store = getattr(handler, "records", None)
        if store:
            all_records = list(store.values())
            # Reported so the manager can say "last 50 of 214" rather than
            # implying the home only ever logged 50 things.
            out_total = len(all_records)
            for entry in all_records[-MAX_LOG_RECORDS:]:
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
    return records, out_total


def _collect_hacs_token(hass: HomeAssistant) -> dict:
    """Never the token: its fingerprint, or null without HACS or a token."""
    try:
        return hacs_token.snapshot_section(hass)
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("hacs token collect failed: %s", type(err).__name__)
        return {"token_fingerprint": None}


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
        info = await get("/info")

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
            # The shape ha_update.install_info returns, from the calls above.
            "install": _install_view(info, core_info, os_info),
        }
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("supervisor collect failed: %s", err)
        return None


def _install_view(info: dict, core_info: dict, os_info: dict) -> dict:
    haos = bool(info.get("hassos"))
    view = {"install": "haos" if haos else "supervised",
            "core": {"version": core_info.get("version"),
                     "latest": core_info.get("version_latest"),
                     "update_available": bool(core_info.get("update_available"))}}
    if haos:
        from .ha_update import running_os_version

        view["os"] = {"version": running_os_version(os_info),
                      "latest": os_info.get("version_latest"),
                      "update_available": bool(os_info.get("update_available")),
                      "boot": os_info.get("boot"),
                      "boot_slots": os_info.get("boot_slots") or {}}
    return view


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
