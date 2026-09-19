"""Drives room panel accounts inside a live Home Assistant.

Runs in the container (it needs aiohttp, which only the container has), with
the owner's access token as its argument, and prints one JSON object: the
problems it found and the evidence. `run_live_panels.py` starts it, after the
agent has been paired to the stub manager in its `drive` scenario.

The manager's side is played by writing commands into the stub manager's
outbox (`send-<id>.json`) and reading its `result-<id>.json`, so every panel
command reaches the agent the way the real manager's would, over the agent's
own socket and through its consent gate. Everything a command claims is then
read back through Home Assistant's own commands: `config/auth/list` as the
owner, and `frontend/get_user_data`, `get_panels` and `lovelace/config` **as
the panel account itself**, signed in through the login flow like the tablet
would. A pass means Home Assistant agrees, not that the agent agrees with
itself.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

BASE = "http://127.0.0.1:8123"
WS_URL = "ws://127.0.0.1:8123/api/websocket"
STATE = Path(os.environ.get("DARTEC_LIVE_STATE", "/tmp/dartec-live"))
HH = "dartec_ha_manager/household"

ROOM = "dartec-room-test"
OTHER = "dartec-room-other"
USERNAME = "panel-test"
# Made up for this container, and shaped like what the technician's phone
# generates. The run fails if either turns up anywhere it should not.
PASSWORD_1 = "kq7m-wd3p-zr9h-x2fa"
PASSWORD_2 = "tb4n-8vce-hm2j-q6ys"
ROOM_CONFIG = {"title": "Test room", "views": [{"title": "Test room", "path": "room",
                                                 "cards": [{"type": "markdown",
                                                            "content": "Room panel"}]}]}

problems: list[str] = []
evidence: dict = {}


def check(ok: bool, message: str) -> None:
    if not ok:
        problems.append(message)


# ── The manager's side: the stub manager's outbox ──────────────────────────

_sent = 0


async def agent(command: dict, timeout: float = 60) -> dict:
    """Send one command to the agent as the manager, and wait for its answer."""
    global _sent
    _sent += 1
    cmd_id = f"p{_sent:03d}-{command['action']}"
    tmp = STATE / f".q-{cmd_id}"
    tmp.write_text(json.dumps({"id": cmd_id, **command}))
    tmp.replace(STATE / f"send-{cmd_id}.json")
    result_file = STATE / f"result-{cmd_id}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result_file.exists():
            try:
                result = json.loads(result_file.read_text())
            except ValueError:
                await asyncio.sleep(0.1)
                continue
            evidence.setdefault("answers", []).append(result)
            return result
        await asyncio.sleep(0.2)
    problems.append(f"no answer to {cmd_id} within {timeout:.0f}s")
    return {}


def snapshot_number() -> int:
    return max((int(f.name[9:-5]) for f in STATE.glob("snapshot-*.json")
                if f.name[9:-5].isdigit()), default=0)


async def snapshot_after(number: int, timeout: float = 150) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        latest = snapshot_number()
        if latest > number:
            try:
                return json.loads((STATE / f"snapshot-{latest}.json").read_text())
            except ValueError:
                pass
        await asyncio.sleep(0.5)
    problems.append("no snapshot arrived after the changes")
    return {}


# ── Home Assistant's side ───────────────────────────────────────────────────

class Conn:
    def __init__(self, session, token):
        self.session, self.token, self.ws, self.next_id = session, token, None, 1

    async def open(self) -> bool:
        self.ws = await self.session.ws_connect(WS_URL)
        await self.ws.receive_json()
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        return (await self.ws.receive_json()).get("type") == "auth_ok"

    async def call(self, payload: dict) -> dict:
        msg_id, self.next_id = self.next_id, self.next_id + 1
        await self.ws.send_json({"id": msg_id, **payload})
        while True:
            msg = await self.ws.receive_json()
            if msg.get("id") == msg_id and msg.get("type") == "result":
                return msg

    async def ok(self, payload: dict, what: str):
        msg = await self.call(payload)
        if not msg.get("success"):
            problems.append(f"{what}: {payload.get('type')} failed: {msg.get('error')}")
            return None
        return msg.get("result")

    async def close(self):
        if self.ws is not None:
            await self.ws.close()


async def login(session, username: str, password: str) -> str | None:
    """The login flow, from the container's loopback: a local address, which
    is what lets a `local_only` account in."""
    client_id = f"{BASE}/"
    async with session.post(f"{BASE}/auth/login_flow", json={
            "client_id": client_id, "handler": ["homeassistant", None],
            "redirect_uri": client_id}) as resp:
        flow = await resp.json()
    async with session.post(f"{BASE}/auth/login_flow/{flow['flow_id']}", json={
            "username": username, "password": password, "client_id": client_id}) as resp:
        step = await resp.json()
    if step.get("type") != "create_entry":
        return None
    async with session.post(f"{BASE}/auth/token", data={
            "grant_type": "authorization_code", "code": step["result"],
            "client_id": client_id}) as resp:
        return (await resp.json()).get("access_token")


async def as_user(session, username, password) -> Conn | None:
    token = await login(session, username, password)
    if not token:
        return None
    conn = Conn(session, token)
    return conn if await conn.open() else None


async def users(owner: Conn) -> dict[str, dict]:
    return {u.get("username") or u["id"]: u
            for u in await owner.ok({"type": "config/auth/list"}, "auth list") or []}


async def user_data(conn: Conn, key: str) -> dict:
    got = await conn.ok({"type": "frontend/get_user_data", "key": key}, f"user data {key}")
    return (got or {}).get("value") or {}


def refused(answer: dict, code: str, what: str) -> None:
    check(answer.get("ok") is False and answer.get("code") == code,
          f"{what}: expected refusal '{code}', got {answer}")


def no_secret(value, what: str) -> None:
    text = json.dumps(value, default=str)
    for secret in (PASSWORD_1, PASSWORD_2):
        check(secret not in text, f"a panel password appeared in {what}")


async def main(owner_token: str) -> None:
    async with aiohttp.ClientSession() as session:
        owner = Conn(session, owner_token)
        assert await owner.open()

        # ── Dashboards: two rooms through the agent, one admin-only ──
        for url_path, title in ((ROOM, "Test room"), (OTHER, "Other room")):
            made = await agent({"action": "lovelace_create", "url_path": url_path,
                                "title": title, "icon": "mdi:sofa",
                                "show_in_sidebar": False, "config": ROOM_CONFIG})
            check(made.get("ok") is True, f"lovelace_create {url_path}: {made}")
        await owner.ok({"type": "lovelace/dashboards/create", "url_path": "staff-only",
                        "title": "Staff", "mode": "storage", "require_admin": True,
                        "show_in_sidebar": True}, "admin-only dashboard")
        listed = {d["url_path"]: d for d in await owner.ok(
            {"type": "lovelace/dashboards/list"}, "dashboards") or []}
        check(listed.get(ROOM, {}).get("show_in_sidebar") is False
              and listed.get(ROOM, {}).get("require_admin") is False,
              f"the room dashboard is {listed.get(ROOM)}")

        # An administrator whose username happens to start with panel-.
        made = await owner.ok({"type": "config/auth/create", "name": "Panel tester",
                               "group_ids": ["system-admin"]}, "admin user")
        admin_id = made["user"]["id"]
        await owner.ok({"type": "config/auth_provider/homeassistant/create",
                        "user_id": admin_id, "username": "panel-admin",
                        "password": "adm1n-live-pw-8k"}, "admin login")

        setup = {"action": "panel_setup", "name": "Test room panel", "username": USERNAME,
                 "password": PASSWORD_1, "url_path": ROOM}

        # ── Without consent it is refused, and nothing is created ──
        closed = await agent({"action": "commissioning_complete"})
        check(closed.get("ok") is True, f"commissioning_complete: {closed}")
        status = await agent({"action": "maintenance_status"})
        check((status.get("consent") or {}).get("allowed") is False,
              f"consent still open after commissioning closed: {status}")
        answer = await agent(setup)
        check(answer.get("ok") is False and answer.get("refused") is True
              and answer.get("code") == "consent", f"setup without consent: {answer}")
        check(USERNAME not in await users(owner), "a refused setup still created the account")

        # The homeowner opens a window, from the home, with Home Assistant's
        # own service call.
        await owner.ok({"type": "call_service", "domain": "dartec_ha_manager",
                        "service": "allow_maintenance", "service_data": {"minutes": 30}},
                       "allow_maintenance")

        # ── Refusals that need no account ──
        refused(await agent({**setup, "url_path": "staff-only"}), "no_dashboard",
                "an admin-only dashboard")
        refused(await agent({**setup, "url_path": "dartec-room-nowhere"}), "no_dashboard",
                "a dashboard that does not exist")
        refused(await agent({**setup, "url_path": "lovelace"}), "no_dashboard",
                "the built-in default dashboard")
        refused(await agent({**setup, "password": "short-pw-9"}), "invalid", "a short password")
        refused(await agent({**setup, "username": "kitchen"}), "invalid", "a username without the prefix")
        refused(await agent({**setup, "username": "panel-admin"}), "username_taken",
                "an administrator's username")
        check(await login(session, "panel-admin", "adm1n-live-pw-8k") is not None,
              "the administrator's login stopped working after a refused setup")

        # ── Set up ──
        before_login = datetime.now(timezone.utc)
        first = await agent(setup)
        evidence["setup"] = first
        no_secret(first, "the setup answer")
        check(first.get("ok") is True and first.get("created") is True
              and first.get("username") == USERNAME and first.get("url_path") == ROOM
              and isinstance(first.get("hidden_panels"), int), f"setup: {first}")
        panel_id = first.get("user_id")
        account = (await users(owner)).get(USERNAME) or {}
        evidence["account"] = account
        check(account.get("id") == panel_id, f"the account is {account}")
        check(account.get("group_ids") == ["system-users"], f"groups {account.get('group_ids')}")
        check(account.get("local_only") is True, "the panel account can sign in from anywhere")
        check(account.get("is_active") is True and account.get("is_owner") is False,
              f"account {account}")
        persons = await owner.ok({"type": "person/list"}, "person list") or {}
        check(all(p.get("user_id") != panel_id for p in persons.get("storage", [])),
              "a person was created for the panel account")

        # ── As the panel itself ──
        panel = await as_user(session, USERNAME, PASSWORD_1)
        check(panel is not None, "the panel account could not sign in")
        if panel:
            me = await panel.ok({"type": "auth/current_user"}, "current user") or {}
            check(me.get("is_admin") is False and me.get("name") == "Test room panel",
                  f"the panel signs in as {me}")
            core = await user_data(panel, "core")
            check(core.get("default_panel") == ROOM, f"its own core data is {core}")
            sidebar = await user_data(panel, "sidebar")
            evidence["sidebar"] = sidebar
            seen = await panel.ok({"type": "get_panels"}, "panel's panels") or {}
            evidence["panels_it_is_sent"] = sorted(seen)
            hidden = set(sidebar.get("hiddenPanels") or [])
            check(hidden == set(seen) - {ROOM},
                  f"hiddenPanels is not every panel it is sent but its room: only sent "
                  f"{sorted(set(seen) - {ROOM} - hidden)}, only hidden {sorted(hidden - set(seen))}")
            for name in ("lovelace", "map", "logbook", "history", "energy", OTHER):
                if name in seen:
                    check(name in hidden, f"'{name}' is not hidden from the panel")
            check({"lovelace", "logbook", "history"} <= hidden,
                  f"the usual entries are not all hidden: {sorted(hidden)}")
            check(ROOM not in hidden, "the room's own dashboard is hidden")
            check(sidebar.get("panelOrder") == [ROOM], f"panelOrder {sidebar.get('panelOrder')}")
            check(first.get("hidden_panels") == len(hidden),
                  f"setup said {first.get('hidden_panels')} hidden, Home Assistant has {len(hidden)}")
            check("config" not in seen and "dartec-household" not in seen,
                  "the panel is sent administrator pages")
            board = await panel.ok({"type": "lovelace/config", "url_path": ROOM},
                                   "the room dashboard as the panel")
            check(isinstance(board, dict) and board.get("views"),
                  f"the room dashboard did not load for the panel: {board}")
            await panel.close()

        # ── Status: signed in, and when ──
        status = await agent({"action": "panel_status"})
        rows = {r.get("username"): r for r in status.get("panels") or []}
        row = rows.get(USERNAME) or {}
        evidence["status"] = status
        check(status.get("ok") is True and set(rows) == {USERNAME},
              f"panel_status lists {sorted(rows)} (the administrator is not a panel)")
        check(row.get("signed_in") is True, f"status after signing in: {row}")
        used = row.get("last_used_at")
        try:
            used_at = datetime.fromisoformat(used)
            check(used_at >= before_login, f"last_used_at {used} is before the sign-in")
        except (TypeError, ValueError):
            problems.append(f"last_used_at is {used!r}")
        check(row.get("default_panel") == ROOM and row.get("local_only") is True,
              f"status row {row}")
        check("ip" not in json.dumps(status).casefold().replace("hidden", ""),
              "panel_status carries an address")
        one = await agent({"action": "panel_status", "user_id": admin_id})
        check(one.get("panels") == [], f"status for a non-panel user: {one}")

        # ── Setup again: same account, new password, put back into shape ──
        await owner.ok({"type": "config/auth/update", "user_id": panel_id,
                        "local_only": False}, "loosen the panel by hand")
        again = await agent({**setup, "password": PASSWORD_2, "name": "Test panel"})
        no_secret(again, "the second setup answer")
        check(again.get("ok") is True and again.get("created") is False
              and again.get("user_id") == panel_id, f"setup again: {again}")
        account = (await users(owner)).get(USERNAME) or {}
        check(account.get("local_only") is True and account.get("name") == "Test panel",
              f"after setup again the account is {account}")
        check(await login(session, USERNAME, PASSWORD_1) is None, "the old password still works")
        check(await login(session, USERNAME, PASSWORD_2) is not None,
              "the new password does not work")

        # ── Update: another room ──
        updated = await agent({"action": "panel_update", "user_id": panel_id, "url_path": OTHER})
        check(updated.get("ok") is True and isinstance(updated.get("hidden_panels"), int),
              f"panel_update: {updated}")
        refused(await agent({"action": "panel_update", "user_id": panel_id,
                             "url_path": "staff-only"}), "no_dashboard", "update to admin-only")
        refused(await agent({"action": "panel_update", "user_id": admin_id, "url_path": OTHER}),
                "not_panel", "update of an administrator")
        panel = await as_user(session, USERNAME, PASSWORD_2)
        if panel:
            core = await user_data(panel, "core")
            check(core.get("default_panel") == OTHER, f"after the update core is {core}")
            hidden = set((await user_data(panel, "sidebar")).get("hiddenPanels") or [])
            check(ROOM in hidden and OTHER not in hidden,
                  f"after the update hiddenPanels is {sorted(hidden)}")
            await panel.close()
        else:
            problems.append("the panel could not sign in after the update")

        # ── Snapshot ──
        number = snapshot_number()
        snap = await snapshot_after(number)
        rows = {r.get("username"): r for r in snap.get("panels") or []}
        evidence["snapshot_panels"] = snap.get("panels")
        check(rows.get(USERNAME, {}).get("default_panel") == OTHER
              and rows.get(USERNAME, {}).get("signed_in") is True,
              f"snapshot panels: {snap.get('panels')}")
        check((snap.get("core") or {}).get("language") == "en",
              f"snapshot core.language is {(snap.get('core') or {}).get('language')!r}")
        expected = {"owner": 1, "admin": 1, "family": 0, "guest": 0, "view_only": 0,
                    "paused": 0, "total": 2}
        check(snap.get("household") == expected,
              f"household counts {snap.get('household')}, expected {expected}")
        no_secret(snap, "the snapshot")

        # ── My Home keeps it apart ──
        state = await owner.ok({"type": f"{HH}/list"}, "My Home list") or {}
        check(all(p.get("id") != panel_id for p in state.get("people") or []),
              "My Home lists the panel account as a person")
        check(state.get("panel_accounts") == 1, f"panel_accounts is {state.get('panel_accounts')}")
        check(not any(d["url_path"].startswith("dartec-room-") for d in state.get("dashboards") or []),
              "My Home offers a room dashboard as a first dashboard")
        msg = await owner.call({"type": f"{HH}/remove", "user_id": panel_id})
        check(not msg.get("success") and (msg.get("error") or {}).get("code") == "panel",
              f"My Home removing the panel: {msg}")
        msg = await owner.call({"type": f"{HH}/create", "name": "Kids", "username": "panel-kids",
                                "password": "w7hz-p4kc-ma3e", "role": "family"})
        check(not msg.get("success") and (msg.get("error") or {}).get("code") == "username_panel",
              f"My Home giving a person a panel- username: {msg}")

        # ── Remove ──
        refused(await agent({"action": "panel_remove", "user_id": admin_id}), "not_panel",
                "removing an administrator")
        owner_id = (await owner.ok({"type": "auth/current_user"}, "owner") or {}).get("id")
        refused(await agent({"action": "panel_remove", "user_id": owner_id}), "not_panel",
                "removing the owner")
        gone = await agent({"action": "panel_remove", "user_id": panel_id})
        check(gone.get("ok") is True and not gone.get("gone"), f"panel_remove: {gone}")
        accounts = await users(owner)
        check(USERNAME not in accounts, "the panel account is still there")
        check("panel-admin" in accounts, "the administrator went with the panel")
        again = await agent({"action": "panel_remove", "user_id": panel_id})
        check(again.get("ok") is True and again.get("gone") is True, f"removing twice: {again}")
        check(await login(session, USERNAME, PASSWORD_2) is None, "a removed panel can sign in")

        # ── The home's own record ──
        book, lines = [], []
        for _ in range(20):
            async with session.get(f"{BASE}/api/logbook", headers={
                    "Authorization": f"Bearer {owner_token}"}) as resp:
                book = await resp.json()
            lines = [e.get("message") or "" for e in book]
            if any("deleted panel account" in line for line in lines):
                break
            await asyncio.sleep(1)
        evidence["logbook"] = [line for line in lines if "panel" in line]
        check(any("Refused remote command 'panel_setup'" in line for line in lines),
              "the refused setup is not in the logbook")
        check(any("Dartec ran 'panel_setup'" in line and USERNAME in line for line in lines),
              "the setup is not in the logbook")
        check(any(f"set panel account '{USERNAME}' to dashboard '{OTHER}'" in line
                  for line in lines), "the update is not in the logbook")
        check(any(f"deleted panel account '{USERNAME}'" in line for line in lines),
              "the removal is not in the logbook")
        no_secret(book, "the logbook")
        no_secret(evidence.get("answers"), "an answer")

        await owner.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
    print(json.dumps({"problems": problems, "evidence": evidence}, default=str))
