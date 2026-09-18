"""dartec-ha-manager#11, through the real dispatcher: the cloud cannot call a
service on this integration's own entities, however routine the service."""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path


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

from dartec_ha_manager import commands  # noqa: E402


class FakeRegistry:
    def __init__(self, entities):
        self.entities = {e.entity_id: e for e in entities}


class FakeHass:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.fired = []
        self.bus = types.SimpleNamespace(
            async_fire=lambda event, data=None: self.fired.append((event, data)))
        self.config_entries = types.SimpleNamespace(async_entries=lambda domain=None: [])

        async def async_call(domain, service, data, blocking=False):
            self.calls.append((domain, service, data))
        self.services = types.SimpleNamespace(async_call=async_call)


def _run(hass, cmd, registry):
    _stub("homeassistant.helpers.entity_registry", async_get=lambda h: registry)
    return asyncio.run(commands.execute_command(hass, cmd))


def _entity(entity_id, platform):
    return types.SimpleNamespace(entity_id=entity_id, platform=platform)


def switch_on(entity_id, **extra):
    return {"action": "call_service", "domain": "switch", "service": "turn_on",
            "service_data": {"entity_id": entity_id, **extra}}


def test_the_consent_switch_cannot_be_switched_on_remotely():
    hass = FakeHass()
    registry = FakeRegistry([_entity("switch.allow_dartec_support", "dartec_ha_manager")])
    result = _run(hass, switch_on("switch.allow_dartec_support"), registry)
    assert result["ok"] is False and result.get("refused")
    assert hass.calls == []
    assert any("Refused" in (data or {}).get("message", "") for _, data in hass.fired)


def test_a_renamed_own_entity_is_still_recognised():
    hass = FakeHass()
    registry = FakeRegistry([_entity("switch.front_door_dartec", "dartec_ha_manager")])
    result = _run(hass, switch_on("switch.front_door_dartec"), registry)
    assert result.get("refused") and hass.calls == []


def test_an_ordinary_switch_still_works():
    hass = FakeHass()
    registry = FakeRegistry([_entity("switch.allow_dartec_support", "dartec_ha_manager"),
                             _entity("switch.porch", "tplink")])
    result = _run(hass, switch_on("switch.porch"), registry)
    assert result["ok"] is True
    assert hass.calls == [("switch", "turn_on", {"entity_id": "switch.porch"})]


def test_an_area_cannot_carry_the_switch_in():
    hass = FakeHass()
    result = _run(hass, switch_on("switch.porch", area_id="hallway"), FakeRegistry([]))
    assert result.get("refused") and hass.calls == []
