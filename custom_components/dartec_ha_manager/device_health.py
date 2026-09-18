"""Batteries, and devices that have stopped answering — summarised on the home.

The manager alerts on both, but it must not be sent every entity's state every
minute to do it: the snapshot is a 60-second heartbeat. So the agent does the
reading and ships two short lists, and the manager does the deciding —
thresholds are per organisation, and "how long has it been down" and "have we
already told anyone" need memory the home does not keep.

**Registry entities only.** Both lists are built from the entity registry,
because a battery or an outage is only useful attached to a *device*, and only
registered entities have one. An entity whose integration gives it no
`unique_id` never enters the registry, so it is invisible here as it is to
every other part of the snapshot (the manager's "registry blind spot"). That
gap is documented, not papered over.

**How an expected-unavailable entity is told from a device that dropped off.**
A device is reported offline when every entity it has that *should* hold a
state is `unavailable` or `unknown`. What is left out, and why:

1. A device the user disabled, and every disabled entity. Home Assistant does
   not run them, so they have no state to judge.
2. A *service* device (`entry_type: service`) — Backup, Sun, a weather
   forecast. It is software, not something in the house that can drop off, and
   most of HA's own Backup device reads `unknown` until the first automatic
   backup runs.
3. Entities of stateless domains (`button`, `event`, `scene`, `notify`, ...).
   Their state is the time they were last used, and `unknown` until then —
   never pressed, not broken. A device made only of these is never judged.
4. A device whose integration is not loaded. A disabled integration is
   deliberate, and a failed one is already its own alert; a second alert per
   device would bury it.
5. Anything carrying the label `dartec_expected_offline` (create a label named
   "Dartec expected offline" in Home Assistant). The escape hatch for a
   seasonal device — pool pump in winter, Christmas lights — on the device to
   skip it, or on one entity to stop that entity counting.

`unknown` counts as down alongside `unavailable`: an integration that cannot
reach a device often reports its last-known state as unknown. The stateless
exclusion above is what keeps that from sweeping up buttons.

"Since when" is the latest `last_changed` among the device's entities — the
moment the last of them went down. Home Assistant resets `last_changed` when it
restarts, so after a restart this is the restart time, which understates an
outage rather than inventing one; the manager keeps its own first-seen time
across restarts and uses the earlier of the two.

Kept free of Home Assistant imports at module level, so the rules can be
tested on their own like signal_health and registry_paging.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

# Domains whose state is a timestamp of the last use, `unknown` until then.
STATELESS_DOMAINS = frozenset({
    "button", "input_button", "event", "scene", "notify", "tts", "stt",
    "conversation", "wake_word", "ai_task", "image",
})
DOWN_STATES = frozenset({"unavailable", "unknown"})
EXPECTED_OFFLINE_LABEL = "dartec_expected_offline"

# Phones and tablets charge every night and are not part of the installation;
# a companion app's battery sensor would alert every evening.
BATTERY_EXCLUDED_PLATFORMS = frozenset({"mobile_app"})

# Lowest first, so a cap drops the healthy end. 200 battery devices is a very
# large house; the true total travels alongside.
MAX_BATTERIES = 200
MAX_OFFLINE = 100


def battery_reading(domain: str, device_class: str | None, state: str | None,
                    unit: str | None) -> tuple[str, Any] | None:
    """("level", percent) | ("low", bool) | None if this is not a usable reading.

    `battery_charging` is a different device class and deliberately not read:
    "not charging" is not "low".
    """
    if device_class != "battery" or state is None or state in DOWN_STATES:
        return None
    if domain == "binary_sensor":
        if state not in ("on", "off"):
            return None
        return ("low", state == "on")
    if domain != "sensor":
        return None
    if unit is not None and unit.strip() != "%":
        # A voltage reported with the battery class is not a percentage, and
        # reading 3.0 V as 3% would page someone about a healthy device.
        return None
    try:
        value = float(state)
    except (TypeError, ValueError):
        return None
    if not 0 <= value <= 100:
        return None
    return ("level", value)


def _sort_key(row: dict) -> tuple:
    level = row.get("level")
    # A device that only says "low" sorts above every known level: it is
    # flagged by the device itself, and has no number to rank it by.
    return (level if level is not None else (-1 if row.get("low") else 101),
            row.get("device") or "")


def summarize_batteries(readings: Iterable[dict]) -> list[dict]:
    """Merge readings into one row per device, lowest first.

    A device with both a percentage and a low flag keeps both: the percentage
    ranks it, the flag is what the device itself thinks.
    """
    by_device: dict[str, dict] = {}
    for reading in readings:
        device_id = reading["device_id"]
        row = by_device.setdefault(device_id, {
            "device_id": device_id, "device": reading.get("device"),
            "area": reading.get("area"), "level": None, "low": None,
            "entity_id": reading.get("entity_id")})
        kind, value = reading["reading"]
        if kind == "level":
            # Two percentage sensors on one device (rare, some locks): the
            # lower one is the one that runs out.
            if row["level"] is None or value < row["level"]:
                row["level"] = value
                row["entity_id"] = reading.get("entity_id")
        else:
            row["low"] = bool(row["low"]) or value
    return sorted(by_device.values(), key=_sort_key)


def device_offline(entities: Iterable[dict]) -> str | None:
    """The ISO time the device went down, or None if it is not offline.

    `entities` are the device's entities as dicts with `domain`, `state`,
    `last_changed` (ISO string), `disabled` and `labels`. See the module
    docstring for what is excluded and why.
    """
    judged = [e for e in entities
              if not e.get("disabled") and e.get("state") is not None
              and e.get("domain") not in STATELESS_DOMAINS
              and EXPECTED_OFFLINE_LABEL not in (e.get("labels") or ())]
    if not judged or any(e["state"] not in DOWN_STATES for e in judged):
        return None
    changed = [e.get("last_changed") for e in judged if e.get("last_changed")]
    return max(changed) if changed else None


def device_is_judged(device: dict, loaded_entries: set[str]) -> bool:
    """Whether a device can be called offline at all (rules 1, 2, 4, 5)."""
    if device.get("disabled") or device.get("entry_type") == "service":
        return False
    if EXPECTED_OFFLINE_LABEL in (device.get("labels") or ()):
        return False
    entries = device.get("config_entries") or ()
    # No config entry at all is a device another integration created by hand;
    # judge it on its entities. Otherwise at least one entry must be running.
    return not entries or any(entry in loaded_entries for entry in entries)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def collect(hass) -> dict[str, Any]:
    """The snapshot sections `batteries` and `offline_devices`, with totals.

    One pass over the entity registry. Best-effort like every collector: a
    failure returns empty sections flagged with `device_health_error`, never a
    lost snapshot — and the manager leaves its memory alone for that cycle.
    """
    out: dict[str, Any] = {"batteries": [], "battery_count": 0,
                           "offline_devices": [], "offline_count": 0,
                           "devices_judged": 0}
    try:
        from homeassistant.config_entries import ConfigEntryState
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        from .registry_access import all_devices

        area_names = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        devices = {d.id: d for d in all_devices(dr.async_get(hass))}
        loaded = {entry.entry_id for entry in hass.config_entries.async_entries()
                  if entry.state is ConfigEntryState.LOADED}

        def device_name(device) -> str | None:
            return device.name_by_user or device.name

        readings: list[dict] = []
        per_device: dict[str, list[dict]] = {}
        for reg in er.async_get(hass).entities.values():
            if not reg.device_id or reg.device_id not in devices:
                continue
            state = hass.states.get(reg.entity_id)
            per_device.setdefault(reg.device_id, []).append({
                "domain": reg.domain,
                "state": state.state if state else None,
                "last_changed": _iso(state.last_changed) if state else None,
                "disabled": reg.disabled_by is not None,
                "labels": getattr(reg, "labels", None) or (),
            })
            if (state is None or reg.disabled_by is not None
                    or reg.platform in BATTERY_EXCLUDED_PLATFORMS):
                continue
            reading = battery_reading(
                reg.domain, reg.device_class or reg.original_device_class,
                state.state, state.attributes.get("unit_of_measurement"))
            if reading is None:
                continue
            device = devices[reg.device_id]
            area_id = reg.area_id or device.area_id
            readings.append({"device_id": reg.device_id, "device": device_name(device),
                             "area": area_names.get(area_id) if area_id else None,
                             "entity_id": reg.entity_id, "reading": reading})

        batteries = summarize_batteries(readings)
        out["battery_count"] = len(batteries)
        out["batteries"] = batteries[:MAX_BATTERIES]

        offline: list[dict] = []
        judged = 0
        for device_id, entities in per_device.items():
            device = devices[device_id]
            facts = {"disabled": device.disabled_by is not None,
                     "entry_type": getattr(device.entry_type, "value", device.entry_type),
                     "labels": getattr(device, "labels", None) or (),
                     "config_entries": device.config_entries}
            if not device_is_judged(facts, loaded):
                continue
            judged += 1
            since = device_offline(entities)
            if since is None:
                continue
            identifiers = sorted(ident[0] for ident in device.identifiers or ())
            offline.append({
                "device_id": device_id, "device": device_name(device),
                "area": area_names.get(device.area_id) if device.area_id else None,
                "since": since,
                "integration": identifiers[0] if identifiers else None,
                "entities": sum(1 for e in entities if not e["disabled"]),
            })
        # Longest-down first, so a cap keeps the ones closest to alerting.
        offline.sort(key=lambda row: row["since"])
        out["devices_judged"] = judged
        out["offline_count"] = len(offline)
        out["offline_devices"] = offline[:MAX_OFFLINE]
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("device health collect failed: %s", err)
        out["device_health_error"] = type(err).__name__
    return out
