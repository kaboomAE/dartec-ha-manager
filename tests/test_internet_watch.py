"""The home's own internet check (owner's request, 2026-10-18).

An outage cannot be reported while it happens: the agent reaches Baytec over
the same line. So what matters is that the home keeps an honest record and
reports it afterwards, and that it never calls a working line "down".
"""
from __future__ import annotations

import asyncio
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import internet_watch as w  # noqa: E402

T0 = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def run(state, rounds, link=False):
    """rounds: a list of True/False, one per 30-second round from T0."""
    changes = []
    for i, reached in enumerate(rounds):
        if state.observe(reached, link, at(i * w.CHECK_INTERVAL_S)):
            changes.append((i, state.state))
    return changes


class TestTheCheck:
    def test_up(self):
        state = w.InternetState()
        assert run(state, [True, True, True]) == [(0, "up")]
        assert state.since == at(0).isoformat(timespec="seconds")

    def test_one_dropped_round_is_not_an_outage(self):
        state = w.InternetState()
        assert run(state, [True, False, True, True]) == [(0, "up")]
        assert state.outages == [] and state.open is None

    def test_an_outage_is_recorded_from_its_first_failed_round(self):
        state = w.InternetState()
        changes = run(state, [True, False, False, False, False, True])
        assert changes == [(0, "up"), (2, "down"), (5, "up")]
        assert state.outages == [{"start": at(30).isoformat(timespec="seconds"),
                                  "end": at(150).isoformat(timespec="seconds"),
                                  "seconds": 120, "across_restart": False}]

    def test_failing_probes_while_baytec_answers_are_blocked_not_down(self):
        """A network that blocks the three resolvers on 443 is not offline."""
        state = w.InternetState()
        assert run(state, [False, False, False], link=True) == [(0, "blocked")]
        assert state.outages == [] and state.open is None

    def test_rounds_are_not_saved_only_changes(self):
        state = w.InternetState()
        assert sum(1 for _ in run(state, [True] * 50)) == 1


class TestTheRecord:
    def test_an_open_outage_survives_a_restart_and_says_so(self):
        state = w.InternetState()
        run(state, [True, False, False])
        stored = state.stored()
        assert stored["open"] == {"start": at(30).isoformat(timespec="seconds")}

        after = w.InternetState(stored)
        assert after.state == "down"
        after.observe(True, False, at(900))
        [outage] = after.outages
        assert outage["across_restart"] is True and outage["seconds"] == 870

    def test_kept_twenty_and_thirty_days(self):
        state = w.InternetState({"outages": [
            {"start": (T0 - timedelta(days=40)).isoformat(),
             "end": (T0 - timedelta(days=40)).isoformat(), "seconds": 60}] + [
            {"start": (T0 - timedelta(hours=h)).isoformat(),
             "end": (T0 - timedelta(hours=h)).isoformat(), "seconds": 60}
            for h in range(30, 0, -1)]})
        state.observe(True, False, T0)
        assert len(state.outages) == w.MAX_OUTAGES
        assert all("days=40" not in o["start"] for o in state.outages)
        assert min(o["start"] for o in state.outages) > (T0 - timedelta(days=w.KEEP_DAYS)).isoformat()

    def test_a_damaged_store_is_an_empty_record(self):
        for stored in (None, "x", {"outages": "x", "open": 5}, {"outages": [{"start": "?"}]}):
            state = w.InternetState(stored)
            assert state.outages == [] and state.open is None and state.state == "unknown"

    def test_the_snapshot(self):
        state = w.InternetState()
        run(state, [True, False, False, True])
        snap = state.snapshot()
        assert snap["state"] == "up" and len(snap["outages"]) == 1
        assert snap["open_outage"] is None and snap["last_check"]


class TestTheProbe:
    def test_a_listening_port_answers_and_nothing_is_sent(self):
        received = []

        async def main():
            async def handle(reader, writer):
                received.append(await reader.read(100))
                writer.close()

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ok = await w.probe((("127.0.0.1", port),), timeout=2)
            await asyncio.sleep(0.1)
            server.close()
            await server.wait_closed()
            return ok

        assert asyncio.run(main()) is True
        assert received == [b""]

    def test_nothing_listening_is_unreached(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        assert asyncio.run(w.probe((("127.0.0.1", port),), timeout=2)) is False

    def test_the_targets_are_addresses_not_names(self):
        """A broken DNS server must not read as a broken line."""
        for host, port in w.PROBE_TARGETS:
            socket.inet_aton(host)
            assert port == 443
