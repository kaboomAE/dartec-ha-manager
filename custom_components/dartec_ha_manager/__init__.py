"""Dartec HA Manager agent — links this Home Assistant instance to the
Dartec centralized fleet dashboard via a single outbound WebSocket."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import maintenance
from .branding import async_setup_branding
from .cloud_link import CloudLink
from .const import CONF_PAIRING_TOKEN, CONF_SERVER_URL, DOMAIN

# The switch that lets a homeowner grant and revoke Dartec's access to the
# sensitive operations. It is the only entity this integration creates, and
# the only control in it a customer is expected to touch.
PLATFORMS = [Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Branding is restored from the entry's options, so a home keeps its
    # installer branding across restarts even if the manager is unreachable.
    await async_setup_branding(hass, entry.options.get("branding"))

    # Sensitive remote operations are gated on a window only someone in the
    # house can open. Registering these is what makes that consent possible.
    await maintenance.async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Commissioning is stored in the entry, so it outlives the restarts an
    # install is full of; this re-arms the timer that turns the switch off at
    # the cap, which is the one part of it that lived in memory.
    maintenance.schedule_commissioning_expiry(hass)

    link = CloudLink(hass, entry.data[CONF_SERVER_URL], entry.data[CONF_PAIRING_TOKEN])
    link.start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = link
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await maintenance.async_unregister_services(hass)
    link: CloudLink | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if link:
        await link.stop()
    return True
