"""HACS token rotation: which token a home holds, and replacing it safely.

The token is a credential, however little it can do, so many of these tests
are claims about where it may and may not appear: the snapshot carries a
fingerprint and never the token, and no response, log line or logbook entry
carries it either. The rest pin the owner's rule for the swap itself — never
leave a home with a worse token than it had: a token GitHub does not accept
never reaches the entry, and a swap HACS does not load with is undone.

Runs without Home Assistant, like the rest of this suite. `hacs_token.py` has
no module-level HA imports; `commands.py` and `collector.py` are loaded against
the same small stand-ins `test_commissioning.py` uses, and GitHub is a fake
session.
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
OLD_FP = hashlib.sha256(OLD_TOKEN.encode()).hexdigest()[:16]


# --- Stand-ins -------------------------------------------------------------------

class FakeEntry:
    def __init__(self, domain, data=None, options=None, entry_id=None):
        self.domain = domain
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.entry_id = entry_id or f"{domain}-entry"
        self.unique_id = entry_id
        self.state = types.SimpleNamespace(value="loaded")


class FakeEntries:
    """Config entries, where HACS loads or not depending on the token it has."""

    def __init__(self, entries):
        self._entries = entries
        self.updates = []
        self.reloads = []
        self.fail_update_with = None     # token whose write raises
        self.fail_reload_with = None     # token whose reload raises
        self.never_loads_with = set()    # tokens HACS reloads with but never loads

    def async_entries(self, domain):
        return [e for e in self._entries if e.domain == domain]

    def async_update_entry(self, entry, data=None, options=None):
        # HA replaces the mapping wholesale, which is why the code under test
        # has to carry the other keys across itself.
        if data is not None and data.get("token") == self.fail_update_with:
            raise ValueError(f"bad data {data!r}")   # an error that echoes it
        self.updates.append({"data": data, "options": options})
        if data is not None:
            entry.data = dict(data)
        if options is not None:
            entry.options = dict(options)

    async def async_reload(self, entry_id):
        entry = next(e for e in self._entries if e.entry_id == entry_id)
        token = entry.data.get("token")
        self.reloads.append(token)
        if token == self.fail_reload_with:
            entry.state = types.SimpleNamespace(value="setup_error")
            raise RuntimeError(f"reload failed for {token}")
        entry.state = types.SimpleNamespace(
            value="setup_retry" if token in self.never_loads_with else "loaded")
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


class FakeResponse:
    def __init__(self, status, headers=None, body=""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeGitHub:
    """The shared session, answering GitHub's way, or failing to connect."""

    def __init__(self):
        self.status = 200
        self.headers = {}
        self.body = ""
        self.raises = None
        self.requests = []

    def get(self, url, headers=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return FakeResponse(self.status, self.headers, self.body)


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


@pytest.fixture(autouse=True)
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(hacs_token, "_session", lambda hass: fake)
    # Waiting for HACS to load is real time in production; not here.
    monkeypatch.setattr(hacs_token, "LOAD_POLL_S", 0.001)
    monkeypatch.setattr(hacs_token, "LOAD_TIMEOUT_S", 0.01)
    return fake


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
        snapshot = {"hacs": collector._collect_hacs(hass),
                    hacs_token.SNAPSHOT_KEY: collector._collect_hacs_token(hass)}
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


# --- Refusals before anything is asked -------------------------------------------

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
    def test_something_that_is_not_a_token(self, token, github):
        hass = FakeHass({"token": OLD_TOKEN})
        fp = hacs_token.fingerprint(token) if isinstance(token, str) else FP
        result = send(hass, token=token, fingerprint=fp)
        assert result["ok"] is False and result["reason"] == "invalid_token"
        assert hass.hacs().data["token"] == OLD_TOKEN
        assert hass.config_entries.updates == [] and github.requests == []

    @pytest.mark.parametrize("fingerprint", [
        None, "", FP.upper(), FP[:15], FP + "0", OLD_FP,
    ])
    def test_a_fingerprint_that_does_not_match_the_token(self, fingerprint, github):
        """A token damaged on the way is refused before it can break HACS."""
        hass = FakeHass({"token": OLD_TOKEN})
        result = send(hass, token=TOKEN, fingerprint=fingerprint)
        assert result["ok"] is False and result["reason"] == "invalid_token"
        assert hass.config_entries.updates == [] and github.requests == []

    def test_classic_tokens_are_accepted_too(self):
        classic = "ghp_" + "A1b2" * 9
        hass = FakeHass({"token": OLD_TOKEN})
        result = send(hass, token=classic, fingerprint=hacs_token.fingerprint(classic))
        assert result["ok"] is True and result["changed"] is True

    def test_no_hacs_entry_is_refused_and_none_is_created(self, github):
        hass = FakeHass(hacs=False)
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result == {"ok": False, "reason": "no_hacs_entry",
                          "detail": result["detail"]}
        assert hass.config_entries.async_entries("hacs") == []
        assert hass.config_entries.updates == [] and github.requests == []


# --- Verifying with GitHub first -----------------------------------------------------

class TestVerification:

    def test_asks_github_with_the_new_token(self, github):
        hass = FakeHass({"token": OLD_TOKEN})
        send(hass, token=TOKEN, fingerprint=FP)
        [request] = github.requests
        assert request["url"] == "https://api.github.com/repos/kaboomAE/dartec-ha-manager"
        assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert request["timeout"] == 15

    @pytest.mark.parametrize("status, headers, body", [
        (401, {}, '{"message": "Bad credentials"}'),
        (403, {"X-RateLimit-Remaining": "4999"}, '{"message": "Bad credentials"}'),
        (404, {}, '{"message": "Not Found"}'),
    ])
    def test_a_token_github_rejects_changes_nothing(self, github, status, headers, body):
        github.status, github.headers, github.body = status, headers, body
        hass = FakeHass({"token": OLD_TOKEN})
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is False and result["reason"] == "token_rejected"
        assert hass.hacs().data["token"] == OLD_TOKEN
        assert hass.config_entries.updates == [] and hass.config_entries.reloads == []
        assert hass.logbook() == []

    @pytest.mark.parametrize("failure", [
        asyncio.TimeoutError(), OSError("connection refused"),
        ConnectionResetError("reset"),
    ])
    def test_a_network_error_changes_nothing(self, github, failure):
        github.raises = failure
        hass = FakeHass({"token": OLD_TOKEN})
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is False and result["reason"] == "github_unreachable"
        assert hass.hacs().data["token"] == OLD_TOKEN
        assert hass.config_entries.updates == [] and hass.config_entries.reloads == []

    @pytest.mark.parametrize("status, headers, body", [
        (500, {}, ""), (502, {}, ""), (503, {}, ""),
        (429, {}, ""),
        (403, {"X-RateLimit-Remaining": "0"}, ""),
        (403, {}, '{"message": "API rate limit exceeded"}'),
    ])
    def test_github_s_own_trouble_is_not_the_token_s_fault(self, github, status,
                                                           headers, body):
        """The server retries `github_unreachable` later; `token_rejected` would
        wrongly tell it the token is bad."""
        github.status, github.headers, github.body = status, headers, body
        hass = FakeHass({"token": OLD_TOKEN})
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["reason"] == "github_unreachable"
        assert hass.config_entries.updates == []

    def test_an_unchanged_token_needs_no_check(self, github):
        hass = FakeHass({"token": TOKEN})
        assert send(hass, token=TOKEN, fingerprint=FP)["changed"] is False
        assert github.requests == []


# --- The swap ---------------------------------------------------------------------

class TestSwap:

    def test_the_same_token_changes_nothing(self):
        hass = FakeHass({"token": TOKEN, "experimental": True})
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is True and result["changed"] is False
        assert result["fingerprint"] == FP
        assert hass.config_entries.updates == []
        assert hass.config_entries.reloads == []
        assert hass.logbook() == []

    def test_a_verified_token_replaces_only_the_token_and_hacs_loads(self):
        data = {"token": OLD_TOKEN, "experimental": True, "sidepanel_title": "HACS"}
        hass = FakeHass(data)
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is True and result["changed"] is True
        assert result["fingerprint"] == FP and result["previous_fingerprint"] == OLD_FP
        assert hass.hacs().data == {**data, "token": TOKEN}
        assert hass.config_entries.updates == [
            {"data": {**data, "token": TOKEN}, "options": None}]
        assert hass.config_entries.reloads == [TOKEN]

    def test_the_swap_is_in_the_home_s_logbook_by_fingerprint(self):
        hass = FakeHass({"token": OLD_TOKEN})
        send(hass, token=TOKEN, fingerprint=FP)
        assert hass.logbook() == [
            f"Dartec updated the HACS GitHub token (fingerprint {FP}, "
            f"replacing {OLD_FP})"]

    def test_the_snapshot_then_reports_the_new_token(self):
        hass = FakeHass({"token": OLD_TOKEN})
        send(hass, token=TOKEN, fingerprint=FP)
        assert hacs_token.snapshot_section(hass) == {"token_fingerprint": FP}

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


# --- Rolling back ---------------------------------------------------------------------

class TestRollback:

    @pytest.mark.parametrize("how", ["reload_raises", "never_loads", "update_raises"])
    def test_a_failed_swap_restores_the_previous_token(self, how):
        data = {"token": OLD_TOKEN, "experimental": True}
        hass = FakeHass(data)
        entries = hass.config_entries
        if how == "reload_raises":
            entries.fail_reload_with = TOKEN
        elif how == "never_loads":
            entries.never_loads_with = {TOKEN}
        else:
            entries.fail_update_with = TOKEN

        result = send(hass, token=TOKEN, fingerprint=FP)

        assert result["ok"] is False and result["reason"] == "rolled_back"
        assert result["changed"] is False
        assert hass.hacs().data == data                 # every key, as it was
        assert hass.config_entries.reloads[-1] == OLD_TOKEN
        assert hass.hacs().state.value == "loaded"
        assert hacs_token.snapshot_section(hass) == {"token_fingerprint": OLD_FP}

    def test_a_rollback_is_in_the_logbook_by_fingerprint(self):
        hass = FakeHass({"token": OLD_TOKEN})
        hass.config_entries.fail_reload_with = TOKEN
        send(hass, token=TOKEN, fingerprint=FP)
        [line] = hass.logbook()
        assert FP in line and OLD_FP in line and "restored" in line

    def test_only_a_failed_rollback_is_reload_failed(self):
        hass = FakeHass({"token": OLD_TOKEN})
        hass.config_entries.never_loads_with = {TOKEN, OLD_TOKEN}
        result = send(hass, token=TOKEN, fingerprint=FP)
        assert result["ok"] is False and result["reason"] == "reload_failed"
        assert hass.hacs().data["token"] == OLD_TOKEN   # still put back
        [line] = hass.logbook()
        assert "needs checking" in line


# --- Where the token must never appear ---------------------------------------------

class TestTheTokenStaysInTheEntry:

    @pytest.mark.parametrize("scenario", [
        "changed", "unchanged", "bad_fingerprint", "no_entry", "rejected",
        "unreachable", "update_raises", "reload_raises", "rollback_fails",
    ])
    def test_not_in_the_response_logs_or_logbook(self, scenario, caplog, github):
        caplog.set_level(logging.DEBUG)
        hass = FakeHass({"token": TOKEN if scenario == "unchanged" else OLD_TOKEN},
                        hacs=scenario != "no_entry")
        entries = hass.config_entries
        fingerprint = "0" * 16 if scenario == "bad_fingerprint" else FP
        if scenario == "rejected":
            github.status, github.body = 401, '{"message": "Bad credentials"}'
        if scenario == "unreachable":
            github.raises = OSError(f"could not connect with {TOKEN}")
        if scenario == "update_raises":
            entries.fail_update_with = TOKEN
        if scenario == "reload_raises":
            entries.fail_reload_with = TOKEN
        if scenario == "rollback_fails":
            entries.fail_reload_with = TOKEN
            entries.never_loads_with = {OLD_TOKEN}

        result = send(hass, token=TOKEN, fingerprint=fingerprint)

        for secret in (TOKEN, OLD_TOKEN):
            assert secret not in json.dumps(result)
            assert secret not in caplog.text
            assert all(secret not in json.dumps(data) for _, data in hass.bus.fired)


# --- Registration ------------------------------------------------------------------

class TestRegistration:

    def test_hacs_cmds_registers_the_handler(self):
        tree = ast.parse((_AGENT / "hacs_cmds.py").read_text(encoding="utf-8"))
        handlers = next(node.value for node in tree.body
                        if isinstance(node, ast.Assign)
                        and any(getattr(t, "id", None) == "HANDLERS" for t in node.targets))
        mapping = {k.value: v.id for k, v in zip(handlers.keys, handlers.values)}
        assert mapping.get("hacs_token_set") == "hacs_token_set"
