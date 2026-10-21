"""The home's own internet check, and a record of the outages it saw.

The owner's request, 2026-10-18: an internet outage must be reported even
though, during one, the home cannot reach Baytec at all (the agent's link is
over the same line). So the agent watches the line itself, keeps what it saw
in the house, and reports it once the line is back:

* **The check.** Every 30 seconds, a TCP connection to three public DNS
  resolvers by address (Cloudflare 1.1.1.1, Google 8.8.8.8, Quad9 9.9.9.9,
  port 443): a handshake and nothing else, no data, no name lookup, so a
  broken DNS server does not read as a broken line. Up as soon as any one
  answers; down only after two rounds in a row where none does, so a single
  dropped round is not an outage. Brand-neutral: it needs no network
  equipment, no integration and nothing set up by an installer.
* **A guard against a false "down".** If every probe fails while the agent
  is connected to Baytec, the internet plainly works and something on the
  network is blocking the probes: the state is `blocked`, never `down`.
* **The record.** Each outage is kept, with when it started and ended, in
  Home Assistant's own storage (`.storage/dartec_ha_manager.internet`), so it
  survives a restart and is still there to report when the line returns. At
  most 20, and none older than 30 days. Written only when the state changes.
  An outage still open when Home Assistant restarts is carried over and
  marked `across_restart`, because the time Home Assistant was down is then
  inside it and nobody watched it.
* **Reconnecting sooner.** When the line comes back the cloud link is told at
  once (SIGNAL_INTERNET_BACK) rather than waiting out its backoff, which can
  be five minutes.

What it cannot see: a power cut stops Home Assistant too, so it leaves no
outage here; the manager tells the two apart from the home's uptime.

The rules (`InternetState`) have no Home Assistant imports and are tested on
their own (tests/test_internet_watch.py); `async_start` is the adapter.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))
PROBE_TIMEOUT_S = 5
CHECK_INTERVAL_S = 30
DOWN_AFTER_ROUNDS = 2
MAX_OUTAGES = 20
KEEP_DAYS = 30
STORE_VERSION = 1
STORE_KEY = "dartec_ha_manager.internet"
DATA_KEY = "dartec_ha_manager_internet"
SIGNAL_INTERNET_BACK = "dartec_ha_manager_internet_back"


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


class InternetState:
    """What the check has seen: the current state and the outages.

    `observe` takes one round's result and returns True when the stored
    record changed (and so must be saved): a state change, never a round.
    """

    def __init__(self, stored: dict | None = None) -> None:
        stored = stored if isinstance(stored, dict) else {}
        self.state = "unknown"        # up, down, blocked, unknown
        self.since: str | None = None
        self.last_check: str | None = None
        self._failed_rounds = 0
        self.outages: list[dict] = [o for o in stored.get("outages") or []
                                    if isinstance(o, dict) and _parse(o.get("start"))]
        open_ = stored.get("open")
        # An outage that was open when Home Assistant stopped: carried over,
        # since nobody can say when it ended, and closed by the first round
        # that reaches the internet.
        self.open: dict | None = (dict(open_, across_restart=True)
                                  if isinstance(open_, dict) and _parse(open_.get("start"))
                                  else None)
        if self.open:
            self.state, self.since = "down", self.open["start"]

    def observe(self, reached: bool, link_up: bool, now: datetime) -> bool:
        self.last_check = _iso(now)
        if reached:
            self._failed_rounds = 0
            changed = self.state != "up"
            if self.open is not None:
                start = _parse(self.open["start"]) or now
                self.outages.append({
                    "start": self.open["start"], "end": _iso(now),
                    "seconds": max(0, int((now - start).total_seconds())),
                    "across_restart": bool(self.open.get("across_restart"))})
                self.open = None
            if changed:
                self.state, self.since = "up", _iso(now)
            self._prune(now)
            return changed
        self._failed_rounds += 1
        if link_up:
            # Baytec is reachable, so the internet is: the probes are blocked.
            changed = self.state != "blocked"
            if changed:
                self.state, self.since = "blocked", _iso(now)
            return changed
        if self._failed_rounds < DOWN_AFTER_ROUNDS or self.state == "down":
            return False
        # Down since the first round that failed, not the one that decided it.
        started = now - timedelta(seconds=CHECK_INTERVAL_S * (DOWN_AFTER_ROUNDS - 1))
        self.state, self.since = "down", _iso(started)
        self.open = {"start": self.since}
        return True

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=KEEP_DAYS)
        self.outages = [o for o in self.outages
                        if (_parse(o.get("end") or o.get("start")) or now) >= cutoff][-MAX_OUTAGES:]

    def stored(self) -> dict:
        return {"outages": self.outages, "open": self.open}

    def snapshot(self) -> dict:
        """What the `network` section's `internet.agent` carries."""
        return {"state": self.state, "since": self.since, "last_check": self.last_check,
                "outages": list(self.outages[-MAX_OUTAGES:]),
                "open_outage": self.open}


async def probe(targets=PROBE_TARGETS, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """Whether any target accepts a TCP connection. Nothing is sent."""
    async def one(host: str, port: int) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout)
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True

    results = await asyncio.gather(*(one(h, p) for h, p in targets))
    return any(results)


def link_up(hass) -> bool:
    from .const import DOMAIN

    return any(getattr(link, "connected", False)
               for link in (hass.data.get(DOMAIN) or {}).values())


def current(hass) -> dict | None:
    """This home's internet as last checked, or None before the first round."""
    watch: InternetState | None = hass.data.get(DATA_KEY)
    return watch.snapshot() if watch is not None and watch.last_check else None


async def async_start(hass, entry) -> None:
    """Load the record and run the check until the entry unloads."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.helpers.storage import Store

    from .const import SIGNAL_SNAPSHOT_NOW

    store = Store(hass, STORE_VERSION, STORE_KEY)
    watch = InternetState(await store.async_load())
    hass.data[DATA_KEY] = watch

    async def loop() -> None:
        while True:
            was = watch.state
            reached = await probe()
            if watch.observe(reached, link_up(hass), datetime.now(timezone.utc)):
                await store.async_save(watch.stored())
                if was in ("down", "unknown") and watch.state == "up":
                    # Reconnect now rather than after the backoff, and if
                    # already connected, report the outage that just ended.
                    async_dispatcher_send(hass, SIGNAL_INTERNET_BACK)
                    async_dispatcher_send(hass, SIGNAL_SNAPSHOT_NOW)
                if watch.state in ("down", "up") and was != "unknown":
                    _LOGGER.info("Internet %s since %s", watch.state, watch.since)
            await asyncio.sleep(CHECK_INTERVAL_S)

    entry.async_create_background_task(hass, loop(), "dartec_ha_manager internet watch")
