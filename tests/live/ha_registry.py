"""Print Home Assistant's own device and area registries as JSON.

Runs inside the Home Assistant container against its loopback websocket, so
the driver needs no websocket client of its own. This is the ground truth the
agent's snapshot is compared with: Home Assistant's API, not the registry
object the agent reads.

With `--assign-area`, it first puts one device in an area, through the same
API, so the snapshot's area names are checked against something other than
None — the demo integration leaves every device room-less.

Usage: python3 ha_registry.py <access token> [--assign-area]
"""
from __future__ import annotations

import asyncio
import json
import sys

import aiohttp


async def main(token: str, assign_area: bool) -> None:
    async with aiohttp.ClientSession() as session, \
            session.ws_connect("http://127.0.0.1:8123/api/websocket") as ws:
        await ws.receive_json()  # auth_required
        await ws.send_json({"type": "auth", "access_token": token})
        reply = await ws.receive_json()
        if reply.get("type") != "auth_ok":
            raise SystemExit(f"auth failed: {reply}")
        next_id = 0

        async def call(command: str, **fields):
            nonlocal next_id
            next_id += 1
            await ws.send_json({"id": next_id, "type": command, **fields})
            reply = await ws.receive_json()
            if not reply.get("success"):
                raise SystemExit(f"{command} failed: {reply}")
            return reply["result"]

        devices = await call("config/device_registry/list")
        areas = await call("config/area_registry/list")
        if assign_area:
            if not devices or not areas:
                raise SystemExit("need at least one device and one area to assign")
            await call("config/device_registry/update",
                       device_id=devices[0]["id"], area_id=areas[0]["area_id"])
            devices = await call("config/device_registry/list")
        print(json.dumps({"devices": devices, "areas": areas}))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--assign-area" in sys.argv[2:]))
