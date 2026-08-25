"""DarTec HA Manager agent — links this Home Assistant instance to the
DarTec centralized fleet dashboard via a single outbound WebSocket."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .cloud_link import CloudLink
from .const import CONF_PAIRING_TOKEN, CONF_SERVER_URL, DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    link = CloudLink(hass, entry.data[CONF_SERVER_URL], entry.data[CONF_PAIRING_TOKEN])
    link.start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = link
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    link: CloudLink | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if link:
        await link.stop()
    return True
