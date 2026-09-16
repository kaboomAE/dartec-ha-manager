"""Commissioning: open from pairing, closed when the install is done.

The owner's rule (2026-09-16): consent on a newly paired home stays open while
it is being commissioned, never for more than 30 days, and is closed by
default afterwards. Every test here pins one edge of that, and the ones in
`TestTheManagerCanOnlyClose` pin the property that makes a cloud-sendable
command acceptable at all — it can take access away and nothing else.

Runs without Home Assistant, like the rest of this suite: the HA surface the
code touches (config entries, the event helper, the config flow base class) is
stood in for below, and is small enough that the stand-ins stay honest.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _stub(name, **attrs):
    module = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Any:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return self


class AbortFlow(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _ConfigFlow:
    """Just enough of HA's ConfigFlow to run our user step."""

    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        for entry in self.hass.config_entries.async_entries("dartec_ha_manager"):
            if entry.unique_id == self.unique_id:
                raise AbortFlow("already_configured")

    def async_create_entry(self, *, title, data, options=None):
        return {"type": "create_entry", "title": title, "data": data,
                "options": options or {}}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


_TIMERS: list = []


def _track_point(hass, action, when):
    handle = {"action": action, "when": when, "cancelled": False}
    _TIMERS.append(handle)

    def cancel():
        handle["cancelled"] = True
    return cancel


_stub("voluptuous", Schema=_Any, Optional=_Any, Required=_Any, All=_Any,
      Coerce=_Any, Range=_Any)
_stub("aiohttp", ClientError=type("ClientError", (Exception,), {}))
_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, ServiceCall=object,
      callback=lambda f: f)
_stub("homeassistant.config_entries", ConfigFlow=_ConfigFlow, OptionsFlow=object,
      ConfigEntry=object)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None)
_stub("homeassistant.helpers.event", async_track_point_in_utc_time=_track_point)
_stub("homeassistant.helpers.aiohttp_client",
      async_get_clientsession=lambda hass: hass.session)

_AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
if "dartec_ha_manager" not in sys.modules:
    _pkg = types.ModuleType("dartec_ha_manager")
    _pkg.__path__ = [str(_AGENT)]
    sys.modules["dartec_ha_manager"] = _pkg

# commands.py imports every handler module when it runs a command. They carry
# Home Assistant imports of their own and are not what is under test, so they
# are empty here: an action reaching one of them would be "unsupported".
for _name in ("backup_cmds", "blueprint_cmds", "hacs_cmds", "helper_cmds",
              "home_cmds", "integration_cmds", "media_cmds", "lovelace_cmds",
              "link_cmds", "registry_cmds", "tunnel_cmds", "user_cmds"):
    sys.modules.setdefault(f"dartec_ha_manager.{_name}",
                           types.ModuleType(f"dartec_ha_manager.{_name}"))
    if not hasattr(sys.modules[f"dartec_ha_manager.{_name}"], "HANDLERS"):
        sys.modules[f"dartec_ha_manager.{_name}"].HANDLERS = {}

from dartec_ha_manager import commands, config_flow, maintenance  # noqa: E402

DAY = timedelta(days=1)


class FakeEntry:
    def __init__(self, options=None, unique_id="inst-1"):
        self.options = dict(options or {})
        self.unique_id = unique_id


class FakeEntries:
    def __init__(self, entries):
        self._entries = entries

    def async_entries(self, domain):
        return self._entries

    def async_update_entry(self, entry, options=None):
        # HA replaces the options mapping wholesale, which is why the code
        # under test must merge rather than write one key.
        if options is not None:
            entry.options = dict(options)


class FakeBus:
    def __init__(self):
        self.fired = []

    def async_fire(self, event, data):
        self.fired.append((event, data))


class FakeHass:
    def __init__(self, options=None, entries=None):
        self.data = {}
        self.bus = FakeBus()
        self.config_entries = FakeEntries(
            entries if entries is not None else [FakeEntry(options)])

    @property
    def options(self):
        return self.config_entries.async_entries(maintenance.DOMAIN)[0].options

    def logbook(self):
        return [data["message"] for event, data in self.bus.fired
                if event == "logbook_entry"]


def restart(hass: FakeHass) -> FakeHass:
    """A Home Assistant restart: memory gone, the config entry kept."""
    fresh = FakeHass(entries=hass.config_entries._entries)
    return fresh


@pytest.fixture
def clock(monkeypatch):
    """Move time without waiting for it."""
    state = {"now": datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(maintenance, "_now", lambda: state["now"])

    class Clock:
        @property
        def now(self):
            return state["now"]

        def advance(self, delta):
            state["now"] += delta
    return Clock()


def paired(clock) -> FakeHass:
    """A home paired right now, the way the config flow writes it."""
    return FakeHass({maintenance.OPT_COMMISSIONING_UNTIL:
                     maintenance.commissioning_deadline().isoformat()})


def run(coro):
    return asyncio.run(coro)


# --- Pairing -----------------------------------------------------------------

class FakeResponse:
    status = 200

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, instance_id="inst-1"):
        self.instance_id = instance_id

    def post(self, url, json=None, timeout=None):
        return FakeResponse({"instance_id": self.instance_id,
                             "customer_name": "Test", "instance_name": "Villa"})


def pair(hass, instance_id="inst-1"):
    hass.session = FakeSession(instance_id)
    flow = config_flow.DartecConfigFlow()
    flow.hass = hass
    return run(flow.async_step_user({"server_url": "https://manager.dartec.ae",
                                     "pairing_token": "tok"}))


class TestPairingOpensCommissioning:

    def test_the_cap_is_thirty_days(self):
        """A named constant, and the owner's number. Changing it is a decision,
        so it should fail a test rather than slip through in a diff."""
        assert maintenance.COMMISSIONING_DAYS == 30

    def test_pairing_writes_a_deadline_thirty_days_out(self):
        result = pair(FakeHass(entries=[]))
        until = datetime.fromisoformat(
            result["options"][maintenance.OPT_COMMISSIONING_UNTIL])
        expected = datetime.now(timezone.utc) + timedelta(days=30)
        assert abs((until - expected).total_seconds()) < 60

    def test_it_is_stored_in_the_entry_not_in_memory(self):
        """Options are what survives a restart; hass.data is not."""
        result = pair(FakeHass(entries=[]))
        assert maintenance.OPT_COMMISSIONING_UNTIL in result["options"]

    def test_a_freshly_paired_home_consents(self, clock):
        hass = paired(clock)
        granted = maintenance.consent(hass)
        assert granted["allowed"] and granted["source"] == "commissioning"
        assert granted["seconds_remaining"] == 30 * 86400

    def test_pairing_an_already_paired_home_again_does_not_reset_the_period(self, clock):
        """The decision on re-pairing: the flow aborts on the existing unique
        id *before* it writes any commissioning, so the running deadline — or
        a completion — is untouched. A fresh period needs someone on site to
        delete the integration and pair again, which is deliberate."""
        entry = FakeEntry({maintenance.OPT_COMMISSIONING_UNTIL:
                           (clock.now + 2 * DAY).isoformat()}, unique_id="inst-1")
        hass = FakeHass(entries=[entry])
        before = dict(entry.options)
        with pytest.raises(AbortFlow) as aborted:
            pair(hass, "inst-1")
        assert aborted.value.reason == "already_configured"
        assert entry.options == before


# --- Restarts ------------------------------------------------------------------

class TestItSurvivesRestarts:

    def test_consent_is_still_there_after_a_restart(self, clock):
        hass = paired(clock)
        clock.advance(3 * DAY)
        after = restart(hass)
        granted = maintenance.consent(after)
        assert granted["allowed"] and granted["source"] == "commissioning"

    def test_setup_rearms_the_timer_from_the_stored_deadline(self, clock):
        hass = paired(clock)
        _TIMERS.clear()
        maintenance.schedule_commissioning_expiry(restart(hass))
        assert len(_TIMERS) == 1
        assert _TIMERS[0]["when"] == clock.now + 30 * DAY

    def test_no_timer_once_commissioning_is_over(self, clock):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        _TIMERS.clear()
        maintenance.schedule_commissioning_expiry(restart(hass))
        assert _TIMERS == []

    def test_a_completion_survives_a_restart_too(self, clock):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        assert maintenance.consent(restart(hass))["allowed"] is False


# --- The cap -------------------------------------------------------------------

class TestItClosesAtTheCap:

    def test_open_on_the_last_day(self, clock):
        hass = paired(clock)
        clock.advance(30 * DAY - timedelta(minutes=1))
        assert maintenance.consent(hass)["allowed"]

    def test_closed_once_the_cap_passes(self, clock):
        hass = paired(clock)
        clock.advance(30 * DAY + timedelta(seconds=1))
        assert maintenance.consent(hass)["allowed"] is False
        assert maintenance.commissioning(hass)["state"] == "expired"

    def test_the_timer_logs_the_expiry_in_the_home(self, clock):
        hass = paired(clock)
        _TIMERS.clear()
        maintenance.schedule_commissioning_expiry(hass)
        clock.advance(30 * DAY + timedelta(seconds=1))
        _TIMERS[0]["action"](clock.now)
        assert any("30-day limit" in line for line in hass.logbook())

    def test_a_deadline_beyond_the_cap_is_not_honoured(self, clock):
        """Nothing this integration writes can set one, so whatever did is not
        trusted — the cap holds regardless of who wrote the option."""
        hass = FakeHass({maintenance.OPT_COMMISSIONING_UNTIL:
                         (clock.now + 365 * DAY).isoformat()})
        assert maintenance.consent(hass)["allowed"] is False
        assert maintenance.commissioning(hass)["state"] == "invalid"


# --- Completing it -------------------------------------------------------------

class TestItClosesWhenMarkedComplete:

    def test_local_complete_closes_it(self, clock):
        hass = paired(clock)
        result = maintenance.complete_commissioning(hass, "local")
        assert result["changed"] is True
        assert maintenance.consent(hass)["allowed"] is False
        state = maintenance.commissioning(hass)
        assert state["state"] == "complete" and state["completed_by"] == "local"
        assert any("marked complete on this home" in line for line in hass.logbook())

    def test_the_local_service_is_registered(self, clock):
        registered = {}

        class Services:
            def async_register(self, domain, name, handler, schema=None):
                registered[name] = handler

        hass = paired(clock)
        hass.services = Services()
        run(maintenance.async_register_services(hass))
        assert maintenance.SERVICE_COMPLETE in registered
        run(registered[maintenance.SERVICE_COMPLETE](None))
        assert maintenance.commissioning(hass)["completed_by"] == "local"

    def test_manager_complete_closes_it(self, clock):
        hass = paired(clock)
        result = run(commands.execute_command(hass, {"action": "commissioning_complete"}))
        assert result["ok"] and result["changed"]
        assert maintenance.consent(hass)["allowed"] is False
        assert maintenance.commissioning(hass)["completed_by"] == "manager"
        assert any("by the Dartec manager" in line for line in hass.logbook())

    def test_the_manager_needs_no_consent_to_close(self, clock):
        """Closing takes permission away, so it must work on a home that has
        none to give — otherwise a stuck home could not be closed at all."""
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        result = run(commands.execute_command(hass, {"action": "commissioning_complete"}))
        assert result["ok"] and not result.get("refused")

    def test_completion_keeps_the_other_options(self, clock):
        hass = paired(clock)
        hass.options[maintenance.OPT_STANDING_CONSENT] = False
        hass.options["branding"] = {"name": "Villa"}
        maintenance.complete_commissioning(hass, "local")
        assert hass.options["branding"] == {"name": "Villa"}
        assert maintenance.OPT_COMMISSIONING_UNTIL in hass.options


class TestTheManagerCanOnlyClose:
    """The property the manager's command exists under."""

    def test_consent_still_takes_only_hass(self):
        assert list(inspect.signature(maintenance.consent).parameters) == ["hass"]

    def test_complete_takes_no_deadline_or_duration(self):
        assert list(inspect.signature(
            maintenance.complete_commissioning).parameters) == ["hass", "by"]

    @pytest.mark.parametrize("extra", [
        {"until": "2099-01-01T00:00:00+00:00"},
        {"days": 365},
        {"extend": True, "force": True},
        {"commissioning_until": "2099-01-01T00:00:00+00:00"},
    ])
    def test_it_cannot_reopen_a_completed_home(self, clock, extra):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        before = dict(hass.options)
        run(commands.execute_command(hass, {"action": "commissioning_complete", **extra}))
        assert maintenance.consent(hass)["allowed"] is False
        assert hass.options == before

    @pytest.mark.parametrize("extra", [
        {"until": "2099-01-01T00:00:00+00:00"},
        {"days": 365, "extend": True},
    ])
    def test_it_cannot_extend_an_open_one(self, clock, extra):
        """Sent to an open home it closes it; it never moves the deadline."""
        hass = paired(clock)
        until = hass.options[maintenance.OPT_COMMISSIONING_UNTIL]
        run(commands.execute_command(hass, {"action": "commissioning_complete", **extra}))
        assert hass.options[maintenance.OPT_COMMISSIONING_UNTIL] == until
        assert maintenance.consent(hass)["allowed"] is False

    def test_it_cannot_reopen_an_expired_home(self, clock):
        hass = paired(clock)
        clock.advance(31 * DAY)
        before = dict(hass.options)
        run(commands.execute_command(hass, {"action": "commissioning_complete"}))
        assert hass.options == before
        assert maintenance.consent(hass)["allowed"] is False

    def test_a_manager_named_by_the_cloud_is_just_the_manager(self, clock):
        """`by` is chosen by the agent, not read from the command."""
        hass = paired(clock)
        run(commands.execute_command(hass, {"action": "commissioning_complete",
                                            "by": "local"}))
        assert maintenance.commissioning(hass)["completed_by"] == "manager"

    def test_sensitive_work_is_refused_after_the_manager_closes(self, clock):
        hass = paired(clock)
        run(commands.execute_command(hass, {"action": "commissioning_complete"}))
        refused = run(commands.execute_command(hass, {"action": "ha_restart"}))
        assert refused.get("refused") is True


# --- After it ends -------------------------------------------------------------

class TestAfterwardsTheOtherConsentsStillWork:

    def test_the_switch_reopens_access(self, clock):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        assert maintenance.consent(hass)["allowed"] is False
        # The window's own timer compares against the real clock.
        maintenance._store(hass)[maintenance._STATE_KEY] = clock.now + timedelta(minutes=60)
        granted = maintenance.consent(hass)
        assert granted["allowed"] and granted["source"] == "window"

    def test_opening_the_window_does_not_revive_commissioning(self, clock):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        maintenance._store(hass)[maintenance._STATE_KEY] = clock.now + timedelta(minutes=60)
        assert maintenance.commissioning(hass)["open"] is False

    def test_the_standing_opt_in_reopens_access(self, clock):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "manager")
        hass.options[maintenance.OPT_STANDING_CONSENT] = True
        granted = maintenance.consent(hass)
        assert granted["allowed"] and granted["source"] == "standing"

    def test_closed_by_default_after_the_cap(self, clock):
        hass = paired(clock)
        clock.advance(31 * DAY)
        assert maintenance.consent(hass) == {"allowed": False, "source": None}


# --- The switch ----------------------------------------------------------------

class TestTheSwitchTellsTheTruth:

    def test_on_while_commissioning_with_no_window(self, clock):
        hass = paired(clock)
        state = maintenance.switch_state(hass)
        assert state["on"] is True and state["commissioning"] is True
        assert state["commissioning_ends_at"] == (clock.now + 30 * DAY).isoformat()
        assert state["ends_at"] == state["commissioning_ends_at"]

    def test_off_once_complete(self, clock):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        state = maintenance.switch_state(hass)
        assert state["on"] is False and state["commissioning_ends_at"] is None

    def test_off_once_the_cap_passes(self, clock):
        hass = paired(clock)
        clock.advance(31 * DAY)
        assert maintenance.switch_state(hass)["on"] is False

    def test_a_window_after_commissioning_shows_on_with_its_own_end(self, clock):
        hass = paired(clock)
        maintenance.complete_commissioning(hass, "local")
        until = clock.now + timedelta(minutes=60)
        maintenance._store(hass)[maintenance._STATE_KEY] = until
        state = maintenance.switch_state(hass)
        assert state["on"] and not state["commissioning"]
        assert state["ends_at"] == until.isoformat()

    def test_status_carries_commissioning_for_the_manager(self, clock):
        status = maintenance.status(paired(clock))
        assert status["commissioning"]["open"] is True
        assert status["commissioning"]["until"] == (clock.now + 30 * DAY).isoformat()

    def test_switching_it_off_ends_commissioning(self, clock):
        """Off has to mean off. During commissioning the window is not what
        keeps access open, so a switch that only closed the window would stay
        on — and the homeowner would have no way to shut the door."""
        _stub("homeassistant.components")
        _stub("homeassistant.components.switch", SwitchEntity=object)
        _stub("homeassistant.const", Platform=types.SimpleNamespace(SWITCH="switch"))
        _stub("homeassistant.helpers.dispatcher",
              async_dispatcher_connect=lambda *a, **k: (lambda: None))
        _stub("homeassistant.helpers.entity", DeviceInfo=dict)
        _stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
        from dartec_ha_manager import switch

        hass = paired(clock)
        entity = switch.MaintenanceSwitch(types.SimpleNamespace(entry_id="e1"))
        entity.hass = hass
        entity.async_write_ha_state = lambda: None
        assert entity.is_on
        run(entity.async_turn_off())
        assert entity.is_on is False
        assert maintenance.consent(hass)["allowed"] is False
        assert maintenance.commissioning(hass)["completed_by"] == "local"
