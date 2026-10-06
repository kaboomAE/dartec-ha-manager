"""Check the agent's formal Arabic state words reach the frontend (#55).

Runs inside the Home Assistant container against its loopback websocket and
asks exactly what the frontend asks on every page load:
`frontend/get_translations` for `entity_component`, in English and Arabic,
one integration at a time. Against `custom_components/dartec_ha_manager/
arabic_states.py` as installed in the container, it checks that:

- every key the table corrects still exists in Home Assistant's English with
  the English the table expects. A key HA renamed or reworded is skipped by
  the agent (it never adds one), so it would silently go back to English;
  this is where that shows;
- in Arabic, each of those keys reads Dartec's word, unless Home Assistant
  has its own Arabic for it, which the agent leaves alone;
- what Home Assistant already translates and the table does not list (the
  climate preset "Home", في المنزل) is untouched;
- English is untouched.

Usage: python3 arabic_states_check.py <access token>
Prints {"problems": [...], "evidence": {...}} as JSON.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys

import aiohttp

MODULE = "/config/custom_components/dartec_ha_manager/arabic_states.py"
# Translated by Home Assistant itself (seen in 2026.9.3 and 2026.10.0b1), and
# not in the table: must reach the frontend as HA has it.
HA_OWN = {
    "component.climate.entity_component._.state_attributes.preset_mode.state.home": "في المنزل",
    "component.binary_sensor.entity_component.presence.state.on": "في المنزل",
}


def load_table():
    spec = importlib.util.spec_from_file_location("arabic_states", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def main(token: str) -> None:
    table = load_table()
    problems: list[str] = []
    evidence: dict = {"dartec": 0, "ha_own_arabic": [], "renamed": []}
    async with aiohttp.ClientSession() as session, \
            session.ws_connect("http://127.0.0.1:8123/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        if (await ws.receive_json()).get("type") != "auth_ok":
            raise SystemExit("auth failed")
        next_id = 0

        async def resources(language: str, domain: str) -> dict:
            nonlocal next_id
            next_id += 1
            await ws.send_json({"id": next_id, "type": "frontend/get_translations",
                                "language": language, "category": "entity_component",
                                "integration": [domain]})
            reply = await ws.receive_json()
            if not reply.get("success"):
                raise SystemExit(f"get_translations {language} {domain}: {reply}")
            return reply["result"]["resources"]

        seen: dict[str, str] = {}
        for domain, entries in table.STATES.items():
            english = await resources("en", domain)
            arabic = await resources("ar", domain)
            seen.update(arabic)
            for suffix, (en_word, ar_word, *wrong) in entries.items():
                key = f"component.{domain}.entity_component.{suffix}"
                if key not in english or english[key] != en_word:
                    evidence["renamed"].append(key)
                    problems.append(f"{key}: Home Assistant's English is "
                                    f"{english.get(key)!r}, the table expects {en_word!r}")
                    continue
                if english[key] == ar_word:
                    problems.append(f"{key}: English was changed to the Arabic")
                got = arabic.get(key)
                if got == ar_word:
                    evidence["dartec"] += 1
                elif got != en_word and got not in wrong:
                    evidence["ha_own_arabic"].append({key: got})  # HA's own: left alone
                else:
                    problems.append(f"{key}: Arabic reads {got!r}, expected {ar_word!r}")
        for key, word in HA_OWN.items():
            if key in seen and seen[key] != word:
                problems.append(f"{key}: Home Assistant's own Arabic {word!r} was "
                                f"replaced with {seen[key]!r}")
    print(json.dumps({"problems": problems, "evidence": evidence}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
