"""The maintenance window — the homeowner's consent, held locally.

Sensitive operations (see ``service_policy.py``) need a window that only
someone standing in the house can open: two Home Assistant services, callable
from a dashboard button, an automation, or Developer Tools. The cloud can
*ask* for one (``maintenance_request`` raises a notification) but cannot grant
itself one.

The window lives in memory on purpose. A restart closes it, so the failure
mode is "support has to ask again", never "the door was left open".

Commissioning is the exception, and deliberately stored the other way. Pairing
opens it and it lasts until the install is marked complete — on the home, or
by the manager, which may only ever close it — or ``COMMISSIONING_DAYS`` pass.
It lives in the config entry's options because an install restarts Home
Assistant many times, and consent that died on each restart is what made the
window alone impractical. After it ends, the default is closed again.

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
SERVICE_COMPLETE = "complete_commissioning"

DEFAULT_MINUTES = 60
MAX_MINUTES = 480

_STATE_KEY = "_maintenance_until"
# Consent that does not need somebody at the tablet right now. Both live in
# the config entry's options, which means both are set on the home — there is
# deliberately no command that turns either on. See `consent()`.
OPT_COMMISSIONING_UNTIL = "commissioning_until"
OPT_STANDING_CONSENT = "unattended_support"
# Commissioning ends when somebody says the install is done, and never later
# than this after pairing. The cap is the owner's decision (2026-09-16): long
# enough that an install spread over several site visits never stalls, short
# enough that an abandoned one does not leave the locks reachable for good.
COMMISSIONING_DAYS = 30
# Written once, when commissioning is marked complete, and never cleared. The
# deadline itself is never rewritten: completion is recorded beside it, so the
# only thing any writer of these can do to the grant is end it.
OPT_COMMISSIONING_COMPLETED_AT = "commissioning_completed_at"
OPT_COMMISSIONING_COMPLETED_BY = "commissioning_completed_by"
_COMPLETED_BY = ("local", "manager")
_REGISTERED_KEY = "_maintenance_services_registered"
_CANCEL_KEY = "_maintenance_expiry_cancel"
_COMMISSIONING_CANCEL_KEY = "_commissioning_expiry_cancel"
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


def commissioning_deadline(paired_at: datetime | None = None) -> datetime:
    """When a home paired at ``paired_at`` stops being in commissioning."""
    return (paired_at or _now()) + timedelta(days=COMMISSIONING_DAYS)


def _parse_time(value) -> datetime | None:
    """An ISO timestamp from the options, or None. Only a string counts: a
    boolean or a number written there is not a deadline, and reading one as
    one is how a flag would turn into a permanent grant."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _LOGGER.debug("Unparseable %s: %r", OPT_COMMISSIONING_UNTIL, value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def commissioning(hass: HomeAssistant) -> dict:
    """Is this home still being commissioned, and until when?

    ``state`` is one of:

    * ``open`` — paired, not yet marked complete, before the deadline.
    * ``complete`` — marked complete, on the home or by the manager.
    * ``expired`` — the deadline passed without anyone marking it complete.
    * ``invalid`` — a deadline further out than the cap allows. Nothing this
      integration writes produces one, so whatever did is not trusted.
    * ``none`` — no commissioning on record.

    Read from the config entry's options every time, so it survives the
    restarts commissioning is full of, and a completion made anywhere is
    honoured everywhere at once.
    """
    options = _entry_options(hass)
    until = _parse_time(options.get(OPT_COMMISSIONING_UNTIL))
    completed_at = options.get(OPT_COMMISSIONING_COMPLETED_AT)
    result = {"open": False, "state": "none",
              "until": until.isoformat() if until else None,
              "seconds_remaining": 0,
              "completed_at": completed_at or None,
              "completed_by": options.get(OPT_COMMISSIONING_COMPLETED_BY) or None}
    if until is None:
        return result
    now = _now()
    if completed_at:
        # Any value at all closes it. A malformed completion stamp is still
        # somebody ending commissioning, and erring towards closed is the
        # direction that cannot hurt a customer.
        result["state"] = "complete"
    elif until <= now:
        result["state"] = "expired"
    elif until > commissioning_deadline(now) + timedelta(minutes=5):
        result["state"] = "invalid"
    else:
        result.update(open=True, state="open",
                      seconds_remaining=int((until - now).total_seconds()))
    return result


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
    * ``commissioning`` — open from pairing until the install is marked
      complete, and never longer than ``COMMISSIONING_DAYS``. Whoever paired
      this home was holding the pairing token and was standing in it; making
      them also flip a switch during an install they are physically performing
      is ceremony, and in practice it stalled installs. The manager may *end*
      it, but nothing remote can open, extend or renew it — see
      `complete_commissioning`.
    * ``standing`` — an explicit opt-in in the integration's options, for
      sites that want unattended support. Off by default, visible in the UI,
      revocable there.
    """
    if is_open(hass):
        return {"allowed": True, "source": "window"}

    options = _entry_options(hass)
    if options.get(OPT_STANDING_CONSENT):
        return {"allowed": True, "source": "standing"}

    state = commissioning(hass)
    if state["open"]:
        return {"allowed": True, "source": "commissioning",
                "until": state["until"],
                "seconds_remaining": state["seconds_remaining"]}
    return {"allowed": False, "source": None}


def status(hass: HomeAssistant) -> dict:
    """Window state, in the shape the manager's UI consumes."""
    until = _store(hass).get(_STATE_KEY)
    if not until or until <= _now():
        return {"open": False, "until": None, "seconds_remaining": 0,
                "consent": consent(hass), "commissioning": commissioning(hass)}
    return {"open": True, "until": until.isoformat(),
            "seconds_remaining": int((until - _now()).total_seconds()),
            "consent": consent(hass), "commissioning": commissioning(hass)}


def switch_state(hass: HomeAssistant) -> dict:
    """What the "Allow Dartec support" switch should say.

    On whenever the window *or* commissioning is open: a homeowner looking at
    an "off" switch during an install would be told access is shut while the
    installer can in fact reach their locks. ``ends_at`` is when the later of
    the two ends, which is when access through this switch actually stops.
    """
    window_until = _store(hass).get(_STATE_KEY)
    window_open = bool(window_until and window_until > _now())
    comm = commissioning(hass)
    ends = [window_until] if window_open else []
    if comm["open"]:
        ends.append(_parse_time(comm["until"]))
    end = max(ends) if ends else None
    return {"on": window_open or comm["open"],
            "ends_at": end.isoformat() if end else None,
            "minutes_remaining": (round((end - _now()).total_seconds() / 60)
                                  if end else 0),
            "commissioning": comm["open"],
            "commissioning_ends_at": comm["until"] if comm["open"] else None}


def complete_commissioning(hass: HomeAssistant, by: str) -> dict:
    """End commissioning now — the only change to it anyone can make.

    Deliberately takes no deadline, duration or flag: it can only close. That
    is what makes it safe for the manager to send, because a compromised cloud
    that sends it has only locked itself out. On a home that is not
    commissioning it changes nothing, and it never rewrites the deadline, so
    it cannot be used to reopen one either.
    """
    by = by if by in _COMPLETED_BY else "local"
    before = commissioning(hass)
    if not before["open"]:
        return {"ok": True, "changed": False, "commissioning": before,
                "detail": f"not commissioning ({before['state']}); nothing to close"}
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={
        **dict(entry.options),
        OPT_COMMISSIONING_COMPLETED_AT: _now().isoformat(),
        OPT_COMMISSIONING_COMPLETED_BY: by})
    _cancel_commissioning_expiry(hass)
    who = "by the Dartec manager" if by == "manager" else "on this home"
    logbook(hass, f"Commissioning marked complete {who}. Sensitive operations "
                  f"now need '{SWITCH_NAME}' switched on")
    _LOGGER.info("Commissioning marked complete (%s)", by)
    _notify_update(hass)
    return {"ok": True, "changed": True, "commissioning": commissioning(hass),
            "detail": "commissioning complete; sensitive operations need consent again"}


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


def _cancel_commissioning_expiry(hass: HomeAssistant) -> None:
    cancel = _store(hass).pop(_COMMISSIONING_CANCEL_KEY, None)
    if cancel:
        try:
            cancel()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not cancel commissioning timer: %s", err)


def schedule_commissioning_expiry(hass: HomeAssistant) -> None:
    """Turn the switch off when commissioning reaches its cap.

    Like the window's timer this is for the display, not the gate: `consent`
    checks the clock on every command. Called at setup, so a restart during
    commissioning re-arms it from the stored deadline.
    """
    from homeassistant.helpers.event import async_track_point_in_utc_time

    _cancel_commissioning_expiry(hass)
    state = commissioning(hass)
    if not state["open"]:
        return

    def _expired(_now) -> None:
        _store(hass).pop(_COMMISSIONING_CANCEL_KEY, None)
        if commissioning(hass)["state"] == "expired":
            logbook(hass, f"Commissioning ended at its {COMMISSIONING_DAYS}-day "
                          "limit without being marked complete. Sensitive "
                          f"operations now need '{SWITCH_NAME}' switched on")
        _notify_update(hass)

    try:
        _store(hass)[_COMMISSIONING_CANCEL_KEY] = async_track_point_in_utc_time(
            hass, _expired, _parse_time(state["until"]))
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not schedule commissioning expiry: %s", err)


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

    async def _complete(call: ServiceCall) -> None:
        complete_commissioning(hass, "local")

    hass.services.async_register(DOMAIN, SERVICE_ALLOW, _allow, schema=ALLOW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_END, _end)
    hass.services.async_register(DOMAIN, SERVICE_COMPLETE, _complete)
    store[_REGISTERED_KEY] = True


async def async_unregister_services(hass: HomeAssistant) -> None:
    store = _store(hass)
    if not store.get(_REGISTERED_KEY):
        return
    hass.services.async_remove(DOMAIN, SERVICE_ALLOW)
    hass.services.async_remove(DOMAIN, SERVICE_END)
    hass.services.async_remove(DOMAIN, SERVICE_COMPLETE)
    _cancel_expiry(hass)
    _cancel_commissioning_expiry(hass)
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
