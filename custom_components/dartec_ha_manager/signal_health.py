"""Radio signal strength, per device.

Zigbee and Wi-Fi devices already publish how well they are heard. Home
Assistant models it, most integrations create the entity, and on the one home
this was written against **32 of 36 of those entities are disabled** — ZHA
creates RSSI sensors disabled by default, so the data exists and nothing is
recording it. Reading it costs one pass over the states machine; the value is
answering "is anything about to drop off?" before a customer notices something
stopped responding.

Deliberately not the mesh topology. Neighbour tables and routing are a
different, much larger job — two collectors, because ZHA and Zigbee2MQTT
expose them completely differently — and they answer a question nobody is
asking yet. Signal strength answers the one people do ask, from data already
modelled.

**Two scales, never mixed.** RSSI is dBm, negative, where -60 is healthy and
-90 is nearly gone. Zigbee2MQTT's link quality is 0-255, where higher is
better. Averaging or thresholding those together produces confident nonsense,
so each reading carries its scale and the thresholds live per scale.
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# A device with no signal reading is not a device with a bad one.
RSSI_UNIT_HINTS = ("dbm",)

# dBm. Below -85 a Zigbee link is usually still working and about to stop.
RSSI_WEAK = -80
RSSI_CRITICAL = -88

# Zigbee2MQTT link quality, 0-255. Under ~25 is where devices start dropping.
LQI_WEAK = 40
LQI_CRITICAL = 20

MAX_READINGS = 400


def classify(kind: str, value: float) -> str:
    """ok | weak | critical, on the scale the reading is actually in."""
    if kind == "lqi":
        if value <= LQI_CRITICAL:
            return "critical"
        return "weak" if value <= LQI_WEAK else "ok"
    if value <= RSSI_CRITICAL:
        return "critical"
    return "weak" if value <= RSSI_WEAK else "ok"


def reading_kind(entity_id: str, unit: str | None, device_class: str | None) -> str | None:
    """Which scale this entity is on, or None if it is not a signal reading.

    Checked in order of how much the source is trusted: an explicit unit of
    dBm is unambiguous, `signal_strength` is Home Assistant's own
    classification, and the entity id is the last resort for integrations that
    set neither — which Zigbee2MQTT's linkquality does not.
    """
    lowered = (entity_id or "").lower()
    if unit and unit.strip().lower() in RSSI_UNIT_HINTS:
        return "rssi"
    if device_class == "signal_strength":
        return "rssi"
    if "linkquality" in lowered or "link_quality" in lowered:
        return "lqi"
    if lowered.endswith("_rssi") or "_rssi_" in lowered:
        return "rssi"
    return None


def collect_signal(hass) -> list[dict[str, Any]]:
    """One reading per device that reports how well it is heard.

    Only enabled entities with a numeric state appear. A disabled entity is
    reported by `disabled_signal_entities` instead, so the manager can say
    "this home has 32 signal sensors switched off" rather than "this home has
    no signal problems".
    """
    readings: list[dict[str, Any]] = []
    try:
        from homeassistant.helpers import area_registry as ar
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        entities = er.async_get(hass)
        devices = dr.async_get(hass)
        area_names = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}

        for reg in entities.entities.values():
            if reg.disabled_by is not None:
                continue
            state = hass.states.get(reg.entity_id)
            if state is None:
                continue
            unit = state.attributes.get("unit_of_measurement")
            kind = reading_kind(reg.entity_id, unit,
                                reg.device_class or reg.original_device_class)
            if kind is None:
                continue
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                # unknown / unavailable. Absence of a reading is not a bad
                # reading, and recording it as one would invent an outage.
                continue

            device = devices.devices.get(reg.device_id) if reg.device_id else None
            area_id = reg.area_id or (device.area_id if device else None)
            readings.append({
                "entity_id": reg.entity_id,
                "device_id": reg.device_id,
                "device": (device.name_by_user or device.name) if device else None,
                "area": area_names.get(area_id) if area_id else None,
                "kind": kind, "value": value, "unit": unit,
                "status": classify(kind, value),
            })
            if len(readings) >= MAX_READINGS:
                break
    except Exception as err:  # noqa: BLE001 — best-effort, like every collector
        _LOGGER.debug("signal collect failed: %s", err)
    return readings


def disabled_signal_entities(hass) -> list[str]:
    """Signal entities that exist but are switched off.

    ZHA creates RSSI sensors disabled by default, so on a typical home this is
    most of them. Worth reporting rather than silently having no data: a home
    with 32 switched-off sensors and a home with genuinely strong radio look
    identical otherwise.
    """
    out: list[str] = []
    try:
        from homeassistant.helpers import entity_registry as er

        for reg in er.async_get(hass).entities.values():
            if reg.disabled_by is None:
                continue
            if reading_kind(reg.entity_id, None,
                            reg.device_class or reg.original_device_class):
                out.append(reg.entity_id)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("disabled signal scan failed: %s", err)
    return out[:MAX_READINGS]
