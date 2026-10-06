"""Enumerating the device registry across Home Assistant versions.

Kept free of Home Assistant imports so it can be tested on its own, like
registry_paging and version.

`DeviceRegistry.devices` changed shape under us. Up to 2026.8 it was a
UserDict keyed by device id, so iterating it yielded ids and the way to the
entries was `.values()`. From 2026.9 it is a view whose iteration yields the
entries themselves, and every mapping use — `.values()`, `.get()`, `[id]`,
`id in` — logs a deprecation that becomes a break in 2027.9. Neither spelling
works on both, and hacs.json still promises 2024.6.

Iterating is the one access supported on both, so this iterates and resolves
whatever comes back: an entry is used as-is, an id is looked up through
`DeviceRegistry.async_get`, which exists on every version we support. A single
lookup by id should call `async_get` directly rather than come through here.

A device's config entries changed shape too. Up to 2026.7 a device could
belong to several, held in the set `DeviceEntry.config_entries`. From 2026.8 it
belongs to exactly one, `DeviceEntry.config_entry_id`, and `config_entries` is
a shim returning that one in a set; from 2026.10 every read of the shim is
reported, and 2027.10 removes it. `device_config_entries` reads whichever the
running version has.
"""
from __future__ import annotations

from typing import Any


def all_devices(registry: Any) -> list[Any]:
    """Every device entry in `registry`, without touching the mapping API."""
    entries = []
    for item in registry.devices:
        if isinstance(item, str):
            # Before 2026.9, iterating the registry yields device ids.
            item = registry.async_get(item)
            if item is None:
                continue
        entries.append(item)
    return entries


_MISSING = object()


def device_config_entries(device: Any) -> set[str]:
    """The ids of the config entries `device` belongs to, on any version.

    2026.8 and later: the one `config_entry_id`, never the deprecated shim.
    Before 2026.8 the attribute does not exist, and `config_entries` is the
    real field. A device with no entry at all comes back as an empty set.
    """
    entry_id = getattr(device, "config_entry_id", _MISSING)
    if entry_id is not _MISSING:
        return {entry_id} if entry_id else set()
    return set(getattr(device, "config_entries", None) or ())
