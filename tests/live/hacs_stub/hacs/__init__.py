"""A stand-in for HACS, for the live test only: its config entry lifecycle, nothing else.

`hacs_token_set` has to survive how HACS really reloads, and the unit fakes
cannot show that. HACS 2.x (custom_components/hacs/__init__.py) does three
things this copies line for line in spirit:

* `async_setup_entry` registers `add_update_listener(async_reload_entry)`
  inside `async_on_unload`, so any write to the entry starts a reload;
* `async_reload_entry` unloads and sets up **by hand**, outside Home
  Assistant's config entry state machine;
* `async_unload_entry` reads `hass.data["hacs"]` without a default, returns
  False while its queue has pending tasks, and unloads the `switch` and
  `update` platforms it forwarded.

That is the combination that left a real HACS in `failed_unload` on the bench
Pi (dartec-ha-manager#8), so the swap is tested against it inside real Home
Assistant.

It also replaces the agent's GitHub check for the token, since the container
must not depend on GitHub accepting a made-up token. That is the only thing it
changes in the agent, and it is test code that is never installed anywhere
but the throwaway container.
"""
from __future__ import annotations

import hashlib
import logging
import time

import custom_components.dartec_ha_manager.hacs_token as agent_hacs_token

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hacs"
PLATFORMS = [Platform.SWITCH, Platform.UPDATE]
STORE_WRITE_S = 0.5


class _Queue:
    has_pending_tasks = False


class StubHacs:
    def __init__(self, token: str) -> None:
        self.queue = _Queue()
        self.fingerprint = hashlib.sha256(token.encode()).hexdigest()[:16]


async def _verified(_hass, _token):
    return agent_hacs_token.VERIFIED


def _bypass_github() -> None:
    """The agent checks a new token with GitHub first. Here, accept it."""
    if agent_hacs_token.verify_token is not _verified:
        agent_hacs_token.verify_token = _verified
        _LOGGER.warning("live test: the agent's GitHub token check is replaced")


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    config_entry.async_on_unload(config_entry.add_update_listener(async_reload_entry))
    _bypass_github()
    hacs = StubHacs(config_entry.data.get("token", ""))
    hass.data[DOMAIN] = hacs
    hass.data.setdefault("hacs_stub_setups", []).append(hacs.fingerprint)
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    hacs: StubHacs = hass.data[DOMAIN]
    if hacs.queue.has_pending_tasks:
        _LOGGER.warning("Pending tasks, can not unload, try again later.")
        return False
    # HACS writes its repository data to disk here, in the executor, before it
    # unloads anything. On a Pi that takes a noticeable time, and it is while a
    # reload waits here that a second one can start (dartec-ha-manager#8).
    await hass.async_add_executor_job(time.sleep, STORE_WRITE_S)
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    hass.data.pop(DOMAIN, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    # Counted, because whether this fires during a swap depends on nothing but
    # how the swap writes the entry, while whether the two reloads then collide
    # depends on timing. The count is the deterministic half.
    hass.data["hacs_stub_listener_reloads"] = hass.data.get("hacs_stub_listener_reloads", 0) + 1
    if not await async_unload_entry(hass, config_entry):
        return
    await async_setup_entry(hass, config_entry)
