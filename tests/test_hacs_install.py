"""`hacs_install`: every install names its version (dartec-ha-manager#54).

A repository's latest release is whatever its maintainer published last. For
a dashboard or a card that is JavaScript in the family's browser, so "latest"
at each rollout sends code nobody at Dartec has tested to every home. What
these pin, against a fake HACS that answers the same websocket commands the
real one does:

* a download always carries the exact version asked for;
* no version, no download, and nothing left behind in HACS;
* `only_if_missing` on something already there still needs no version;
* the downgrade guard holds for a pinned version as it did for latest;
* the one exception, this integration's own latest release, is reachable
  only from `agent_update`, never from a remote command.
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

def _load(name):
    """A private copy of a module from the package. test_commissioning.py puts
    empty stand-ins for the handler modules into sys.modules at import time,
    so a plain import here could get one of those, depending on order."""
    spec = importlib.util.spec_from_file_location(f"dartec_ha_manager._test_{name}",
                                                  _AGENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hacs_cmds = _load("hacs_cmds")
home_cmds = _load("home_cmds")

DWAINS = "dwainscheeren/dwains-dashboard-next"
THEME = "kaboomAE/dartec-theme"
AGENT = "kaboomAE/dartec-ha-manager"


class FakeHacs:
    """HACS as its websocket commands show it: a list of repositories, and
    `add`, `refresh` and `download` (with or without `version`)."""

    def __init__(self, repos=None, latest=None):
        # full_name -> {"id", "installed_version", "available_version"}
        self.repos = {name: {"id": str(i), "full_name": name, **data}
                      for i, (name, data) in enumerate((repos or {}).items(), 1)}
        self.latest = latest or {}      # what GitHub would say is newest
        self.calls: list[dict] = []

    def _by_id(self, repo_id):
        return next(r for r in self.repos.values() if r["id"] == repo_id)

    async def ws(self, hass, msg, timeout=None):
        self.calls.append(dict(msg))
        kind = msg["type"]
        if kind == "hacs/repositories/list":
            return {"success": True, "result": [dict(r) for r in self.repos.values()]}
        if kind == "hacs/repositories/add":
            name = msg["repository"]
            self.repos[name] = {"id": str(len(self.repos) + 100), "full_name": name,
                                "installed_version": None,
                                "available_version": self.latest.get(name)}
            return {"success": True, "result": {}}
        if kind == "hacs/repository/refresh":
            repo = self._by_id(msg["repository"])
            repo["available_version"] = self.latest.get(repo["full_name"],
                                                        repo.get("available_version"))
            return {"success": True, "result": {}}
        if kind == "hacs/repository/download":
            repo = self._by_id(msg["repository"])
            repo["installed_version"] = msg.get("version") or repo.get("available_version")
            return {"success": True, "result": {}}
        raise AssertionError(f"unexpected {kind}")

    def downloads(self):
        return [c for c in self.calls if c["type"] == "hacs/repository/download"]

    def adds(self):
        return [c for c in self.calls if c["type"] == "hacs/repositories/add"]


class Hass:
    def __init__(self):
        self.data = {"hacs": object()}
        self.config = types.SimpleNamespace(components=set())


@pytest.fixture
def hacs(monkeypatch):
    fake = FakeHacs({DWAINS: {"installed_version": "v1.7.0", "available_version": "v1.8.0"},
                     AGENT: {"installed_version": "0.23.0", "available_version": "0.23.0"}},
                    latest={DWAINS: "v1.9.0", THEME: "v1.2.0", AGENT: "0.24.0"})
    monkeypatch.setattr(hacs_cmds, "call_own_ws", fake.ws)
    monkeypatch.setattr(hacs_cmds, "ADD_POLL_S", 0)
    # agent_update imports hacs_install when it runs; give it this copy.
    monkeypatch.setitem(sys.modules, "dartec_ha_manager.hacs_cmds", hacs_cmds)
    return fake


def install(cmd, **kw):
    return asyncio.run(hacs_cmds.hacs_install(Hass(), {"category": "plugin", **cmd}, **kw))


class TestPinned:
    def test_downloads_exactly_the_version_asked_for(self, hacs):
        result = install({"repo": DWAINS, "version": "v1.8.0"})
        assert result["ok"] and result["changed"] and result["installed_version"] == "v1.8.0"
        assert hacs.downloads() == [{"type": "hacs/repository/download", "repository": "1",
                                     "version": "v1.8.0"}]
        assert result["pinned"] is True

    def test_not_the_newer_release_github_has(self, hacs):
        """The whole point: a release published after approval stays out."""
        install({"repo": DWAINS, "version": "v1.8.0"})
        assert hacs.repos[DWAINS]["installed_version"] != "v1.9.0"

    def test_a_pinned_install_asks_github_nothing_first(self, hacs):
        """The latest-release index is irrelevant to a named version, and its
        refresh can take two minutes."""
        install({"repo": DWAINS, "version": "v1.8.0"})
        assert not [c for c in hacs.calls if c["type"] == "hacs/repository/refresh"]

    def test_a_new_repository_is_added_then_pinned(self, hacs):
        result = install({"repo": THEME, "category": "theme", "version": "v1.1.0"})
        assert result["ok"] and result["installed_version"] == "v1.1.0"
        assert hacs.adds() == [{"type": "hacs/repositories/add", "repository": THEME,
                                "category": "theme"}]
        assert hacs.downloads()[0]["version"] == "v1.1.0"

    def test_already_at_that_version_downloads_nothing(self, hacs):
        result = install({"repo": DWAINS, "version": "1.7.0"})
        assert result["ok"] and result["changed"] is False and not hacs.downloads()

    def test_an_older_version_is_a_downgrade_and_refused(self, hacs):
        result = install({"repo": DWAINS, "version": "v1.6.0"})
        assert not result["ok"] and result["code"] == "downgrade" and not hacs.downloads()
        assert "the version asked for is v1.6.0" in result["detail"]

    def test_a_downgrade_can_still_be_asked_for_explicitly(self, hacs):
        result = install({"repo": DWAINS, "version": "v1.6.0", "allow_downgrade": True})
        assert result["ok"] and hacs.downloads()[0]["version"] == "v1.6.0"

    def test_the_home_s_own_copy_counts_as_installed(self, hacs):
        """A hand-installed newer build is not overwritten by an older pin."""
        result = install({"repo": DWAINS, "version": "v1.8.0", "current_version": "1.9.1"})
        assert not result["ok"] and result["code"] == "downgrade" and not hacs.downloads()

    def test_a_commit_that_cannot_be_compared_is_not_forced_over_a_release(self, hacs):
        result = install({"repo": DWAINS, "version": "a1b2c3d"})
        assert not result["ok"] and not hacs.downloads()
        assert "cannot tell" in result["detail"]

    @pytest.mark.parametrize("version", ["v1.8.0", "1.8.0", "2.1.26b1", "v0.0.11", "a1b2c3d",
                                         "0" * 40])
    def test_tags_and_commits_are_versions(self, version):
        assert hacs_cmds.check_version({"repo": DWAINS, "version": version}) is None

    def test_a_commit_on_a_home_without_it_installs(self, hacs):
        result = install({"repo": THEME, "category": "theme", "version": "a1b2c3d"})
        assert result["ok"] and hacs.downloads()[0]["version"] == "a1b2c3d"

    def test_hacs_recording_another_release_is_not_a_success(self, hacs, monkeypatch):
        async def wrong(hass, msg, timeout=None):
            result = await FakeHacs.ws(hacs, hass, msg, timeout)
            if msg["type"] == "hacs/repository/download":
                hacs.repos[DWAINS]["installed_version"] = "v1.9.0"
            return result
        monkeypatch.setattr(hacs_cmds, "call_own_ws", wrong)
        result = install({"repo": DWAINS, "version": "v1.8.0"})
        assert result["ok"] is False and result["changed"] is True
        assert result["code"] == "version_mismatch"

    @pytest.mark.parametrize("version", ["", " ", "v1.8.0; rm -rf", "../v1", "-v1", 180,
                                         ["v1.8.0"], "v" * 65, "main", "master", "dev",
                                         "latest", "release"])
    def test_a_malformed_version_or_a_branch_is_refused_before_hacs_is_asked(self, hacs,
                                                                             version):
        """A branch is accepted by HACS but moves, which is what pinning stops."""
        result = install({"repo": DWAINS, "version": version})
        assert not result["ok"] and result["code"] == "invalid" and not hacs.calls


class TestNoVersion:
    def test_is_refused_and_nothing_is_downloaded(self, hacs):
        result = install({"repo": DWAINS})
        assert result == {**result, "ok": False, "code": "version_required", "changed": False}
        assert not hacs.downloads() and not hacs.calls

    def test_only_if_missing_false_is_the_same(self, hacs):
        """How the manager's dashboard catalogue installs Dwains today."""
        result = install({"repo": DWAINS, "only_if_missing": False})
        assert result["code"] == "version_required" and not hacs.downloads()

    def test_only_if_missing_on_something_installed_needs_none(self, hacs):
        """Nothing is downloaded, so there is nothing to pin."""
        result = install({"repo": DWAINS, "only_if_missing": True})
        assert result["ok"] and result["changed"] is False and not hacs.downloads()
        assert result["installed_version"] == "v1.7.0"

    def test_only_if_missing_on_something_missing_is_refused_and_leaves_nothing(self, hacs):
        """Installing it would take the latest release. Refused before HACS
        is told about the repository, so no custom repository is left behind."""
        result = install({"repo": THEME, "category": "theme", "only_if_missing": True})
        assert result["code"] == "version_required"
        assert not hacs.adds() and not hacs.downloads() and THEME not in hacs.repos

    def test_the_agent_s_own_repository_needs_one_from_the_cloud(self, hacs):
        """The exception belongs to agent_update, not to the repository name."""
        result = install({"repo": AGENT, "category": "integration"})
        assert result["code"] == "version_required" and not hacs.downloads()

    def test_latest_ok_does_not_open_other_repositories(self, hacs):
        result = install({"repo": DWAINS}, latest_ok=True)
        assert result["code"] == "version_required" and not hacs.downloads()

    def test_a_command_cannot_set_latest_ok(self, hacs):
        result = install({"repo": AGENT, "category": "integration", "latest_ok": True})
        assert result["code"] == "version_required" and not hacs.downloads()


class TestTheOneException:
    def test_the_rule_is_one_entry_long_and_says_who_decided(self):
        assert list(hacs_cmds.LATEST_ALLOWED) == [AGENT.lower()]
        assert "owner decision 2026-09-18" in hacs_cmds.LATEST_ALLOWED[AGENT.lower()]

    def test_agent_update_takes_the_latest_release(self, hacs):
        result = asyncio.run(home_cmds.agent_update(Hass(), {"restart": False}))
        assert result["ok"], result
        assert hacs.downloads() == [{"type": "hacs/repository/download", "repository": "2"}]
        assert hacs.repos[AGENT]["installed_version"] == "0.24.0"

    def test_agent_update_keeps_its_downgrade_guard(self, hacs):
        hacs.latest[AGENT] = "0.22.0"
        result = asyncio.run(home_cmds.agent_update(Hass(), {"restart": False}))
        assert not result["ok"] and not hacs.downloads()
