"""Guarded Home Assistant updates: the policy, and the job across restarts.

The job is the part that has to be right, because it runs in houses nobody is
attending and restarts the thing it runs in. So the fake Supervisor below can
kill the "process" mid-call exactly the way a real Core update does, and each
test then starts a fresh Home Assistant against the same on-disk store and
lets `async_setup` carry on. What these pin:

* no backup, no update;
* only one exact, newer, offered version, and only in the class's shape;
* an unhealthy update is rolled back, and a rollback that does not bring the
  home back restores the backup;
* every step is written down before it is taken, and to the logbook;
* the homeowner's opt-out holds, and the manager cannot flip it.
"""
from __future__ import annotations

import asyncio
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

from dartec_ha_manager import commands, ha_update, service_policy  # noqa: E402
from dartec_ha_manager.const import DOMAIN  # noqa: E402


class Died(BaseException):
    """Home Assistant being stopped under us. A BaseException, like the
    CancelledError a real stop raises, so nothing in the job catches it."""


# ── fakes ───────────────────────────────────────────────────────────────────

class Disk:
    """What survives a restart: the Store's file."""

    def __init__(self):
        self.data = None

    def store(self):
        disk = self

        class _Store:
            async def async_load(self):
                return disk.data

            async def async_save(self, data):
                import copy
                disk.data = copy.deepcopy(data)
        return _Store()


class Supervisor:
    """The Supervisor's side of the house: versions, backups, and what an
    update does to the process that asked for it."""

    def __init__(self, *, core="2026.9.2", core_latest="2026.9.3", hassos="16.2",
                 os_version="16.2", os_latest="16.3"):
        self.core, self.core_latest = core, core_latest
        self.hassos = hassos
        self.os, self.os_latest = os_version, os_latest
        self.boot = "A"
        self.slots = {"A": {"version": os_version}, "B": {"version": "16.1"}}
        self.backups: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.config_ok = True
        self.fail = {}               # path -> error message
        self.kills = True            # an update stops the process
        self.core_wont_start = set() # versions the Supervisor itself backs out of
        self.addons = [{"slug": "core_mosquitto", "state": "started"}]

    async def __call__(self, hass, method, path, body=None, timeout=60):
        self.calls.append((method, path, body))
        if path in self.fail:
            raise ha_update.SupervisorError(self.fail[path])
        if path == "/info":
            return {"hassos": self.hassos}
        if path == "/core/info":
            return {"version": self.core, "version_latest": self.core_latest}
        if path == "/os/info":
            return {"version": self.os, "version_latest": self.os_latest,
                    "boot": self.boot, "boot_slots": self.slots}
        if path == "/core/check":
            if not self.config_ok:
                raise ha_update.SupervisorError("POST /core/check: 400 Invalid config")
            return {}
        if path == "/addons":
            return {"addons": self.addons}
        if path == "/backups/new/full":
            slug = f"bk{len(self.backups) + 1}"
            self.backups[slug] = {"slug": slug, "core": self.core}
            return {"slug": slug}
        if path.startswith("/backups/") and path.endswith("/info"):
            slug = path.split("/")[2]
            if slug not in self.backups:
                raise ha_update.SupervisorError("404")
            return self.backups[slug]
        if path == "/core/update":
            target = body["version"]
            if target not in self.core_wont_start:
                self.core = target
            if self.kills:
                raise Died()
            return {}
        if path == "/os/update":
            self.os = body["version"]
            self.boot = "B" if self.boot == "A" else "A"
            self.slots[self.boot] = {"version": self.os}
            raise Died()
        if path == "/os/boot-slot":
            self.boot = body["boot_slot"]
            self.os = self.slots[self.boot]["version"]
            raise Died()
        if path.endswith("/restore/partial"):
            slug = path.split("/")[2]
            self.core = self.backups[slug]["core"]
            raise Died()
        raise AssertionError(f"unexpected Supervisor call {method} {path}")


class Entry:
    def __init__(self, entry_id, domain, state="loaded", options=None):
        self.entry_id, self.domain = entry_id, domain
        self.state = types.SimpleNamespace(value=state)
        self.options = options or {}


class Link:
    connected = True


class Hass:
    def __init__(self, entries, config_dir):
        self.data = {DOMAIN: {"entry": Link()}}
        self.logbook: list[str] = []
        self.state = types.SimpleNamespace(value="RUNNING")
        self.config = types.SimpleNamespace(config_dir=str(config_dir))
        self._entries = entries
        self.tasks = []
        self.bus = types.SimpleNamespace(async_fire=self._fire)
        self.config_entries = types.SimpleNamespace(async_entries=self._async_entries)
        self.states = types.SimpleNamespace(async_all=lambda domain=None: [])

    def _fire(self, event, data=None):
        if event == "logbook_entry":
            self.logbook.append(data["message"])

    def _async_entries(self, domain=None):
        return [e for e in self._entries if domain is None or e.domain == domain]

    def async_create_background_task(self, coro, name=None):
        task = asyncio.get_event_loop().create_task(coro)
        self.tasks.append(task)
        return task


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    for name in ("POLL_S", "SETTLE_S", "CONNECT_WAIT_S", "RESTART_EXPECTED_WITHIN_S"):
        monkeypatch.setattr(ha_update, name, 0)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test")


class Home:
    """One house across its restarts."""

    def __init__(self, tmp_path, monkeypatch, sup=None, options=None):
        self.disk = Disk()
        self.sup = sup or Supervisor()
        self.options = options if options is not None else {}
        self.dir = tmp_path
        self.entries_after: dict[str, str] = {}
        self.logbook: list[str] = []
        monkeypatch.setattr(ha_update, "supervisor", self.sup)
        monkeypatch.setattr(ha_update, "_store", lambda hass: self.disk.store())
        self.boot()

    def boot(self):
        """Home Assistant starts (again)."""
        entries = [Entry("ours", DOMAIN, options=self.options),
                   Entry("zha1", "zha", self.entries_after.get("zha1", "loaded")),
                   Entry("hue1", "hue", self.entries_after.get("hue1", "loaded"))]
        self.hass = Hass(entries, self.dir)
        self.hass.logbook = self.logbook

    async def _settle(self):
        """Run background work until it finishes or the process 'dies'."""
        while True:
            pending = [t for t in self.hass.tasks if not t.done()]
            if not pending:
                return False
            try:
                await asyncio.gather(*pending)
            except Died:
                return True

    def send(self, cmd):
        async def run():
            result = await commands.execute_command(self.hass, cmd)
            died = await self._settle()
            return result, died
        return asyncio.run(run())

    def restart(self):
        """The process was stopped; Home Assistant comes back and resumes."""
        async def run():
            self.boot()
            await ha_update.async_setup(self.hass)
            return await self._settle()
        return asyncio.run(run())

    def run_to_end(self, cmd, max_restarts=6):
        result, died = self.send(cmd)
        restarts = 0
        while died and restarts < max_restarts:
            died = self.restart()
            restarts += 1
        return result

    @property
    def job(self):
        return self.disk.data["job"]


def core(version="2026.9.3", **extra):
    return {"type": "command", "id": "c1", "action": "ha_core_update",
            "version": version, "job_id": "abcdef0123456789", **extra}


def os_cmd(version="16.3"):
    return {"type": "command", "id": "c2", "action": "ha_os_update", "version": version}


# ── the policy ──────────────────────────────────────────────────────────────

class TestPolicy:
    def test_guarded_actions_need_no_window_and_are_not_sensitive(self):
        # agent_update is the one guarded action that is also sensitive: with
        # consent it keeps its full path. See TestGuardedAgentUpdate.
        for action in set(service_policy.GUARDED_ACTIONS) - service_policy.GUARDED_WITHOUT_CONSENT:
            assert not service_policy.is_sensitive({"action": action})
            assert action not in service_policy.SENSITIVE_ACTIONS

    def test_restart_and_other_code_paths_are_not_in_the_class(self):
        for action in ("ha_restart", "hacs_install", "integration_setup"):
            assert action not in service_policy.GUARDED_ACTIONS
            assert service_policy.is_sensitive({"action": action})

    @pytest.mark.parametrize("extra", [{"allow_downgrade": True}, {"force": True},
                                       {"script": "x"}, {"backup": False}])
    def test_nothing_but_a_version_is_accepted(self, extra):
        refused = service_policy.check_guarded(core(**extra), {})
        assert refused and refused[0] == "invalid"

    @pytest.mark.parametrize("version", ["2026.10.0b1", "2026.9", "latest", "",
                                         "2026.9.3; rm -rf /", "dev", "2026.13.1"])
    def test_only_exact_stable_core_versions(self, version):
        refused = service_policy.check_guarded(core(version), {})
        assert refused and refused[0] == "invalid"

    @pytest.mark.parametrize("version", ["16.3.rc1", "16", "17.0.dev20260901"])
    def test_only_exact_stable_os_versions(self, version):
        refused = service_policy.check_guarded(os_cmd(version), {})
        assert refused and refused[0] == "invalid"

    def test_opt_out_is_on_unless_explicitly_false(self):
        assert service_policy.check_guarded(core(), {}) is None
        assert service_policy.check_guarded(core(), {"guarded_updates": "false"}) is None
        refused = service_policy.check_guarded(core(), {"guarded_updates": False})
        assert refused[0] == "consent"

    @pytest.mark.parametrize("current,target,ok", [
        ("2026.9.2", "2026.9.3", True), ("2026.9.3", "2026.10.0", True),
        ("2026.10.0", "2026.9.9", False), ("2026.9.3", "2026.9.3", False),
        (None, "2026.9.3", False), ("16.2", "16.10", True)])
    def test_upgrade_only(self, current, target, ok):
        assert (service_policy.check_upgrade(current, target) is None) is ok

    def test_the_booted_slot_is_the_running_os(self):
        info = {"version": "16.3", "version_pending": "16.3", "boot": "A",
                "boot_slots": {"A": {"version": "16.2"}, "B": {"version": "16.3"}}}
        assert ha_update.running_os_version(info) == "16.2"
        assert ha_update.running_os_version({"version": "16.2"}) == "16.2"

    def test_status_reports_the_tier(self):
        assert service_policy.guarded_status({}) == {
            "enabled": True, "actions": ["agent_update", "ha_core_update", "ha_os_update"]}
        assert service_policy.guarded_status({"guarded_updates": False})["enabled"] is False


# ── the job ─────────────────────────────────────────────────────────────────

class TestHealthyUpdate:
    def test_backup_update_check_succeed(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        result = home.run_to_end(core())
        assert result["ok"] and result["accepted"] and result["job_id"] == "abcdef0123456789"
        assert home.job["state"] == ha_update.SUCCEEDED
        assert home.sup.core == "2026.9.3"
        paths = [c[1] for c in home.sup.calls]
        # The backup exists, confirmed, before the update is sent.
        assert paths.index("/backups/new/full") < paths.index("/backups/bk1/info") \
            < paths.index("/core/update")
        assert home.job["backup_slug"] == "bk1"
        assert all(c["ok"] for c in home.job["health"]["checks"])

    def test_every_step_is_in_the_logbook_with_the_versions(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.run_to_end(core())
        lines = [l for l in home.logbook if "guarded update" in l]
        assert lines and all("2026.9.2 → 2026.9.3" in l for l in lines)
        for words in ("accepted", "backup", "installing", "checking", "done, healthy"):
            assert any(words in l for l in lines), words

    def test_the_update_is_written_down_before_it_is_sent(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        _, died = home.send(core())
        assert died
        assert home.job["state"] == ha_update.UPDATING
        assert home.job["update_sent_at"]              # so a resume does not resend
        home.restart()
        assert [c[1] for c in home.sup.calls].count("/core/update") == 1

    def test_os_update_on_haos(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.run_to_end(os_cmd())
        assert home.job["state"] == ha_update.SUCCEEDED and home.sup.os == "16.3"


class TestUnhealthyUpdate:
    def test_a_lost_integration_rolls_core_back(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        _, died = home.send(core())
        home.entries_after = {"hue1": "setup_error"}     # broken by the new version
        home.restart()                                    # -> checking -> rolling back
        home.entries_after = {}                           # fine again on the old one
        while home.job["state"] not in ha_update.TERMINAL:
            home.restart()
        assert home.job["state"] == ha_update.ROLLED_BACK
        assert home.sup.core == "2026.9.2"
        assert home.job["rollback_method"] == "core_version"
        failed = [c["name"] for c in home.job["health"]["checks"] if not c["ok"]]
        assert failed == ["integrations"]
        assert not home.job.get("restore_used")

    def test_the_bench_file_forces_a_rollback(self, tmp_path, monkeypatch):
        (tmp_path / ha_update.TEST_FILE).write_text("fail_health_check\n")
        home = Home(tmp_path, monkeypatch)
        home.run_to_end(core())
        assert home.job["state"] == ha_update.ROLLED_BACK
        assert home.sup.core == "2026.9.2"
        assert any(c["name"] == "bench_test" and not c["ok"]
                   for c in home.job["health"]["checks"])

    def test_still_unhealthy_on_the_old_version_restores_the_backup(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.send(core())
        home.entries_after = {"zha1": "setup_error"}     # the radio does not come back
        home.restart()                                    # checking -> rolling back
        home.restart()                                    # back on 2026.9.2, still broken
        assert home.job["state"] == ha_update.RESTORING
        home.entries_after = {}                           # the restore fixes it
        while home.job["state"] not in ha_update.TERMINAL:
            home.restart()
        assert home.job["state"] == ha_update.ROLLED_BACK
        assert home.job["restore_used"] is True
        assert any(c[1] == "/backups/bk1/restore/partial" for c in home.sup.calls)
        radios = next(c for c in home.job["health"]["checks"] if c["name"] == "radios")
        assert not radios["ok"] and "zha" in radios["detail"]

    def test_nothing_brings_it_back_is_rollback_failed(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.send(core())
        home.entries_after = {"zha1": "setup_error"}
        for _ in range(6):
            if home.job["state"] in ha_update.TERMINAL:
                break
            home.restart()
        assert home.job["state"] == ha_update.ROLLBACK_FAILED
        assert any("ROLLBACK FAILED" in l for l in home.logbook)

    def test_core_the_supervisor_backed_out_of_is_reported(self, tmp_path, monkeypatch):
        sup = Supervisor()
        sup.core_wont_start = {"2026.9.3"}
        home = Home(tmp_path, monkeypatch, sup)
        home.run_to_end(core())
        assert home.job["state"] == ha_update.ROLLED_BACK
        assert home.job["rollback_method"] == "automatic"

    def test_os_rolls_back_to_the_other_slot(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.send(os_cmd())
        home.entries_after = {"hue1": "setup_error"}
        home.restart()
        home.entries_after = {}
        while home.job["state"] not in ha_update.TERMINAL:
            home.restart()
        assert home.job["state"] == ha_update.ROLLED_BACK
        assert home.sup.os == "16.2"
        assert home.job["rollback_method"] == "boot_slot_A"


class TestNothingChanges:
    def test_no_backup_no_update(self, tmp_path, monkeypatch):
        sup = Supervisor()
        sup.fail["/backups/new/full"] = "no space left"
        home = Home(tmp_path, monkeypatch, sup)
        home.run_to_end(core())
        assert home.job["state"] == ha_update.FAILED
        assert "nothing was changed" in home.job["detail"]
        assert not any(c[1] == "/core/update" for c in sup.calls)

    def test_supervised_installs_refuse_the_os(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch, Supervisor(hassos=None))
        result, _ = home.send(os_cmd())
        assert result["ok"] is False and result["code"] == "not_applicable"
        assert "Supervised" in result["detail"]

    def test_supervised_installs_still_take_core(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch, Supervisor(hassos=None))
        home.run_to_end(core())
        assert home.job["state"] == ha_update.SUCCEEDED

    def test_container_installs_refuse(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        monkeypatch.delenv("SUPERVISOR_TOKEN")
        result, _ = home.send(core())
        assert result["code"] == "not_applicable"

    @pytest.mark.parametrize("version,code", [("2026.9.2", "already"),
                                              ("2026.9.1", "not_upgrade"),
                                              ("2026.10.0", "not_offered")])
    def test_only_a_newer_offered_version(self, tmp_path, monkeypatch, version, code):
        home = Home(tmp_path, monkeypatch)
        result, _ = home.send(core(version))
        assert result["ok"] is False and result["code"] == code
        assert home.disk.data is None                    # no job was taken

    def test_a_broken_config_is_not_updated(self, tmp_path, monkeypatch):
        sup = Supervisor()
        sup.config_ok = False
        home = Home(tmp_path, monkeypatch, sup)
        result, _ = home.send(core())
        assert result["code"] == "precheck"

    def test_one_at_a_time(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.send(core())                                 # dies mid-update
        result, _ = home.send(os_cmd())
        assert result["code"] == "busy"

    def test_the_opt_out_refuses_and_is_logged(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch, options={"guarded_updates": False})
        result, _ = home.send(core())
        assert result == {"ok": False, "refused": True, "code": "consent",
                          "detail": result["detail"]}
        assert any("ha_core_update 2026.9.3" in l and "Refused" in l for l in home.logbook)
        assert not home.sup.calls

    def test_the_manager_cannot_switch_the_opt_out_back_on(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        _stub("homeassistant.helpers.entity_registry", async_get=lambda h: types.SimpleNamespace(
            entities={"switch.dartec_approved_updates": types.SimpleNamespace(
                entity_id="switch.dartec_approved_updates", platform=DOMAIN)}))
        calls = []

        async def async_call(*a, **k):
            calls.append(a)
        home.hass.services = types.SimpleNamespace(async_call=async_call)
        result, _ = home.send({"type": "command", "id": "x", "action": "call_service",
                               "domain": "switch", "service": "turn_on",
                               "service_data": {"entity_id": "switch.dartec_approved_updates"}})
        assert result.get("refused") and not calls


class TestRestarts:
    def test_a_job_that_keeps_restarting_is_ended(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.send(core())
        home.sup.core = "2026.9.2"          # every resume finds it unfinished...
        home.sup.kills = True
        home.disk.data["job"]["state"] = ha_update.CHECKING
        monkeypatch.setitem(ha_update._STEPS, ha_update.CHECKING, _dies)
        for _ in range(ha_update.MAX_RESUMES + 2):
            home.restart()
        assert home.job["state"] == ha_update.ROLLBACK_FAILED
        assert "restarts" in home.job["detail"]

    def test_the_snapshot_carries_the_job_and_the_install(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        home.run_to_end(core())
        home.boot()
        asyncio.run(ha_update.async_setup(home.hass))
        info = {"install": "haos", "core": {"version": "2026.9.3"}}
        section = ha_update.snapshot_section(home.hass, info)
        assert section["install"] == "haos" and section["enabled"] is True
        assert section["job"]["state"] == ha_update.SUCCEEDED
        assert "baseline" not in section["job"]
        assert section["history"][0]["id"] == "abcdef0123456789"


async def _dies(hass, job):
    raise Died()


# ── the agent's own update (owner decision 2026-09-18) ──────────────────────

def agent_cmd(**extra):
    return {"type": "command", "id": "a1", "action": "agent_update", "restart": True,
            "rollout_id": "0123456789abcdef0123456789abcdef", **extra}


class TestGuardedAgentUpdate:
    """agent_update keeps its consent path, and without consent may still run
    in the guarded shape: the latest release, upgrade only, and never once the
    homeowner has turned approved updates off."""

    @pytest.fixture
    def ran(self, monkeypatch):
        from dartec_ha_manager import home_cmds

        calls = []

        async def fake(hass, cmd):
            calls.append(dict(cmd))
            return {"ok": True, "detail": "installed 0.18.1; restarting", "restarting": True}
        monkeypatch.setitem(home_cmds.HANDLERS, "agent_update", fake)
        return calls

    def test_it_is_in_the_tier_and_still_sensitive(self):
        assert "agent_update" in service_policy.GUARDED_ACTIONS
        assert "agent_update" in service_policy.GUARDED_WITHOUT_CONSENT
        assert service_policy.is_sensitive({"action": "agent_update"})
        assert "agent_update" in service_policy.guarded_status({})["actions"]

    def test_runs_without_consent_and_says_so_in_the_logbook(self, tmp_path, monkeypatch, ran):
        home = Home(tmp_path, monkeypatch)
        result, _ = home.send(agent_cmd())
        assert result["ok"] and len(ran) == 1
        assert any("as an approved update" in line for line in home.logbook)

    def test_the_opt_out_refuses_it_as_consent(self, tmp_path, monkeypatch, ran):
        home = Home(tmp_path, monkeypatch, options={"guarded_updates": False})
        result, _ = home.send(agent_cmd())
        assert result["refused"] and result["code"] == "consent" and not ran
        assert "Allow Dartec to install approved updates" in result["detail"]

    @pytest.mark.parametrize("extra", [{"allow_downgrade": True}, {"force": True},
                                       {"repo": "someone/else"}, {"restart": "yes"}])
    def test_nothing_else_rides_along_without_consent(self, tmp_path, monkeypatch, ran, extra):
        home = Home(tmp_path, monkeypatch)
        result, _ = home.send(agent_cmd(**extra))
        assert result["refused"] and result["code"] == "invalid" and not ran

    def test_with_consent_it_runs_as_before_downgrade_included(self, tmp_path, monkeypatch, ran):
        """The exception only adds a way in; a homeowner who switched support
        on can still let Dartec downgrade, as before."""
        home = Home(tmp_path, monkeypatch, options={"unattended_support": True})
        result, _ = home.send(agent_cmd(allow_downgrade=True))
        assert result["ok"] and ran[0]["allow_downgrade"] is True
        assert not any("as an approved update" in line for line in home.logbook)

    def test_consent_with_the_opt_out_off_still_runs(self, tmp_path, monkeypatch, ran):
        home = Home(tmp_path, monkeypatch, options={"unattended_support": True,
                                                    "guarded_updates": False})
        result, _ = home.send(agent_cmd())
        assert result["ok"] and len(ran) == 1

    def test_other_sensitive_actions_are_not_widened(self, tmp_path, monkeypatch):
        home = Home(tmp_path, monkeypatch)
        for action in ("ha_restart", "hacs_install"):
            result, _ = home.send({"type": "command", "id": "x", "action": action})
            assert result["refused"] and result["code"] == "consent"
