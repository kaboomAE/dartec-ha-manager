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
