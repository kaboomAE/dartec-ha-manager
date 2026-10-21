"""Camera health, in one shape whatever the brand: the `cameras` section.

The owner's decision, 2026-10-18 (kaboomAE/dartec-ops#45): cameras are
monitored for **health only** — is each camera online, is it recording, how
full is the recorder's disk. Baytec never sees a picture, a snapshot, a
stream or a video, and recordings stay on the recorder in the house. Nothing
here reads one: adapters are handed facts with an allowlist of attributes
(ha_facts.py) that has no picture or stream in it, the agent cannot be asked
for one (service_policy.py), and media_guard.py removes any that might still
appear at the door. tests/test_media_guard.py holds the agent to all three.

Like network_health.py, two parts: a common shape the manager reads, and an
adapter per brand. `unifi_protect` is the first (the network is UniFi), for
Home Assistant's UniFi Protect integration (`unifiprotect`). Any other camera
in the home gets the generic adapter: online or not, and recording when its
own state says so, which is all a camera entity says about itself. A Reolink
adapter, say, is one function and one line in `ADAPTERS`.

**What UniFi Protect gives** (Home Assistant's source,
`homeassistant/components/unifiprotect`, 2026.10). Unique ids are
`<device mac>_<key>`:

| Fact | Entity | On by default |
|---|---|---|
| Camera online | the camera entity: Protect marks every entity of a camera unavailable while it is disconnected | yes (the highest-quality channel) |
| Recording now | the camera entity's state, `recording` while it records | yes |
| Recording mode | sensor `recording_mode` (`always`, `detections`, `schedule`, `adaptive`, `never`); the select of the same key as a fallback | yes |
| Footage on disk per camera | sensor `storage_used` (bytes, shown in the unit the sensor reports) | yes |
| Recorder disk used | sensor `storage_utilization` on the console (%) | yes |
| Recorder free space | sensor `resolution_free` ("Resolution: free space", %) | yes |
| Recording capacity | sensor `record_capacity` (seconds of footage the disk holds) | yes |
| Disk health | binary sensor per disk, unique id `<mac>_disk_health_<n>`, `on` = a problem | yes |

**A Protect recorder is meant to be full.** It overwrites its oldest footage
to make room, so a disk near 100% is a recorder working as designed: what
filling up costs is how far back recordings go, not whether they are made.
The manager therefore says so beside the number, and alerts on a failing
disk more urgently than on a full one.

"Recording stopped" is read conservatively: a camera set to `never`, or one
set to `always` that is online and not recording. A camera on `detections`
or a schedule is waiting for something to record, not stopped.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

try:
    from .device_health import expected_offline
except ImportError:  # imported on its own, as the tests do
    from device_health import expected_offline

_LOGGER = logging.getLogger(__name__)

CAMERAS_VERSION = 1
MAX_CAMERAS = 64
MAX_RECORDERS = 8
DOWN_STATES = frozenset({"unavailable", "unknown"})

# Recording, as the common shape says it.
#   recording — recording now
#   standby   — set to record on events or a schedule, waiting
#   stopped   — set to record continuously, online, and not recording
#   off       — recording switched off on the camera
#   unknown   — the camera is offline, or its brand does not say
RECORDING_STATES = ("recording", "standby", "stopped", "off", "unknown")

# Camera entities that are not cameras in the house: a file on disk, a proxy
# of another camera, a template. Never judged.
NOT_HARDWARE = frozenset({"local_file", "proxy", "template", "image_upload"})

# ── UniFi Protect ───────────────────────────────────────────────────────────

PROTECT = "unifiprotect"
PROTECT_STANDBY_MODES = frozenset({"detections", "schedule", "adaptive"})

_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "pb": 1e15,
          "kib": 2 ** 10, "mib": 2 ** 20, "gib": 2 ** 30, "tib": 2 ** 40}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_bytes(state: Any, unit: str | None) -> int | None:
    """A data-size sensor's state in bytes, whatever unit it is shown in."""
    value = _number(state)
    scale = _UNITS.get((unit or "B").strip().lower())
    if value is None or scale is None or value < 0:
        return None
    return int(value * scale)


def _key(fact: dict, key: str) -> bool:
    """Protect's entity for `key`: by translation key, or `<mac>_<key>`."""
    return fact.get("translation_key") == key or (fact.get("unique_id") or "").endswith(f"_{key}")


def camera_online(camera_facts: list[dict]) -> tuple[bool | None, str | None]:
    """(online, since) from a device's camera entities. None when none runs."""
    running = [f for f in camera_facts if not f.get("disabled") and f.get("state") is not None]
    if not running:
        return None, None
    online = any(f["state"] not in DOWN_STATES for f in running)
    since = max((f.get("last_changed") or "" for f in running), default="") or None
    return online, since


def protect_recording(online: bool | None, mode: str | None, recording_now: bool) -> str:
    if mode == "never":
        return "off"
    if not online:
        return "unknown"
    if recording_now:
        return "recording"
    if mode == "always":
        return "stopped"
    if mode in PROTECT_STANDBY_MODES:
        return "standby"
    return "unknown"


def unifi_protect(entities: list[dict], devices: dict[str, dict]) -> dict:
    by_device: dict[str, list[dict]] = {}
    for fact in entities:
        if fact.get("platform") == PROTECT and fact.get("device_id"):
            by_device.setdefault(fact["device_id"], []).append(fact)

    cameras, recorders = [], []
    for device_id, facts in by_device.items():
        device = devices.get(device_id) or {}
        if device.get("disabled"):
            continue
        camera_facts = [f for f in facts if f["domain"] == "camera"]
        utilization = next((f for f in facts if f["domain"] == "sensor"
                            and _key(f, "storage_utilization")), None)
        if camera_facts:
            online, since = camera_online(camera_facts)
            mode_fact = (next((f for f in facts if f["domain"] == "sensor"
                               and _key(f, "recording_mode")), None)
                         or next((f for f in facts if f["domain"] == "select"
                                  and _key(f, "recording_mode")), None))
            mode = mode_fact.get("state") if mode_fact else None
            mode = mode if mode not in DOWN_STATES else None
            used = next((f for f in facts if f["domain"] == "sensor"
                         and _key(f, "storage_used")), None)
            recording_now = any(f.get("state") == "recording" for f in camera_facts
                                if not f.get("disabled"))
            cameras.append({
                "device_id": device_id, "name": device.get("name"),
                "model": device.get("model"), "area": device.get("area"),
                "adapter": PROTECT, "online": online, "since": since,
                "recording": protect_recording(online, mode, recording_now),
                "recording_mode": mode,
                "storage_used_bytes": (to_bytes(used.get("state"),
                                                (used.get("attributes") or {}).get("unit_of_measurement"))
                                       if used else None),
                "expected_offline": _expected_offline(device, camera_facts),
            })
        elif utilization is not None:
            free = next((f for f in facts if f["domain"] == "sensor"
                         and _key(f, "resolution_free")), None)
            capacity = next((f for f in facts if f["domain"] == "sensor"
                             and _key(f, "record_capacity")), None)
            disks = [f for f in facts if f["domain"] == "binary_sensor"
                     and "_disk_health_" in (f.get("unique_id") or "")]
            judged = [f for f in facts if not f.get("disabled") and f.get("state") is not None
                      and f["domain"] in ("sensor", "binary_sensor")]
            online = (None if not judged else
                      any(f["state"] not in DOWN_STATES for f in judged))
            capacity_s = _number(capacity.get("state")) if capacity else None
            recorders.append({
                "device_id": device_id, "name": device.get("name"),
                "model": device.get("model"), "area": device.get("area"),
                "adapter": PROTECT, "online": online,
                "since": max((f.get("last_changed") or "" for f in judged), default="") or None,
                "storage_used_pct": _number(utilization.get("state")),
                "storage_free_pct": _number(free.get("state")) if free else None,
                "recording_capacity_s": int(capacity_s) if capacity_s else None,
                # Protect overwrites its oldest footage when full.
                "rotates": True,
                "disks": [{"name": (f.get("attributes") or {}).get("friendly_name"),
                           "healthy": (False if f.get("state") == "on"
                                       else True if f.get("state") == "off" else None)}
                          for f in sorted(disks, key=lambda f: f.get("unique_id") or "")
                          if not f.get("disabled")],
                "expected_offline": _expected_offline(device, []),
            })
    return {"cameras": cameras, "recorders": recorders}


def generic_cameras(entities: list[dict], devices: dict[str, dict]) -> dict:
    """Every camera of a brand with no adapter: online, and recording if its
    state says so. Nothing else about it is known, so nothing else is said."""
    by_device: dict[str, list[dict]] = {}
    for fact in entities:
        if (fact.get("domain") == "camera" and fact.get("platform") not in ADAPTERS
                and fact.get("platform") not in NOT_HARDWARE):
            by_device.setdefault(fact.get("device_id") or f"entity:{fact['entity_id']}",
                                 []).append(fact)
    cameras = []
    for key, facts in by_device.items():
        device = devices.get(key) or {}
        if device.get("disabled"):
            continue
        online, since = camera_online(facts)
        recording_now = any(f.get("state") == "recording" for f in facts)
        cameras.append({
            "device_id": key,
            "name": device.get("name") or (facts[0].get("attributes") or {}).get("friendly_name"),
            "model": device.get("model"), "area": device.get("area"),
            "adapter": facts[0].get("platform"), "online": online, "since": since,
            "recording": "recording" if online and recording_now else "unknown",
            "recording_mode": None, "storage_used_bytes": None,
            "expected_offline": _expected_offline(device, facts),
        })
    return {"cameras": cameras, "recorders": []}


def _expected_offline(device: dict, facts: list[dict]) -> bool:
    return expected_offline(device.get("labels")) or any(
        expected_offline(f.get("labels")) for f in facts)


ADAPTERS: dict[str, Callable[[list[dict], dict[str, dict]], dict]] = {
    PROTECT: unifi_protect,
}


def summarize(entities: list[dict], devices: dict[str, dict]) -> dict | None:
    """The `cameras` section, or None when the home has no camera at all."""
    cameras, recorders, adapters = [], [], []
    for platform, adapter in ADAPTERS.items():
        if any(f.get("platform") == platform for f in entities):
            part = adapter(entities, devices)
            if part["cameras"] or part["recorders"]:
                adapters.append(platform)
            cameras += part["cameras"]
            recorders += part["recorders"]
    generic = generic_cameras(entities, devices)
    if generic["cameras"]:
        adapters.append("generic")
        cameras += generic["cameras"]
    if not cameras and not recorders:
        return None
    cameras.sort(key=lambda c: (c["online"] is not False, str(c.get("name") or "")))
    return {
        "version": CAMERAS_VERSION,
        "adapters": adapters,
        "camera_count": len(cameras),
        "cameras": cameras[:MAX_CAMERAS],
        "recorders": recorders[:MAX_RECORDERS],
    }


def collect(hass) -> dict:
    """{"cameras": section} or {} for a home without cameras. Best-effort."""
    try:
        from . import ha_facts

        entities = ha_facts.entity_facts(hass, ADAPTERS, domains=("camera",))
        devices = ha_facts.device_facts(hass, (f["device_id"] for f in entities))
        section = summarize(entities, devices)
        return {"cameras": section} if section else {}
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("camera collect failed: %s", err)
        return {"cameras": {"version": CAMERAS_VERSION, "error": type(err).__name__}}
