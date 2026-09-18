"""A stand-in Supervisor, so a guarded update runs inside a real Home Assistant.

A Home Assistant container has no Supervisor, and the image cannot really be
swapped for another version from inside itself. What a guarded update depends
on is narrower than that, and all of it is real here:

* the Supervisor's *word* on which version is installed (`/core/info`,
  `/os/info`), which is what the agent trusts after a restart;
* the restart itself. An update, a rollback, a restore and an OS "reboot"
  each restart the real Home Assistant process, so the agent is killed
  mid-job exactly as on a real home, and has to carry on from its store;
* backups that exist, or do not.

Runs inside the container (`docker run --add-host supervisor:127.0.0.1`, port
80) with the interpreter and aiohttp the image ships. State lives in a file,
so it outlives nothing but the container. The driver passes an access token
for restarting Home Assistant through its own API.

Knobs, as files under STATE_DIR:
* `sup-config-invalid`: `/core/check` fails.
* `sup-update-refused`: `/core/update` to a new version is refused.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import aiohttp
from aiohttp import web

STATE_DIR = Path(os.environ.get("DARTEC_LIVE_STATE", "/tmp/dartec-live"))
STATE = STATE_DIR / "supervisor.json"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "live-supervisor-token")
HA_TOKEN_FILE = STATE_DIR / "ha-token"

DEFAULT = {"core": "2026.9.2", "core_latest": "2026.9.4", "os": "16.2",
           "os_latest": "16.3", "boot": "A",
           "slots": {"A": {"version": "16.2", "state": "active"},
                     "B": {"version": "16.1", "state": "inactive"}},
           "backups": {}, "calls": [], "restarts": 0}


def load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else json.loads(json.dumps(DEFAULT))


def save(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)


def ok(data=None):
    return web.json_response({"result": "ok", "data": data or {}})


def error(message: str, status: int = 400):
    return web.json_response({"result": "error", "message": message}, status=status)


async def restart_home_assistant(state: dict) -> None:
    """What the Supervisor does to Core after an update: stop it and start it.
    Here, Home Assistant's own restart, a moment after answering."""
    state["restarts"] += 1
    save(state)

    async def later():
        await asyncio.sleep(1)
        token = HA_TOKEN_FILE.read_text().strip()
        async with aiohttp.ClientSession() as session:
            await session.post("http://127.0.0.1:8123/api/services/homeassistant/restart",
                               headers={"Authorization": f"Bearer {token}"}, json={})
    asyncio.get_running_loop().create_task(later())


@web.middleware
async def auth(request, handler):
    if request.headers.get("Authorization") != f"Bearer {TOKEN}":
        return error("unauthorized", 401)
    state = load()
    state["calls"].append(f"{request.method} {request.path}")
    save(state)
    return await handler(request)


async def info(_):
    return ok({"hassos": load()["os"], "homeassistant": load()["core"]})


async def core_info(_):
    s = load()
    return ok({"version": s["core"], "version_latest": s["core_latest"],
               "update_available": s["core"] != s["core_latest"]})


async def os_info(_):
    s = load()
    return ok({"version": s["os"], "version_latest": s["os_latest"],
               "update_available": s["os"] != s["os_latest"],
               "boot": s["boot"], "boot_slots": s["slots"]})


async def core_check(_):
    if (STATE_DIR / "sup-config-invalid").exists():
        return error("Invalid config: stand-in check told to fail")
    return ok()


async def addons(_):
    return ok({"addons": []})


async def host_info(_):
    return ok({"disk_used": 5.0, "disk_total": 30.0, "operating_system":
               f"Home Assistant OS {load()['os']}", "kernel": "live"})


async def supervisor_info(_):
    return ok({"version": "2026.09.1"})


async def backup_new_full(request):
    body = await request.json()
    s = load()
    slug = f"live{len(s['backups']) + 1:03d}"
    s["backups"][slug] = {"slug": slug, "name": body.get("name", ""), "core": s["core"],
                          "type": "full", "size": 1.0}
    save(s)
    return ok({"slug": slug})


async def backup_info(request):
    slug = request.match_info["slug"]
    backup = load()["backups"].get(slug)
    return ok(backup) if backup else error(f"no backup {slug}", 404)


async def backup_restore_partial(request):
    slug = request.match_info["slug"]
    s = load()
    backup = s["backups"].get(slug)
    if not backup:
        return error(f"no backup {slug}", 404)
    s["core"] = backup["core"]
    await restart_home_assistant(s)
    return ok()


async def core_update(request):
    body = await request.json()
    s = load()
    version = body.get("version") or s["core_latest"]
    if version != s["core"] and (STATE_DIR / "sup-update-refused").exists() \
            and version != s.get("core_before_update"):
        return error("stand-in refused the update")
    s.setdefault("core_before_update", s["core"])
    s["core"] = version
    await restart_home_assistant(s)
    return ok()


async def os_update(request):
    body = await request.json()
    s = load()
    s["os"] = body.get("version") or s["os_latest"]
    other = "B" if s["boot"] == "A" else "A"
    s["slots"][s["boot"]]["state"] = "inactive"
    s["slots"][other] = {"version": s["os"], "state": "active"}
    s["boot"] = other
    await restart_home_assistant(s)        # a reboot, as far as Core can tell
    return ok()


async def os_boot_slot(request):
    body = await request.json()
    s = load()
    slot = body.get("boot_slot")
    if slot not in s["slots"]:
        return error(f"no slot {slot}")
    s["slots"][s["boot"]]["state"] = "inactive"
    s["boot"] = slot
    s["slots"][slot]["state"] = "active"
    s["os"] = s["slots"][slot]["version"]
    await restart_home_assistant(s)
    return ok()


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE.exists():
        save(load())
    app = web.Application(middlewares=[auth])
    app.router.add_get("/info", info)
    app.router.add_get("/core/info", core_info)
    app.router.add_get("/os/info", os_info)
    app.router.add_post("/core/check", core_check)
    app.router.add_get("/addons", addons)
    app.router.add_get("/host/info", host_info)
    app.router.add_get("/supervisor/info", supervisor_info)
    app.router.add_post("/backups/new/full", backup_new_full)
    app.router.add_get("/backups/{slug}/info", backup_info)
    app.router.add_post("/backups/{slug}/restore/partial", backup_restore_partial)
    app.router.add_post("/core/update", core_update)
    app.router.add_post("/os/update", os_update)
    app.router.add_post("/os/boot-slot", os_boot_slot)
    web.run_app(app, host="127.0.0.1", port=80, print=None)


if __name__ == "__main__":
    main()
