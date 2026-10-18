"""No media folder in anything Baytec makes or takes (owner, 2026-10-18).

Home Assistant's `media` folder is where a household's automations save
camera snapshots and clips. A backup Baytec starts leaves it out and cannot be
asked to include it, setting the automatic schedule takes it out of the
automatic backups, and a backup that holds it is never copied offsite.
"""
from __future__ import annotations

import asyncio
import importlib.util
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


_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, ServiceCall=object, callback=lambda f: f)

_AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
if "dartec_ha_manager" not in sys.modules:
    _pkg = types.ModuleType("dartec_ha_manager")
    _pkg.__path__ = [str(_AGENT)]
    sys.modules["dartec_ha_manager"] = _pkg

# A private copy: other tests put stand-ins for handler modules in sys.modules.
_spec = importlib.util.spec_from_file_location("dartec_ha_manager._media_backup_cmds",
                                               _AGENT / "backup_cmds.py")
backup_cmds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_cmds)


class Home:
    """Home Assistant's backup websocket API, as much as these commands use."""

    def __init__(self, backups=(), include_folders=None):
        self.sent = []
        self.backups = list(backups)
        self.include_folders = include_folders

    async def ws(self, hass, payload, timeout=60):
        self.sent.append(payload)
        kind = payload["type"]
        if kind == "backup/agents/info":
            return {"success": True, "result": {"agents": [{"agent_id": "backup.local"}]}}
        if kind == "backup/info":
            return {"success": True, "result": {"backups": self.backups}}
        if kind == "backup/config/info":
            return {"success": True, "result": {"config": {
                "create_backup": {"include_folders": self.include_folders},
                "schedule": {}}}}
        if kind == "backup/details":
            found = next(b for b in self.backups if b["backup_id"] == payload["backup_id"])
            return {"success": True, "result": {"backup": found}}
        return {"success": True, "result": {"backup_job_id": "job1"}}

    def last(self, kind):
        return [p for p in self.sent if p["type"] == kind][-1]


@pytest.fixture
def home(monkeypatch):
    def make(supervisor=True, **kwargs):
        h = Home(**kwargs)
        monkeypatch.setattr(backup_cmds, "call_own_ws", h.ws)
        monkeypatch.setattr(backup_cmds, "_has_supervisor", lambda: supervisor)
        return h
    return make


def run(coro):
    return asyncio.run(coro)


class TestBaytecBackups:
    def test_a_backup_leaves_media_out(self, home):
        h = home()
        assert run(backup_cmds.backup_create(None, {}))["ok"]
        assert h.last("backup/generate")["include_folders"] == ["share", "ssl"]

    @pytest.mark.parametrize("folders", [["media"], ["share", "media"], ["MEDIA"]])
    def test_the_manager_cannot_ask_for_media(self, home, folders):
        h = home()
        result = run(backup_cmds.backup_create(None, {"include_folders": folders}))
        assert result["ok"] is False and result["refused"] is True
        assert not [p for p in h.sent if p["type"] == "backup/generate"]

    def test_other_folders_can_still_be_chosen(self, home):
        h = home()
        assert run(backup_cmds.backup_create(None, {"include_folders": ["share"]}))["ok"]
        assert h.last("backup/generate")["include_folders"] == ["share"]

    def test_a_container_install_sends_no_folders(self, home):
        h = home(supervisor=False)
        assert run(backup_cmds.backup_create(None, {}))["ok"]
        assert "include_folders" not in h.last("backup/generate")


class TestTheSchedule:
    def test_media_is_taken_out_of_the_automatic_backups(self, home):
        h = home(include_folders=["media", "share"])
        assert run(backup_cmds.backup_schedule(None, {"recurrence": "daily"}))["ok"]
        assert h.last("backup/config/update")["create_backup"]["include_folders"] == ["share"]

    def test_media_alone_becomes_no_folders(self, home):
        h = home(include_folders=["media"])
        run(backup_cmds.backup_schedule(None, {"recurrence": "daily"}))
        assert h.last("backup/config/update")["create_backup"]["include_folders"] is None

    @pytest.mark.parametrize("chosen", [None, ["share", "ssl"]])
    def test_the_households_folders_are_otherwise_left_alone(self, home, chosen):
        h = home(include_folders=chosen)
        run(backup_cmds.backup_schedule(None, {"recurrence": "daily"}))
        assert "include_folders" not in h.last("backup/config/update")["create_backup"]


class TestOffsite:
    def cmd(self):
        return {"action": "backup_upload", "backup_id": "a1b2c3d4",
                "upload_url": "https://manager.dartec.ae/agent/backup-upload",
                "upload_token": "tok"}

    def test_a_backup_with_media_is_never_copied(self, home, monkeypatch):
        minted = []

        async def mint(hass):
            minted.append(1)
            return "r", "a"
        monkeypatch.setattr(backup_cmds, "mint_owner_token", mint)
        home(backups=[{"backup_id": "a1b2c3d4", "name": "Household's own",
                       "folders": ["share", "media"], "agents": {"backup.local": {}}}])
        result = run(backup_cmds.backup_upload(None, self.cmd()))
        assert result["ok"] is False and result["refused"] is True
        assert "media folder" in result["detail"]
        # Refused before Home Assistant was asked for a download token.
        assert minted == []

    def test_the_list_says_which_backups_hold_media(self, home):
        home(backups=[{"backup_id": "a", "name": "a", "folders": ["media", "share"]},
                      {"backup_id": "b", "name": "b", "folders": ["share"]},
                      {"backup_id": "c", "name": "c"}])
        rows = {b["backup_id"]: b for b in run(backup_cmds.backup_list(None, {}))["backups"]}
        assert [rows[k]["includes_media"] for k in "abc"] == [True, False, False]
