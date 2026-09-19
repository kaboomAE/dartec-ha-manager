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
  and backups destroyed in it.
* **ROUTINE** — reversible commissioning and diagnostic calls. No window
  needed; still logged to the homeowner's own logbook.

Anything absent from all three is denied. Adding a capability is therefore a
deliberate edit here rather than a side effect of the cloud learning a new
trick.

A fourth class, **GUARDED**, is narrower than all three: an update of Home
Assistant itself to one exact, approved version, with a backup first, a health
check after, and a rollback if it is unhealthy. It needs no window, because
some updates are security fixes that cannot wait for someone to be home. See
``GUARDED_ACTIONS`` for the decision and its limits.

Separately from the tiers, a few actions need a standing **opt-in** instead of
a window (``OPT_IN_ACTIONS``). Offsite backup copies are the case: they move
the home's whole configuration and recorder history onto Dartec storage, which
needs the customer's agreement, but they are meant to run on a schedule with
nobody at the house, which a window can never serve.
"""
from __future__ import annotations

import re
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
    # A room panel's account is standing access too: a login that works on
    # the home network for as long as it exists, with a password the manager
    # relayed. Creating one, or resetting its password, is the same decision
    # as `user_create`. The other panel commands are routine; see below.
    "panel_setup",
    # Code entering the home. `agent_update` replaces this integration and
    # `hacs_install` adds a third-party one, so both are remote code
    # deployment however routine they feel.
    "agent_update", "hacs_install", "ha_restart",
    # Setting an integration up runs its code and creates configuration in
    # the house - the step that actually makes installed code do anything.
    "integration_setup",
    # `automation_create` is here specifically because an automation is a
    # stored service call: without this gate the cloud could write an
    # automation whose action is `shell_command.*` and let a trigger run it,
    # walking straight around the allowlist above.
    "automation_create",
    # Reachability, and data destroyed in the house. `backup_upload` is
    # deliberately not here: data leaving the house is gated by the home's
    # offsite opt-in instead (see OPT_IN_ACTIONS below).
    "tunnel_setup", "tunnel_stop",
    "link_setup", "link_stop",
    "backup_delete",
    # Add-ons are services in their own right.
    "addon_restart", "addon_start", "addon_stop",
})

# `hacs_token_set` is deliberately NOT in the set above either: it is routine.
# It replaces the GitHub token in a HACS entry that already exists, and that
# token is a read-only, zero-permission token for public repositories that
# only keeps HACS under GitHub's rate limit. Swapping it cannot add a
# repository, download or run code, or create the HACS entry (setting HACS up
# is `integration_setup`, above). It cannot affect this home's connection to
# Dartec: the agent authenticates with its own pairing token, never HACS's.
# And it cannot leave HACS with worse access than it had: the new token is
# verified against GitHub before the entry is touched, and a swap after which
# HACS does not load is rolled back to the previous token. Every swap and
# every rollback is written to the home's logbook, by fingerprint. Rotation
# has to reach every home, including ones nobody will open a window on.
# Confirmed by the owner on 2026-09-17. Reversing it is one line: add
# "hacs_token_set" to SENSITIVE_ACTIONS.

# `panel_update`, `panel_remove` and `panel_status` are deliberately NOT in
# the set above: they are routine. None of them can grant anything. Each one
# acts only on a panel account (`panels.is_panel_account`: a `panel-` username
# on an account that is not the owner, not Home Assistant's own and not an
# administrator) and refuses any other account, so they cannot reach a
# person's login. `panel_update` changes only a panel's name and which of the
# home's non-admin dashboards it opens on, and hides the rest of its sidebar;
# it cannot change its password, its group, whether it is active or where it
# may sign in from. `panel_remove` only takes access away, and removing a
# panel should never have to wait for someone to be at home: a tablet taken
# off the wall, or a password that got out, is exactly when it is needed.
# `panel_status` reads. The updates and removals are written to the home's
# logbook all the same (commands.py).

# `blueprint_install` is deliberately NOT in the set above, because whether it
# is sensitive depends on the command rather than the action. See is_sensitive.

# Actions that need a standing opt-in on the home rather than a window: the
# config entry option that must be switched on, keyed by action.
#
# An offsite copy is the whole configuration and recorder history leaving the
# house, so it needs the customer's agreement. But it is also meant to run on
# a schedule, with nobody there, and a window only ever exists while someone
# is. The window is therefore not a substitute for the opt-in: an installer
# with the switch on still cannot send a home's data offsite unless the home
# has agreed to offsite copies. Like every other consent, the option is set in
# Home Assistant and read there; no command can turn it on.
OPT_OFFSITE_BACKUPS = "offsite_backups"
OPT_IN_ACTIONS = {
    "backup_upload": OPT_OFFSITE_BACKUPS,
}


# ── Guarded updates ─────────────────────────────────────────────────────────
#
# The owner's decision, 2026-09-18. Before it, nothing applied a Home Assistant
# update remotely: `ha_restart` is sensitive, and an update is a restart and
# new code at once. But some updates are critical security fixes, and a fix
# that waits for a homeowner to open a window reaches most homes late or
# never. So an update of Home Assistant Core or OS runs **without a
# maintenance window**, as its own narrow class, and only in this shape:
#
# * **One exact version, upgrade only.** The command carries a version and
#   nothing else that could change what happens: any key outside the action's
#   list below is refused, so there is no `allow_downgrade`, `force` or extra
#   argument to slip in. The version must be a stable release (no betas) and
#   newer than what is installed, and the agent refuses one the Supervisor
#   does not offer on this home's update channel.
# * **Approved on the bench first.** The manager only sends versions an admin
#   has marked approved after a recorded bench pass, and rolls them out bench
#   -> one pilot home -> the rest, stopping at the first failure. That part is
#   enforced on the manager; this module enforces what a home can enforce.
# * **Backup, update, health check, roll back.** A full backup is taken and
#   confirmed before anything changes. After the update the home checks
#   itself, and if it is unhealthy puts back the version it had, restoring the
#   backup if that alone does not bring it back. The only downgrade that ever
#   happens is that rollback, to the version this home recorded before the
#   update, and it never arrives as a command. See ha_update.py.
# * **Always in the logbook**, with the target version: accepted, each step,
#   the verdict, and any refusal.
# * **The homeowner can turn it off**, locally: the "Allow Dartec to install
#   approved updates" switch, or the same setting in this
#   integration's options (OPT_GUARDED_UPDATES). On by default, because the
#   point is that updates reach homes nobody is attending; the owner confirmed
#   the default on 2026-09-18, knowing a home that turns it off stops
#   receiving security fixes from Dartec. Off stops the next
#   update; one already under way finishes, rollback included, because an
#   update stopped half-way is worse than either end of it. The manager cannot
#   switch it back on (check_own_entities).
#
# What it is not: a way to run code, to restart for any other reason, or to
# install anything but Home Assistant itself. `ha_restart`, `hacs_install` and
# the rest keep their consent requirements. Each entry below is its own
# decision, recorded here with who made it and when.
GUARDED_ACTIONS: dict[str, frozenset[str]] = {
    "ha_core_update": frozenset({"version", "job_id", "rollout_id"}),
    "ha_os_update": frozenset({"version", "job_id", "rollout_id"}),
    # The owner's decision, 2026-09-18, asked and answered in the session
    # that built the manager's staged agent rollout and confirmed again
    # directly the same day: agent updates get the same exception. Why: a home whose commissioning has closed never took
    # an agent update again unless someone at the house switched support on,
    # which is how a live home was still on 0.14.6 with 0.17.x released.
    # Its shape is narrower than the others because agent_update already is:
    # it installs the *latest* release of this integration's own repository
    # through HACS, and HACS refuses a downgrade unless `allow_downgrade` is
    # sent, which is not in the list below. The manager stages it (bench ->
    # pilot -> batches) and stops at the first home that does not come back
    # on the new version.
    "agent_update": frozenset({"restart", "job_id", "rollout_id"}),
}

# Guarded actions that are ALSO sensitive. With consent they run exactly as
# they did before the exception, with every option the command has (a
# window-backed agent_update may still downgrade). Without consent they run
# only in the guarded shape above, under the homeowner's opt-out. Reversing
# the agent decision is two lines: remove "agent_update" here and above.
GUARDED_WITHOUT_CONSENT = frozenset({"agent_update"})
# The transport's own keys on every command.
_ENVELOPE_KEYS = frozenset({"type", "id", "action"})

# Opt-out, not opt-in: absent means on. Only an explicit False turns it off,
# the mirror image of check_opt_in's "only True counts": in both, only the
# homeowner's explicit choice moves the default.
OPT_GUARDED_UPDATES = "guarded_updates"
# What the homeowner sees. It covers the Dartec agent too since 2026-09-18,
# so it no longer says "Home Assistant" updates.
GUARDED_SWITCH_NAME = "Allow Dartec to install approved updates"

# Stable releases only. Core is YYYY.M.patch; the OS is major.minor.
CORE_VERSION_RE = re.compile(r"^20\d{2}\.(?:1[0-2]|[1-9])\.\d{1,3}$")
OS_VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}$")
GUARDED_VERSION_FORMATS = {"ha_core_update": CORE_VERSION_RE,
                           "ha_os_update": OS_VERSION_RE}
JOB_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


def guarded_enabled(options: dict) -> bool:
    return options.get(OPT_GUARDED_UPDATES) is not False


def guarded_status(options: dict) -> dict:
    """What `maintenance_status` reports, so the manager can tell from the
    home's own answer whether it takes guarded updates."""
    return {"enabled": guarded_enabled(options), "actions": sorted(GUARDED_ACTIONS)}


def check_guarded(cmd: dict, options: dict) -> tuple[str, str] | None:
    """(code, refusal) if a guarded command may not run, else None.

    `code` is "consent" when the homeowner has turned guarded updates off,
    which the manager treats as blocked rather than failed, and "invalid" for
    a command that is malformed or asks for more than the class allows.
    `options` must be the home's own config entry options.
    """
    action = cmd.get("action")
    allowed = GUARDED_ACTIONS.get(action)
    if allowed is None:
        return None
    if not guarded_enabled(options):
        return ("consent", f"'{action}' is refused: the homeowner has turned off "
                           f"'{GUARDED_SWITCH_NAME}'.")
    extra = sorted(set(cmd) - allowed - _ENVELOPE_KEYS)
    if extra:
        return ("invalid", f"'{action}' is refused: unexpected field(s) "
                           f"{', '.join(extra)}. It carries only: "
                           f"{', '.join(sorted(allowed))}.")
    if "restart" in cmd and not isinstance(cmd["restart"], bool):
        return ("invalid", f"'{action}' is refused: restart must be true or false.")
    pattern = GUARDED_VERSION_FORMATS.get(action)
    if pattern is not None and not pattern.match(str(cmd.get("version") or "")):
        return ("invalid", f"'{action}' is refused: '{cmd.get('version')}' is not an "
                           "exact stable version.")
    job_id = cmd.get("job_id")
    if job_id is not None and not JOB_ID_RE.match(str(job_id)):
        return ("invalid", f"'{action}' is refused: malformed job id.")
    return None


def _numbers(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(version)))


def version_newer(candidate: str, than: str) -> bool:
    left, right = _numbers(candidate), _numbers(than)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def check_upgrade(current: str | None, target: str) -> str | None:
    """Refusal if `target` is not strictly newer than `current`. An unknown
    current version is a refusal: an update we cannot compare is one we cannot
    promise is an upgrade."""
    if not current or not _numbers(current):
        return "the installed version could not be read"
    if not version_newer(target, current):
        return f"{target} is not newer than the installed {current}; only upgrades are accepted"
    return None


def is_sensitive(cmd: dict) -> bool:
    """Does this command need an open maintenance window?

    For nearly every action the answer is a property of the action alone.
    `blueprint_install` is the exception, and the distinction is real rather
    than a convenience:

    * Staging a **new** blueprint writes a file nothing references. It defines
      no automation, runs no code, and changes nothing about how the house
      behaves. Gating it would mean a homeowner had to stand at their tablet
      for us to put an inert file on disk, and the practical result is that
      the library never gets staged — so the expensive work lands in the one
      short window we do get.
    * **Overriding** an existing blueprint is the opposite. Home Assistant
      reloads every automation using that path the moment the file lands, so
      the new logic is live in someone's house immediately, with no restart
      and no further consent. That is remote code deployment.

    Only `allow_override` distinguishes them, and it is safe to read from the
    command because it is self-limiting: with it false, HA itself refuses to
    replace an existing blueprint. A cloud that lies by setting it true only
    moves itself *behind* the window.
    """
    action = cmd.get("action")
    if action == "blueprint_install":
        return bool(cmd.get("allow_override"))
    return action in SENSITIVE_ACTIONS


def check_opt_in(cmd: dict, options: dict) -> str | None:
    """Refusal string if this command needs an opt-in the home has not given,
    or None. `options` must be the home's own config entry options, never
    anything from the command.

    Only a real ``True`` counts. A string "false" is truthy, and an option
    that reads as consent by accident is the one mistake this must not make.
    """
    option = OPT_IN_ACTIONS.get(cmd.get("action"))
    if option is None or options.get(option) is True:
        return None
    return (f"'{cmd.get('action')}' is refused: offsite backup copies are off "
            "for this home. Someone at the home can turn on 'Copy backups "
            "offsite to Dartec' in this integration's options in Home "
            "Assistant. The maintenance window does not stand in for it.")


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


# Keys by which Home Assistant targets entities without naming them. It
# expands each one alongside any `entity_id`, so a harmless entity can satisfy
# the "must name an entity" rule below while an area does the real targeting —
# including an area holding this integration's own consent switch
# (dartec-ha-manager#11). Nothing Dartec sends needs them.
INDIRECT_TARGET_KEYS = ("area_id", "device_id", "floor_id", "label_id")


def _indirect_targets(service_data: dict[str, Any]) -> list[str]:
    return [key for holder in (service_data, service_data.get("target") or {})
            if isinstance(holder, dict)
            for key in INDIRECT_TARGET_KEYS if key in holder]


def validate_target(domain: str, service: str,
                    service_data: dict[str, Any]) -> str | None:
    """Reject house-wide targeting. Returns an error string, or None if fine.

    ``entity_id: all`` is Home Assistant's documented wildcard, and an absent
    target means the same thing for most services — either would turn a single
    mistaken command into every light, lock or cover at once.
    """
    pair = f"{domain}.{service}"
    indirect = _indirect_targets(service_data)
    if indirect:
        return (f"{pair} refused: target entities by entity_id only "
                f"({', '.join(sorted(set(indirect)))} is not accepted)")
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


def check_own_entities(service_data: dict[str, Any], own_entity_ids) -> str | None:
    """Refuse a call aimed at one of this integration's own entities.

    They are the home's controls over Dartec: the consent switch, and any
    opt-out beside it. `switch.turn_on` is routine for every other switch in
    the house, and without this the cloud could switch on the very thing that
    is supposed to be the home's to switch on (dartec-ha-manager#11).
    `own_entity_ids` comes from the entity registry, so a renamed entity is
    still recognised.
    """
    own = {str(e).strip().lower() for e in own_entity_ids}
    hit = sorted({e.strip().lower() for e in _targets(service_data)} & own)
    if hit:
        return (f"refused: {', '.join(hit)} belongs to the Dartec integration and "
                "is controlled only from this home")
    return None
