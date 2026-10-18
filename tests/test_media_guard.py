"""No picture, snapshot, stream or video of a home ever reaches Baytec.

The owner's rule of 2026-10-18 (kaboomAE/dartec-ops#45), held three ways
(media_guard.py) and tested each way here:

1. What is read: the network and camera collectors run against a fake Home
   Assistant whose camera and image entities carry everything a real one
   does — `entity_picture` with a live token, `access_token`, a stream type,
   an RTSP address — and none of it comes out.
2. What can be asked for: the services that make a picture or a clip are
   NEVER, and no source in the agent calls a camera image, stream or WebRTC
   API, or Home Assistant's camera proxy.
3. What is sent: `strip` removes every picture and stream key and URL from a
   payload, and cloud_link applies it to both things it sends.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
sys.path.insert(0, str(AGENT))

import media_guard as g  # noqa: E402
from service_policy import check_call_service, classify  # noqa: E402

TOKEN = "f1d2d2f924e986ac86fdf7b36c94bcdf32beec15"
PROXY = f"/api/camera_proxy/camera.front_door?token={TOKEN}"
# Anything that would let someone see what a camera sees.
LEAK = re.compile(rf"{TOKEN}|camera_proxy|image_proxy|rtsps?://|entity_picture|"
                  r"access_token|frontend_stream_type|/api/unifiprotect/", re.IGNORECASE)


# ── 3. What is sent ─────────────────────────────────────────────────────────

class TestStrip:
    def test_picture_keys_go_wherever_they_are(self):
        payload = {"entities": [{"entity_id": "camera.front_door", "state": "recording",
                                 "attributes": {"entity_picture": PROXY,
                                                "access_token": TOKEN,
                                                "frontend_stream_type": "hls",
                                                "brand": "Ubiquiti"}}]}
        cleaned, removed = g.strip(payload)
        assert cleaned == {"entities": [{"entity_id": "camera.front_door",
                                         "state": "recording",
                                         "attributes": {"brand": "Ubiquiti"}}]}
        assert removed == ["entities[0].attributes.entity_picture",
                           "entities[0].attributes.access_token",
                           "entities[0].attributes.frontend_stream_type"]

    @pytest.mark.parametrize("url", [
        PROXY, "/api/camera_proxy_stream/camera.gate", "/api/image_proxy/image.door",
        "rtsps://192.168.1.1:7441/abcdEFGH?enableSrtp", "rtsp://user:pw@10.0.0.5/live",
        "/api/hls/abc/master_playlist.m3u8", "/api/unifiprotect/thumbnail/nvr/event1",
        "/api/unifiprotect/video/nvr/cam/1/2", "media-source://unifiprotect/nvr:event:1",
        "media-source://camera/camera.front_door", "/api/reolink/video/x",
        "https://home.example/api/camera_proxy/camera.x?token=1"])
    def test_picture_and_stream_urls_go_wherever_they_are(self, url):
        cleaned, removed = g.strip({"logs": [{"message": url}], "list": ["ok", url]})
        assert cleaned == {"logs": [{"message": g.REMOVED}], "list": ["ok", g.REMOVED]}
        assert removed == ["logs[0].message", "list[1]"]

    def test_everything_else_is_untouched(self):
        payload = {"core": {"version": "2026.10.2"}, "host": {"cpu_percent": 4.5},
                   "dashboards": [{"url_path": "dartec-room-x", "title": "Majlis"}],
                   "media": [{"media_content_id":
                              "media-source://media_source/local/dartec/athan.mp3"}],
                   "cameras": {"cameras": [{"name": "Gate", "online": True,
                                            "recording": "recording"}]},
                   "flag": True, "none": None, "n": 3}
        cleaned, removed = g.strip(payload)
        assert cleaned == payload and removed == []

    def test_a_dashboards_own_camera_card_survives_a_round_trip(self):
        """`camera_image` and `image` in Lovelace name an entity or a file for
        the household's screen. Stripping them would lose them when an
        installer saves the dashboard back through the manager."""
        card = {"type": "picture-glance", "camera_image": "camera.front_door",
                "image": "/local/floorplan.png", "entities": ["light.porch"]}
        assert g.strip({"config": {"views": [{"cards": [card]}]}})[0] == {
            "config": {"views": [{"cards": [card]}]}}

    def test_a_url_used_as_a_key_goes(self):
        cleaned, removed = g.strip({"seen": {PROXY: 1, "ok": 2}})
        assert cleaned == {"seen": {"ok": 2}} and removed == ["seen"]

    def test_cloud_link_strips_both_things_it_sends(self):
        source = (AGENT / "cloud_link.py").read_text(encoding="utf-8")
        assert source.count("_without_camera_media(") == 3   # the def and two calls
        assert "scrub_snapshot(snapshot), \"snapshot\")" in source
        assert "_without_camera_media(both.scrub(result)" in source


# ── 2. What can be asked for ────────────────────────────────────────────────

class TestNothingCanAskForAPicture:
    @pytest.mark.parametrize("pair", ["camera.snapshot", "camera.record",
                                      "camera.play_stream", "image.snapshot"])
    def test_never(self, pair):
        domain, service = pair.split(".")
        assert classify(domain, service) == "never"
        refusal = check_call_service({"domain": domain, "service": service,
                                      "service_data": {"entity_id": "camera.front_door",
                                                       "filename": "/media/x.jpg"}},
                                     maintenance_open=True)
        assert refusal and "permanently blocked" in refusal

    @pytest.mark.parametrize("service", ["turn_on", "turn_off", "enable_motion_detection",
                                         "disable_motion_detection"])
    def test_no_other_camera_service_is_allowed_either(self, service):
        assert classify("camera", service) in ("never", "unlisted")

    def _code(self, path: Path):
        """Every name, attribute and string in a module, docstrings left out."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                yield node.attr
            elif isinstance(node, ast.Name):
                yield node.id
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and id(node) not in docstrings):
                yield node.value

    # Home Assistant's ways of getting at what a camera sees.
    FORBIDDEN_CALLS = {"async_camera_image", "async_get_image", "async_get_still_stream",
                       "async_get_mjpeg_stream", "async_handle_web_rtc_offer",
                       "async_handle_async_webrtc_offer", "async_get_stream_source",
                       "stream_source", "async_resolve_media", "async_browse_media",
                       "async_create_stream", "async_image"}
    FORBIDDEN_STRINGS = re.compile(
        r"camera/stream|camera/web_rtc|camera/webrtc|camera/capabilities|"
        r"camera_thumbnail|camera_proxy|image_proxy|media_source/resolve_media|"
        r"unifiprotect/|reolink/|entity_picture|stream_source", re.IGNORECASE)

    @pytest.mark.parametrize("module", sorted(p.name for p in AGENT.glob("*.py")))
    def test_no_module_reaches_for_a_picture(self, module):
        if module == "media_guard.py":
            return    # it names them, to remove them
        names = list(self._code(AGENT / module))
        assert not (set(names) & self.FORBIDDEN_CALLS), module
        bad = [n for n in names if self.FORBIDDEN_STRINGS.search(n)]
        assert not bad, f"{module}: {bad}"

    def test_media_is_only_ever_browsed_in_our_own_folder(self):
        source = (AGENT / "media_cmds.py").read_text(encoding="utf-8")
        assert re.search(r'^FOLDER = "dartec"', source, re.MULTILINE)
        calls = source.count('"media_source/browse_media"')
        ours = re.findall(r'"media_source/browse_media",\s*"media_content_id":\s*'
                          r'f"\{LOCAL_ROOT\}/\{FOLDER\}"', source)
        assert calls and len(ours) == calls
        assert "resolve_media" not in source


# ── 1. What is read ─────────────────────────────────────────────────────────

def _stub(name, **attrs):
    module = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


if "dartec_ha_manager" not in sys.modules:
    _pkg = types.ModuleType("dartec_ha_manager")
    _pkg.__path__ = [str(AGENT)]
    sys.modules["dartec_ha_manager"] = _pkg


def reg(entity_id, platform, unique_id, device_id, translation_key=None):
    return types.SimpleNamespace(entity_id=entity_id, domain=entity_id.split(".")[0],
                                 platform=platform, unique_id=unique_id,
                                 translation_key=translation_key, device_id=device_id,
                                 disabled_by=None, labels=set())


def state(value, **attributes):
    return types.SimpleNamespace(state=value, attributes=attributes,
                                 last_changed="2026-10-18T08:00:00+00:00")


class FakeHass:
    """A home with a Protect camera, a Reolink doorbell, an image entity and a
    UniFi access point, every one carrying what a real one carries."""

    def __init__(self):
        self.registry = [
            reg("camera.front_door", "unifiprotect", "aa11_0", "cam"),
            reg("sensor.front_door_recording_mode", "unifiprotect", "aa11_recording_mode",
                "cam", "recording_mode"),
            reg("image.front_door_event", "unifiprotect", "aa11_event_image", "cam"),
            reg("camera.doorbell", "reolink", "rl_doorbell_main", "bell"),
            reg("sensor.nvr_storage_utilization", "unifiprotect", "nvr_storage_utilization",
                "nvr", "storage_utilization"),
            reg("sensor.ap_state", "unifi", "device_state-aa:bb", "ap", "device_state"),
            reg("image.home_wifi_qr_code", "unifi", "qr_code-wlan1", "wlan", "wlan_qr_code"),
            reg("sensor.home_wifi", "unifi", "wlan_clients-wlan1", "wlan", "wlan_clients"),
        ]
        rtsp = "rtsps://192.168.1.1:7441/AbCdEfGh?enableSrtp"
        self._states = {
            "camera.front_door": state("recording", entity_picture=PROXY, access_token=TOKEN,
                                       frontend_stream_type="web_rtc", friendly_name="Front door",
                                       stream_source=rtsp),
            "sensor.front_door_recording_mode": state("always"),
            "image.front_door_event": state("2026-10-18T07:59:00+00:00",
                                            entity_picture=f"/api/image_proxy/image.x?token={TOKEN}",
                                            access_token=TOKEN),
            "camera.doorbell": state("idle", entity_picture=PROXY.replace("front", "bell"),
                                     access_token=TOKEN),
            "sensor.nvr_storage_utilization": state("96.1"),
            "sensor.ap_state": state("connected"),
            # The UniFi Wi-Fi QR code is an image entity whose picture is the
            # network's password.
            "image.home_wifi_qr_code": state("2026-10-18T07:00:00+00:00",
                                             entity_picture=f"/api/image_proxy/image.qr?token={TOKEN}"),
            "sensor.home_wifi": state("12"),
        }
        self.states = types.SimpleNamespace(get=self._states.get)
        names = {"cam": "Front door", "bell": "Doorbell", "nvr": "UNVR", "ap": "AP",
                 "wlan": "Home Wi-Fi"}
        self.devices = {d: types.SimpleNamespace(
            id=d, name=n, name_by_user=None, manufacturer="x", model="UAL6" if d == "ap" else "m",
            sw_version="1", area_id=None, labels=set(), disabled_by=None,
            entry_type="service" if d == "wlan" else None) for d, n in names.items()}

    def install(self):
        entities = types.SimpleNamespace(entities={r.entity_id: r for r in self.registry})
        devices = types.SimpleNamespace(async_get=self.devices.get)
        areas = types.SimpleNamespace(async_list_areas=lambda: [])
        er = _stub("homeassistant.helpers.entity_registry", async_get=lambda h: entities)
        dr = _stub("homeassistant.helpers.device_registry", async_get=lambda h: devices)
        ar = _stub("homeassistant.helpers.area_registry", async_get=lambda h: areas)
        _stub("homeassistant")
        _stub("homeassistant.helpers", entity_registry=er, device_registry=dr,
              area_registry=ar)


class TestNothingIsRead:
    def test_the_collectors_send_health_and_nothing_else(self):
        from dartec_ha_manager import camera_health, network_health

        hass = FakeHass()
        hass.install()
        out = {**network_health.collect(hass), **camera_health.collect(hass)}
        assert "error" not in out["cameras"] and "error" not in out["network"]
        text = json.dumps(out)
        assert not LEAK.search(text), LEAK.search(text)
        # And it is the health that is there.
        cams = {c["name"]: c for c in out["cameras"]["cameras"]}
        assert cams["Front door"]["recording"] == "recording"
        assert cams["Doorbell"]["online"] is True
        assert out["cameras"]["recorders"][0]["storage_used_pct"] == 96.1
        assert out["network"]["clients"]["wireless"] == 12

    def test_facts_hold_only_allowed_attributes(self):
        from dartec_ha_manager import ha_facts

        hass = FakeHass()
        hass.install()
        for f in ha_facts.entity_facts(hass, ("unifi", "unifiprotect"), domains=("camera",)):
            assert set(f["attributes"]) <= set(ha_facts.ALLOWED_ATTRIBUTES)
            assert not LEAK.search(json.dumps(f))
