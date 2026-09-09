"""The homeowner's consent, as something they can actually touch.

The maintenance window has always been two service calls. That is fine for an
installer and useless for the person who owns the house: nobody outside this
trade knows what Developer Tools is, and a notification that says "run the
dartec_ha_manager.end_maintenance service" is, to a customer, a notification
that says there is nothing they can do.

So the same window is also a switch. On grants Dartec support the sensitive
operations for the default period; off revokes it immediately. It appears on
their dashboard next to their lights, and it is the only control in this
integration a customer is ever expected to use.

Three things worth keeping true:

**The switch reports the window, it does not hold it.** State comes from
``maintenance.is_open`` every time it is asked, so a window opened by the
service call, closed by a restart, or expired on its own shows correctly here.
Two sources of truth for "may Dartec open the front door" is exactly the bug
nobody wants.

**It follows the window rather than polling it.** ``SIGNAL_UPDATE`` fires on
every open and close, including the timed expiry, so the toggle flips at the
moment the window really ends. A switch still reading "on" after access has
lapsed tells the owner something false about who can reach their locks.

**It is a switch, not a button.** A button could open a window; only something
with state can show one is open, and showing it is half the point.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import maintenance
from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([MaintenanceSwitch(entry)])


class MaintenanceSwitch(SwitchEntity):
    """Allow Dartec support — on for the length of the window, off otherwise."""

    _attr_has_entity_name = True
    _attr_name = "Allow Dartec support"
    _attr_icon = "mdi:account-wrench"
    # Nothing to poll: the window is in memory and announces its own changes.
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_allow_dartec_support"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Dartec HA Manager",
            manufacturer="Dartec",
        )

    @property
    def is_on(self) -> bool:
        return maintenance.is_open(self.hass)

    @property
    def extra_state_attributes(self) -> dict:
        """When it ends, so a dashboard can say so without a template."""
        state = maintenance.status(self.hass)
        return {"ends_at": state["until"],
                "minutes_remaining": round(state["seconds_remaining"] / 60)}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, maintenance.SIGNAL_UPDATE, self._window_changed))

    @callback
    def _window_changed(self) -> None:
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        maintenance.open_window(self.hass, maintenance.DEFAULT_MINUTES)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        maintenance.close_window(self.hass)
        self.async_write_ha_state()
