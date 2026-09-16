"""Consent: who can allow a sensitive operation, and who cannot.

These are the tests that stop a future change from quietly turning the cloud
into an authority over a customer's house. The commissioning allowance and the
standing opt-in both widen when sensitive work may run, so each one is pinned
here to the property that makes it acceptable: it is decided on the home.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# maintenance.py legitimately imports Home Assistant and voluptuous at module
# level, and the agent suite runs without either (see test_service_policy.py on
# why: CI in seconds, no HA install). Stubbing the imports is cheaper than
# splitting a coherent module apart just to make it testable, and the logic
# under test — consent() — touches none of the stubbed surface.
def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Any:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return self


_stub("voluptuous", Schema=_Any, Optional=_Any, Required=_Any, All=_Any,
      Coerce=_Any, Range=_Any)
_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, ServiceCall=object, callback=lambda f: f)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None)

_AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
_pkg = types.ModuleType("dartec_ha_manager")
_pkg.__path__ = [str(_AGENT)]
sys.modules["dartec_ha_manager"] = _pkg

from dartec_ha_manager import maintenance  # noqa: E402


class FakeEntry:
    def __init__(self, options=None):
        self.options = options or {}


class FakeEntries:
    def __init__(self, entries):
        self._entries = entries

    def async_entries(self, domain):
        return self._entries


class FakeHass:
    def __init__(self, options=None, window_until=None):
        self.data = {maintenance.DOMAIN: {maintenance._STATE_KEY: window_until}}
        self.config_entries = FakeEntries([FakeEntry(options)])


def in_(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


class TestNothingIsGrantedByDefault:

    def test_a_paired_home_with_no_allowance_refuses(self):
        assert maintenance.consent(FakeHass())["allowed"] is False

    def test_an_expired_commissioning_period_refuses(self):
        """It is a timestamp precisely so it runs out on its own."""
        hass = FakeHass({maintenance.OPT_COMMISSIONING_UNTIL: in_(-1)})
        assert maintenance.consent(hass)["allowed"] is False

    def test_junk_in_the_option_refuses_rather_than_crashing(self):
        hass = FakeHass({maintenance.OPT_COMMISSIONING_UNTIL: "not a date"})
        assert maintenance.consent(hass)["allowed"] is False

    def test_no_config_entry_refuses(self):
        hass = FakeHass()
        hass.config_entries = FakeEntries([])
        assert maintenance.consent(hass)["allowed"] is False


class TestTheThreeWaysToSayYes:

    def test_the_switch_still_works_and_still_wins(self):
        hass = FakeHass(window_until=datetime.now(timezone.utc) + timedelta(minutes=5))
        granted = maintenance.consent(hass)
        assert granted["allowed"] and granted["source"] == "window"

    def test_commissioning_covers_the_install(self):
        """Whoever paired this home was holding the pairing token and standing
        in it. That is the same evidence the switch collects."""
        hass = FakeHass({maintenance.OPT_COMMISSIONING_UNTIL: in_(30)})
        granted = maintenance.consent(hass)
        assert granted["allowed"] and granted["source"] == "commissioning"
        assert granted["seconds_remaining"] > 0

    def test_a_site_can_opt_in_to_unattended_support(self):
        hass = FakeHass({maintenance.OPT_STANDING_CONSENT: True})
        granted = maintenance.consent(hass)
        assert granted["allowed"] and granted["source"] == "standing"

    def test_revoking_the_opt_in_takes_effect_immediately(self):
        """Options are read fresh on every command, so switching it off in the
        UI is not something anyone has to wait for."""
        hass = FakeHass({maintenance.OPT_STANDING_CONSENT: True})
        assert maintenance.consent(hass)["allowed"]
        hass.config_entries = FakeEntries([FakeEntry({maintenance.OPT_STANDING_CONSENT: False})])
        assert maintenance.consent(hass)["allowed"] is False

    def test_the_source_is_always_reported(self):
        """The logbook says which consent was relied on, so a homeowner asking
        'why did that run while I was out' has an answer in their own system."""
        for options, expected in (({maintenance.OPT_COMMISSIONING_UNTIL: in_(5)},
                                   "commissioning"),
                                  ({maintenance.OPT_STANDING_CONSENT: True}, "standing")):
            assert maintenance.consent(FakeHass(options))["source"] == expected


class TestTheCloudCannotGrantItself:
    """The property the whole module exists for."""

    def test_there_is_no_command_field_that_opens_the_gate(self):
        """`force` is the obvious one to try; it must do nothing. consent()
        takes only `hass` — it cannot see a command at all, which is the
        structural reason rather than a check someone could delete."""
        import inspect

        assert list(inspect.signature(maintenance.consent).parameters) == ["hass"]

    @pytest.mark.parametrize("options", [
        {"force": True},
        {"allow_maintenance": True},
        {"unattended_support": False, "force": True},
    ])
    def test_lookalike_options_do_not_grant_anything(self, options):
        assert maintenance.consent(FakeHass(options))["allowed"] is False

    def test_commissioning_is_a_timestamp_not_a_flag(self):
        """A boolean would be a permanent grant the moment anything wrote it
        true. A timestamp in the past is simply no longer consent."""
        assert maintenance.consent(FakeHass(
            {maintenance.OPT_COMMISSIONING_UNTIL: True}))["allowed"] is False

    def test_status_reports_consent_so_the_manager_can_stop_guessing(self):
        hass = FakeHass({maintenance.OPT_COMMISSIONING_UNTIL: in_(10)})
        status = maintenance.status(hass)
        assert status["open"] is False           # the switch itself is off
        assert status["consent"]["allowed"] is True
        assert status["consent"]["source"] == "commissioning"
