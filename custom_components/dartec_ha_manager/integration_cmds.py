"""Setting up an integration a blueprint brought in, and checking it worked.

Installing an integration through HACS puts code on disk. It does not make
anything run: an integration's services come from a **config entry**, and
until one exists the integration is inert. The manager's Tier 2 pipeline used
to install and then restart, on the belief that the restart was what made
services exist. Tested against a real home, it was not — the restart did
nothing for a new install, and the only reason one integration ended up
configured in that test was that a probe happened to start its setup flow.

``integration_setup``   run the integration's own config flow with answers
                        the catalogue supplies; stop and name the fields if it
                        asks for anything else
``integration_status``  is the domain loaded, what entries does it have, what
                        services does it register — read-only, safe to poll

The flow goes through Home Assistant's own config-flow API, the same one the
UI uses, so validation, unique-id checks and "already configured" handling are
HA's rather than ours.

Guards enforced here rather than trusted from the cloud:

* **custom integrations only** — the ones HACS brought in. Setting up a
  built-in integration (a cloud link, a remote-access integration) with answers
  chosen by a server is a far larger power than a blueprint needs
* **answers are plain values** — no nested structures to smuggle through
* **no guessing** — a required field with no default and no supplied answer
  aborts the flow and reports the field, so an installer can decide
* **flows that need a human** — OAuth, external steps, menus — are abandoned
  and reported, never half-completed
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_DOMAIN = re.compile(r"^[a-z0-9_]+$")
MAX_STEPS = 6
# Aborts that mean "this is already done", not "this failed".
ALREADY_DONE = frozenset({"already_configured", "single_instance_allowed"})
_PRIMITIVES = (str, int, float, bool)


def valid_domain(domain: str) -> bool:
    return bool(_DOMAIN.match(domain or ""))


def validate_answers(answers: Any) -> str | None:
    """Refusal reason, or None. Plain values and flat lists of them only."""
    if answers is None:
        return None
    if not isinstance(answers, dict):
        return "answers must be an object"
    for key, value in answers.items():
        if not isinstance(key, str) or not key:
            return "answer names must be non-empty strings"
        if isinstance(value, list):
            if not all(isinstance(v, _PRIMITIVES) for v in value):
                return f"answer '{key}' may only list plain values"
        elif not isinstance(value, _PRIMITIVES):
            return f"answer '{key}' must be a plain value"
    return None


def plan_step(fields: list[dict], answers: dict) -> tuple[dict, list[str]]:
    """What to submit for one form step, and which required fields nobody
    answered.

    A field is only "missing" when it is required AND has no default AND was
    not supplied. Fields with a default are left out of the payload so Home
    Assistant applies its own default rather than one we invented.
    """
    payload, missing = {}, []
    for field in fields or []:
        name = field.get("name")
        if not name:
            continue
        if name in answers:
            payload[name] = answers[name]
        elif field.get("required") and "default" not in field:
            missing.append(name)
    return payload, missing


async def integration_status(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    domain = (cmd.get("domain") or "").strip()
    if not valid_domain(domain):
        return {"ok": False, "detail": f"invalid domain '{domain}'"}
    entries = [{"entry_id": e.entry_id, "title": e.title,
                "state": getattr(e.state, "value", str(e.state))}
               for e in hass.config_entries.async_entries(domain)]
    services = sorted((hass.services.async_services().get(domain) or {}).keys())
    loaded = domain in hass.config.components
    return {"ok": True, "domain": domain, "loaded": loaded, "entries": entries,
            "services": services,
            "detail": (f"{domain} loaded, {len(entries)} entr"
                       f"{'y' if len(entries) == 1 else 'ies'}, "
                       f"{len(services)} service(s)") if loaded
                      else f"{domain} is not loaded"}


async def integration_setup(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Create a config entry for one custom integration, if it has none."""
    from homeassistant.loader import async_get_integration

    from .ws_bridge import call_own_rest

    domain = (cmd.get("domain") or "").strip()
    if not valid_domain(domain):
        return {"ok": False, "detail": f"invalid domain '{domain}'"}
    answers = cmd.get("answers") or {}
    refusal = validate_answers(answers)
    if refusal:
        return {"ok": False, "detail": refusal}

    try:
        integration = await async_get_integration(hass, domain)
    except Exception:                                            # noqa: BLE001
        return {"ok": False,
                "detail": f"{domain} is not installed on this home, so there is "
                          "nothing to set up"}
    if integration.is_built_in:
        return {"ok": False, "refused": True,
                "detail": f"{domain} is a built-in integration; only integrations "
                          "a blueprint brought in through HACS may be set up remotely"}
    if not getattr(integration, "config_flow", False):
        return {"ok": False, "needs_installer": True,
                "detail": f"{domain} is configured in YAML, not through a config "
                          "flow — an installer has to set it up"}

    existing = hass.config_entries.async_entries(domain)
    if existing:
        return {"ok": True, "created": False, "domain": domain,
                "entry_id": existing[0].entry_id,
                "detail": f"{domain} is already set up"}

    started = await call_own_rest(hass, "POST", "/api/config/config_entries/flow",
                                  {"handler": domain, "show_advanced_options": False})
    if not started.get("ok"):
        return {"ok": False, "detail": f"could not start {domain}'s setup: "
                                       f"HTTP {started.get('status')} {started.get('body')}"}
    result = started.get("body") or {}

    async def _abandon(reason: str, **extra) -> dict:
        flow_id = result.get("flow_id")
        if flow_id:
            await call_own_rest(hass, "DELETE",
                                f"/api/config/config_entries/flow/{flow_id}")
        return {"ok": False, "domain": domain, "detail": reason, **extra}

    for _ in range(MAX_STEPS):
        kind = result.get("type")
        if kind == "create_entry":
            entry_id = (result.get("result") or {}).get("entry_id")
            return {"ok": True, "created": True, "domain": domain, "entry_id": entry_id,
                    "detail": f"set up {domain}"}
        if kind == "abort":
            reason = result.get("reason")
            if reason in ALREADY_DONE:
                return {"ok": True, "created": False, "domain": domain,
                        "detail": f"{domain} is already set up ({reason})"}
            return {"ok": False, "domain": domain,
                    "detail": f"{domain}'s setup aborted: {reason}"}
        if kind != "form":
            # progress (OAuth, device codes), external steps, menus: all need
            # a person, and a half-finished flow is worse than none.
            return await _abandon(
                f"{domain}'s setup needs a person to complete it ({kind}) — "
                "an installer has to finish it in Home Assistant",
                needs_installer=True)

        payload, missing = plan_step(result.get("data_schema") or [], answers)
        if missing:
            return await _abandon(
                f"{domain}'s setup asks for {', '.join(missing)}, which this "
                "blueprint does not supply — nothing was guessed",
                needs_input=missing, step_id=result.get("step_id"))
        if result.get("errors"):
            return await _abandon(f"{domain}'s setup rejected the answers: "
                                  f"{result.get('errors')}",
                                  errors=result.get("errors"))

        stepped = await call_own_rest(
            hass, "POST", f"/api/config/config_entries/flow/{result['flow_id']}", payload)
        if not stepped.get("ok"):
            return await _abandon(f"{domain}'s setup refused a step: HTTP "
                                  f"{stepped.get('status')} {stepped.get('body')}")
        result = stepped.get("body") or {}

    return await _abandon(f"{domain}'s setup did not finish within {MAX_STEPS} steps")


HANDLERS = {"integration_setup": integration_setup,
            "integration_status": integration_status}
