"""What the cloud is allowed to do inside this home.

The guardrail this module replaces checked the *action name* only. Since
``call_service`` was one of the permitted action names, and its ``domain`` and
``service`` came straight off the wire, the cloud could invoke anything
registered in this instance — unlock the front door, disarm the alarm, reboot
the host, or run a ``shell_command``. The allowlist stopped at the door and
waved everything through it.

So the unit of permission here is the ``domain.service`` pair, not the action,
and the default is deny. Three tiers:

* **NEVER** — unbounded by construction (arbitrary shell/Python) or
  unrecoverable from the cloud (host shutdown, ``homeassistant.stop``: no
  remote path back). Not reachable through any consent flow. Deliberately not
  overridable, because a "temporarily allow arbitrary code" switch is just
  arbitrary code with extra steps.
* **SENSITIVE** — legitimate for support work but consequential in a home:
  locks, covers, alarms, climate, reboots, history deletion, notifications.
  Requires an open maintenance window, which only the homeowner can grant (see
  ``maintenance.py``). ``SENSITIVE_ACTIONS`` extends the same gate to
  non-service commands of equal weight — HA accounts, code entering the home,
  and backups leaving it.
* **ROUTINE** — reversible commissioning and diagnostic calls. No window
  needed; still logged to the homeowner's own logbook.

Anything absent from all three is denied. Adding a capability is therefore a
deliberate edit here rather than a side effect of the cloud learning a new
trick.
"""
from __future__ import annotations

from typing import Any, Literal

Tier = Literal["never", "routine", "sensitive", "unlisted"]

# Whole domains that must never be callable, whatever the service name.
NEVER_DOMAINS = frozenset({
    "shell_command",   # arbitrary shell, by design
    "python_script",   # arbitrary Python inside the HA process
})

# Individual services that must never be callable.
NEVER_SERVICES = frozenset({
    "hassio.addon_stdin",     # writes to an add-on's stdin == arbitrary execution
    "hassio.host_shutdown",   # powers the house off; nothing can turn it back on
    "homeassistant.stop",     # same problem — no remote route back
})

# Reversible, low-consequence, needed for commissioning and diagnostics.
ROUTINE_SERVICES = frozenset({
    "homeassistant.update_entity",
    "homeassistant.reload_config_entry",
    "light.turn_on", "light.turn_off", "light.toggle",
    "switch.turn_on", "switch.turn_off", "switch.toggle",
    "fan.turn_on", "fan.turn_off",
    "scene.turn_on", "scene.reload",
    "script.reload",
    "automation.reload", "automation.turn_on", "automation.turn_off",
    "backup.create",
    "input_boolean.turn_on", "input_boolean.turn_off",
    "persistent_notification.create", "persistent_notification.dismiss",
})

# Consequential but legitimate during a supervised support session.
SENSITIVE_SERVICES = frozenset({
    "homeassistant.restart",
    "hassio.host_reboot",
    "hassio.addon_restart", "hassio.addon_start", "hassio.addon_stop",
    "recorder.purge", "recorder.purge_entities",
})

# Domains where every service is consequential enough to need a window.
SENSITIVE_DOMAINS = frozenset({
    "lock",
    "cover",
    "alarm_control_panel",
    "climate",
    "water_heater",
    "notify",
    "vacuum",
})

# Services that legitimately act on no particular entity. Everything else must
# name its target, so a stray call cannot sweep the whole house.
NO_TARGET_SERVICES = frozenset({
    "homeassistant.restart",
    "hassio.host_reboot",
    "hassio.addon_restart", "hassio.addon_start", "hassio.addon_stop",
    "backup.create",
    "recorder.purge", "recorder.purge_entities",
    "scene.reload", "script.reload", "automation.reload",
    "persistent_notification.create", "persistent_notification.dismiss",
})

# Non-``call_service`` actions that are every bit as powerful as a sensitive
# service call and are gated the same way. Creating an HA account or changing
# a password hands over standing access, so it belongs behind the window
# rather than in the cloud's unattended reach.
SENSITIVE_ACTIONS = frozenset({
    # Standing access to the home.
    "user_create", "user_update", "user_set_password", "user_delete",
    # Code entering the home. `agent_update` replaces this integration and
    # `hacs_install` adds a third-party one, so both are remote code
    # deployment however routine they feel.
    "agent_update", "hacs_install", "ha_restart",
    # `automation_create` is here specifically because an automation is a
    # stored service call: without this gate the cloud could write an
    # automation whose action is `shell_command.*` and let a trigger run it,
    # walking straight around the allowlist above.
    "automation_create",
    # Reachability, data leaving the house, and data destroyed in it.
    "tunnel_setup", "tunnel_stop",
    "backup_upload", "backup_delete",
    # Add-ons are services in their own right.
    "addon_restart", "addon_start", "addon_stop",
})


def classify(domain: str, service: str) -> Tier:
    """Tier for one ``domain.service`` pair. Unknown pairs are ``unlisted``,
    which callers must treat as a denial."""
    if not domain or not service:
        return "never"
    pair = f"{domain}.{service}"
    if domain in NEVER_DOMAINS or pair in NEVER_SERVICES:
        return "never"
    if pair in ROUTINE_SERVICES:
        return "routine"
    if pair in SENSITIVE_SERVICES or domain in SENSITIVE_DOMAINS:
        return "sensitive"
    return "unlisted"


def _targets(service_data: dict[str, Any]) -> list[str]:
    """Every entity_id named in the payload, including inside a ``target``
    block, flattened to a list of strings."""
    found: list[str] = []
    for holder in (service_data, service_data.get("target") or {}):
        if not isinstance(holder, dict):
            continue
        value = holder.get("entity_id")
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, (list, tuple)):
            found.extend(str(v) for v in value)
    return found


def validate_target(domain: str, service: str,
                    service_data: dict[str, Any]) -> str | None:
    """Reject house-wide targeting. Returns an error string, or None if fine.

    ``entity_id: all`` is Home Assistant's documented wildcard, and an absent
    target means the same thing for most services — either would turn a single
    mistaken command into every light, lock or cover at once.
    """
    pair = f"{domain}.{service}"
    if pair in NO_TARGET_SERVICES:
        return None
    entities = _targets(service_data)
    if not entities:
        return f"{pair} must name an entity_id (house-wide calls are refused)"
    if any(e.strip().lower() == "all" for e in entities):
        return f"{pair} refused: entity_id 'all' targets the whole house"
    if any("." not in e for e in entities):
        return f"{pair} refused: malformed entity_id {entities!r}"
    return None


def check_call_service(cmd: dict[str, Any], *, maintenance_open: bool) -> str | None:
    """Full check for one ``call_service`` command. Returns a refusal string,
    or None when the call may proceed."""
    domain = str(cmd.get("domain") or "").strip()
    service = str(cmd.get("service") or "").strip()
    service_data = cmd.get("service_data") or {}
    if not isinstance(service_data, dict):
        return "service_data must be an object"

    tier = classify(domain, service)
    pair = f"{domain}.{service}"
    if tier == "never":
        return f"{pair} is permanently blocked by the agent and cannot be enabled"
    if tier == "unlisted":
        return (f"{pair} is not on the agent's allowlist — add it to "
                "service_policy.py if it is genuinely needed")
    if tier == "sensitive" and not maintenance_open:
        return (f"{pair} needs an open maintenance window. Ask the homeowner to "
                "run 'Dartec: allow maintenance' in Home Assistant.")
    return validate_target(domain, service, service_data)
