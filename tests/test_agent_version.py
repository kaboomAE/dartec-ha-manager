"""The agent's own version, reported without blocking the event loop.

The snapshot used to read manifest.json with Path.read_text every 60 seconds,
inside the event loop, which Home Assistant 2026.9 logs as a blocking call.
The version now comes from Home Assistant's loader, which parsed the manifest
at startup. These tests pin that it is asked once, that no file is opened to
answer, and that a failure is retried rather than remembered.
"""
from __future__ import annotations

import asyncio
import builtins
import pathlib
import sys
import types
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
sys.path.insert(0, str(PACKAGE))

import version  # noqa: E402

DOMAIN = "dartec_ha_manager"


class Loader:
    """Stands in for homeassistant.loader; counts how often it is asked."""

    def __init__(self, version_value="0.15.0", fail=False):
        self.calls = 0
        self.version_value = version_value
        self.fail = fail

    async def async_get_integration(self, hass, domain):
        self.calls += 1
        assert domain == DOMAIN
        if self.fail:
            raise RuntimeError("integration not loaded")
        return types.SimpleNamespace(version=self.version_value)


@pytest.fixture
def loader(monkeypatch):
    fake = Loader()
    package = types.ModuleType("homeassistant")
    module = types.ModuleType("homeassistant.loader")
    module.async_get_integration = fake.async_get_integration
    package.loader = module
    monkeypatch.setitem(sys.modules, "homeassistant", package)
    monkeypatch.setitem(sys.modules, "homeassistant.loader", module)
    monkeypatch.setattr(version, "_running_version", None)
    return fake


@pytest.fixture
def no_file_io(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"file I/O while resolving the agent version: {args!r}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse)
    monkeypatch.setattr(pathlib.Path, "open", refuse)


def snapshots(count: int) -> list[str | None]:
    async def run():
        return [await version.async_agent_version(object(), DOMAIN) for _ in range(count)]
    return asyncio.run(run())


class TestNoBlockingRead:
    def test_the_version_comes_from_the_loader_with_no_file_opened(self, loader, no_file_io):
        assert snapshots(1) == ["0.15.0"]

    def test_a_day_of_snapshots_asks_the_loader_once(self, loader, no_file_io):
        assert snapshots(1440) == ["0.15.0"] * 1440
        assert loader.calls == 1

    def test_an_awesomeversion_is_reported_as_plain_text(self, loader):
        class AwesomeVersion:
            def __str__(self):
                return "0.15.0"
        loader.version_value = AwesomeVersion()
        assert snapshots(1) == ["0.15.0"]


class TestFailureIsNotRemembered:
    def test_a_failed_lookup_reports_unknown_and_is_asked_again(self, loader):
        loader.fail = True
        assert snapshots(2) == [None, None]
        assert loader.calls == 2

        loader.fail = False
        assert snapshots(1) == ["0.15.0"]

    def test_a_manifest_with_no_version_is_not_cached_as_known(self, loader):
        loader.version_value = None
        assert snapshots(2) == [None, None]
        assert loader.calls == 2


class TestTheCollectorNoLongerReadsItsManifest:
    """collector.py imports Home Assistant at module level, so it is checked
    by source here rather than imported."""

    def test_collector_has_no_manifest_read(self):
        source = (PACKAGE / "collector.py").read_text(encoding="utf-8")
        assert "manifest.json" not in source.replace(
            "From the loader, not manifest.json", "")
        assert "read_text" not in source
        assert "_agent_version()" not in source
