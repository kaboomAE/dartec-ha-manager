"""The homeowner's consent, as something they can actually touch.

The maintenance window has always been two service calls. That is fine for an
installer and useless for the person who owns the house: nobody outside this
trade knows what Developer Tools is, and a notification that says "run the
dartec_ha_manager.end_maintenance service" is, to a customer, a notification
that says there is nothing they can do.

So the same window is also a switch. On grants Dartec support the sensitive
operations for the default period; off revokes it immediately. It appears on
their dashboard next to their lights.

A second switch, "Allow Dartec to install approved updates",
is the homeowner's opt-out from guarded updates (service_policy.py,
GUARDED_ACTIONS). It is on by default and is stored in the entry's options, so
it survives restarts, unlike the window. Neither switch can be operated by the
manager: calls on this integration's own entities are refused
(``check_own_entities``).

Three things worth keeping true:

**The switch reports the window, it does not hold it.** State comes from
``maintenance.switch_state`` every time it is asked, so a window opened by the
service call, closed by a restart, or expired on its own shows correctly here.
Two sources of truth for "may Dartec open the front door" is exactly the bug
nobody wants.

**It is on while the home is commissioning.** Pairing opens a commissioning
period (see ``maintenance.commissioning``), and during it Dartec can operate
the locks whether or not a window is open. A switch reading "off" then would
tell the homeowner access is shut when it is not. So it reads on, and its
``commissioning_ends_at`` attribute says when that ends. Switching it off ends
commissioning for good, not just the window: "off" has to mean off, and there
is no way back into commissioning short of pairing the home again.

**It follows the window rather than polling it.** ``SIGNAL_UPDATE`` fires on
every open and close, including the timed expiry, so the toggle flips at the
moment the window really ends. A switch still reading "on" after access has
lapsed tells the owner something false about who can reach their locks.

**It is a switch, not a button.** A button could open a window; only something
with state can show one is open, and showing it is half the point.

**Its entity id is `switch.allow_dartec_support`, deliberately.** Left to
itself the platform derives the id from the device and produces
`switch.dartec_ha_manager_allow_dartec_support`, which stutters "Dartec"
twice and is not what anyone types when they go looking for it. The first
person to put this on a dashboard reached for `switch.allow_dartec_support`,
got nothing, and could not save the card. This is a control a homeowner is
meant to find, so it is named the way they will look for it — see the
entity_id line in __init__.
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
from .service_policy import GUARDED_SWITCH_NAME, OPT_GUARDED_UPDATES, guarded_enabled


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([MaintenanceSwitch(entry), GuardedUpdatesSwitch(entry)])


class MaintenanceSwitch(SwitchEntity):
    """Allow Dartec support — on for the length of the window, off otherwise."""

    # has_entity_name is Home Assistant's convention and it earns its keep on
    # the device page, where this shows as just "Allow Dartec support". It has
    # no bearing on the entity id, which is set outright below — turning it off
    # was tried and changed the id not at all.
    _attr_has_entity_name = True
    _attr_name = "Allow Dartec support"
    _attr_icon = "mdi:account-wrench"
    # Nothing to poll: the window is in memory and announces its own changes.
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        # Suggest the entity id outright. Turning off has_entity_name was not
        # enough on its own: the platform still derived the id from the device,
        # giving switch.dartec_ha_manager_allow_dartec_support. Setting
        # entity_id here is Home Assistant's supported way for an entity to
        # propose its own, and it only ever suggests — a collision would still
        # be resolved with a suffix rather than stealing another entity's id.
        self.entity_id = "switch.allow_dartec_support"
        self._attr_unique_id = f"{entry.entry_id}_allow_dartec_support"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Dartec HA Manager",
            manufacturer="Dartec",
        )

    @property
    def is_on(self) -> bool:
        return maintenance.switch_state(self.hass)["on"]

    @property
    def extra_state_attributes(self) -> dict:
        """When it ends, so a dashboard can say so without a template."""
        state = maintenance.switch_state(self.hass)
        return {"ends_at": state["ends_at"],
                "minutes_remaining": state["minutes_remaining"],
                "commissioning": state["commissioning"],
                "commissioning_ends_at": state["commissioning_ends_at"]}

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
        # Off means off: during commissioning the window was never what kept
        # access open, so closing it alone would leave the switch on.
        maintenance.complete_commissioning(self.hass, "local")
        self.async_write_ha_state()


class GuardedUpdatesSwitch(SwitchEntity):
    """The homeowner's opt-out from approved updates: Home Assistant Core
    and OS, and (since 2026-09-18) the Dartec agent's own.

    On by default: the owner's decision (2026-09-18) is that security fixes
    reach homes nobody is attending. Off stops the next update. An update
    already under way finishes or rolls back, because an update stopped
    half-way is worse than either end of it. Each change is written to the
    logbook, since it changes what Dartec may do in the house.
    """

    _attr_has_entity_name = True
    _attr_name = GUARDED_SWITCH_NAME
    _attr_icon = "mdi:update"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        # Named the way someone would look for it, as with the switch above.
        self.entity_id = "switch.dartec_approved_updates"
        self._attr_unique_id = f"{entry.entry_id}_guarded_updates"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def is_on(self) -> bool:
        return guarded_enabled(self._entry.options)

    async def async_added_to_hass(self) -> None:
        # The options form changes the same setting, and fires this signal.
        self.async_on_remove(async_dispatcher_connect(
            self.hass, maintenance.SIGNAL_UPDATE, self.async_write_ha_state))

    async def _set(self, value: bool) -> None:
        if guarded_enabled(self._entry.options) == value:
            return
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, OPT_GUARDED_UPDATES: value})
        maintenance.logbook(self.hass, "Approved updates from Dartec "
                                       f"turned {'on' if value else 'off'} here")
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)
