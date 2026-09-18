"""Stages the old "DarTec" spellings inside a live Home Assistant.

Runs in the container (it needs aiohttp, which only the container has), with
the owner's access token as its first argument and a step as its second:

    stage  - create a dashboard titled "DarTec Dashboard", retitle the agent's
             own config entry "DarTec: ...", and reload the entry, the way a
             home paired before v0.6.1 would start
    read   - the agent's entry title, the dashboard's title, and the logbook
             lines the agent wrote

Prints one JSON object. `run_live_brand.py` starts it.
"""
from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8123"
WS_URL = "ws://127.0.0.1:8123/api/websocket"
DOMAIN = "dartec_ha_manager"
DASHBOARD = "dartec-brandtest"


async def ws_session(session, token):
    ws = await session.ws_connect(WS_URL)
    await ws.receive_json()
    await ws.send_json({"type": "auth", "access_token": token})
    await ws.receive_json()
    counter = {"id": 0}

    async def call(msg):
        counter["id"] += 1
        await ws.send_json({"id": counter["id"], **msg})
        while True:
            reply = await ws.receive_json()
            if reply.get("id") == counter["id"]:
                return reply
    return ws, call


async def entry(call) -> dict:
    listed = await call({"type": "config_entries/get", "domain": DOMAIN})
    return (listed.get("result") or [{}])[0]


async def main(token: str, step: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        ws, call = await ws_session(session, token)
        try:
            if step == "stage":
                created = await call({"type": "lovelace/dashboards/create",
                                      "url_path": DASHBOARD, "title": "DarTec Dashboard",
                                      "mode": "storage", "require_admin": False,
                                      "show_in_sidebar": True})
                ours = await entry(call)
                retitled = await call({"type": "config_entries/update",
                                       "entry_id": ours["entry_id"],
                                       "title": "DarTec: Live test / Integration rig"})
                async with session.post(f"{BASE}/api/config/config_entries/entry/"
                                        f"{ours['entry_id']}/reload") as response:
                    reloaded = response.status
                await asyncio.sleep(3)
                return {"dashboard_created": created.get("success"),
                        "entry_retitled": retitled.get("success"),
                        "reload_status": reloaded,
                        "entry_title_after_reload": (await entry(call)).get("title")}
            dashboards = await call({"type": "lovelace/dashboards/list"})
            async with session.get(f"{BASE}/api/logbook") as response:
                logbook = await response.json()
            return {"entry_title": (await entry(call)).get("title"),
                    "dashboard_title": next((d.get("title") for d in dashboards["result"]
                                             if d.get("url_path") == DASHBOARD), None),
                    "logbook": [f"{row.get('name')}: {row.get('message')}" for row in logbook
                                if "Dartec" in str(row.get("message") or "")
                                or "Dartec" in str(row.get("name") or "")]}
        finally:
            await ws.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1], sys.argv[2]))))
