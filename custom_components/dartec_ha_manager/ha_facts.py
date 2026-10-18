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
    the generic camera adapter finds cameras of brands with no adapter.
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
            "attributes": {k: attributes.get(k) for k in ALLOWED_ATTRIBUTES
                           if attributes.get(k) is not None},
        })
    return facts


def labelled_facts(hass, label_ids: Iterable[str]) -> list[dict]:
    """Every registered entity carrying one of these labels, as a fact."""
    from homeassistant.helpers import entity_registry as er

    wanted = set(label_ids)
    platforms = {reg.platform for reg in er.async_get(hass).entities.values()
                 if wanted & set(getattr(reg, "labels", None) or ())}
    return [f for f in entity_facts(hass, platforms) if wanted & set(f["labels"])]


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
