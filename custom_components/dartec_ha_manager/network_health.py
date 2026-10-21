"""The home's network, in one shape whatever the brand: the `network` section.

The owner's decision, 2026-10-18 (kaboomAE/dartec-ops#45): Baytec supplies
each home's network equipment and the ISP's router stays only as the internet
connection. The brand is UniFi today, and may not be the only one later. So
this is two parts:

* **The common shape** (`summarize`, below) is what the manager reads. It
  says nothing about any brand: an internet state, a list of network devices
  with a role (gateway, access point, switch), whether each is online, its
  client count and its firmware, and the Wi-Fi networks with their clients.
* **An adapter per brand** maps that brand's Home Assistant entities onto
  the shape. `unifi_network` is the first, for Home Assistant's own UniFi
  Network integration (`unifi`). Adding a brand is one function and one line
  in `ADAPTERS`; nothing on the manager changes.

Adapters read plain facts (ha_facts.py) and never Home Assistant itself, so
each one is tested without it, and they match entities by integration and
`unique_id` / `translation_key`, never by entity id, which the household can
rename.

**What the UniFi integration gives, and what it does not** (checked against
Home Assistant's source, `homeassistant/components/unifi`, 2026.10):

| Fact | Entity | On by default |
|---|---|---|
| Device online | sensor "State" (`device_state`, unique id `device_state-<mac>`), one of `connected`, `disconnected`, `heartbeat_missed`, `upgrading`, ...; else the device tracker (unique id `<mac>`) `home`/`not_home` | yes |
| Firmware | update entity (unique id `device_update-<mac>`), `installed_version`, `latest_version` | yes |
| Clients per Wi-Fi network | sensor "Clients" on the WLAN (`wlan_clients`) | yes |
| Clients per device | sensor "Clients" on the device (`device_clients`) | **no** |
| WAN | sensor "<target> <WAN> latency" (`wan_latency`, unique id `<target>_<wan>_latency-<mac>`) on the gateway | **no** |

There is **no WAN up/down entity** in the integration. The latency sensors
are the closest thing, and they report UniFi's *average* latency to a
monitor target, which may keep its last value while the line is down. So the
internet state is read, in order of trust, from:

1. any `binary_sensor` carrying the label `baytec_internet` (create a label
   named "Baytec internet" and put it on, say, a Ping (ICMP) sensor): `on` is
   up. An installer's explicit choice, so it wins;
2. the agent's own check (internet_watch.py), on every home, whatever its
   equipment: up, down, or `blocked` when the probes fail while Baytec is
   reachable;
3. the UniFi WAN latency sensors, when an installer has enabled them: a
   number is up; all of a WAN's targets `unavailable`/`unknown` is down;
4. otherwise unknown.

An internet outage with no second WAN takes the home off the manager, since
the agent reaches Baytec over that same line. The agent's check keeps a
record of every outage in the house and reports it when the line is back
(`internet.agent.outages`), so the manager can say why the home went quiet.
The WAN rows are sent whatever the source, so a failover to a second line is
still seen.

Labels: a device carrying `baytec_expected_offline` (device_health.py) is
still listed, marked `expected_offline`, and the manager does not alert on it.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from typing import Any

try:
    from .device_health import expected_offline
except ImportError:  # imported on its own, as the tests do
    from device_health import expected_offline

_LOGGER = logging.getLogger(__name__)

NETWORK_VERSION = 1
INTERNET_LABEL = "baytec_internet"
MAX_DEVICES = 100
MAX_WLANS = 16
DOWN_STATES = frozenset({"unavailable", "unknown"})

ROLES = ("gateway", "access_point", "switch", "device")
STATES = ("online", "offline", "busy", "unknown")

# ── UniFi Network ───────────────────────────────────────────────────────────

UNIFI = "unifi"

# The integration's Device State sensor values (unifi/const.py DEVICE_STATES).
# A firmware mismatch is a device that is up and talking; upgrading,
# provisioning and adopting are a device busy on purpose, never alerted on.
UNIFI_ONLINE = frozenset({"connected", "firmware_mismatch"})
UNIFI_BUSY = frozenset({"upgrading", "provisioning", "adopting", "pending", "deleting"})
UNIFI_OFFLINE = frozenset({"disconnected", "heartbeat_missed", "isolated",
                           "inform_error", "adoption_failed"})

# The integration puts UniFi's own model code in the device registry (e.g.
# UAL6, U7PG2, UDMPRO, USL16LP). Its device type (`uap`, `usw`, `ugw`) is not
# exposed to Home Assistant, so the role is read from the code's prefix, and a
# device with a WAN latency sensor is a gateway whatever its code. Some
# integrations give a product name instead ("UniFi Dream Machine"), so names
# are read too. Checked against a real home on 2026-10-18 (U7LR, U6IW,
# UALR6v2, "UniFi Dream Machine"); a model the table does not know is shown
# as a "network device" and still alerts when offline.
UNIFI_GATEWAY_PREFIXES = ("UDM", "UDR", "UCG", "UXG", "UGW", "UDW", "UX", "UCGF")
UNIFI_AP_PREFIXES = ("U7", "U6", "UAL", "UAP", "UAE", "UHD", "UFL", "UKPW", "U2",
                     "UAC", "UMA", "E7")
UNIFI_SWITCH_EXCLUDED = ("USP",)   # SmartPower plugs and PDUs, not switches
UNIFI_SWITCH_PREFIXES = ("USW", "USL", "USM", "USF", "USC", "US")

UNIFI_GATEWAY_NAMES = ("DREAM MACHINE", "DREAM ROUTER", "DREAM WALL", "CLOUD GATEWAY",
                       "SECURITY GATEWAY", "NEXT-GEN GATEWAY", "EXPRESS")
UNIFI_AP_NAMES = ("ACCESS POINT", " AP ", "NANOHD", "FLEXHD", "BEACONHD")
UNIFI_SWITCH_NAMES = ("SWITCH",)

WAN_LATENCY_RE = re.compile(r"^(?P<target>[a-z0-9]+)_(?P<wan>wan\d?)_latency-")


def unifi_role(model: str | None, has_wan: bool) -> str:
    if has_wan:
        return "gateway"
    name = f" {(model or '').strip().upper()} "
    if " " in name.strip():
        # A product name, not a code: its words first, then its first word
        # as a code ("USW Flex Mini", "U6 Lite").
        for role, words in (("gateway", UNIFI_GATEWAY_NAMES), ("access_point", UNIFI_AP_NAMES),
                            ("switch", UNIFI_SWITCH_NAMES)):
            if any(word in name for word in words):
                return role
        model = name.split()[0]
    code = (model or "").strip().upper().replace("-", "")
    if not code:
        return "device"
    if code.startswith(UNIFI_GATEWAY_PREFIXES):
        return "gateway"
    if code.startswith(UNIFI_AP_PREFIXES):
        return "access_point"
    if code.startswith(UNIFI_SWITCH_EXCLUDED):
        return "device"
    if code.startswith(UNIFI_SWITCH_PREFIXES):
        return "switch"
    return "device"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _kind(fact: dict, key: str, prefix: str = "", exact: bool = False) -> bool:
    """Whether a fact is the entity a translation key / unique id prefix names."""
    if fact.get("translation_key") == key:
        return True
    unique_id = fact.get("unique_id") or ""
    return unique_id == prefix if exact else bool(prefix) and unique_id.startswith(prefix)


def unifi_device_state(state_fact: dict | None, tracker_fact: dict | None) -> tuple[str, str | None, str | None]:
    """(online|offline|busy|unknown, the integration's own word, since)."""
    if state_fact is not None and not state_fact.get("disabled"):
        value = state_fact.get("state")
        since = state_fact.get("last_changed")
        if value in UNIFI_ONLINE:
            return "online", value, since
        if value in UNIFI_BUSY:
            return "busy", value, since
        if value in UNIFI_OFFLINE:
            return "offline", value, since
        if value not in DOWN_STATES and value is not None:
            # A state the integration added after this was written: shown as
            # it is, never alerted on.
            return "unknown", value, since
    if tracker_fact is not None and not tracker_fact.get("disabled"):
        value = tracker_fact.get("state")
        if value == "home":
            return "online", "connected", tracker_fact.get("last_changed")
        if value == "not_home":
            return "offline", "disconnected", tracker_fact.get("last_changed")
    # Both unavailable: Home Assistant cannot reach the UniFi controller. That
    # is the integration's own failure, already an alert of its own.
    return "unknown", None, None


def unifi_network(entities: list[dict], devices: dict[str, dict]) -> dict:
    """UniFi Network entities -> the common shape's devices, Wi-Fi and WANs."""
    by_device: dict[str, list[dict]] = {}
    for fact in entities:
        if fact.get("platform") == UNIFI and fact.get("device_id"):
            by_device.setdefault(fact["device_id"], []).append(fact)

    rows, wlans, wans = [], [], []
    for device_id, facts in by_device.items():
        device = devices.get(device_id) or {}
        if device.get("disabled"):
            continue
        wlan_clients = [f for f in facts if f["domain"] == "sensor"
                        and _kind(f, "wlan_clients", "wlan_clients-")]
        if device.get("entry_type") == "service" or wlan_clients:
            # A WLAN is a service device with one "Clients" sensor.
            for fact in wlan_clients:
                if fact.get("disabled"):
                    continue
                count = _number(fact.get("state"))
                wlans.append({"name": device.get("name") or fact.get("attributes", {}).get("friendly_name"),
                              "clients": int(count) if count is not None else None})
            continue
        state_fact = next((f for f in facts if f["domain"] == "sensor"
                           and _kind(f, "device_state", "device_state-")), None)
        tracker_fact = next((f for f in facts if f["domain"] == "device_tracker"), None)
        update_fact = next((f for f in facts if f["domain"] == "update"
                            and (f.get("unique_id") or "").startswith("device_update-")), None)
        clients_fact = next((f for f in facts if f["domain"] == "sensor"
                             and _kind(f, "device_clients", "device_clients-")), None)
        wan_facts = [f for f in facts if f["domain"] == "sensor"
                     and WAN_LATENCY_RE.match(f.get("unique_id") or "")]
        if state_fact is None and tracker_fact is None:
            continue    # a client, or a sub-device with nothing to judge
        state, word, since = unifi_device_state(state_fact, tracker_fact)
        firmware = None
        if update_fact is not None and not update_fact.get("disabled"):
            attrs = update_fact.get("attributes") or {}
            firmware = {"installed": attrs.get("installed_version"),
                        "latest": attrs.get("latest_version"),
                        "update_available": (True if update_fact.get("state") == "on"
                                             else False if update_fact.get("state") == "off"
                                             else None)}
        clients = None
        if clients_fact is not None and not clients_fact.get("disabled"):
            count = _number(clients_fact.get("state"))
            clients = int(count) if count is not None else None
        rows.append({
            "device_id": device_id,
            "name": device.get("name"),
            "model": device.get("model"),
            "area": device.get("area"),
            "adapter": UNIFI,
            "role": unifi_role(device.get("model"), bool(wan_facts)),
            "state": state,
            "detail": word,
            "since": since,
            "clients": clients,
            "clients_sensor": clients_fact is not None and not clients_fact.get("disabled"),
            "firmware": firmware or {"installed": device.get("sw_version"), "latest": None,
                                     "update_available": None},
            "expected_offline": _expected_offline(device, facts),
        })
        wans.extend(unifi_wans(wan_facts))
    return {"devices": rows, "wlans": wlans, "wans": wans}


def unifi_wans(facts: list[dict]) -> list[dict]:
    """One row per WAN from its latency sensors: up, down or unknown."""
    per_wan: dict[str, list[dict]] = {}
    for fact in facts:
        if fact.get("disabled"):
            continue
        match = WAN_LATENCY_RE.match(fact.get("unique_id") or "")
        if match:
            per_wan.setdefault(match["wan"].upper(), []).append(fact)
    rows = []
    for wan, wan_facts in sorted(per_wan.items()):
        readings = [_number(f.get("state")) for f in wan_facts]
        numbers = [r for r in readings if r is not None]
        if numbers:
            state = "up"
        elif all(f.get("state") in DOWN_STATES for f in wan_facts):
            state = "down"
        else:
            state = "unknown"
        rows.append({"name": wan, "state": state,
                     "latency_ms": min(numbers) if numbers else None,
                     "since": max((f.get("last_changed") or "" for f in wan_facts), default="") or None,
                     "source": "unifi_wan_latency"})
    return rows


def _expected_offline(device: dict, facts: list[dict]) -> bool:
    """The device, or the entity its state is read from, carries the label."""
    return expected_offline(device.get("labels")) or any(
        expected_offline(f.get("labels")) for f in facts)


# Integration -> adapter. Each returns {"devices", "wlans", "wans"}.
ADAPTERS: dict[str, Callable[[list[dict], dict[str, dict]], dict]] = {
    UNIFI: unifi_network,
}


def internet_state(labelled: list[dict], wans: list[dict], agent: dict | None = None) -> dict:
    """The internet as the common shape reports it. See the module docstring
    for the order of trust. `agent` is internet_watch's report, carried along
    whatever decides the state, for its record of outages."""
    out = _internet_state(labelled, wans, agent)
    if agent is not None:
        out["agent"] = agent
    return out


def _internet_state(labelled: list[dict], wans: list[dict], agent: dict | None) -> dict:
    checks = [f for f in labelled if f.get("domain") == "binary_sensor"
              and not f.get("disabled") and f.get("state") in ("on", "off")]
    if checks:
        up = any(f["state"] == "on" for f in checks)
        deciding = [f for f in checks if (f["state"] == "on") == up]
        return {"state": "up" if up else "down", "source": "label",
                "since": max((f.get("last_changed") or "" for f in deciding), default="") or None,
                "checks": len(checks), "wans": wans}
    if agent and agent.get("state") in ("up", "down"):
        return {"state": agent["state"], "source": "agent", "since": agent.get("since"),
                "checks": 0, "wans": wans}
    if wans:
        if any(w["state"] == "up" for w in wans):
            state = "up"
        elif all(w["state"] == "down" for w in wans):
            state = "down"
        else:
            state = "unknown"
        return {"state": state, "source": "unifi_wan_latency", "since": None,
                "checks": 0, "wans": wans}
    return {"state": "unknown", "source": "none", "since": None, "checks": 0, "wans": []}


def summarize(entities: list[dict], devices: dict[str, dict],
              labelled: list[dict], agent: dict | None = None,
              unadapted: list[dict] | None = None) -> dict | None:
    """The `network` section, or None when no adapter found anything, no
    internet check is labelled and the agent's own check has not run yet
    (nothing to say, so nothing is sent)."""
    rows, wlans, wans, adapters = [], [], [], []
    for platform, adapter in ADAPTERS.items():
        if not any(f.get("platform") == platform for f in entities):
            continue
        part = adapter(entities, devices)
        adapters.append(platform)
        rows += part["devices"]
        wlans += part["wlans"]
        wans += part["wans"]
    if not adapters and not labelled and agent is None and not unadapted:
        return None
    order = {role: i for i, role in enumerate(ROLES)}
    rows.sort(key=lambda r: (order.get(r["role"], 9), str(r.get("name") or "")))
    wireless = [w["clients"] for w in wlans if w.get("clients") is not None]
    return {
        "version": NETWORK_VERSION,
        "adapters": adapters,
        "internet": internet_state(labelled, wans, agent),
        "device_count": len(rows),
        "devices": rows[:MAX_DEVICES],
        "wlans": wlans[:MAX_WLANS],
        "clients": {"wireless": sum(wireless) if wireless else None,
                    "per_device_sensors": sum(1 for r in rows if r.get("clients_sensor"))},
        # Network equipment that is in Home Assistant through an integration
        # with no adapter, so none of its health can be read
        # (ha_facts.devices_from). The manager says which integration to add.
        "unadapted": list(unadapted or []),
    }


def collect(hass) -> dict:
    """{"network": section} or {} when there is nothing to report. Best-effort
    like every collector: a failure sends `{"network": {"error": ...}}`, so the
    manager can tell "no network equipment" from "could not read it"."""
    try:
        from . import ha_facts, internet_watch

        entities = ha_facts.entity_facts(hass, ADAPTERS)
        labelled = ha_facts.labelled_facts(hass, (INTERNET_LABEL,))
        devices = ha_facts.device_facts(hass, (f["device_id"] for f in entities))
        unadapted = ha_facts.devices_from(hass, "ubiquiti", ADAPTERS)
        section = summarize(entities, devices, labelled, internet_watch.current(hass),
                            unadapted)
        return {"network": section} if section else {}
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        _LOGGER.debug("network collect failed: %s", err)
        return {"network": {"version": NETWORK_VERSION, "error": type(err).__name__}}
