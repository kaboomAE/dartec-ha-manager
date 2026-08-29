"""The maintenance window — the homeowner's consent, held locally.

Sensitive operations (see ``service_policy.py``) need a window that only
someone standing in the house can open: two Home Assistant services, callable
from a dashboard button, an automation, or Developer Tools. The cloud can
*ask* for one (``maintenance_request`` raises a notification) but cannot grant
itself one.

The window lives in memory on purpose. A restart closes it, so the failure
mode is "support has to ask again", never "the door was left open".

Every command the cloud executes is also written to this instance's own
logbook. That matters commercially as much as technically: the homeowner can
audit what Dartec did in their house using their own system, rather than
taking our word from our own database.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_ALLOW = "allow_maintenance"
SERVICE_END = "end_maintenance"

DEFAULT_MINUTES = 60
MAX_MINUTES = 480

_STATE_KEY = "_maintenance_until"
_REGISTERED_KEY = "_maintenance_services_registered"
_NOTIFY_ID = "dartec_maintenance_window"

ALLOW_SCHEMA = vol.Schema({
    vol.Optional("minutes", default=DEFAULT_MINUTES):
        vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_MINUTES)),
})


def _store(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DOMAIN, {})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_open(hass: HomeAssistant) -> bool:
    until = _store(hass).get(_STATE_KEY)
    return bool(until and until > _now())


def status(hass: HomeAssistant) -> dict:
    """Window state, in the shape the manager's UI consumes."""
    until = _store(hass).get(_STATE_KEY)
    if not until or until <= _now():
        return {"open": False, "until": None, "seconds_remaining": 0}
    return {"open": True, "until": until.isoformat(),
            "seconds_remaining": int((until - _now()).total_seconds())}


def logbook(hass: HomeAssistant, message: str) -> None:
    """Write one line into the homeowner's own logbook."""
    try:
        hass.bus.async_fire("logbook_entry", {
            "name": "Dartec HA Manager", "message": message, "domain": DOMAIN})
    except Exception as err:  # noqa: BLE001 — auditing must never break a command
        _LOGGER.debug("Could not write logbook entry: %s", err)


def _notify(hass: HomeAssistant, title: str, message: str) -> None:
    try:
        from homeassistant.components import persistent_notification

        persistent_notification.async_create(
            hass, message, title=title, notification_id=_NOTIFY_ID)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not raise notification: %s", err)


def _dismiss(hass: HomeAssistant) -> None:
    try:
        from homeassistant.components import persistent_notification

        persistent_notification.async_dismiss(hass, _NOTIFY_ID)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not dismiss notification: %s", err)


def open_window(hass: HomeAssistant, minutes: int = DEFAULT_MINUTES) -> dict:
    minutes = max(1, min(int(minutes), MAX_MINUTES))
    until = _now() + timedelta(minutes=minutes)
    _store(hass)[_STATE_KEY] = until
    logbook(hass, f"Maintenance window opened for {minutes} minutes — Dartec "
                  "support may now perform sensitive operations")
    _notify(hass, "Dartec maintenance window open",
            f"Dartec support can perform sensitive operations (locks, covers, "
            f"alarm, reboots) for the next {minutes} minutes. Run the "
            f"'{DOMAIN}.{SERVICE_END}' service to end it immediately.")
    _LOGGER.info("Maintenance window opened for %s minutes", minutes)
    return status(hass)


def close_window(hass: HomeAssistant) -> dict:
    was_open = is_open(hass)
    _store(hass)[_STATE_KEY] = None
    if was_open:
        logbook(hass, "Maintenance window closed")
        _LOGGER.info("Maintenance window closed")
    _dismiss(hass)
    return status(hass)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the two homeowner-facing services, once per HA instance."""
    store = _store(hass)
    if store.get(_REGISTERED_KEY):
        return

    async def _allow(call: ServiceCall) -> None:
        open_window(hass, call.data.get("minutes", DEFAULT_MINUTES))

    async def _end(call: ServiceCall) -> None:
        close_window(hass)

    hass.services.async_register(DOMAIN, SERVICE_ALLOW, _allow, schema=ALLOW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_END, _end)
    store[_REGISTERED_KEY] = True


async def async_unregister_services(hass: HomeAssistant) -> None:
    store = _store(hass)
    if not store.get(_REGISTERED_KEY):
        return
    hass.services.async_remove(DOMAIN, SERVICE_ALLOW)
    hass.services.async_remove(DOMAIN, SERVICE_END)
    store[_REGISTERED_KEY] = False
    store[_STATE_KEY] = None


def request_window(hass: HomeAssistant, reason: str = "") -> dict:
    """The cloud asking the homeowner to open a window. Raises a notification
    in the house; grants nothing."""
    detail = f"\n\nReason given: {reason}" if reason else ""
    _notify(hass, "Dartec support needs permission",
            "Dartec support has asked to perform a sensitive operation in your "
            f"home (locks, covers, alarm, reboots or account changes).{detail}"
            f"\n\nTo allow it, run the '{DOMAIN}.{SERVICE_ALLOW}' service. It "
            "expires automatically.")
    logbook(hass, f"Dartec support requested a maintenance window. {reason}".strip())
    return {"ok": True, "detail": "the homeowner has been asked to open a window",
            **status(hass)}
