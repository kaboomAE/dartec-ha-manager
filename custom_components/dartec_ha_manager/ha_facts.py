"""The plain facts the network and camera adapters read, built once from HA.

An adapter maps one brand's Home Assistant entities onto Baytec's common
shape (network_health.py, camera_health.py). It never touches Home Assistant
itself: it is handed lists of dicts built here, so every adapter can be tested
without Home Assistant, and so what an adapter can see is decided in one
place.

**What is copied, and what is not.** An entity fact holds its registry
identity (platform, domain, `unique_id`, `translation_key`, device), its
state and when that last changed, its labels, and only the state attributes
in `ALLOWED_ATTRIBUTES`. A camera entity's `entity_picture` and
`access_token`, an image entity's URL, a stream's source — none of them is in
that list, so no adapter can pass one on (media_guard.py, the owner's rule of
2026-10-18).

Entities are matched by **integration and `unique_id` / `translation_key`**,
never by entity id. An entity id is made from a name the household can change
at any time; the unique id is the integration's own and is stable.
"""
from __future__ import annotations

import logging
from datetime import datetime
from collections.abc import Iterable
from typing import Any

_LOGGER = logging.getLogger(__name__)

# The only state attributes an adapter is given. Versions for the firmware
# column, the unit to read a size or a duration correctly, and the class.
ALLOWED_ATTRIBUTES = ("installed_version", "latest_version", "unit_of_measurement",
                      "device_class", "friendly_name")


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def entity_facts(hass, platforms: Iterable[str] | None = None,
                 domains: Iterable[str] | None = None) -> list[dict]:
    """One fact per registered entity of the given integrations (or domains).

    `platforms` selects by integration (`unifi`, `unifiprotect`); `domains`
    adds every entity of those domains whatever its integration, which is how
    the generic camera adapter finds cameras of brands with no adapter. For
    `domains` it also adds the ones Home Assistant runs outside its registry
    (no `unique_id`: a YAML camera), with no platform, device or unique id.
    """
    from homeassistant.helpers import entity_registry as er

    wanted_platforms = set(platforms or ())
    wanted_domains = set(domains or ())
    facts = []
    for reg in er.async_get(hass).entities.values():
        if reg.platform not in wanted_platforms and reg.domain not in wanted_domains:
            continue
        state = hass.states.get(reg.entity_id)
        attributes = state.attributes if state is not None else {}
        facts.append({
            "entity_id": reg.entity_id,
            "domain": reg.domain,
            "platform": reg.platform,
            "unique_id": str(reg.unique_id or ""),
            "translation_key": getattr(reg, "translation_key", None),
            "device_id": reg.device_id,
            "disabled": reg.disabled_by is not None,
            "labels": sorted(getattr(reg, "labels", None) or ()),
            "state": state.state if state is not None else None,
            "last_changed": _iso(state.last_changed) if state is not None else None,
            "attributes": _allowed(attributes),
        })
    if wanted_domains:
        registered = {reg.entity_id for reg in er.async_get(hass).entities.values()}
        for state in hass.states.async_all(tuple(wanted_domains)):
            if state.entity_id in registered:
                continue
            facts.append({
                "entity_id": state.entity_id, "domain": state.domain, "platform": None,
                "unique_id": "", "translation_key": None, "device_id": None,
                "disabled": False, "labels": [], "state": state.state,
                "last_changed": _iso(state.last_changed),
                "attributes": _allowed(state.attributes),
            })
    return facts


def _allowed(attributes) -> dict:
    """Only the attributes in ALLOWED_ATTRIBUTES, and only plain values."""
    return {k: attributes.get(k) for k in ALLOWED_ATTRIBUTES
            if isinstance(attributes.get(k), (str, int, float, bool))}


def labelled_facts(hass, label_ids: Iterable[str]) -> list[dict]:
    """Every registered entity carrying one of these labels, as a fact."""
    from homeassistant.helpers import entity_registry as er

    wanted = set(label_ids)
    platforms = {reg.platform for reg in er.async_get(hass).entities.values()
                 if wanted & set(getattr(reg, "labels", None) or ())}
    return [f for f in entity_facts(hass, platforms) if wanted & set(f["labels"])]


def devices_from(hass, manufacturer: str, adapted: Iterable[str]) -> list[dict]:
    """Devices of one manufacturer that arrive through an integration with no
    adapter: {"integration", "devices", "models"} per integration.

    UniFi equipment can be in Home Assistant through an integration that
    reports none of its health (the owner's home, 2026-10-18: UniFi Network
    Rules, which manages firewall rules, port forwards and Wi-Fi, and has no
    device state, clients or firmware). Saying so is what lets the manager
    tell staff which integration to add, rather than show an empty panel."""
    from homeassistant.helpers import device_registry as dr

    from .registry_access import all_devices, device_config_entries

    domains = {entry.entry_id: entry.domain for entry in hass.config_entries.async_entries()}
    adapted = set(adapted)
    wanted = manufacturer.lower()
    found: dict[str, dict] = {}
    for device in all_devices(dr.async_get(hass)):
        if wanted not in str(device.manufacturer or "").lower() or device.disabled_by is not None:
            continue
        integrations = {domains.get(entry) for entry in device_config_entries(device)} - {None}
        if not integrations or integrations & adapted:
            continue
        for integration in sorted(integrations):
            row = found.setdefault(integration, {"integration": integration, "devices": 0,
                                                 "models": []})
            row["devices"] += 1
            if device.model and device.model not in row["models"]:
                row["models"].append(str(device.model)[:40])
    return sorted(found.values(), key=lambda r: r["integration"])


def device_facts(hass, device_ids: Iterable[str]) -> dict[str, dict]:
    """The devices these entities belong to, by id."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr

    areas = {area.id: area.name for area in ar.async_get(hass).async_list_areas()}
    registry = dr.async_get(hass)
    out = {}
    for device_id in set(device_ids):
        if not device_id:
            continue
        device = registry.async_get(device_id)
        if device is None:
            continue
        out[device_id] = {
            "device_id": device_id,
            "name": device.name_by_user or device.name,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "sw_version": device.sw_version,
            "area": areas.get(device.area_id) if device.area_id else None,
            "labels": sorted(getattr(device, "labels", None) or ()),
            "disabled": device.disabled_by is not None,
            "entry_type": getattr(device.entry_type, "value", device.entry_type),
        }
    return out
