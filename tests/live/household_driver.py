"""Drives "My Home" inside a live Home Assistant, as several real people.

Runs in the container (it needs aiohttp, which only the container has), with
the owner's access token as its argument, and prints one JSON object: the
problems it found and the evidence. `run_live_household.py` starts it.

Everything the panel changes is read back through Home Assistant's own
commands (`config/auth/list`, `person/list`, `frontend/get_user_data` as the
person themselves, the logbook), not through the panel's own answers, so a
pass means Home Assistant agrees rather than that the panel agrees with
itself.
"""
from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8123"
WS_URL = "ws://127.0.0.1:8123/api/websocket"
HH = "dartec_ha_manager/household"

problems: list[str] = []
evidence: dict = {}


def check(ok: bool, message: str) -> None:
    if not ok:
        problems.append(message)


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

    async def refused(self, payload: dict, code: str, what: str) -> None:
        msg = await self.call(payload)
        got = (msg.get("error") or {}).get("code")
        check(not msg.get("success") and got == code,
              f"{what}: expected refusal '{code}', got "
              f"{'success' if msg.get('success') else got}")

    async def close(self):
        if self.ws is not None:
            await self.ws.close()


async def login(session, username: str, password: str) -> str | None:
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


def person(state: dict, name: str) -> dict:
    return next((p for p in state["people"] if p["name"] == name), {})


async def main(owner_token: str) -> None:
    async with aiohttp.ClientSession() as session:
        owner = Conn(session, owner_token)
        assert await owner.open()

        # The panel is registered, admin-only, and its files are served.
        panels = await owner.ok({"type": "get_panels"}, "owner panels")
        panel = (panels or {}).get("dartec-household") or {}
        check(panel.get("require_admin") is True, f"panel not admin-only: {panel}")
        module_url = ((panel.get("config") or {}).get("_panel_custom") or {}).get("module_url")
        evidence["panel"] = {"title": panel.get("title"), "module_url": module_url}
        for path in (module_url, "/dartec_household/i18n/en.json",
                     "/dartec_household/i18n/ar.json"):
            async with session.get(f"{BASE}{path}") as resp:
                check(resp.status == 200, f"{path} answered {resp.status}")

        # Dashboards: one Dartec template, one of the home's own, one admin-only.
        for url_path, title, admin in (("dartec-home", "Dartec Home", False),
                                       ("kids-room", "Kids", False),
                                       ("staff-only", "Staff", True)):
            await owner.ok({"type": "lovelace/dashboards/create", "url_path": url_path,
                            "title": title, "mode": "storage", "require_admin": admin,
                            "show_in_sidebar": True}, f"dashboard {url_path}")

        # Dartec's support account, made the way the onboarding app makes it.
        created = await owner.ok({"type": "config/auth/create", "name": "Dartec",
                                  "group_ids": ["system-admin"]}, "dartec user")
        dartec_id = created["user"]["id"]
        await owner.ok({"type": "config/auth_provider/homeassistant/create",
                        "user_id": dartec_id, "username": "dartec",
                        "password": "dartec-live-pw-9x7"}, "dartec login")

        state = await owner.ok({"type": f"{HH}/list"}, "list")
        boards = {d["url_path"]: d for d in state["dashboards"]}
        evidence["dashboards"] = state["dashboards"]
        check("staff-only" not in boards, "an admin-only dashboard was offered")
        check(boards.get("dartec-home", {}).get("from_dartec") is True,
              "the Dartec template is not marked as from Dartec")
        check("kids-room" in boards, "the home's own dashboard was not offered")
        check(state["maintenance_account"] is True, "Dartec's account was not noticed")
        check(all(p["name"] != "Dartec" for p in state["people"]),
              "Dartec's account is listed as a member of the household")
        check(state["me"]["is_owner"] is True, "the onboarded user is not the owner")
        owner_id = state["me"]["id"]

        # Add three people.
        await owner.ok({"type": f"{HH}/create", "name": "Omar", "username": "omar",
                        "password": "q3vr-8kdm-ytn2", "role": "admin"}, "create Omar")
        await owner.ok({"type": f"{HH}/create", "name": "Layla", "username": "Layla ",
                        "password": "w7hz-p4kc-ma3e", "role": "family",
                        "dashboard": "kids-room"}, "create Layla")
        state = await owner.ok({"type": f"{HH}/create", "name": "Sam", "username": "sam",
                                "password": "t9xe-2bqf-hj6u", "role": "guest",
                                "local_only": False}, "create Sam")
        await owner.refused({"type": f"{HH}/create", "name": "Twin", "username": "omar",
                             "password": "zz8k-3mdq-wp4r", "role": "family"},
                            "username_taken", "duplicate username")
        await owner.refused({"type": f"{HH}/create", "name": "Fake", "username": "dartec",
                             "password": "zz8k-3mdq-wp4r", "role": "family"},
                            "username_reserved", "the dartec username")

        ha_users = {u["name"]: u for u in await owner.ok({"type": "config/auth/list"}, "auth list")}
        check(ha_users["Omar"]["group_ids"] == ["system-admin"], f"Omar: {ha_users['Omar']}")
        check(ha_users["Layla"]["group_ids"] == ["system-users"]
              and ha_users["Layla"]["username"] == "layla", f"Layla: {ha_users['Layla']}")
        check(ha_users["Sam"]["local_only"] is True, "a guest was allowed to sign in from anywhere")
        check(person(state, "Sam").get("role") == "guest", "Sam is not shown as a guest")
        persons = await owner.ok({"type": "person/list"}, "person list")
        linked = {p.get("user_id") for p in persons.get("storage", [])}
        check({ha_users[n]["id"] for n in ("Omar", "Layla", "Sam")} <= linked,
              f"not every new user has a person: {persons}")

        # Layla, signed in as herself: her first dashboard, no panel, no commands.
        layla = await as_user(session, "layla", "w7hz-p4kc-ma3e")
        check(layla is not None, "Layla could not sign in")
        if layla:
            core = await layla.ok({"type": "frontend/get_user_data", "key": "core"}, "Layla core")
            check((core or {}).get("value", {}).get("default_panel") == "kids-room",
                  f"Layla's first dashboard is {core}")
            her_panels = await layla.ok({"type": "get_panels"}, "Layla panels") or {}
            check("dartec-household" not in her_panels,
                  "a regular user was sent the household panel")
            await layla.refused({"type": f"{HH}/list"}, "unauthorized", "non-admin list")
            await layla.refused({"type": f"{HH}/remove", "user_id": owner_id},
                                "unauthorized", "non-admin remove")

        # Omar, an admin who is not the owner.
        omar = await as_user(session, "omar", "q3vr-8kdm-ytn2")
        check(omar is not None, "Omar could not sign in")
        if omar:
            omar_id = ha_users["Omar"]["id"]
            await omar.ok({"type": f"{HH}/list"}, "Omar list")
            await omar.refused({"type": f"{HH}/update", "user_id": omar_id, "role": "family"},
                               "own_role", "own role")
            await omar.refused({"type": f"{HH}/update", "user_id": omar_id,
                                "is_active": False}, "self_pause", "own pause")
            await omar.refused({"type": f"{HH}/update", "user_id": owner_id,
                                "is_active": False}, "owner", "pause the owner")
            await omar.refused({"type": f"{HH}/remove", "user_id": owner_id},
                               "owner", "remove the owner")
            await omar.refused({"type": f"{HH}/remove", "user_id": dartec_id},
                               "maintenance", "remove Dartec")
            await omar.refused({"type": f"{HH}/set_password", "user_id": ha_users["Layla"]["id"],
                                "password": "n4km-7wqa-zd2p"}, "owner_only",
                               "an admin setting a password")

        # Dartec's own account may look, but not change anything here.
        dartec = await as_user(session, "dartec", "dartec-live-pw-9x7")
        if dartec:
            listed = await dartec.ok({"type": f"{HH}/list"}, "Dartec list")
            check((listed or {}).get("me", {}).get("can_manage") is False,
                  "Dartec's account was told it can manage the household")
            await dartec.refused({"type": f"{HH}/update", "user_id": ha_users["Layla"]["id"],
                                  "is_active": False}, "actor_maintenance", "Dartec pausing")
        else:
            problems.append("Dartec's account could not sign in")

        # Edit, pause (signs Layla out), resume.
        layla_id = ha_users["Layla"]["id"]
        await owner.ok({"type": f"{HH}/update", "user_id": layla_id, "name": "Layla K",
                        "local_only": True}, "edit Layla")
        await owner.ok({"type": f"{HH}/update", "user_id": layla_id, "is_active": False},
                       "pause Layla")
        if layla:
            # Pausing removes their sessions, and Home Assistant closes the
            # socket of one that is open: that is what "signed out" means.
            try:
                answer = await layla.call({"type": "get_config"})
                still_in = bool(answer.get("success"))
            except Exception as err:  # noqa: BLE001 - a closed socket is the pass
                still_in, answer = False, {"closed": type(err).__name__}
            evidence["paused_session"] = answer
            check(not still_in, "a paused person's open session kept working")
            layla = None
        check(await login(session, "layla", "w7hz-p4kc-ma3e") is None,
              "a paused person could still sign in")
        await owner.ok({"type": f"{HH}/update", "user_id": layla_id, "is_active": True},
                       "resume Layla")
        ha_users = {u["id"]: u for u in await owner.ok({"type": "config/auth/list"}, "list")}
        check(ha_users[layla_id]["name"] == "Layla K" and ha_users[layla_id]["local_only"]
              and ha_users[layla_id]["is_active"], f"Layla after edits: {ha_users[layla_id]}")
        persons = await owner.ok({"type": "person/list"}, "person list")
        check(any(p.get("user_id") == layla_id and p.get("name") == "Layla K"
                  for p in persons.get("storage", [])), "Layla's person was not renamed")

        # Reset the password (owner only), signing her out everywhere.
        await owner.ok({"type": f"{HH}/set_password", "user_id": layla_id,
                        "password": "n4km-7wqa-zd2p", "sign_out": True}, "reset Layla")
        check(await login(session, "layla", "w7hz-p4kc-ma3e") is None,
              "the old password still works")
        check(await login(session, "layla", "n4km-7wqa-zd2p") is not None,
              "the new password does not work")

        # First dashboard for Sam, then back to the home's default.
        sam_id = next(i for i, u in ha_users.items() if u["name"] == "Sam")
        await owner.ok({"type": f"{HH}/set_dashboard", "user_id": sam_id,
                        "url_path": "dartec-home"}, "Sam's dashboard")
        await owner.refused({"type": f"{HH}/set_dashboard", "user_id": sam_id,
                             "url_path": "staff-only"}, "dashboard_unknown",
                            "an admin-only dashboard")
        sam = await as_user(session, "sam", "t9xe-2bqf-hj6u")
        if sam:
            core = await sam.ok({"type": "frontend/get_user_data", "key": "core"}, "Sam core")
            check((core or {}).get("value", {}).get("default_panel") == "dartec-home",
                  f"Sam's first dashboard is {core}")
            await sam.close()
        else:
            problems.append("Sam could not sign in")

        # Remove Sam: the user and the person go, the guest mark goes.
        state = await owner.ok({"type": f"{HH}/remove", "user_id": sam_id}, "remove Sam")
        ha_users = {u["id"]: u for u in await owner.ok({"type": "config/auth/list"}, "list")}
        check(sam_id not in ha_users, "Sam's user is still there")
        persons = await owner.ok({"type": "person/list"}, "person list")
        check(all(p.get("name") != "Sam" for p in persons.get("storage", [])),
              "Sam's person is still there")
        evidence["activity"] = state["activity"]
        check(len(state["activity"]) >= 9, f"activity has {len(state['activity'])} entries")
        check("n4km-7wqa-zd2p" not in json.dumps(state), "a password came back in an answer")

        # The logbook says who did it, and never the password.
        # The recorder commits about once a second, so ask until the last
        # change has arrived.
        book, mine = [], []
        for _ in range(20):
            async with session.get(f"{BASE}/api/logbook", headers={
                    "Authorization": f"Bearer {owner_token}"}) as resp:
                book = await resp.json()
            mine = [e for e in book if e.get("name") == "My Home"]
            if any("removed Sam" in (e.get("message") or "") for e in mine):
                break
            await asyncio.sleep(1)
        evidence["logbook"] = [{k: e.get(k) for k in ("message", "context_user_id")}
                               for e in mine]
        messages = " | ".join(e.get("message", "") for e in mine)
        check("Live test added Layla as Family" in messages, f"logbook: {messages}")
        check("Live test paused Layla K" in messages, f"logbook: {messages}")
        check("Live test removed Sam from the household" in messages, f"logbook: {messages}")
        check(all(e.get("context_user_id") == owner_id for e in mine),
              "logbook entries are not attributed to the person who made them")
        check("n4km-7wqa-zd2p" not in json.dumps(book), "a password reached the logbook")

        for conn in (owner, layla, omar, dartec):
            if conn:
                await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
    print(json.dumps({"problems": problems, "evidence": evidence}))
