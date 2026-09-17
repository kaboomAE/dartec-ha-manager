from __future__ import annotations

from homeassistant.components.update import UpdateEntity


async def async_setup_entry(hass, config_entry, async_add_entities):
    async_add_entities([StubUpdate()])


class StubUpdate(UpdateEntity):
    _attr_name = "HACS stub"
    _attr_unique_id = "hacs_stub_update"
    _attr_installed_version = "1.0.0"
    _attr_latest_version = "1.0.0"
