"""HACS token rotation: which token a home holds, and replacing it.

The token is a credential, however little it can do, so most of these tests
are claims about where it may and may not appear: the snapshot carries a
fingerprint and never the token, and no response, log line or logbook entry
carries it either. The rest pin what the command may change — the token in a
HACS entry that already exists, and nothing else.

Runs without Home Assistant, like the rest of this suite. `hacs_token.py` has
no HA imports; `commands.py` and `collector.py` are loaded against the same
small stand-ins `test_commissioning.py` uses.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import sys
import types
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


_stub("voluptuous", Schema=_Any, Optional=_Any, Required=_Any, All=_Any,
      Coerce=_Any, Range=_Any)
_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, ServiceCall=object,
      callback=lambda f: f)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None)

_AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
if "dartec_ha_manager" not in sys.modules:
    _pkg = types.ModuleType("dartec_ha_manager")
    _pkg.__path__ = [str(_AGENT)]
    sys.modules["dartec_ha_manager"] = _pkg

from dartec_ha_manager import collector, commands, hacs_token, maintenance  # noqa: E402

# Shaped like the real thing, and obviously not one.
TOKEN = "github_pat_11TESTTESTTEST0123456789_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab"
OLD_TOKEN = "github_pat_11OLDOLDOLDOLD0123456789_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab"
FP = hashlib.sha256(TOKEN.encode()).hexdigest()[:16]


class FakeEntry:
    def __init__(self, domain, data=None, options=None, entry_id=None):
        self.domain = domain
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.entry_id = entry_id or f"{domain}-entry"
        self.unique_id = entry_id


class FakeEntries:
    def __init__(self, entries):
        self._entries = entries
        self.updates = []
        self.reloads = []
        self.fail_reload = False

    def async_entries(self, domain):
        return [e for e in self._entries if e.domain == domain]

    def async_update_entry(self, entry, data=None, options=None):
        # HA replaces the mapping wholesale, which is why the code under test
        # has to carry the other keys across itself.
        self.updates.append({"data": data, "options": options})
        if data is not None:
            entry.data = dict(data)
        if options is not None:
            entry.options = dict(options)

    async def async_reload(self, entry_id):
        if self.fail_reload:
            raise RuntimeError("reload failed")
        self.reloads.append(entry_id)
        return True


class FakeBus:
    def __init__(self):
        self.fired = []

    def async_fire(self, event, data):
        self.fired.append((event, data))


class FakeHass:
    def __init__(self, hacs_data=None, *, hacs=True, options=None):
        self.data = {}
        self.bus = FakeBus()
        entries = [FakeEntry(maintenance.DOMAIN, options=options)]
        if hacs:
            entries.append(FakeEntry("hacs", data=hacs_data))
        self.config_entries = FakeEntries(entries)

    def hacs(self):
        return self.config_entries.async_entries("hacs")[0]

    def logbook(self):
        return [data["message"] for event, data in self.bus.fired
                if event == "logbook_entry"]


def run(coro):
    return asyncio.run(coro)


def send(hass, **payload):
    """Through the real dispatcher, so the consent gate is part of the test."""
    return run(commands.execute_command(hass, {"action": "hacs_token_set", **payload}))


@pytest.fixture(autouse=True)
def dispatcher(monkeypatch):
    """`commands.py` looks handlers up per module. Other suites leave an empty
    `hacs_cmds` behind, so give it the handler `hacs_cmds.py` registers (which
    `TestRegistration` checks is really there)."""
    module = types.ModuleType("dartec_ha_manager.hacs_cmds")
    module.HANDLERS = dict(hacs_token.HANDLERS)
    monkeypatch.setitem(sys.modules, "dartec_ha_manager.hacs_cmds", module)


# --- The fingerprint -----------------------------------------------------------

class TestFingerprint:

    def test_is_the_first_16_hex_characters_of_sha256(self):
        assert hacs_token.fingerprint(TOKEN) == FP
        assert len(FP) == 16 and int(FP, 16) >= 0

    def test_different_tokens_differ(self):
        assert hacs_token.fingerprint(OLD_TOKEN) != FP


# --- The snapshot ----------------------------------------------------------------

class TestSnapshot:

    def test_reports_the_fingerprint_of_the_token_hacs_holds(self):
        hass = FakeHass({"token": TOKEN})
        assert hacs_token.snapshot_section(hass) == {"token_fingerprint": FP}

    def test_null_without_a_hacs_entry(self):
        assert hacs_token.snapshot_section(FakeHass(hacs=False)) == {
            "token_fingerprint": None}

    @pytest.mark.parametrize("data", [{}, {"token": ""}, {"token": None}])
    def test_null_without_a_token(self, data):
        assert hacs_token.snapshot_section(FakeHass(data)) == {
            "token_fingerprint": None}

    def test_collector_carries_it_beside_the_repository_list(self, monkeypatch):
        """`hacs` is the list of repositories the manager already reads; the
        fingerprint must not replace it, and the token must not be anywhere in
        what is sent."""
        hass = FakeHass({"token": TOKEN})
        repos = [{"name": "hacs/integration", "installed_version": "2.0.0"}]
        monkeypatch.setattr(collector, "_collect_hacs", lambda h: repos)
        section = collector._collect_hacs_token(hass)
        snapshot = {"hacs": collector._collect_hacs(hass),
                    hacs_token.SNAPSHOT_KEY: section}
        assert snapshot["hacs"] == repos
        assert snapshot["hacs_token"] == {"token_fingerprint": FP}
        assert TOKEN not in json.dumps(snapshot)

    def test_collect_snapshot_includes_it(self):
        """The wiring, read off the source: a section defined and never
        called is the easy way for this to ship doing nothing."""
        source = (_AGENT / "collector.py").read_text(encoding="utf-8")
        assert "snapshot[hacs_token.SNAPSHOT_KEY] = _collect_hacs_token(hass)" in source

    def test_a_broken_entry_does_not_cost_the_snapshot(self):
        class Broken:
            config_entries = None
        assert collector._collect_hacs_token(Broken()) == {"token_fingerprint": None}


# --- Refusals --------------------------------------------------------------------

class TestRefusals:

    @pytest.mark.parametrize("token", [
        None, 42, "",
        "not-a-token",
        "gho_" + "a" * 36,                   # an OAuth token, not a PAT
        "ghp_short",
        "ghp_" + "a" * 36 + " ",             # trailing whitespace from a paste
        " ghp_" + "a" * 36,
        "ghp_" + "a" * 18 + "\n" + "a" * 18,
        '"ghp_' + "a" * 36 + '"',            # pasted with its quotes
        "ghp_" + "a" * 400,                  # not a token, whatever it is
    ])
    def test_something_that_is_not_a_token(self, token):
        hass = FakeHass({"token": OLD_TOKEN})
        fp = hacs_token.fingerprint(token) if isinstance(token, str) else FP
        result = send(hass, token=token, fingerprint=fp)
        assert result["ok"] is False and result["reason"] == "invalid_token"
        assert hass.hacs().data["token"] == OLD_TOKEN
        assert hass.config_entries.updates == []

    @pytest.mark.parametrize("fingerprint", [
        None, "", FP.upper(), FP[:15], FP + "0",
        hashlib.sha256(OLD_TOKEN.encode()).hexdigest()[:16],
    ])
    def test_a_fingerprint_that_does_not_match_the_token(self, fingerprint):
        """A token damaged on the way is refused before it can break HACS."""
        hass = FakeHass({"token": OLD_TOKEN})
        result = send(hass, token=TOKEN, fingerprint=fingerprint)
        assert result["ok"] is False and result["reason"] == "invalid_token"
        assert hass.config_entries.updates == []

    def test_classic_tokens_are_accepted_too(self):
        classic = "ghp_" + "A1b2" * 9
        hass = FakeHass({"token": OLD_TOKEN})
        result = send(hass, token=classic, fingerprint=hacs_token.fingerprint(classic))
        assert result["ok"] is True and result["changed"] is True

    def test_no_hacs_entry_is_refused_and_none_is_created(self):
        hass = FakeHass(hacs=False)
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result == {"ok": False, "reason": "no_hacs_entry",
                          "detail": result["detail"]}
        assert hass.config_entries.async_entries("hacs") == []
        assert hass.config_entries.updates == []


# --- Setting it ------------------------------------------------------------------

class TestSetting:

    def test_the_same_token_changes_nothing(self):
        hass = FakeHass({"token": TOKEN, "experimental": True})
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is True and result["changed"] is False
        assert result["fingerprint"] == FP
        assert hass.config_entries.updates == []
        assert hass.config_entries.reloads == []
        assert hass.logbook() == []

    def test_a_new_token_replaces_only_the_token_and_reloads_hacs(self):
        data = {"token": OLD_TOKEN, "experimental": True, "sidepanel_title": "HACS"}
        hass = FakeHass(data)
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is True and result["changed"] is True
        assert result["fingerprint"] == FP
        assert hass.hacs().data == {**data, "token": TOKEN}
        assert hass.config_entries.updates == [
            {"data": {**data, "token": TOKEN}, "options": None}]
        assert hass.config_entries.reloads == [hass.hacs().entry_id]

    def test_the_change_is_in_the_home_s_logbook_by_fingerprint(self):
        hass = FakeHass({"token": OLD_TOKEN})
        send(hass, token=TOKEN, fingerprint=FP)
        assert hass.logbook() == [
            f"Dartec updated the HACS GitHub token (fingerprint {FP})"]

    def test_the_snapshot_then_reports_the_new_token(self):
        hass = FakeHass({"token": OLD_TOKEN})
        send(hass, token=TOKEN, fingerprint=FP)
        assert hacs_token.snapshot_section(hass) == {"token_fingerprint": FP}

    def test_a_failed_reload_says_the_token_was_saved(self):
        hass = FakeHass({"token": OLD_TOKEN})
        hass.config_entries.fail_reload = True
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is False and result["reason"] == "reload_failed"
        assert result["changed"] is True
        assert hass.hacs().data["token"] == TOKEN

    def test_needs_no_consent(self):
        """Routine: no window, no commissioning, no standing opt-in. The
        proposal awaiting sign-off — see service_policy.py. If the owner
        decides otherwise, this test flips with the one-line change there."""
        from dartec_ha_manager.service_policy import SENSITIVE_ACTIONS, is_sensitive

        hass = FakeHass({"token": OLD_TOKEN}, options={})
        assert maintenance.consent(hass)["allowed"] is False
        assert not is_sensitive({"action": "hacs_token_set"})
        assert "hacs_token_set" not in SENSITIVE_ACTIONS
        assert send(hass, token=TOKEN, fingerprint=FP)["changed"] is True


# --- Where the token must never appear ---------------------------------------------

class TestTheTokenStaysInTheEntry:

    @pytest.mark.parametrize("scenario", [
        "changed", "unchanged", "bad_fingerprint", "no_entry", "reload_failed",
        "update_failed",
    ])
    def test_not_in_the_response_logs_or_logbook(self, scenario, caplog):
        caplog.set_level(logging.DEBUG)
        hass = FakeHass({"token": TOKEN if scenario == "unchanged" else OLD_TOKEN},
                        hacs=scenario != "no_entry")
        fingerprint = "0" * 16 if scenario == "bad_fingerprint" else FP
        if scenario == "reload_failed":
            hass.config_entries.fail_reload = True
        if scenario == "update_failed":
            def refuse(entry, data=None, options=None):
                raise ValueError(f"bad data {data!r}")   # an error that echoes it
            hass.config_entries.async_update_entry = refuse

        result = send(hass, token=TOKEN, fingerprint=fingerprint)

        assert TOKEN not in json.dumps(result)
        assert TOKEN not in caplog.text
        assert all(TOKEN not in line for line in hass.logbook())
        assert all(TOKEN not in json.dumps(data) for _, data in hass.bus.fired)


# --- Registration ------------------------------------------------------------------

class TestRegistration:

    def test_hacs_cmds_registers_the_handler(self):
        tree = ast.parse((_AGENT / "hacs_cmds.py").read_text(encoding="utf-8"))
        handlers = next(node.value for node in tree.body
                        if isinstance(node, ast.Assign)
                        and any(getattr(t, "id", None) == "HANDLERS" for t in node.targets))
        mapping = {k.value: v.id for k, v in zip(handlers.keys, handlers.values)}
        assert mapping.get("hacs_token_set") == "hacs_token_set"
