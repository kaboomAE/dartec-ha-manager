"""Automation blueprint staging.

A blueprint is the logic; an automation that references it supplies only the
entity ids. That split is why this exists: we can own, test and version one
YAML file instead of generating different automation code for every house.

Three commands, all through Home Assistant's own blueprint websocket API so
HA owns the file, its cache and the reload of anything using it:

``blueprint_install``   save (or override) a blueprint on this home
``blueprint_list``      what is staged here, and at which version
``blueprint_substitute`` render a blueprint against inputs WITHOUT creating
                        anything — the preflight

Two properties are enforced here rather than trusted from the cloud.

**The namespace.** Only ``dartec/*.yaml`` can be written. A customer's own
blueprints and the ones HA ships (``homeassistant/motion_light.yaml``) are
outside our reach entirely — not by policy on the server, which could be
compromised, but because this refuses the path.

**Staging a new blueprint is inert; overriding one is not.** A file nothing
references runs nothing, which is why ``blueprint_install`` can stage the
library unattended. Overriding a path that live automations use is different:
HA reloads every one of them the moment the file lands
(``blueprint/models.py::async_add_blueprint``), so the new logic is running in
someone's house immediately, with no restart. That is remote code deployment,
and ``service_policy.is_sensitive`` puts it behind the maintenance window.
``allow_override`` is self-limiting on top of that — with it false, HA itself
refuses to replace an existing blueprint.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # keeps this module importable without
    from homeassistant.core import HomeAssistant   # HA, like service_policy.py

_LOGGER = logging.getLogger(__name__)

# Blueprints exist for scripts too, but nothing plans to ship one yet and an
# unused domain is surface we would have to reason about.
DOMAINS = frozenset({"automation"})

NAMESPACE = "dartec"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def normalise_path(path: str) -> str | None:
    """``dartec/athan.yaml`` → itself. Anything else → ``None``.

    Deliberately a whitelist of one shape rather than a blacklist of traversal
    tricks: ``..`` is only the obvious attack, and a path that merely *looks*
    fine ("homeassistant/motion_light.yaml") is the one that would quietly
    overwrite something we do not own.
    """
    path = (path or "").strip().strip("/")
    if not path.endswith(".yaml"):
        return None
    parts = path.split("/")
    if len(parts) != 2 or parts[0] != NAMESPACE:
        return None
    if not _NAME.match(parts[1][: -len(".yaml")]):
        return None
    return path


def flatten_inputs(block: Any) -> dict[str, dict]:
    """A blueprint's `input:` block → {input name: {"has_default": bool}}.

    Blueprints may group inputs into collapsible sections, where a key holds
    `{name, input: {...}}` rather than an input itself. The manager compares
    these names across versions to refuse a breaking upgrade, so a section must
    not be mistaken for an input called "advanced_settings".

    Mirrored on the server in automation_blueprints.blueprint_inputs, which
    parses the new version's YAML; the two must agree on what an input is.
    """
    found: dict[str, dict] = {}
    for key, spec in (block or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        if isinstance(spec.get("input"), dict):
            found.update(flatten_inputs(spec["input"]))
        else:
            found[str(key)] = {"has_default": "default" in spec}
    return found


_AUTOMATION_ID = re.compile(r"^automation\.[a-z0-9_]+$")


def validate_automation_ids(ids: Any) -> str | None:
    """Refusal reason, or None. Only automation entity ids — this is a read of
    their states, and there is no reason for it to accept anything else."""
    if not isinstance(ids, list):
        return "entity_ids must be a list"
    if len(ids) > 500:
        return "too many entity_ids"
    bad = [i for i in ids if not (isinstance(i, str) and _AUTOMATION_ID.match(i))]
    return f"not automation entity ids: {bad[:5]}" if bad else None


def _domain(cmd: dict[str, Any]) -> str | None:
    domain = (cmd.get("domain") or "automation").strip()
    return domain if domain in DOMAINS else None


def _ws_error(msg: dict) -> str:
    error = msg.get("error") or {}
    return str(error.get("message") or error.get("code") or error or "unknown error")


async def blueprint_install(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Stage one blueprint. Idempotent only in the sense that HA decides: with
    ``allow_override`` false an existing path is refused, not silently kept."""
    from .ws_bridge import call_own_ws

    domain = _domain(cmd)
    if domain is None:
        return {"ok": False, "detail": f"unsupported blueprint domain "
                                       f"'{cmd.get('domain')}'"}
    path = normalise_path(cmd.get("path") or "")
    if path is None:
        return {"ok": False,
                "detail": f"path must be '{NAMESPACE}/<name>.yaml' — refused "
                          f"'{cmd.get('path')}'"}
    content = cmd.get("yaml")
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "detail": "yaml (string) required"}

    payload: dict[str, Any] = {"type": "blueprint/save", "domain": domain,
                               "path": path, "yaml": content,
                               "allow_override": bool(cmd.get("allow_override"))}
    # The version stamp. HA persists this into the saved file and hands it back
    # from blueprint/list, so a home reports which tag it is running without
    # the manager keeping a second set of books that can disagree.
    if cmd.get("source_url"):
        payload["source_url"] = cmd["source_url"]

    msg = await call_own_ws(hass, payload, timeout=60)
    if not msg.get("success"):
        return {"ok": False, "detail": f"HA refused the blueprint: {_ws_error(msg)}"}

    overrode = bool((msg.get("result") or {}).get("overrides_existing"))
    return {"ok": True, "path": path, "domain": domain, "overrides_existing": overrode,
            "source_url": cmd.get("source_url"),
            "detail": (f"replaced {domain} blueprint {path} — automations using it "
                       "have been reloaded" if overrode
                       else f"staged {domain} blueprint {path}")}


async def blueprint_list(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Every blueprint on this home, ours flagged and versioned.

    Reports all of them, not just ``dartec/``: a customer's own blueprint is
    not ours to touch, but knowing it is there is how "why did this home
    behave differently" gets answered.
    """
    from .ws_bridge import call_own_ws

    domain = _domain(cmd)
    if domain is None:
        return {"ok": False, "detail": f"unsupported blueprint domain "
                                       f"'{cmd.get('domain')}'"}

    msg = await call_own_ws(hass, {"type": "blueprint/list", "domain": domain})
    if not msg.get("success"):
        return {"ok": False, "detail": f"could not list blueprints: {_ws_error(msg)}"}

    blueprints = []
    for path, entry in (msg.get("result") or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        metadata = entry.get("metadata") or {}
        blueprints.append({
            "path": path,
            "domain": domain,
            "name": metadata.get("name"),
            # Present only on blueprints saved with one; that is the tag for
            # ours and usually the upstream repo for a customer's.
            "source_url": metadata.get("source_url"),
            "dartec": path.startswith(f"{NAMESPACE}/"),
            # What the staged version accepts, so the manager can tell whether
            # overriding it with a new version would break the automations
            # already using it — before it does so, not after.
            "inputs": flatten_inputs(metadata.get("input")),
            # A blueprint HA could not parse still occupies its path, and an
            # automation pointing at it is already broken. Surface it.
            "error": entry.get("error"),
        })
    ours = sum(1 for b in blueprints if b["dartec"])
    return {"ok": True, "blueprints": blueprints,
            "detail": f"{len(blueprints)} {domain} blueprint(s), {ours} from Dartec"}


async def blueprint_substitute(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Render a staged blueprint against inputs and return the config HA would
    run — creating nothing.

    This is the preflight, and it is worth more than any validation we could
    write on the server: it is HA's own substitution, on this home, against the
    file actually staged here. An input naming an entity this house does not
    have fails here, in front of the installer, instead of becoming an
    automation that never fires and that nobody looks at again.
    """
    from .ws_bridge import call_own_ws

    domain = _domain(cmd)
    if domain is None:
        return {"ok": False, "detail": f"unsupported blueprint domain "
                                       f"'{cmd.get('domain')}'"}
    path = normalise_path(cmd.get("path") or "")
    if path is None:
        return {"ok": False, "detail": f"path must be '{NAMESPACE}/<name>.yaml' — "
                                       f"refused '{cmd.get('path')}'"}
    inputs = cmd.get("input")
    if not isinstance(inputs, dict):
        return {"ok": False, "detail": "input (object) required"}

    msg = await call_own_ws(hass, {"type": "blueprint/substitute", "domain": domain,
                                   "path": path, "input": inputs}, timeout=60)
    if not msg.get("success"):
        return {"ok": False, "detail": f"these inputs do not render on this home: "
                                       f"{_ws_error(msg)}"}

    config = (msg.get("result") or {}).get("substituted_config")
    return {"ok": True, "path": path, "config": config,
            "detail": f"{path} renders on this home"}


async def blueprint_consumers(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """The automations using one blueprint, and whether each actually loaded.

    This is how an upgrade is checked. Overriding a blueprint reloads every
    automation on it at once, and an automation whose blueprint no longer
    validates does not error anywhere visible — its entity simply goes
    `unavailable`. Asked straight after an upgrade, this is the difference
    between "pushed" and "pushed and still working".

    Uses Home Assistant's own `automations_with_blueprint`, the helper the
    blueprint component itself uses to find what to reload, so "uses this
    blueprint" means exactly what HA means by it.
    """
    from homeassistant.components.automation import automations_with_blueprint

    path = normalise_path(cmd.get("path") or "")
    if path is None:
        return {"ok": False, "detail": f"path must be '{NAMESPACE}/<name>.yaml' — "
                                       f"refused '{cmd.get('path')}'"}
    expected = cmd.get("entity_ids") or []
    refusal = validate_automation_ids(expected)
    if refusal:
        return {"ok": False, "detail": refusal}

    # The union, not HA's list alone. Found live: once an automation fails to
    # reload, automations_with_blueprint stops listing it — a broken automation
    # no longer "uses" the blueprint as far as HA is concerned. Asked only that,
    # an upgrade that broke every automation on it reported "0 automations
    # reloaded and running", i.e. success. So the caller passes the ids it saw
    # BEFORE the upgrade, and their states are read directly.
    consumers = []
    for entity_id in sorted(set(automations_with_blueprint(hass, path)) | set(expected)):
        state = hass.states.get(entity_id)
        consumers.append({"entity_id": entity_id,
                          "state": state.state if state else "missing"})
    broken = [c for c in consumers if c["state"] in ("unavailable", "missing")]
    return {"ok": True, "path": path, "automations": consumers,
            "broken": [c["entity_id"] for c in broken],
            "detail": f"{len(consumers)} automation(s) use {path}"
                      + (f", {len(broken)} not loaded" if broken else "")}


HANDLERS = {"blueprint_install": blueprint_install,
            "blueprint_list": blueprint_list,
            "blueprint_substitute": blueprint_substitute,
            "blueprint_consumers": blueprint_consumers}
