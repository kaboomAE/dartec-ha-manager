"""A deliberately badly-behaved custom integration, for the live test only.

It commits the two mistakes the agent must not: it uses
`device_registry.devices` as a mapping and reads a file in the event loop.
The driver requires Home Assistant to log both against *this* integration.
Without that, "no warning about dartec_ha_manager" could pass because the
warning's wording changed, or because detection is off in this image, rather
than because the agent is clean.

Never installed anywhere but the throwaway test container.
"""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dartec_live_canary"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def misbehave(_event: Event) -> None:
        from homeassistant.helpers import device_registry as dr

        try:
            count = len(list(dr.async_get(hass).devices.values()))
            _LOGGER.info("canary: mapping access returned %s devices", count)
        except Exception:  # noqa: BLE001 — the log line is what is being tested
            _LOGGER.exception("canary: mapping access raised")
        try:
            with open(Path(__file__).with_name("manifest.json"), encoding="utf-8") as handle:
                handle.read()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("canary: blocking open raised")

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, misbehave)
    return True
