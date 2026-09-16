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
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_ALLOW = "allow_maintenance"
SERVICE_END = "end_maintenance"

DEFAULT_MINUTES = 60
MAX_MINUTES = 480

_STATE_KEY = "_maintenance_until"
# Consent that does not need somebody at the tablet right now. Both live in
# the config entry's options, which means both are set on the home — there is
# deliberately no command that turns either on. See `consent()`.
OPT_COMMISSIONING_UNTIL = "commissioning_until"
OPT_STANDING_CONSENT = "unattended_support"
COMMISSIONING_MINUTES = 120
_REGISTERED_KEY = "_maintenance_services_registered"
_CANCEL_KEY = "_maintenance_expiry_cancel"
_NOTIFY_ID = "dartec_maintenance_window"

# Anything showing the window's state listens for this. Without it a switch
# would only notice the window closing when something happened to poll it,
# which for a 60-minute window means minutes of showing the wrong thing.
SIGNAL_UPDATE = f"{DOMAIN}_maintenance_updated"

# What the homeowner sees and touches. Named as an instruction rather than a
# state ("Allow Dartec support" reads as a thing you grant) because the whole
# point is that someone who has never opened Developer Tools can work it.
SWITCH_NAME = "Allow Dartec support"

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


def _entry_options(hass: HomeAssistant) -> dict:
    """Our config entry's options, read fresh — the installer can change them
    mid-session through the options flow."""
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
        return dict(entries[0].options) if entries else {}
    except Exception:                    # noqa: BLE001 — never break a command
        return {}


def consent(hass: HomeAssistant) -> dict:
    """May a sensitive operation run right now, and on whose authority?

    Three ways to say yes. What they have in common is the only property that
    matters: **each one is decided on this home.** The cloud may ask
    (`maintenance_request`) and may read the answer, but no field in any
    command grants it anything — a server we no longer control is still a
    server that cannot act unattended in a house that has not agreed. That is
    why there is no "force" here; it would hand the compromised-cloud case
    exactly the key this whole module exists to withhold.

    * ``window`` — the switch, opened by someone standing there. Unchanged.
    * ``commissioning`` — a time-boxed allowance written once, at pairing.
      Whoever paired this home was holding the pairing token and was standing
      in it; making them also flip a switch during an install they are
      physically performing is ceremony, and in practice it stalled installs.
      It expires on its own and nothing remote can renew it.
    * ``standing`` — an explicit opt-in in the integration's options, for
      sites that want unattended support. Off by default, visible in the UI,
      revocable there.
    """
    if is_open(hass):
        return {"allowed": True, "source": "window"}

    options = _entry_options(hass)
    if options.get(OPT_STANDING_CONSENT):
        return {"allowed": True, "source": "standing"}

    until = options.get(OPT_COMMISSIONING_UNTIL)
    if until:
        try:
            expiry = datetime.fromisoformat(str(until))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > _now():
                return {"allowed": True, "source": "commissioning",
                        "seconds_remaining": int((expiry - _now()).total_seconds())}
        except ValueError:
            _LOGGER.debug("Unparseable %s: %r", OPT_COMMISSIONING_UNTIL, until)
    return {"allowed": False, "source": None}


def status(hass: HomeAssistant) -> dict:
    """Window state, in the shape the manager's UI consumes."""
    until = _store(hass).get(_STATE_KEY)
    if not until or until <= _now():
        return {"open": False, "until": None, "seconds_remaining": 0,
                "consent": consent(hass)}
    return {"open": True, "until": until.isoformat(),
            "seconds_remaining": int((until - _now()).total_seconds()),
            "consent": consent(hass)}


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


def _notify_update(hass: HomeAssistant) -> None:
    try:
        async_dispatcher_send(hass, SIGNAL_UPDATE)
    except Exception as err:  # noqa: BLE001 — never let the display break the gate
        _LOGGER.debug("Could not signal maintenance update: %s", err)


def _cancel_expiry(hass: HomeAssistant) -> None:
    cancel = _store(hass).pop(_CANCEL_KEY, None)
    if cancel:
        try:
            cancel()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not cancel expiry timer: %s", err)


def _schedule_expiry(hass: HomeAssistant, until: datetime) -> None:
    """Close the window at its own deadline.

    ``is_open`` already compares against the clock, so the gate is correct
    without this. What this adds is that the *switch* turns itself off at the
    right moment instead of when something next happens to look — a homeowner
    watching a toggle that still says "on" twenty minutes after the window
    expired has been told something false about who can reach their locks.
    """
    from homeassistant.helpers.event import async_track_point_in_utc_time

    _cancel_expiry(hass)

    def _expired(_now) -> None:
        _store(hass).pop(_CANCEL_KEY, None)
        if not is_open(hass):
            logbook(hass, "Maintenance window expired")
            _dismiss(hass)
            _notify_update(hass)

    try:
        _store(hass)[_CANCEL_KEY] = async_track_point_in_utc_time(
            hass, _expired, until)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not schedule expiry: %s", err)


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
    _notify(hass, "Dartec support access is on",
            f"Dartec can now operate locks, covers, the alarm and restarts in "
            f"your home. This ends by itself in {minutes} minutes.\n\n"
            f"To end it now, switch **{SWITCH_NAME}** off.")
    _schedule_expiry(hass, until)
    _notify_update(hass)
    _LOGGER.info("Maintenance window opened for %s minutes", minutes)
    return status(hass)


def close_window(hass: HomeAssistant) -> dict:
    was_open = is_open(hass)
    _store(hass)[_STATE_KEY] = None
    _cancel_expiry(hass)
    if was_open:
        logbook(hass, "Maintenance window closed")
        _LOGGER.info("Maintenance window closed")
    _dismiss(hass)
    _notify_update(hass)
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
    _cancel_expiry(hass)
    store[_REGISTERED_KEY] = False
    store[_STATE_KEY] = None


def request_window(hass: HomeAssistant, reason: str = "") -> dict:
    """The cloud asking the homeowner to open a window. Raises a notification
    in the house; grants nothing."""
    detail = f"\n\nWhy: {reason}" if reason else ""
    _notify(hass, "Dartec is asking for permission",
            "Dartec support wants to operate locks, covers, the alarm or "
            f"restarts in your home.{detail}"
            f"\n\nTo allow it, switch **{SWITCH_NAME}** on. It turns itself "
            f"off again after {DEFAULT_MINUTES} minutes, and you can switch it "
            "off sooner at any time.\n\nIf you were not expecting this, "
            "leave it off — nothing happens until you allow it.")
    logbook(hass, f"Dartec support requested a maintenance window. {reason}".strip())
    return {"ok": True, "detail": "the homeowner has been asked to open a window",
            **status(hass)}


def entry_options(hass: HomeAssistant) -> dict:
    """The home's own options, for gates outside this module — the offsite
    backup opt-in (``service_policy.check_opt_in``) reads them here so it sees
    exactly what ``consent()`` sees, fresh on every command."""
    return _entry_options(hass)
