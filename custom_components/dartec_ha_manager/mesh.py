"""The shape of the Zigbee mesh: who routes through whom, and how well.

The last and most expensive piece of the plan-to-home work, and the one worth
being most careful about. Signal strength (signal_health) says how well each
device is heard. This says how the network is *arranged* — which is what a
designed coverage map can actually be compared against.

**Two stacks, two completely different answers.** A home can run ZHA,
Zigbee2MQTT, or — like the real paired home — both at once. ZHA exposes
neighbours over Home Assistant's own websocket. Zigbee2MQTT exposes them by
publishing a network map onto an MQTT topic in response to a request, which
means a round trip through a broker and a scan that can take a minute on a
large network. They share no shapes, so they get separate collectors and one
normalised result.

**Not in the snapshot.** Everything else the agent collects rides along with
the 60-second heartbeat. This does not: a Zigbee2MQTT network scan floods the
mesh with route requests and is the sort of thing that makes a network worse
while you measure it. It is a command, run when someone asks, and the manager
is expected to ask rarely.

**Absence is reported, never inferred.** A home with no Zigbee coordinator, a
Zigbee2MQTT that does not answer within the timeout, a ZHA that returns no
neighbours — all of those are different from "the mesh is fine", and each says
which it is. A coverage comparison built on a silently empty topology would
conclude that a perfectly good home has no network at all.
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# A Zigbee2MQTT network scan is not free — it asks every router for its tables.
# Long enough for a real house, short enough that a stuck request gives up.
Z2M_SCAN_TIMEOUT_S = 120
ZHA_TIMEOUT_S = 60

MAX_NODES = 500
MAX_LINKS = 4000

# Zigbee LQI, 0-255. Kept identical to signal_health's thresholds on purpose:
# two modules disagreeing about what counts as a weak link is how a dashboard
# ends up contradicting itself.
LINK_WEAK = 40
LINK_CRITICAL = 20


def classify_link(lqi: float | None) -> str:
    if lqi is None:
        return "unknown"
    if lqi <= LINK_CRITICAL:
        return "critical"
    return "weak" if lqi <= LINK_WEAK else "ok"


def normalise_zha(devices: list[dict]) -> dict[str, Any]:
    """ZHA's device list into nodes and links.

    ZHA reports neighbours per device with an IEEE address and an LQI. The
    relationship field distinguishes a router from an end device, which is what
    decides whether a node can carry traffic for anything else — and therefore
    whether a coverage plan's assumptions still hold.
    """
    nodes, links = [], []
    for device in devices[:MAX_NODES]:
        ieee = device.get("ieee")
        if not ieee:
            continue
        nodes.append({
            "id": ieee,
            "name": device.get("user_given_name") or device.get("name"),
            "type": (device.get("device_type") or "").lower(),
            "manufacturer": device.get("manufacturer"),
            "model": device.get("model"),
            "lqi": device.get("lqi"),
            "rssi": device.get("rssi"),
            "available": device.get("available"),
            "stack": "zha",
        })
        for neighbour in (device.get("neighbors") or [])[:64]:
            try:
                lqi = int(neighbour.get("lqi"))
            except (TypeError, ValueError):
                lqi = None
            links.append({
                "from": ieee, "to": neighbour.get("ieee"),
                "lqi": lqi, "status": classify_link(lqi),
                "relationship": neighbour.get("relationship"),
                "stack": "zha",
            })
            if len(links) >= MAX_LINKS:
                break
    return {"nodes": nodes, "links": links}


def normalise_z2m(networkmap: dict) -> dict[str, Any]:
    """Zigbee2MQTT's networkmap response into the same shape.

    Z2M publishes `{"nodes": [...], "links": [...]}` already, but with its own
    field names — `ieeeAddr`, `linkquality`, `sourceIeeeAddr` — and it includes
    the coordinator as a node like any other.
    """
    nodes, links = [], []
    for node in (networkmap.get("nodes") or [])[:MAX_NODES]:
        address = node.get("ieeeAddr") or node.get("ieee_address")
        if not address:
            continue
        nodes.append({
            "id": address,
            "name": node.get("friendlyName") or node.get("friendly_name"),
            "type": (node.get("type") or "").lower(),
            "manufacturer": node.get("manufacturerName") or node.get("manufacturer"),
            "model": node.get("modelID") or node.get("definition", {}).get("model"),
            "lqi": None, "rssi": None,
            "available": not node.get("failed"),
            "stack": "z2m",
        })
    for link in (networkmap.get("links") or [])[:MAX_LINKS]:
        try:
            lqi = int(link.get("linkquality", link.get("lqi")))
        except (TypeError, ValueError):
            lqi = None
        links.append({
            "from": link.get("sourceIeeeAddr") or link.get("source", {}).get("ieeeAddr"),
            "to": link.get("targetIeeeAddr") or link.get("target", {}).get("ieeeAddr"),
            "lqi": lqi, "status": classify_link(lqi),
            "relationship": link.get("relationship"),
            "stack": "z2m",
        })
    return {"nodes": nodes, "links": links}


def summarise(topology: dict) -> dict[str, Any]:
    """The handful of numbers a coverage comparison actually turns on.

    `orphans` is the one worth explaining: a node with no link to anything is
    either newly joined, or it has lost its route and is about to stop
    working. Both matter to whoever designed the mesh, and neither shows up in
    a per-device signal reading — a device can be heard perfectly by the
    coordinator and still have no route through the house.
    """
    nodes = topology.get("nodes") or []
    links = topology.get("links") or []
    linked = {link.get("from") for link in links} | {link.get("to") for link in links}
    routers = [n for n in nodes if n.get("type") in ("router", "coordinator")]
    return {
        "nodes": len(nodes),
        "links": len(links),
        "routers": len(routers),
        "end_devices": len(nodes) - len(routers),
        "weak_links": sum(1 for link in links if link.get("status") == "weak"),
        "critical_links": sum(1 for link in links if link.get("status") == "critical"),
        "orphans": [n.get("name") or n.get("id") for n in nodes
                    if n.get("id") not in linked][:20],
        "stacks": sorted({n.get("stack") for n in nodes if n.get("stack")}),
    }


async def collect_mesh(hass, stacks: list[str] | None = None) -> dict[str, Any]:
    """Ask whichever Zigbee stacks this home runs for their topology.

    Returns what was found and, separately, what could not be asked. A home
    running both ZHA and Zigbee2MQTT gets one merged map; a home running
    neither is told it is running neither, rather than handed an empty map that
    reads like a dead network.
    """
    wanted = set(stacks or ["zha", "z2m"])
    merged: dict[str, Any] = {"nodes": [], "links": []}
    sources: list[str] = []
    problems: list[str] = []

    if "zha" in wanted:
        try:
            from .ws_bridge import call_own_ws

            result = await call_own_ws(hass, {"type": "zha/devices"},
                                       timeout=ZHA_TIMEOUT_S)
            if result.get("success"):
                part = normalise_zha(result.get("result") or [])
                if part["nodes"]:
                    merged["nodes"] += part["nodes"]
                    merged["links"] += part["links"]
                    sources.append("zha")
                else:
                    problems.append("ZHA is present but reported no devices")
            else:
                problems.append("ZHA is not set up on this home")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("zha topology failed: %s", err)
            problems.append(f"ZHA did not answer: {err}")

    if "z2m" in wanted:
        try:
            part = await _collect_z2m(hass)
            if part is None:
                problems.append("Zigbee2MQTT is not set up on this home")
            elif part["nodes"]:
                merged["nodes"] += part["nodes"]
                merged["links"] += part["links"]
                sources.append("z2m")
            else:
                problems.append("Zigbee2MQTT answered with an empty map")
        except TimeoutError:
            problems.append(
                f"Zigbee2MQTT did not finish its scan within {Z2M_SCAN_TIMEOUT_S}s")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("z2m topology failed: %s", err)
            problems.append(f"Zigbee2MQTT scan failed: {err}")

    return {"ok": bool(sources), "sources": sources, "problems": problems,
            **merged, "summary": summarise(merged)}


async def _collect_z2m(hass):
    """Request a network map over MQTT and wait for the reply.

    Zigbee2MQTT answers a request on `.../bridge/request/networkmap` with a
    message on `.../bridge/response/networkmap`, so this subscribes first,
    publishes, and waits. Returns None when MQTT is not configured at all,
    which is a different thing from an empty map.
    """
    import asyncio
    import json

    try:
        from homeassistant.components import mqtt
    except ImportError:
        return None
    if not hass.config_entries.async_entries("mqtt"):
        return None

    loop = asyncio.get_running_loop()
    answer: asyncio.Future = loop.create_future()

    @callback_safe
    def _on_message(message):
        if answer.done():
            return
        try:
            payload = json.loads(message.payload)
        except (ValueError, TypeError):
            return
        data = payload.get("data") or {}
        answer.set_result(data.get("value") or data)

    unsubscribe = await mqtt.async_subscribe(
        hass, "zigbee2mqtt/bridge/response/networkmap", _on_message)
    try:
        await mqtt.async_publish(
            hass, "zigbee2mqtt/bridge/request/networkmap",
            json.dumps({"type": "raw", "routes": True}))
        raw = await asyncio.wait_for(answer, timeout=Z2M_SCAN_TIMEOUT_S)
    finally:
        unsubscribe()
    return normalise_z2m(raw or {})


def callback_safe(func):
    """Home Assistant's @callback where it is importable, and a passthrough
    where it is not — so this module stays importable outside HA for tests."""
    try:
        from homeassistant.core import callback

        return callback(func)
    except ImportError:
        return func
