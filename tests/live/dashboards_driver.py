"""Builds the dashboard test villa inside a live Home Assistant and applies the
dashboards to it through the agent.

Runs in the container (it needs aiohttp and PyYAML, which only the container
has), started by `run_live_dashboards.py`:

    python3 /dashboards_driver.py build <owner token>
    python3 /dashboards_driver.py language <owner token> ar|en

`build` prints one JSON object: the problems found, the evidence, and the
dashboards applied with their views, which the runner then opens in a
browser.

**The villa** is the one the dashboards guide was researched on
(`docs/dashboards/prototypes/README.md`), built the same way the bench build
was (`bench-log.jsonl`, and the owner's `setup_villa.py`): only Home
Assistant's own UI helpers, through its own config flows and websocket
commands. Two floors, seven Arabic-named rooms, six ACs (generic thermostats
with Home, Sleep and Away presets), dimmable and on/off lights, curtains, a
leak sensor, a 4-channel relay and a dual wall switch under their raw names,
a kids' room that is offline while a helper is off, and a guest suite with 26
circuits. Nothing is installed and no YAML is written, so it builds on any
Home Assistant the agent is tested on, and the entity ids come out as the
prototypes name them.

**The dashboards** are the prototypes' YAML, applied as the manager applies a
dashboard: `lovelace_create` sent through the stub manager's outbox to the
agent, with the config. Then every entity a dashboard names must exist in
Home Assistant's state machine, and every action it performs must be a
service Home Assistant has: a missing entity is otherwise a silent blank
card on a family's wall.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import aiohttp
import yaml

BASE = "http://127.0.0.1:8123"
WS_URL = "ws://127.0.0.1:8123/api/websocket"
STATE = Path(os.environ.get("DARTEC_LIVE_STATE", "/tmp/dartec-live"))
PROTOTYPES = Path("/prototypes")
LABEL = "Dartec dashboard test"

# (url_path, file stem): the prototypes README's own mapping, in both languages.
DASHBOARDS = [(f"dartec-proto-{key}-{lang}", f"{stem}.{lang}.yaml", lang)
              for lang in ("en", "ar")
              for key, stem in (("current", "today-generator"), ("home", "home-overview"),
                                ("room", "room-majlis"), ("panel", "panel-master-bedroom"))]

ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

problems: list[str] = []
evidence: dict = {}


def check(ok: bool, message: str) -> None:
    if not ok:
        problems.append(message)


# ── The manager's side: the stub manager's outbox ──────────────────────────

_sent = 0


async def agent(command: dict, timeout: float = 60) -> dict:
    global _sent
    _sent += 1
    cmd_id = f"d{_sent:03d}-{command['action']}"
    tmp = STATE / f".q-{cmd_id}"
    tmp.write_text(json.dumps({"id": cmd_id, **command}))
    tmp.replace(STATE / f"send-{cmd_id}.json")
    result_file = STATE / f"result-{cmd_id}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result_file.exists():
            try:
                return json.loads(result_file.read_text())
            except ValueError:
                pass
        await asyncio.sleep(0.2)
    problems.append(f"no answer to {cmd_id} within {timeout:.0f}s")
    return {}


# ── Home Assistant's side ───────────────────────────────────────────────────

class Home:
    def __init__(self, session, token):
        self.session, self.token, self.ws, self.next_id = session, token, None, 1
        self.created = {"entities": {}, "entries": 0}

    async def open(self) -> None:
        self.ws = await self.session.ws_connect(WS_URL, max_msg_size=64 * 2**20)
        await self.ws.receive_json()
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        assert (await self.ws.receive_json()).get("type") == "auth_ok"

    async def call(self, payload: dict) -> dict:
        msg_id, self.next_id = self.next_id, self.next_id + 1
        await self.ws.send_json({"id": msg_id, **payload})
        while True:
            msg = await self.ws.receive_json()
            if msg.get("id") == msg_id and msg.get("type") == "result":
                return msg

    async def ok(self, payload: dict):
        msg = await self.call(payload)
        if not msg.get("success"):
            raise RuntimeError(f"{payload.get('type')}: {msg.get('error')}")
        return msg.get("result")

    async def rest(self, method: str, path: str, body=None):
        async with self.session.request(method, BASE + path, json=body, headers={
                "Authorization": f"Bearer {self.token}"}) as resp:
            return await resp.json() if resp.content_type == "application/json" else {}

    async def flow(self, handler: str, data: dict, menu_step: str | None = None,
                   presets: dict | None = None) -> str:
        """One helper through Home Assistant's own config flow, as the UI
        makes it. Returns the new entry's id."""
        started = await self.rest("POST", "/api/config/config_entries/flow",
                                  {"handler": handler, "show_advanced_options": True})
        flow_id = started["flow_id"]
        if menu_step:
            await self.rest("POST", f"/api/config/config_entries/flow/{flow_id}",
                            {"next_step_id": menu_step})
        result = await self.rest("POST", f"/api/config/config_entries/flow/{flow_id}", data)
        if result.get("type") == "form" and result.get("step_id") == "presets":
            result = await self.rest("POST", f"/api/config/config_entries/flow/{flow_id}",
                                     presets or {})
        if result.get("type") != "create_entry":
            raise RuntimeError(f"{handler}/{menu_step} {data.get('name')}: {result}")
        self.created["entries"] += 1
        return result["result"]["entry_id"]

    async def entities_of(self, entry_id: str) -> list[dict]:
        for _ in range(30):
            found = [e for e in await self.ok({"type": "config/entity_registry/list"})
                     if e.get("config_entry_id") == entry_id]
            if found:
                return found
            await asyncio.sleep(0.3)
        raise RuntimeError(f"entry {entry_id} made no entity")

    async def place(self, entry_id: str, area: str | None, label: str, *, hidden=False,
                    key: str | None = None) -> str:
        placed = []
        for entity in await self.entities_of(entry_id):
            update = {"type": "config/entity_registry/update",
                      "entity_id": entity["entity_id"], "labels": [label]}
            if area:
                update["area_id"] = area
            if hidden:
                update["hidden_by"] = "user"
            await self.ok(update)
            placed.append(entity["entity_id"])
        if key:
            self.created["entities"][key] = placed[0]
        return placed[0]


def act(service: str, entity: str, value) -> list[dict]:
    return [{"action": service, "target": {"entity_id": entity}, "data": {"value": value}}]


async def build_villa(home: Home) -> dict:
    """The test villa, as setup_villa.py builds it on the bench."""
    label = (await home.ok({"type": "config/label_registry/create", "name": LABEL,
                            "icon": "mdi:test-tube", "color": "orange"}))["label_id"]
    floors = {}
    for key, name, level, icon in (("g", "الطابق الأرضي", 0, "mdi:home-floor-g"),
                                   ("f", "الطابق الأول", 1, "mdi:home-floor-1")):
        floors[key] = (await home.ok({"type": "config/floor_registry/create", "name": name,
                                      "level": level, "icon": icon}))["floor_id"]
    areas = {}
    for key, name, floor, icon in (
            ("majlis", "المجلس", "g", "mdi:sofa"),
            ("hall", "الصالة العائلية", "g", "mdi:television"),
            ("kitchen", "المطبخ", "g", "mdi:stove"),
            ("maid", "غرفة الخادمة", "g", "mdi:bed-single"),
            ("master", "غرفة النوم الرئيسية", "f", "mdi:bed-king"),
            ("kids", "غرفة الأطفال", "f", "mdi:teddy-bear"),
            ("suite", "جناح الضيوف الشرقي مع الحمام وغرفة الملابس", "f", "mdi:bed-queen")):
        areas[key] = (await home.ok({"type": "config/area_registry/create", "name": name,
                                     "floor_id": floors[floor], "icon": icon,
                                     "labels": [label]}))["area_id"]

    async def number(name: str, top: int) -> str:
        made = await home.ok({"type": "input_number/create", "name": name, "min": 0,
                              "max": top, "step": 1, "mode": "slider",
                              "icon": "mdi:test-tube"})
        entity_id = "input_number." + made["id"]
        await home.ok({"type": "config/entity_registry/update", "entity_id": entity_id,
                       "hidden_by": "user", "labels": [label]})
        return entity_id

    online = await home.ok({"type": "input_boolean/create", "name": "Test kids room online",
                            "icon": "mdi:lan-disconnect"})
    online_id = "input_boolean." + online["id"]

    async def tswitch(name, area, hidden=False, key=None):
        entry = await home.flow("template", {"name": name}, "switch")
        return await home.place(entry, area, label, hidden=hidden, key=key)

    async def sensor(name, area, value, unit, device_class, key=None, avail=None):
        data = {"name": name, "state": str(value), "unit_of_measurement": unit,
                "device_class": device_class, "state_class": "measurement"}
        if avail:
            data["additional_options"] = {"availability": avail}
        entry = await home.flow("template", data, "sensor")
        return await home.place(entry, area, label, key=key)

    async def ac(room, name, temp, hum=None):
        target = await sensor(f"{name} temperature", areas[room], temp, "°C", "temperature",
                              key=f"{room}_temp")
        humid = (await sensor(f"{name} humidity", areas[room], hum, "%", "humidity",
                              key=f"{room}_hum")) if hum else None
        compressor = await tswitch(f"{name} AC compressor", areas[room], hidden=True)
        entry = await home.flow("generic_thermostat", {
            "name": f"{name} AC", "ac_mode": True, "target_sensor": target,
            "heater": compressor, "cold_tolerance": 0.3, "hot_tolerance": 0.3,
            "min_temp": 16, "max_temp": 30},
            presets={"away_temp": 28, "home_temp": 24, "sleep_temp": 23})
        climate = await home.place(entry, areas[room], label, key=f"{room}_ac")
        await home.ok({"type": "config/area_registry/update", "area_id": areas[room],
                       "temperature_entity_id": target,
                       **({"humidity_entity_id": humid} if humid else {})})
        return climate

    async def light(name, room, key):
        switch = await tswitch(name, areas[room])
        entry = await home.flow("switch_as_x", {"entity_id": switch, "target_domain": "light"})
        return await home.place(entry, areas[room], label, key=key)

    async def dimmer(name, room, key, avail=None):
        level = await number(f"{name} level", 255)
        data = {"name": name, "state": f"{{{{ states('{level}')|int(0) > 0 }}}}",
                "level": f"{{{{ states('{level}')|int(0) }}}}",
                "turn_on": act("input_number.set_value", level, 200),
                "turn_off": act("input_number.set_value", level, 0),
                "set_level": act("input_number.set_value", level, "{{ brightness }}")}
        if avail:
            data["additional_options"] = {"availability": avail}
        entry = await home.flow("template", data, "light")
        return await home.place(entry, areas[room], label, key=key)

    async def cover(name, room, device_class, key):
        position = await number(f"{name} position", 100)
        entry = await home.flow("template", {
            "name": name, "device_class": device_class,
            "state": f"{{{{ 'open' if states('{position}')|int(0) > 0 else 'closed' }}}}",
            "position": f"{{{{ states('{position}')|int(0) }}}}",
            "open_cover": act("input_number.set_value", position, 100),
            "close_cover": act("input_number.set_value", position, 0),
            "set_cover_position": act("input_number.set_value", position, "{{ position }}")},
            "cover")
        return await home.place(entry, areas[room], label, key=key)

    await ac("majlis", "Majlis", 23.5, 48)
    for i, name in enumerate(["Majlis chandelier", "Majlis downlights",
                              "إضاءة السقف المخفية حول المجلس الرئيسي",
                              "Majlis wall washers"]):
        await light(name, "majlis", f"majlis_light_{i}")
    await dimmer("Majlis reading lamp", "majlis", "majlis_dimmer")
    await cover("Majlis curtains", "majlis", "curtain", "majlis_curtain")
    for i in range(1, 5):
        await tswitch(f"SONOFF 4CHPROR3 Switch {i}", areas["majlis"], key=f"relay_{i}")
    await tswitch("Aqara Wall Switch H1 (Dual) Left", areas["majlis"], key="aqara_l")
    await tswitch("Aqara Wall Switch H1 (Dual) Right", areas["majlis"], key="aqara_r")

    await ac("hall", "Family hall", 24.0, 52)
    await light("Family hall ceiling", "hall", "hall_light_0")
    await light("Family hall spots", "hall", "hall_light_1")
    await cover("Family hall blinds", "hall", "blind", "hall_blind")

    await light("Kitchen ceiling", "kitchen", "kitchen_light_0")
    await light("Kitchen under-cabinet", "kitchen", "kitchen_light_1")
    entry = await home.flow("template", {"name": "Kitchen water leak", "state": "off",
                                         "device_class": "moisture"}, "binary_sensor")
    await home.place(entry, areas["kitchen"], label, key="kitchen_leak")

    await ac("maid", "Maid's room", 25.5)
    await light("Maid's room light", "maid", "maid_light")

    await ac("master", "Master bedroom", 22.0, 45)
    await dimmer("Master bedroom ceiling", "master", "master_dimmer")
    await light("Bedside left", "master", "master_light_l")
    await light("Bedside right", "master", "master_light_r")
    await cover("Master bedroom blackout", "master", "curtain", "master_blackout")

    # Offline while the helper is off, which it is from the start.
    avail = f"{{{{ is_state('{online_id}', 'on') }}}}"
    await ac("kids", "Kids' room", 24.5)
    await dimmer("Kids' room light", "kids", "kids_light", avail=avail)
    await sensor("Kids' room air quality", areas["kids"], 12, "μg/m³", "pm25",
                 "kids_pm25", avail=avail)

    await ac("suite", "East guest suite", 24.0, 50)
    circuits = ["ceiling downlights near the window", "ceiling downlights near the door",
                "cove lighting", "wardrobe lights", "dressing room mirror",
                "bathroom ceiling", "bathroom mirror", "bathroom exhaust fan",
                "shower niche", "toilet ceiling", "bedside reading left",
                "bedside reading right", "floor washers", "TV wall backlight",
                "entrance lobby", "balcony wall lights", "balcony floor lights",
                "desk lamp socket", "coffee station socket", "hair dryer socket",
                "towel warmer", "water heater", "curtain motor power",
                "night light", "wardrobe dehumidifier", "spare circuit"]
    for i, name in enumerate(circuits, 1):
        await tswitch(f"East guest suite circuit {i:02d} ({name})", areas["suite"],
                      key=f"suite_{i:02d}")
    home.created["label"] = label
    return home.created


async def set_states(home: Home, made: dict) -> None:
    """Realistic states, as states.py set them on the bench: ACs cooling,
    some lights on, a curtain half open."""
    entities = made["entities"]

    def call(domain, service, data):
        return home.ok({"type": "call_service", "domain": domain, "service": service,
                        "service_data": data})

    # Everything off first, so no template switch is left "unknown".
    registry = await home.ok({"type": "config/entity_registry/list"})
    plain = [e["entity_id"] for e in registry
             if made["label"] in (e.get("labels") or []) and not e.get("hidden_by")
             and e["entity_id"].split(".")[0] in ("switch", "light")]
    await call("homeassistant", "turn_off", {"entity_id": plain})
    await call("climate", "set_hvac_mode", {
        "entity_id": [entities[k] for k in ("majlis_ac", "hall_ac", "master_ac", "suite_ac",
                                            "maid_ac")], "hvac_mode": "cool"})
    await call("climate", "set_temperature", {"entity_id": entities["majlis_ac"],
                                              "temperature": 22})
    await call("climate", "set_temperature", {"entity_id": entities["hall_ac"],
                                              "temperature": 24})
    await call("climate", "set_preset_mode", {"entity_id": entities["master_ac"],
                                              "preset_mode": "sleep"})
    await call("light", "turn_on", {"entity_id": [entities[k] for k in (
        "majlis_light_0", "majlis_light_1", "hall_light_0", "master_light_l",
        "kitchen_light_1")]})
    await call("light", "turn_on", {"entity_id": entities["majlis_dimmer"], "brightness": 150})
    await call("cover", "set_cover_position", {"entity_id": entities["majlis_curtain"],
                                               "position": 50})
    await call("cover", "open_cover", {"entity_id": entities["hall_blind"]})
    await call("switch", "turn_on", {"entity_id": [entities[k] for k in (
        "relay_1", "relay_3", "aqara_l", "suite_01", "suite_03", "suite_06", "suite_11",
        "suite_14")]})


# ── What a dashboard names ──────────────────────────────────────────────────

def references(node, entities: set, actions: set) -> None:
    """Every entity id and every action a dashboard config names, wherever
    Lovelace allows one: cards, badges, features, visibility conditions,
    tap actions, and `scene.apply`'s entity map."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("entity", "entity_id") and isinstance(value, str):
                entities.add(value)
            elif key in ("entity", "entity_id") and isinstance(value, list):
                entities.update(v for v in value if isinstance(v, str))
            elif key == "entities" and isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        entities.add(item)
            elif key == "entities" and isinstance(value, dict):
                entities.update(value)
            elif key in ("perform_action", "service") and isinstance(value, str):
                actions.add(value)
            references(value, entities, actions)
    elif isinstance(node, list):
        for item in node:
            references(item, entities, actions)


def views_of(config: dict) -> list[str]:
    return [view.get("path") or str(i) for i, view in enumerate(config.get("views") or [])]


async def build(owner_token: str) -> dict:
    async with aiohttp.ClientSession() as session:
        home = Home(session, owner_token)
        await home.open()
        started = time.monotonic()
        made = await build_villa(home)
        await set_states(home, made)
        evidence["villa"] = {"entries": made["entries"], "entities": made["entities"],
                             "seconds": round(time.monotonic() - started)}

        states = {s["entity_id"]: s for s in await home.ok({"type": "get_states"})}
        services = await home.ok({"type": "get_services"})
        for key, entity_id in made["entities"].items():
            check(entity_id in states, f"the villa's {key} ({entity_id}) has no state")
        # The offline room is part of the test: it must be offline.
        check(states.get(made["entities"].get("kids_light"), {}).get("state") == "unavailable",
              "the kids' room light is not offline")

        applied = []
        for url_path, filename, lang in DASHBOARDS:
            config = yaml.safe_load((PROTOTYPES / filename).read_text(encoding="utf-8"))
            answer = await agent({"action": "lovelace_create", "url_path": url_path,
                                  "title": config.get("title") or url_path,
                                  "icon": "mdi:test-tube", "show_in_sidebar": False,
                                  "config": config})
            check(answer.get("ok") is True and "initial config" in (answer.get("detail") or "")
                  and "failed" not in (answer.get("detail") or ""),
                  f"lovelace_create {url_path}: {answer}")
            stored = await home.ok({"type": "lovelace/config", "url_path": url_path})
            check(stored == config, f"{url_path}: Home Assistant does not hold the config sent")

            entities, actions = set(), set()
            references(config, entities, actions)
            missing = sorted(e for e in entities if ENTITY_ID.match(e) and e not in states)
            malformed = sorted(e for e in entities if not ENTITY_ID.match(e))
            no_service = sorted(a for a in actions
                                if a.partition(".")[2] not in services.get(a.partition(".")[0], {}))
            check(not missing, f"{filename}: entities Home Assistant does not have: {missing}")
            check(not malformed, f"{filename}: not entity ids: {malformed}")
            check(not no_service, f"{filename}: actions Home Assistant does not have: "
                                  f"{no_service}")
            applied.append({"url_path": url_path, "file": filename, "language": lang,
                            "title": config.get("title"), "views": views_of(config),
                            "entities": len(entities), "actions": sorted(actions)})
        await home.ws.close()
    return {"problems": problems, "evidence": evidence, "dashboards": applied}


async def set_language(owner_token: str, language: str) -> dict:
    """The owner's own language, the way the bench switched to Arabic for
    its screenshots (its profile's setting, the whole locale)."""
    async with aiohttp.ClientSession() as session:
        home = Home(session, owner_token)
        await home.open()
        await home.ok({"type": "frontend/set_user_data", "key": "language", "value": {
            "language": language, "number_format": "language", "time_format": "language",
            "date_format": "language", "time_zone": "local", "first_weekday": "language"}})
        await home.ws.close()
    return {"problems": [], "language": language}


if __name__ == "__main__":
    mode, token = sys.argv[1], sys.argv[2]
    if mode == "build":
        try:
            out = asyncio.run(build(token))
        except Exception as err:  # noqa: BLE001 - reported, not raised, so the runner sees it
            out = {"problems": problems + [f"{type(err).__name__}: {err}"],
                   "evidence": evidence, "dashboards": []}
    else:
        out = asyncio.run(set_language(token, sys.argv[3]))
    print(json.dumps(out, ensure_ascii=False, default=str))
