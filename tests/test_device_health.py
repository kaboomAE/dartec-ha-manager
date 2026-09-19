"""Batteries and offline devices, as the agent summarises them.

The two ways this goes wrong are a false alarm — a button that was never
pressed, a Backup "device" before the first backup, a phone on charge, a
voltage read as a percentage — and a missed one. These tests are mostly about
the first, because a monitoring system that cries wolf gets muted.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from device_health import (EXPECTED_OFFLINE_LABEL, battery_reading,  # noqa: E402
                           device_is_judged, device_offline, device_presence,
                           summarize_batteries)


class TestReadingABattery:
    def test_a_percentage_sensor(self):
        assert battery_reading("sensor", "battery", "12", "%") == ("level", 12.0)

    def test_a_binary_low_sensor(self):
        assert battery_reading("binary_sensor", "battery", "on", None) == ("low", True)
        assert battery_reading("binary_sensor", "battery", "off", None) == ("low", False)

    def test_charging_is_not_low(self):
        """The demo integration has exactly this entity, reading `off`."""
        assert battery_reading("binary_sensor", "battery_charging", "off", None) is None
        assert battery_reading("binary_sensor", "battery_charging", "on", None) is None

    @pytest.mark.parametrize("state", ["unavailable", "unknown", None, "n/a"])
    def test_no_reading_is_not_a_flat_battery(self, state):
        assert battery_reading("sensor", "battery", state, "%") is None

    def test_a_voltage_is_not_a_percentage(self):
        assert battery_reading("sensor", "battery", "3.0", "V") is None

    def test_out_of_range_is_ignored(self):
        assert battery_reading("sensor", "battery", "255", "%") is None
        assert battery_reading("sensor", "battery", "-1", "%") is None

    def test_other_domains_are_ignored(self):
        assert battery_reading("number", "battery", "5", "%") is None


def reading(device_id, kind, value, name=None):
    return {"device_id": device_id, "device": name or device_id, "area": None,
            "entity_id": f"sensor.{device_id}_{kind}", "reading": (kind, value)}


class TestOneRowPerDevice:
    def test_lowest_first(self):
        rows = summarize_batteries([reading("a", "level", 80), reading("b", "level", 7),
                                    reading("c", "level", 35)])
        assert [r["device_id"] for r in rows] == ["b", "c", "a"]

    def test_a_percentage_and_a_flag_on_one_device_merge(self):
        rows = summarize_batteries([reading("lock", "level", 40),
                                    reading("lock", "low", True)])
        assert len(rows) == 1
        assert rows[0]["level"] == 40 and rows[0]["low"] is True

    def test_two_percentages_keep_the_lower(self):
        rows = summarize_batteries([reading("lock", "level", 60),
                                    reading("lock", "level", 15)])
        assert rows[0]["level"] == 15

    def test_a_flag_only_device_that_says_low_sorts_first(self):
        rows = summarize_batteries([reading("a", "level", 3), reading("b", "low", True),
                                    reading("c", "low", False)])
        assert [r["device_id"] for r in rows] == ["b", "a", "c"]


def entity(domain="sensor", state="unavailable", changed="2026-09-18T01:00:00+00:00",
           **extra):
    return {"domain": domain, "state": state, "last_changed": changed,
            "disabled": False, "labels": (), **extra}


class TestCallingADeviceOffline:
    def test_every_entity_down(self):
        assert device_offline([entity(), entity(state="unknown")]) is not None

    def test_one_entity_still_answering_means_online(self):
        assert device_offline([entity(), entity(state="21.5")]) is None

    def test_since_is_when_the_last_entity_went_down(self):
        since = device_offline([entity(changed="2026-09-18T01:00:00+00:00"),
                                entity(changed="2026-09-18T03:00:00+00:00")])
        assert since == "2026-09-18T03:00:00+00:00"

    def test_a_never_pressed_button_is_not_an_outage(self):
        """Push, from the demo integration: a button and an event, both unknown."""
        assert device_offline([entity("button", "unknown"),
                               entity("event", "unknown")]) is None

    def test_stateless_entities_do_not_keep_a_dead_device_online(self):
        assert device_offline([entity("button", "2026-09-17T10:00:00+00:00"),
                               entity("sensor", "unavailable")]) is not None

    def test_disabled_entities_are_not_judged(self):
        assert device_offline([entity(state=None, disabled=True)]) is None
        assert device_offline([entity(state="on", disabled=True),
                               entity()]) is not None

    def test_a_labelled_entity_does_not_count(self):
        assert device_offline([entity(labels=(EXPECTED_OFFLINE_LABEL,))]) is None
        assert device_offline([entity(labels=(EXPECTED_OFFLINE_LABEL,)),
                               entity(state="on")]) is None


class TestWhichDevicesAreJudged:
    LOADED = {"entry-1"}

    def test_an_ordinary_device(self):
        assert device_is_judged({"config_entries": {"entry-1"}}, self.LOADED)

    def test_a_disabled_device_is_not(self):
        assert not device_is_judged({"disabled": True, "config_entries": {"entry-1"}},
                                    self.LOADED)

    def test_a_service_device_is_not(self):
        """Most of HA's Backup device is `unknown` until the first backup."""
        assert not device_is_judged({"entry_type": "service",
                                     "config_entries": {"entry-1"}}, self.LOADED)

    def test_a_device_on_an_integration_that_is_not_running_is_not(self):
        assert not device_is_judged({"config_entries": {"entry-2"}}, self.LOADED)

    def test_one_running_entry_is_enough(self):
        assert device_is_judged({"config_entries": {"entry-1", "entry-2"}}, self.LOADED)

    def test_the_label_opts_a_device_out(self):
        assert not device_is_judged({"labels": {EXPECTED_OFFLINE_LABEL},
                                     "config_entries": {"entry-1"}}, self.LOADED)


class TestPresence:
    """`available` and `last_seen` on every device row, not only the down ones."""

    @staticmethod
    def entity(state, domain="sensor", updated="2026-09-19T10:00:00+00:00", **extra):
        return {"domain": domain, "state": state, "last_changed": updated,
                "last_updated": updated, "disabled": False, "labels": (), **extra}

    def test_one_answering_entity_makes_the_device_available(self):
        available, _ = device_presence([self.entity("unavailable"), self.entity("21.5")])
        assert available is True

    def test_every_entity_down_makes_it_unavailable(self):
        available, last_seen = device_presence([
            self.entity("unavailable", updated="2026-09-19T08:00:00+00:00"),
            self.entity("unknown", updated="2026-09-19T09:00:00+00:00")])
        assert available is False
        assert last_seen == "2026-09-19T09:00:00+00:00", "the moment the last one went down"

    def test_last_seen_is_the_latest_update_not_the_latest_change(self):
        """A sensor re-reporting the same value moves last_updated only."""
        available, last_seen = device_presence([
            self.entity("on", updated="2026-09-19T08:00:00+00:00"),
            {**self.entity("21.5"), "last_changed": "2026-09-18T00:00:00+00:00",
             "last_updated": "2026-09-19T11:30:00+00:00"}])
        assert (available, last_seen) == (True, "2026-09-19T11:30:00+00:00")

    def test_a_device_of_buttons_cannot_be_judged(self):
        """A never-pressed button reads `unknown`; that says nothing about the device."""
        available, last_seen = device_presence([self.entity("unknown", domain="button")])
        assert available is None
        assert last_seen == "2026-09-19T10:00:00+00:00"

    def test_disabled_entities_and_missing_states_are_ignored(self):
        available, last_seen = device_presence([
            self.entity("unavailable", disabled=True),
            {"domain": "sensor", "state": None, "disabled": False}])
        assert (available, last_seen) == (None, None)

    def test_the_expected_offline_label_does_not_hide_a_fact(self):
        """The label stops alerts; it does not make a dead device answer."""
        available, _ = device_presence([self.entity("unavailable",
                                                    labels=(EXPECTED_OFFLINE_LABEL,))])
        assert available is False

    def test_it_agrees_with_device_offline(self):
        down = [self.entity("unavailable"), self.entity("unknown")]
        up = [self.entity("unavailable"), self.entity("on")]
        assert (device_presence(down)[0] is False) == (device_offline(down) is not None)
        assert (device_presence(up)[0] is True) == (device_offline(up) is None)
