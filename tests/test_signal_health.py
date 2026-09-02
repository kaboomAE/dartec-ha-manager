"""Reading how well each device is heard.

The whole point of this module is that the data already exists and nothing was
recording it — on the one real home, 32 of 36 signal entities are disabled
because ZHA creates its RSSI sensors that way.

These tests are about the two ways that can go wrong once it is being read:
mixing up the scales, and treating a missing reading as a bad one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from signal_health import (LQI_CRITICAL, LQI_WEAK, RSSI_CRITICAL,  # noqa: E402
                           RSSI_WEAK, classify, reading_kind)


class TestTheTwoScalesAreNeverMixed:
    """RSSI is dBm, negative, lower is worse. Zigbee2MQTT link quality is
    0-255, higher is better. -60 is a healthy radio; 60 on the LQI scale is
    fine too, and 60 read as dBm would be nonsense. Thresholding them together
    produces confident wrong answers."""

    def test_a_healthy_rssi_is_not_read_as_a_weak_lqi(self):
        assert classify("rssi", -60) == "ok"

    def test_a_healthy_lqi_is_not_read_as_an_impossible_rssi(self):
        assert classify("lqi", 200) == "ok"

    @pytest.mark.parametrize("value,expected", [
        (-55, "ok"), (RSSI_WEAK, "weak"), (-85, "weak"),
        (RSSI_CRITICAL, "critical"), (-95, "critical")])
    def test_rssi_thresholds(self, value, expected):
        assert classify("rssi", value) == expected

    @pytest.mark.parametrize("value,expected", [
        (120, "ok"), (LQI_WEAK, "weak"), (30, "weak"),
        (LQI_CRITICAL, "critical"), (5, "critical")])
    def test_lqi_thresholds(self, value, expected):
        assert classify("lqi", value) == expected

    def test_the_same_number_means_different_things_on_each_scale(self):
        assert classify("lqi", 30) == "weak"
        assert classify("rssi", 30) == "ok", (
            "a positive dBm is not real, but it must not be read as a weak LQI")


class TestRecognisingASignalEntity:
    def test_an_explicit_dbm_unit_is_trusted_first(self):
        assert reading_kind("sensor.anything", "dBm", None) == "rssi"

    def test_home_assistants_own_device_class_is_trusted_next(self):
        assert reading_kind("sensor.whatever", None, "signal_strength") == "rssi"

    def test_zigbee2mqtt_linkquality_is_recognised_by_name(self):
        """Z2M sets neither a unit nor a device class on linkquality, so the
        entity id is the only thing left to go on."""
        assert reading_kind("sensor.kitchen_switch_linkquality", None, None) == "lqi"

    def test_zha_rssi_entities_are_recognised_by_name(self):
        assert reading_kind("sensor.tz3000_qewo8dlz_ts0013_rssi", None, None) == "rssi"
        assert reading_kind("sensor.tz3000_qewo8dlz_ts0013_rssi_2", None, None) == "rssi"

    def test_an_ordinary_sensor_is_not_a_signal_reading(self):
        assert reading_kind("sensor.kitchen_temperature", "°C", "temperature") is None
        assert reading_kind("sensor.living_room_battery", "%", "battery") is None

    def test_a_name_that_merely_contains_the_word_is_not_enough(self):
        """"Signal" appears in plenty of names that are not radio readings —
        a doorbell chime, a traffic-light helper."""
        assert reading_kind("light.hallway_signal_lamp", None, None) is None
