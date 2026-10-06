"""Reading the device registry without the mapping API Home Assistant retired.

HA 2026.9 turned `DeviceRegistry.devices` from a dict keyed by device id into a
view that iterates entries and logs every mapping use — `.values()`, `.get()`,
`[id]` — as deprecated, breaking in 2027.9. hacs.json still promises 2024.6,
where iterating the same attribute yields ids. These tests pin both shapes, and
sweep the integration so the mapping form cannot quietly come back.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
sys.path.insert(0, str(PACKAGE))

from registry_access import all_devices, device_config_entries  # noqa: E402


class Entry:
    def __init__(self, device_id: str, area_id: str | None = None):
        self.id = device_id
        self.area_id = area_id


class MappingUsed(AssertionError):
    pass


class NewView:
    """2026.9: iteration yields entries; any mapping use is the deprecation."""

    def __init__(self, entries):
        self._entries = {e.id: e for e in entries}

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self):
        return len(self._entries)

    def __getitem__(self, key):
        raise MappingUsed(f"devices[{key!r}]")

    def __contains__(self, key):
        raise MappingUsed("id in devices")

    def __getattr__(self, name):
        raise MappingUsed(f"devices.{name}")


class Registry:
    def __init__(self, devices, entries):
        self.devices = devices
        self._by_id = {e.id: e for e in entries}

    def async_get(self, device_id):
        return self._by_id.get(device_id)


def new_registry(*entries):
    return Registry(NewView(entries), entries)


def old_registry(*entries):
    # 2024.6 through 2026.8: a UserDict keyed by id, so a dict is faithful.
    return Registry({e.id: e for e in entries}, entries)


class TestBothShapesYieldEntries:
    def test_current_home_assistant_never_touches_the_mapping(self):
        a, b = Entry("a", "kitchen"), Entry("b")
        assert all_devices(new_registry(a, b)) == [a, b]

    def test_oldest_supported_home_assistant_resolves_ids_to_entries(self):
        a, b = Entry("a", "kitchen"), Entry("b")
        assert all_devices(old_registry(a, b)) == [a, b]

    def test_an_id_that_vanished_mid_iteration_is_skipped_not_returned_as_none(self):
        a = Entry("a")
        registry = Registry({"a": a, "gone": None}, [a])
        assert all_devices(registry) == [a]

    def test_an_empty_registry_is_an_empty_list(self):
        assert all_devices(new_registry()) == []
        assert all_devices(old_registry()) == []


class TestTheMappingFormStaysGone:
    """A sweep, not a unit test: the deprecation is only logged, so a new
    `devices.devices.values()` would pass every other test here and surface
    months later as a warning on a customer's home."""

    MAPPING_USE = re.compile(r"\.devices(\.(values|get|items|keys|get_entry)\s*\(|\[)")

    def test_no_module_uses_the_device_registry_as_a_mapping(self):
        offenders = [f"{path.name}:{number}: {line.strip()}"
                     for path in sorted(PACKAGE.glob("*.py"))
                     for number, line in enumerate(
                         path.read_text(encoding="utf-8").splitlines(), start=1)
                     if self.MAPPING_USE.search(line)]
        assert offenders == []


class DeprecatedShimRead(AssertionError):
    pass


class DeviceSince2026_8:
    """2026.8 on: one `config_entry_id`; `config_entries` is a shim that
    2026.10 reports on every read. Slots, like HA's attrs classes."""

    __slots__ = ("config_entry_id",)

    def __init__(self, config_entry_id):
        self.config_entry_id = config_entry_id

    @property
    def config_entries(self):
        raise DeprecatedShimRead("DeviceEntry.config_entries")

    @property
    def config_entries_subentries(self):
        raise DeprecatedShimRead("DeviceEntry.config_entries_subentries")

    @property
    def primary_config_entry(self):
        raise DeprecatedShimRead("DeviceEntry.primary_config_entry")


class DeviceUpTo2026_7:
    """2024.6 through 2026.7: a set of entries and no `config_entry_id` at all."""

    __slots__ = ("config_entries",)

    def __init__(self, *entry_ids):
        self.config_entries = set(entry_ids)


class TestADevicesConfigEntries:
    def test_current_home_assistant_reads_the_one_entry_not_the_shim(self):
        assert device_config_entries(DeviceSince2026_8("entry-1")) == {"entry-1"}

    def test_oldest_supported_home_assistant_reads_the_set(self):
        assert device_config_entries(DeviceUpTo2026_7("entry-1", "entry-2")) ==             {"entry-1", "entry-2"}

    def test_a_device_with_no_entry_is_an_empty_set_on_both(self):
        assert device_config_entries(DeviceSince2026_8(None)) == set()
        assert device_config_entries(DeviceUpTo2026_7()) == set()

    def test_the_old_set_is_copied_not_shared(self):
        device = DeviceUpTo2026_7("entry-1")
        device_config_entries(device).add("entry-2")
        assert device.config_entries == {"entry-1"}

    def test_both_shapes_feed_the_offline_rules_the_same_way(self):
        from device_health import device_is_judged
        loaded = {"entry-1"}
        for device in (DeviceSince2026_8("entry-1"), DeviceUpTo2026_7("entry-1")):
            assert device_is_judged({"config_entries": device_config_entries(device)}, loaded)
        for device in (DeviceSince2026_8("entry-2"), DeviceUpTo2026_7("entry-2")):
            assert not device_is_judged({"config_entries": device_config_entries(device)},
                                        loaded)


class TestTheDeprecatedEntryShimsStayGone:
    """Like the mapping sweep: 2026.10 only logs these reads, so a new one
    would pass every other unit test and break on a home in 2027.10.
    `hass.config_entries` and `homeassistant.config_entries` are a different
    thing (the config entry manager) and are not matched."""

    SHIM_USE = re.compile(r"(?<!hass)(?<!homeassistant)\.config_entries\b"
                          r"|\.config_entries_subentries\b|\.primary_config_entry\b")

    def test_no_module_reads_a_devices_deprecated_entry_attributes(self):
        offenders = [f"{path.name}:{number}: {line.strip()}"
                     for path in sorted(PACKAGE.glob("*.py"))
                     if path.name != "registry_access.py"  # the one place that may
                     for number, line in enumerate(
                         path.read_text(encoding="utf-8").splitlines(), start=1)
                     if self.SHIM_USE.search(line)]
        assert offenders == []

    def test_the_sweep_would_catch_the_line_that_broke_on_2026_10(self):
        assert self.SHIM_USE.search('"config_entries": device.config_entries}')
        assert not self.SHIM_USE.search("for entry in hass.config_entries.async_entries():")
        assert not self.SHIM_USE.search("from homeassistant.config_entries import ConfigEntry")
