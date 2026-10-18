"""The network section: UniFi's entities onto Baytec's common shape.

Facts are shaped exactly as ha_facts.entity_facts builds them, with unique
ids and translation keys as Home Assistant's UniFi Network integration
creates them (homeassistant/components/unifi, 2026.10). Entity ids are made
up on purpose and renamed in one test: nothing may depend on them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import network_health as n  # noqa: E402

MAC_GW, MAC_AP1, MAC_AP2, MAC_SW = ("aa:00:00:00:00:01", "aa:00:00:00:00:02",
                                    "aa:00:00:00:00:03", "aa:00:00:00:00:04")


def fact(entity_id, domain, unique_id, device_id, state, *, translation_key=None,
         platform="unifi", attributes=None, labels=(), disabled=False,
         last_changed="2026-10-18T08:00:00+00:00"):
    return {"entity_id": entity_id, "domain": domain, "platform": platform,
            "unique_id": unique_id, "translation_key": translation_key,
            "device_id": device_id, "disabled": disabled, "labels": list(labels),
            "state": state, "last_changed": last_changed,
            "attributes": attributes or {}}


def device(device_id, name, model, *, labels=(), entry_type=None, disabled=False,
           sw="7.1.66"):
    return {"device_id": device_id, "name": name, "manufacturer": "Ubiquiti Networks",
            "model": model, "sw_version": sw, "area": None, "labels": list(labels),
            "disabled": disabled, "entry_type": entry_type}


def unifi_device(dev_id, mac, state="connected", *, tracker="home", update="off",
                 clients=None, latest="7.1.68"):
    out = [fact(f"sensor.{dev_id}_state", "sensor", f"device_state-{mac}", dev_id, state,
                translation_key="device_state"),
           fact(f"device_tracker.{dev_id}", "device_tracker", mac, dev_id, tracker),
           fact(f"update.{dev_id}", "update", f"device_update-{mac}", dev_id, update,
                attributes={"installed_version": "7.1.66", "latest_version": latest})]
    if clients is not None:
        out.append(fact(f"sensor.{dev_id}_clients", "sensor", f"device_clients-{mac}",
                        dev_id, str(clients), translation_key="device_clients"))
    return out


def home(**overrides):
    devices = {
        "gw": device("gw", "Gateway", "UCGMAX"),
        "ap1": device("ap1", "Living room AP", "UAL6"),
        "ap2": device("ap2", "Majlis AP", "U7PG2"),
        "sw": device("sw", "Cabinet switch", "USL16LP"),
        "wlan": device("wlan", "Home Wi-Fi", "UniFi WLAN", entry_type="service"),
    }
    entities = (unifi_device("gw", MAC_GW) + unifi_device("ap1", MAC_AP1, clients=9)
                + unifi_device("ap2", MAC_AP2, **overrides.get("ap2", {}))
                + unifi_device("sw", MAC_SW)
                + [fact("sensor.home_wifi", "sensor", "wlan_clients-6512", "wlan", "14",
                        translation_key="wlan_clients")])
    return entities, devices


def rows_by_name(section):
    return {r["name"]: r for r in section["devices"]}


class TestDevices:
    def test_every_unifi_device_with_its_role(self):
        section = n.summarize(*home(), [])
        rows = rows_by_name(section)
        assert section["adapters"] == ["unifi"]
        assert rows["Gateway"]["role"] == "gateway"
        assert rows["Living room AP"]["role"] == "access_point"
        assert rows["Majlis AP"]["role"] == "access_point"
        assert rows["Cabinet switch"]["role"] == "switch"
        # Gateways first, then access points, then switches.
        assert [r["role"] for r in section["devices"]] == [
            "gateway", "access_point", "access_point", "switch"]
        assert section["device_count"] == 4

    def test_a_wlan_is_not_a_device_and_its_clients_are_counted(self):
        section = n.summarize(*home(), [])
        assert "Home Wi-Fi" not in rows_by_name(section)
        assert section["wlans"] == [{"name": "Home Wi-Fi", "clients": 14}]
        assert section["clients"]["wireless"] == 14

    @pytest.mark.parametrize("state, expected", [
        ("connected", "online"), ("firmware_mismatch", "online"),
        ("disconnected", "offline"), ("heartbeat_missed", "offline"),
        ("isolated", "offline"), ("upgrading", "busy"), ("provisioning", "busy"),
        ("adopting", "busy")])
    def test_the_integrations_state_word(self, state, expected):
        section = n.summarize(*home(ap2={"state": state}), [])
        row = rows_by_name(section)["Majlis AP"]
        assert row["state"] == expected and row["detail"] == state

    def test_the_tracker_answers_when_the_state_sensor_is_unavailable(self):
        section = n.summarize(*home(ap2={"state": "unavailable", "tracker": "not_home"}), [])
        assert rows_by_name(section)["Majlis AP"]["state"] == "offline"

    def test_both_unavailable_is_unknown_not_offline(self):
        """Home Assistant cannot reach the controller: the integration's own
        failure, already an alert. Every access point offline would bury it."""
        section = n.summarize(*home(ap2={"state": "unavailable", "tracker": "unavailable"}), [])
        assert rows_by_name(section)["Majlis AP"]["state"] == "unknown"

    def test_firmware(self):
        section = n.summarize(*home(ap2={"update": "on"}), [])
        rows = rows_by_name(section)
        assert rows["Majlis AP"]["firmware"] == {"installed": "7.1.66", "latest": "7.1.68",
                                                 "update_available": True}
        assert rows["Gateway"]["firmware"]["update_available"] is False

    def test_clients_per_device_only_where_the_sensor_is_on(self):
        """`Device clients` is disabled by default in the integration."""
        rows = rows_by_name(n.summarize(*home(), []))
        assert rows["Living room AP"]["clients"] == 9
        assert rows["Living room AP"]["clients_sensor"] is True
        assert rows["Majlis AP"]["clients"] is None
        assert rows["Majlis AP"]["clients_sensor"] is False

    def test_an_unknown_model_is_a_network_device(self):
        assert n.unifi_role("UCK-G2-PLUS", False) == "device"
        assert n.unifi_role("USPPDUP", False) == "device"
        assert n.unifi_role(None, False) == "device"
        assert n.unifi_role("UCK-G2-PLUS", True) == "gateway"

    def test_the_expected_offline_label(self):
        entities, devices = home(ap2={"state": "disconnected"})
        devices["ap2"]["labels"] = ["baytec_expected_offline"]
        assert rows_by_name(n.summarize(entities, devices, []))["Majlis AP"]["expected_offline"]

    def test_renamed_entity_ids_change_nothing(self):
        entities, devices = home(ap2={"state": "disconnected"})
        before = n.summarize(entities, devices, [])
        for i, f in enumerate(entities):
            f["entity_id"] = f"{f['domain']}.renamed_by_the_family_{i}"
        assert n.summarize(entities, devices, []) == before

    def test_other_integrations_are_not_network_equipment(self):
        entities, devices = home()
        entities.append(fact("sensor.hue_bridge_state", "sensor", "device_state-x", "hue",
                             "disconnected", platform="hue"))
        assert "hue" not in {r["device_id"] for r in n.summarize(entities, devices, [])["devices"]}

    def test_nothing_to_report_is_no_section(self):
        assert n.summarize([], {}, []) is None


class TestInternet:
    def wan(self, target, wan, state, mac=MAC_GW):
        return fact(f"sensor.gw_{target}_{wan}", "sensor", f"{target}_{wan}_latency-{mac}",
                    "gw", state, translation_key="wan_latency")

    def test_unknown_without_a_source(self):
        """The integration has no WAN up/down entity; its latency sensors are
        off by default. Unknown, never a guessed "up"."""
        internet = n.summarize(*home(), [])["internet"]
        assert internet["state"] == "unknown" and internet["source"] == "none"

    def test_wan_latency_up(self):
        entities, devices = home()
        entities += [self.wan("google", "wan", "12"), self.wan("cloudflare", "wan", "9")]
        internet = n.summarize(entities, devices, [])["internet"]
        assert internet["state"] == "up"
        assert internet["wans"][0] | {"since": None} == {
            "name": "WAN", "state": "up", "latency_ms": 9.0, "since": None,
            "source": "unifi_wan_latency"}

    def test_failover_to_a_second_wan(self):
        entities, devices = home()
        entities += [self.wan("google", "wan", "unavailable"),
                     self.wan("google", "wan2", "40")]
        internet = n.summarize(entities, devices, [])["internet"]
        assert internet["state"] == "up"
        assert {w["name"]: w["state"] for w in internet["wans"]} == {"WAN": "down", "WAN2": "up"}

    def test_every_wan_down(self):
        entities, devices = home()
        entities += [self.wan("google", "wan", "unknown")]
        assert n.summarize(entities, devices, [])["internet"]["state"] == "down"

    def test_the_label_beats_the_latency_sensors(self):
        entities, devices = home()
        entities += [self.wan("google", "wan", "12")]
        check = fact("binary_sensor.ping_1_1_1_1", "binary_sensor", "ping-1", None, "off",
                     platform="ping", labels=["baytec_internet"],
                     last_changed="2026-10-18T09:00:00+00:00")
        internet = n.summarize(entities, devices, [check])["internet"]
        assert internet == {"state": "down", "source": "label",
                            "since": "2026-10-18T09:00:00+00:00", "checks": 1,
                            "wans": internet["wans"]}

    def test_a_label_alone_makes_a_section(self):
        """A home with a labelled check but another brand's network."""
        check = fact("binary_sensor.ping", "binary_sensor", "p", None, "on",
                     platform="ping", labels=["baytec_internet"])
        section = n.summarize([], {}, [check])
        assert section["internet"]["state"] == "up" and section["devices"] == []


class TestARealHome:
    """The owner's home, read through the manager on 2026-10-18: UniFi gear
    through UniFi Network Rules, which reports no health."""

    @pytest.mark.parametrize("model, role", [
        ("UniFi Dream Machine", "gateway"), ("U7LR", "access_point"),
        ("U6IW", "access_point"), ("UALR6v2", "access_point"),
        # Names other integrations give, and codes with words after them.
        ("UniFi Dream Router", "gateway"), ("UniFi Express", "gateway"),
        ("UDM-Pro", "gateway"), ("USW Flex Mini", "switch"), ("U6 Lite", "access_point"),
        ("UniFi Network Video Recorder", "device")])
    def test_its_models(self, model, role):
        assert n.unifi_role(model, False) == role

    def test_gear_with_no_adapter_is_said_so(self):
        unadapted = [{"integration": "unifi_network_rules", "devices": 4,
                      "models": ["UniFi Dream Machine", "U7LR", "U6IW", "UALR6v2"]}]
        section = n.summarize([], {}, [], None, unadapted)
        assert section["unadapted"] == unadapted and section["devices"] == []
        assert section["adapters"] == []

    def test_nothing_unadapted_is_an_empty_list(self):
        assert n.summarize(*home(), [])["unadapted"] == []


class TestDevicesFrom:
    """ha_facts.devices_from, against a fake device registry."""

    def run(self, devices, entries):
        import types

        AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
        for name in ("homeassistant", "homeassistant.helpers"):
            sys.modules.setdefault(name, types.ModuleType(name))
        # 2026.9 and later: iterating the registry yields the entries.
        registry = types.SimpleNamespace(devices=list(devices))
        dr = types.ModuleType("homeassistant.helpers.device_registry")
        dr.async_get = lambda hass: registry
        sys.modules["homeassistant.helpers.device_registry"] = dr
        sys.modules["homeassistant.helpers"].device_registry = dr
        if "dartec_ha_manager" not in sys.modules:
            pkg = types.ModuleType("dartec_ha_manager")
            pkg.__path__ = [str(AGENT)]
            sys.modules["dartec_ha_manager"] = pkg
        from dartec_ha_manager import ha_facts

        hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(
            async_entries=lambda domain=None: [types.SimpleNamespace(entry_id=k, domain=v)
                                               for k, v in entries.items()]))
        return ha_facts.devices_from(hass, "ubiquiti", ("unifi",))

    def device(self, i, manufacturer, model, entry, disabled=None):
        import types

        return types.SimpleNamespace(id=f"d{i}", manufacturer=manufacturer, model=model,
                                     config_entry_id=entry, primary_config_entry=entry,
                                     config_entries={entry}, disabled_by=disabled)

    def test_the_owners_home(self):
        devices = [self.device(1, "Ubiquiti, Inc.", "UniFi Dream Machine", "e1"),
                   self.device(2, "Ubiquiti, Inc.", "U7LR", "e1"),
                   self.device(3, "Ubiquiti Networks", "UAL6", "e2"),
                   self.device(4, "Signify", "Hue bridge", "e3"),
                   self.device(5, "Ubiquiti, Inc.", "U6IW", "e1", disabled="user")]
        found = self.run(devices, {"e1": "unifi_network_rules", "e2": "unifi", "e3": "hue"})
        assert found == [{"integration": "unifi_network_rules", "devices": 2,
                          "models": ["UniFi Dream Machine", "U7LR"]}]
