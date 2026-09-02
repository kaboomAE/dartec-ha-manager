"""Normalising two Zigbee stacks into one map.

A home can run ZHA, Zigbee2MQTT, or — like the real paired home — both at
once. They share no field names and no shapes, so the risk is a merged map
that quietly means different things in each half.

The other risk these guard against is silence. A ZHA that is not installed, a
Zigbee2MQTT that times out mid-scan, and a genuinely empty network all produce
"no nodes", and a coverage comparison built on that would conclude a working
house has no radio at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from mesh import (LINK_CRITICAL, LINK_WEAK, classify_link,  # noqa: E402
                  normalise_z2m, normalise_zha, summarise)

ZHA_DEVICES = [
    {"ieee": "00:11", "name": "Coordinator", "device_type": "Coordinator",
     "manufacturer": "Silicon Labs", "model": "EZSP", "available": True,
     "neighbors": [{"ieee": "00:22", "lqi": "180", "relationship": "Child"}]},
    {"ieee": "00:22", "name": "Hallway switch", "device_type": "Router",
     "manufacturer": "_TZ3000_qewo8dlz", "model": "TS0013", "available": True,
     "lqi": 160, "rssi": -62,
     "neighbors": [{"ieee": "00:33", "lqi": "15", "relationship": "Child"}]},
    {"ieee": "00:33", "name": "Bedroom sensor", "device_type": "EndDevice",
     "manufacturer": "_TZ3000_gszjt2xx", "model": "TS0207", "available": True,
     "neighbors": []},
]

Z2M_MAP = {
    "nodes": [
        {"ieeeAddr": "0xAA", "friendlyName": "Coordinator", "type": "Coordinator"},
        {"ieeeAddr": "0xBB", "friendlyName": "Kitchen switch", "type": "Router",
         "manufacturerName": "Tuya", "modelID": "TS0013"},
        {"ieeeAddr": "0xCC", "friendlyName": "Lost plug", "type": "EndDevice",
         "failed": True},
    ],
    "links": [{"sourceIeeeAddr": "0xAA", "targetIeeeAddr": "0xBB",
               "linkquality": 200, "relationship": "Child"}],
}


class TestClassifyingALink:
    @pytest.mark.parametrize("lqi,expected", [
        (200, "ok"), (LINK_WEAK, "weak"), (30, "weak"),
        (LINK_CRITICAL, "critical"), (3, "critical")])
    def test_thresholds(self, lqi, expected):
        assert classify_link(lqi) == expected

    def test_a_missing_lqi_is_unknown_rather_than_critical(self):
        """Not every stack reports link quality on every link. Absent is not
        the same as terrible, and treating it as terrible invents faults."""
        assert classify_link(None) == "unknown"

    def test_the_thresholds_match_the_signal_module(self):
        """Two modules disagreeing about what counts as weak is how a
        dashboard ends up contradicting itself."""
        from signal_health import LQI_CRITICAL, LQI_WEAK
        assert (LINK_WEAK, LINK_CRITICAL) == (LQI_WEAK, LQI_CRITICAL)


class TestZha:
    def test_devices_and_their_neighbours_become_nodes_and_links(self):
        out = normalise_zha(ZHA_DEVICES)
        assert [n["id"] for n in out["nodes"]] == ["00:11", "00:22", "00:33"]
        assert len(out["links"]) == 2

    def test_a_string_lqi_is_read_as_a_number(self):
        """ZHA reports neighbour LQI as a string. Compared as text, "15" would
        sort above "180" and a failing link would look healthy."""
        out = normalise_zha(ZHA_DEVICES)
        bad = next(link for link in out["links"] if link["to"] == "00:33")
        assert bad["lqi"] == 15
        assert bad["status"] == "critical"

    def test_a_device_with_no_address_is_skipped_rather_than_given_a_blank_one(self):
        out = normalise_zha([{"name": "Nameless", "neighbors": []}])
        assert out["nodes"] == []

    def test_every_node_says_which_stack_it_came_from(self):
        assert {n["stack"] for n in normalise_zha(ZHA_DEVICES)["nodes"]} == {"zha"}


class TestZigbee2Mqtt:
    def test_its_own_field_names_are_translated(self):
        out = normalise_z2m(Z2M_MAP)
        node = next(n for n in out["nodes"] if n["id"] == "0xBB")
        assert node["name"] == "Kitchen switch"
        assert node["model"] == "TS0013"
        assert node["stack"] == "z2m"

    def test_linkquality_becomes_a_classified_link(self):
        out = normalise_z2m(Z2M_MAP)
        assert out["links"][0]["lqi"] == 200
        assert out["links"][0]["status"] == "ok"

    def test_a_failed_node_is_marked_unavailable(self):
        out = normalise_z2m(Z2M_MAP)
        assert next(n for n in out["nodes"] if n["id"] == "0xCC")["available"] is False

    def test_an_empty_map_is_empty_rather_than_an_error(self):
        assert normalise_z2m({}) == {"nodes": [], "links": []}


class TestSummary:
    def test_routers_and_end_devices_are_counted_apart(self):
        """Only routers carry traffic for anything else, which is what a
        coverage plan's assumptions rest on."""
        out = summarise(normalise_zha(ZHA_DEVICES))
        assert out["routers"] == 2      # coordinator + router
        assert out["end_devices"] == 1

    def test_a_node_with_no_link_is_an_orphan(self):
        """It can be heard perfectly by the coordinator and still have no
        route through the house — which a per-device signal reading cannot
        show."""
        out = summarise(normalise_z2m(Z2M_MAP))
        assert out["orphans"] == ["Lost plug"]

    def test_weak_and_critical_links_are_counted_separately(self):
        out = summarise(normalise_zha(ZHA_DEVICES))
        assert out["critical_links"] == 1
        assert out["weak_links"] == 0

    def test_a_merged_map_records_both_stacks(self):
        merged = {"nodes": normalise_zha(ZHA_DEVICES)["nodes"]
                           + normalise_z2m(Z2M_MAP)["nodes"],
                  "links": normalise_zha(ZHA_DEVICES)["links"]
                           + normalise_z2m(Z2M_MAP)["links"]}
        assert summarise(merged)["stacks"] == ["z2m", "zha"]

    def test_an_empty_topology_summarises_to_zero_rather_than_failing(self):
        out = summarise({"nodes": [], "links": []})
        assert out["nodes"] == 0 and out["orphans"] == []
