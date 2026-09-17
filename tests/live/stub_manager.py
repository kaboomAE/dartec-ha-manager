"""A stand-in for the Dartec manager, just enough to pair an agent and read it.

Runs *inside* the Home Assistant container, with the interpreter and aiohttp
that image already ships. That keeps the server URL on loopback — the only
plain-http URL the config flow accepts — and needs no network between the
test runner and the container beyond the one published HA port.

It speaks the two endpoints the agent uses: `POST /api/agent/validate` for the
config flow, and the `/agent/ws` socket. Every snapshot and command result is
written under STATE_DIR, which the driver reads back with `docker exec`.

After the first snapshot it asks for the device registry through
`registry_query`, the command that pages the live registry rather than the
snapshot's capped copy, so both paths through `registry_access` run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from aiohttp import WSMsgType, web

TOKEN = os.environ.get("DARTEC_LIVE_TOKEN", "live-test-pairing-token")
PORT = int(os.environ.get("DARTEC_LIVE_PORT", "8765"))
STATE_DIR = Path(os.environ.get("DARTEC_LIVE_STATE", "/tmp/dartec-live"))

INFO = {"instance_id": "live-test-home", "customer_name": "Live test",
        "instance_name": "Integration rig"}


def _write(name: str, payload) -> None:
    tmp = STATE_DIR / f".{name}.tmp"
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    tmp.replace(STATE_DIR / name)


async def health(_request):
    return web.json_response({"ok": True})


async def validate(request):
    body = await request.json()
    if body.get("token") != TOKEN:
        return web.json_response({"detail": "unknown token"}, status=401)
    return web.json_response(INFO)


async def agent_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    auth = await ws.receive_json()
    if auth.get("type") != "auth" or auth.get("token") != TOKEN:
        await ws.send_json({"type": "auth_failed", "reason": "bad token"})
        await ws.close()
        return ws
    await ws.send_json({"type": "auth_ok"})

    snapshots = 0
    asked = False
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            break
        data = json.loads(msg.data)
        if data.get("type") == "snapshot":
            snapshots += 1
            _write(f"snapshot-{snapshots}.json", data.get("data"))
            if not asked:
                asked = True
                await ws.send_json({"type": "command", "id": "devices",
                                    "action": "registry_query", "kind": "devices",
                                    "limit": 10000})
        elif data.get("type") == "command_result":
            _write(f"result-{data.get('id')}.json", data)
    return ws


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/api/agent/validate", validate)
    app.router.add_get("/agent/ws", agent_ws)
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
