"""The cameras section: UniFi Protect's health entities, and every other
camera's online state, onto Baytec's common shape.

Unique ids and keys are Home Assistant's own (homeassistant/components/
unifiprotect, 2026.10): `<mac>_<key>`. Nothing here is a picture: see
test_media_guard.py for the rule that keeps it so.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import camera_health as c  # noqa: E402


def fact(entity_id, domain, unique_id, device_id, state, *, platform="unifiprotect",
         translation_key=None, attributes=None, labels=(), disabled=False,
         last_changed="2026-10-18T08:00:00+00:00"):
    return {"entity_id": entity_id, "domain": domain, "platform": platform,
            "unique_id": unique_id, "translation_key": translation_key,
            "device_id": device_id, "disabled": disabled, "labels": list(labels),
            "state": state, "last_changed": last_changed,
            "attributes": attributes or {}}


def device(device_id, name, model="G5 Bullet", labels=()):
    return {"device_id": device_id, "name": name, "manufacturer": "Ubiquiti",
            "model": model, "sw_version": "4.75", "area": "Garden",
            "labels": list(labels), "disabled": False, "entry_type": None}


def camera(dev, mac, state="recording", mode="always", used="1520.5", unit="MB"):
    return [
        fact(f"camera.{dev}_high", "camera", f"{mac}_0", dev, state),
        # The lower-quality channels are off by default.
        fact(f"camera.{dev}_low", "camera", f"{mac}_2", dev, None, disabled=True),
        fact(f"sensor.{dev}_recording_mode", "sensor", f"{mac}_recording_mode", dev, mode,
             translation_key="recording_mode"),
        fact(f"select.{dev}_recording_mode", "select", f"{mac}_recording_mode", dev, mode,
             translation_key="recording_mode"),
        fact(f"sensor.{dev}_storage_used", "sensor", f"{mac}_storage_used", dev, used,
             translation_key="storage_used", attributes={"unit_of_measurement": unit}),
        fact(f"binary_sensor.{dev}_is_dark", "binary_sensor", f"{mac}_dark", dev, "off",
             translation_key="is_dark"),
    ]


def recorder(dev="nvr", mac="nvr00", used="97.4", free="2.6", capacity="2592000",
             disks=("off", "off"), state_override=None):
    out = [
        fact("sensor.nvr_storage_utilization", "sensor", f"{mac}_storage_utilization", dev,
             used, translation_key="storage_utilization"),
        fact("sensor.nvr_free", "sensor", f"{mac}_resolution_free", dev, free,
             translation_key="resolution_free_space"),
        fact("sensor.nvr_capacity", "sensor", f"{mac}_record_capacity", dev, capacity,
             translation_key="recording_capacity"),
        fact("sensor.nvr_uptime", "sensor", f"{mac}_uptime", dev, "2026-10-01T00:00:00+00:00",
             translation_key="uptime"),
    ]
    for i, state in enumerate(disks):
        out.append(fact(f"binary_sensor.nvr_disk_{i}", "binary_sensor",
                        f"{mac}_disk_health_{i}", dev, state,
                        attributes={"friendly_name": f"NVR HDD {i + 1}"}))
    if state_override:
        for f in out:
            f["state"] = state_override
    return out


def home(*cams, nvr=None):
    entities, devices = [], {"nvr": device("nvr", "Recorder", "UNVR")}
    for i, (state, mode) in enumerate(cams):
        dev = f"cam{i}"
        devices[dev] = device(dev, f"Camera {i}")
        entities += camera(dev, f"mac{i}", state, mode)
    entities += nvr if nvr is not None else recorder()
    return entities, devices


class TestProtectCameras:
    @pytest.mark.parametrize("state, mode, online, recording", [
        ("recording", "always", True, "recording"),
        ("idle", "always", True, "stopped"),
        ("idle", "detections", True, "standby"),
        ("recording", "detections", True, "recording"),
        ("idle", "schedule", True, "standby"),
        ("idle", "never", True, "off"),
        ("unavailable", "always", False, "unknown"),
        ("unavailable", "never", False, "off"),
    ])
    def test_online_and_recording(self, state, mode, online, recording):
        entities, devices = home((state, mode))
        cam = c.summarize(entities, devices)["cameras"][0]
        assert (cam["online"], cam["recording"]) == (online, recording)
        assert cam["adapter"] == "unifiprotect"

    def test_storage_used_in_bytes_whatever_the_unit(self):
        entities, devices = home(("recording", "always"))
        assert c.summarize(entities, devices)["cameras"][0]["storage_used_bytes"] == 1_520_500_000
        assert c.to_bytes("1.5", "GiB") == int(1.5 * 2 ** 30)
        assert c.to_bytes("12", "B") == 12
        assert c.to_bytes("unavailable", "MB") is None
        assert c.to_bytes("3", "furlongs") is None

    def test_the_select_answers_when_the_mode_sensor_is_missing(self):
        entities, devices = home(("idle", "never"))
        entities = [f for f in entities if not (f["domain"] == "sensor"
                                                and f["translation_key"] == "recording_mode")]
        assert c.summarize(entities, devices)["cameras"][0]["recording"] == "off"

    def test_offline_cameras_first(self):
        entities, devices = home(("recording", "always"), ("unavailable", "always"))
        assert [cam["name"] for cam in c.summarize(entities, devices)["cameras"]] == [
            "Camera 1", "Camera 0"]

    def test_the_expected_offline_label(self):
        entities, devices = home(("unavailable", "always"))
        devices["cam0"]["labels"] = ["baytec_expected_offline"]
        assert c.summarize(entities, devices)["cameras"][0]["expected_offline"] is True


class TestProtectRecorder:
    def test_disk_and_capacity(self):
        entities, devices = home(("recording", "always"))
        nvr = c.summarize(entities, devices)["recorders"][0]
        assert nvr["name"] == "Recorder" and nvr["online"] is True
        assert nvr["storage_used_pct"] == 97.4 and nvr["storage_free_pct"] == 2.6
        assert nvr["recording_capacity_s"] == 2_592_000
        assert nvr["rotates"] is True
        assert nvr["disks"] == [{"name": "NVR HDD 1", "healthy": True},
                                {"name": "NVR HDD 2", "healthy": True}]

    def test_a_failing_disk(self):
        entities, devices = home(("recording", "always"), nvr=recorder(disks=("off", "on")))
        disks = c.summarize(entities, devices)["recorders"][0]["disks"]
        assert [d["healthy"] for d in disks] == [True, False]

    def test_a_recorder_home_assistant_cannot_reach(self):
        entities, devices = home(("recording", "always"),
                                 nvr=recorder(state_override="unavailable"))
        nvr = c.summarize(entities, devices)["recorders"][0]
        assert nvr["online"] is False and nvr["storage_used_pct"] is None


class TestOtherCameras:
    def test_a_camera_of_another_brand(self):
        entities = [fact("camera.porch", "camera", "porch-1", "porch", "idle",
                         platform="reolink"),
                    fact("camera.gate", "camera", "gate-1", "gate", "unavailable",
                         platform="reolink")]
        devices = {"porch": device("porch", "Porch"), "gate": device("gate", "Gate")}
        section = c.summarize(entities, devices)
        assert section["adapters"] == ["generic"]
        rows = {cam["name"]: cam for cam in section["cameras"]}
        assert rows["Porch"]["online"] is True and rows["Porch"]["recording"] == "unknown"
        assert rows["Gate"]["online"] is False
        assert section["recorders"] == []

    def test_a_file_or_a_proxy_is_not_a_camera_in_the_house(self):
        entities = [fact("camera.floorplan", "camera", "f", None, "unavailable",
                         platform="local_file"),
                    fact("camera.cropped", "camera", "p", None, "idle", platform="proxy")]
        assert c.summarize(entities, {}) is None

    def test_no_cameras_is_no_section(self):
        assert c.summarize([], {}) is None
