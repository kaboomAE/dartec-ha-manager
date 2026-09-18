"""Stage batteries and offline devices on the demo home, and print the truth.

Runs inside the Home Assistant container, like ha_registry.py. Everything is
done through Home Assistant's own APIs — the REST state endpoint to take
entities down, the label and device registries for the opt-out — so the
driver then checks the agent against what Home Assistant was told, not against
anything the agent computed.

The demo's entities are static, so a state written here stays written:

- `sensor.outside_humidity` goes unavailable: its device has no other entity,
  so it is **offline**.
- `cover.kitchen_window` goes unavailable too, but its device carries the
  `dartec_expected_offline` label: **not reported**.
- `sensor.outside_temperature` goes unavailable while the same device's
  battery sensor keeps answering: **not offline** — one dead entity is not a
  dead device.
- `sensor.carbon_dioxide_battery` drops to 4%: the **lowest battery**, ahead
  of the demo's own 12%.

Prints {"offline": [device ids], "not_offline": [...], "batteries":
{device id: level}} for the driver.

Usage: python3 device_health_setup.py <access token>
"""
from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8123"
LABEL_NAME = "Dartec expected offline"
LABEL_ID = "dartec_expected_offline"


async def main(token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session, \
            session.ws_connect(f"{BASE}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        if (await ws.receive_json()).get("type") != "auth_ok":
            raise SystemExit("auth failed")
        next_id = 0

        async def call(command: str, **fields):
            nonlocal next_id
            next_id += 1
            await ws.send_json({"id": next_id, "type": command, **fields})
            reply = await ws.receive_json()
            if not reply.get("success"):
                raise SystemExit(f"{command} failed: {reply}")
            return reply["result"]

        async def set_state(entity_id: str, state: str, **attributes) -> None:
            async with session.get(f"{BASE}/api/states/{entity_id}") as resp:
                current = await resp.json()
            async with session.post(f"{BASE}/api/states/{entity_id}", json={
                    "state": state,
                    "attributes": {**current.get("attributes", {}), **attributes}}) as resp:
                if resp.status not in (200, 201):
                    raise SystemExit(f"setting {entity_id} failed: {resp.status}")

        entities = {e["entity_id"]: e for e in await call("config/entity_registry/list")}

        def device_of(entity_id: str) -> str:
            return entities[entity_id]["device_id"]

        label = await call("config/label_registry/create", name=LABEL_NAME)
        if label["label_id"] != LABEL_ID:
            raise SystemExit(f"label id is {label['label_id']}, expected {LABEL_ID}")
        await call("config/device_registry/update",
                   device_id=device_of("cover.kitchen_window"), labels=[LABEL_ID])

        await set_state("sensor.outside_humidity", "unavailable")
        await set_state("cover.kitchen_window", "unavailable")
        await set_state("sensor.outside_temperature", "unavailable")
        await set_state("sensor.carbon_dioxide_battery", "4")

        # The battery truth, from Home Assistant's own states and registry:
        # every registered sensor that HA shows as a battery percentage. The
        # device class comes from the state's attributes — what HA displays —
        # since the registry list does not carry the integration's own class.
        async with session.get(f"{BASE}/api/states") as resp:
            states = {s["entity_id"]: s for s in await resp.json()}
        batteries: dict[str, float] = {}
        for entity_id, entry in entities.items():
            state = states.get(entity_id)
            attributes = (state or {}).get("attributes") or {}
            if (entity_id.startswith("sensor.") and entry.get("device_id")
                    and attributes.get("device_class") == "battery"
                    and attributes.get("unit_of_measurement") == "%"):
                level = float(state["state"])
                device_id = entry["device_id"]
                batteries[device_id] = min(level, batteries.get(device_id, level))

        print(json.dumps({
            "offline": [device_of("sensor.outside_humidity")],
            "not_offline": [device_of("cover.kitchen_window"),
                            device_of("sensor.outside_temperature")],
            "batteries": batteries,
        }))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
