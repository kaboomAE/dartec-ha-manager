"""One entity per platform HACS forwards, so unloading them is real work."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from . import DOMAIN


async def async_setup_entry(hass, config_entry, async_add_entities):
    async_add_entities([StubSwitch(hass)])


class StubSwitch(SwitchEntity):
    _attr_name = "HACS stub"
    _attr_unique_id = "hacs_stub_switch"
    _attr_is_on = True

    def __init__(self, hass) -> None:
        # Which token the running setup was given, and how often HACS's own
        # update listener has reloaded it.
        self._attr_extra_state_attributes = {
            "token_fingerprint": hass.data[DOMAIN].fingerprint,
            "listener_reloads": hass.data.get("hacs_stub_listener_reloads", 0),
        }
