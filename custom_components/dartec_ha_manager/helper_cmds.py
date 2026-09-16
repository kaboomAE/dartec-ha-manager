"""Helper creation — the prerequisites a blueprint needs but cannot make.

A blueprint can require an `input_boolean` (the athan blueprint's "playing"
toggle is one) and a fresh home has none. Without this the whole pipeline
stops at a manual step in someone's browser, which is exactly the per-home
hand-work blueprints exist to remove.

Scope is deliberately tiny, because this is the one command here that creates
a *new object* in a customer's home rather than acting on one they already
have:

* one domain, `input_boolean` — a toggle, which executes nothing
* one namespace, `dartec_*` — we cannot touch or collide with a helper the
  customer made
* create-if-missing, never update, **never delete** — nothing in this file can
  remove something from a house

That is why it is not behind the maintenance window: the blast radius of a
wrongly-created toggle is a stray entity, and the registry commands that
create rooms and floors are treated the same way.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAINS = frozenset({"input_boolean"})
NAMESPACE = "dartec_"
_SLUG = re.compile(r"^dartec_[a-z0-9]+(?:_[a-z0-9]+)*$")


def slugify(name: str) -> str:
    """HA's entity-id slug for the names we actually use.

    Not a general reimplementation of `homeassistant.util.slugify` — it does
    not need to be, because we only ever pass names we chose ourselves, and
    `validate` below refuses anything where the two could disagree.
    """
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (name or "").lower())).strip("_")


def validate(domain: str, slug: str, name: str) -> str | None:
    """Refusal reason, or None. Pure, so CI can hold the namespace rule."""
    if domain not in DOMAINS:
        return f"unsupported helper domain '{domain}'"
    if not _SLUG.match(slug or ""):
        return (f"helper id must look like '{NAMESPACE}<name>' — refused "
                f"'{slug}'")
    if not (name or "").strip():
        return "helper name required"
    # The caller predicts the entity_id from the slug and uses it as a
    # blueprint input. If the name would not produce that slug, the prediction
    # is wrong and the automation would point at an entity that never exists.
    if slugify(name) != slug:
        return (f"name '{name}' would create '{slugify(name)}', not '{slug}' — "
                "refusing rather than creating an entity nobody is expecting")
    return None


async def helper_create(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Create one helper if it is missing. Returns its entity_id either way."""
    from .ws_bridge import call_own_ws

    domain = (cmd.get("domain") or "input_boolean").strip()
    slug = (cmd.get("slug") or "").strip()
    name = (cmd.get("name") or "").strip()
    refusal = validate(domain, slug, name)
    if refusal:
        return {"ok": False, "detail": refusal}

    entity_id = f"{domain}.{slug}"
    if hass.states.get(entity_id) is not None:
        return {"ok": True, "entity_id": entity_id, "created": False,
                "detail": f"{entity_id} already exists"}

    payload: dict[str, Any] = {"type": f"{domain}/create", "name": name}
    if cmd.get("icon"):
        payload["icon"] = cmd["icon"]
    msg = await call_own_ws(hass, payload, timeout=60)
    if not msg.get("success"):
        error = msg.get("error") or {}
        return {"ok": False,
                "detail": f"HA refused to create the helper: "
                          f"{error.get('message') or error.get('code') or error}"}

    # HA derives the entity_id from the name rather than letting us name it, so
    # confirm what actually appeared instead of assuming. A home that already
    # had a same-named entity would have produced `..._2`, and a blueprint
    # input pointing at the id we *expected* would silently never fire.
    if hass.states.get(entity_id) is None:
        actual = next((eid for eid in hass.states.async_entity_ids(domain)
                       if (hass.states.get(eid).attributes.get("friendly_name")
                           == name)), None)
        if actual is None:
            return {"ok": False,
                    "detail": f"created the helper but '{entity_id}' did not appear; "
                              "something else on this home may own that id"}
        return {"ok": True, "entity_id": actual, "created": True,
                "detail": f"created {actual} (not {entity_id} — that id was taken)"}

    return {"ok": True, "entity_id": entity_id, "created": True,
            "detail": f"created {entity_id}"}


HANDLERS = {"helper_create": helper_create}
