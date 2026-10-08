"""`theme_set` when the theme is not loaded: a code, and whether the line is there.

The manager applies a new home's default theme by itself (server
docs/35-new-home-look.md). When the theme is not loaded it has to tell the
installer one of two different things: add the `frontend: themes:` line to
configuration.yaml and restart, or only restart. It used to have only a
sentence to go on. These pin the two fields it now reads, and that finding
the answer never writes anything.
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
_stub("homeassistant.core", HomeAssistant=object, ServiceCall=object,
      callback=lambda f: f)

_AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
if "dartec_ha_manager" not in sys.modules:
    _pkg = types.ModuleType("dartec_ha_manager")
    _pkg.__path__ = [str(_AGENT)]
    sys.modules["dartec_ha_manager"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"dartec_ha_manager._test_{name}",
                                                  _AGENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


home_cmds = _load("home_cmds")


class Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))


class Hass:
    def __init__(self):
        self.services = Services()


@pytest.fixture
def home(monkeypatch):
    """A home with `loaded` themes and a configuration.yaml of `config`."""
    state = {"loaded": {}, "config": {}, "reads": 0}

    async def call_own_ws(hass, msg, timeout=None):
        assert msg == {"type": "frontend/get_themes"}
        return {"success": True, "result": {"themes": state["loaded"]}}

    async def async_hass_config_yaml(hass):
        state["reads"] += 1
        if isinstance(state["config"], Exception):
            raise state["config"]
        return state["config"]

    monkeypatch.setitem(sys.modules, "dartec_ha_manager.ws_bridge",
                        types.SimpleNamespace(call_own_ws=call_own_ws))
    monkeypatch.setitem(sys.modules, "homeassistant.config",
                        types.SimpleNamespace(async_hass_config_yaml=async_hass_config_yaml))
    return state


def theme_set(hass=None, **cmd):
    return asyncio.run(home_cmds.theme_set(hass or Hass(), {"theme": "Dartec", **cmd}))


class TestNotLoaded:
    def test_line_missing(self, home):
        home["config"] = {"homeassistant": {}, "frontend": {}}
        result = theme_set()
        assert not result["ok"]
        assert result["code"] == "theme_not_loaded"
        assert result["themes_line"] is False
        # The sentence an older manager shows is the one it always was.
        assert "!include_dir_merge_named themes" in result["detail"]

    def test_bare_frontend_key_is_no_line(self, home):
        home["config"] = {"frontend": None}
        assert theme_set()["themes_line"] is False

    def test_line_present_means_restart(self, home):
        home["config"] = {"frontend": {"themes": {}}}
        result = theme_set()
        assert result["code"] == "theme_not_loaded" and result["themes_line"] is True

    def test_unreadable_configuration_is_unknown(self, home):
        home["config"] = ValueError("bad yaml")
        result = theme_set()
        assert result["code"] == "theme_not_loaded" and result["themes_line"] is None

    def test_nothing_is_set_or_written(self, home):
        hass = Hass()
        theme_set(hass)
        assert hass.services.calls == []


class TestLoaded:
    def test_sets_the_default_without_reading_configuration(self, home):
        home["loaded"] = {"Dartec": {}}
        hass = Hass()
        result = theme_set(hass)
        assert result["ok"] and "code" not in result
        assert hass.services.calls == [("frontend", "set_theme", {"name": "Dartec"})]
        assert home["reads"] == 0
