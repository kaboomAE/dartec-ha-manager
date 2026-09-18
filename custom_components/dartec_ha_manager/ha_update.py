"""Guarded Home Assistant updates: Core or OS to one exact version.

The flow, for `ha_core_update` and `ha_os_update` (the policy that lets these
run without a maintenance window is in service_policy.py, GUARDED_ACTIONS):

    accepted -> backing_up -> updating -> checking -> succeeded
                                              |
                                              +-> rolling_back -> rolled_back
                                                      |
                                                      +-> restoring (Core) -> rolled_back
                                                                 |
                                                                 +-> rollback_failed

* **backing_up**: a full backup through the Supervisor, confirmed to exist
  before anything changes. No backup, no update.
* **updating**: the Supervisor installs the exact version. For Core that
  restarts Home Assistant; for the OS it reboots the machine. Either way this
  process dies mid-step, so **every step is written to disk before it is
  taken** (a `Store`), and `async_setup` resumes the job when Home Assistant
  comes back. The result never travels as the command's reply, because the
  socket it came down is gone by then. It is reported in every snapshot
  (`snapshot.ha_update.job`), and each step is pushed at once.
* **checking**: once Home Assistant has started and settled: Core running on
  the target version, the configuration valid, the agent reconnected to the
  manager, every integration that was loaded before loaded again, no new
  repairs of error or critical severity, no CRITICAL log records, the add-ons
  that were running still running, and any Zigbee, Z-Wave or other radio
  coordinator that was up before still up.
* **rolling_back**: Core is put back on the version it had; the OS boots its
  other slot, or, where the Supervisor cannot switch slots, is downgraded to
  the previous version. The rollback is part of the update that was accepted,
  so it needs no fresh consent: it only ever returns the home to where it was.
  A lighter health check follows it.
* **restoring** (Core only): if Core on the old version is still unhealthy,
  typically because the new version migrated configuration or the database in
  a way the old one cannot read, the Home Assistant part of the backup taken
  at the start is restored.

What can and cannot be undone is written out in the manager repository, in
docs/18-guarded-updates.md. In short: Core can be put back, and its
configuration restored from the backup, but anything the house recorded
between the backup and the restore is lost with it. The OS can be put back on
HAOS. A Supervised install's OS is not Home Assistant's to update at all, and
those homes refuse `ha_os_update`.

A bench can force the unhealthy path: a file named `.dartec_update_test` in
the configuration directory, containing `fail_health_check`, fails the first
health check. Only someone with access to that directory can create it, and
the manager does not have that access.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from . import maintenance
from .const import DOMAIN, SIGNAL_SNAPSHOT_NOW
from .service_policy import check_upgrade, version_newer

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
STORE_KEY = f"{DOMAIN}.ha_update"
STORE_VERSION = 1
_DATA_KEY = "_ha_update"

TEST_FILE = ".dartec_update_test"
TEST_FAIL_HEALTH = "fail_health_check"

# Job states. The last four end a job.
ACCEPTED, BACKING_UP, UPDATING = "accepted", "backing_up", "updating"
CHECKING, ROLLING_BACK, RESTORING = "checking", "rolling_back", "restoring"
SUCCEEDED, FAILED = "succeeded", "failed"
ROLLED_BACK, ROLLBACK_FAILED = "rolled_back", "rollback_failed"
TERMINAL = (SUCCEEDED, FAILED, ROLLED_BACK, ROLLBACK_FAILED)

KIND_CORE, KIND_OS = "core", "os"
_ACTION_KIND = {"ha_core_update": KIND_CORE, "ha_os_update": KIND_OS}

BACKUP_TIMEOUT_S = 60 * 60
RESTORE_TIMEOUT_S = 60 * 60
UPDATE_CALL_TIMEOUT_S = 30 * 60
# After the Supervisor accepts an update, this process is expected to be
# stopped. If it is still here this long afterwards, nothing happened.
RESTART_EXPECTED_WITHIN_S = 20 * 60
# Integrations get this long after start to finish loading before the health
# check judges them. Zigbee radios and cloud integrations are slow.
SETTLE_S = 5 * 60
CONNECT_WAIT_S = 10 * 60
POLL_S = 10
# A job resumed in the same step more than this many times is looping (each
# resume is a restart), and is ended rather than tried again.
MAX_RESUMES = 3
HISTORY = 5
STEP_LOG = 30

# Coordinators whose loss would take a house's devices with them.
RADIO_DOMAINS = ("zha", "zwave_js", "matter", "thread", "deconz", "insteon")


class SupervisorError(Exception):
    pass


# What a call looks like when the connection, not the Supervisor, gave out:
# expected while an update restarts Home Assistant under the call. aiohttp's
# ServerDisconnectedError is not an OSError, so it is named here too.
try:
    from aiohttp import ClientError as _ClientError
except ImportError:          # the unit tests run without aiohttp
    class _ClientError(Exception):
        pass
TRANSPORT_ERRORS = (asyncio.TimeoutError, OSError, _ClientError)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _label(job: dict) -> str:
    what = "Home Assistant Core" if job["kind"] == KIND_CORE else "Home Assistant OS"
    return f"{what} {job['previous']} → {job['target']}"


# ── Supervisor ──────────────────────────────────────────────────────────────

async def supervisor(hass, method: str, path: str, body: dict | None = None,
                     timeout: float = 60) -> dict:
    """One Supervisor call. Returns its `data`; raises SupervisorError on a
    refusal. Transport errors (timeouts, a dropped connection) propagate as
    they are, because while an update runs they are expected."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise SupervisorError("no Supervisor on this install")
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    async with session.request(method, f"{SUPERVISOR_URL}{path}", json=body,
                               headers={"Authorization": f"Bearer {token}"},
                               timeout=timeout) as resp:
        try:
            payload = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            payload = {"message": (await resp.text())[:300]}
        if not isinstance(payload, dict):
            payload = {"message": str(payload)[:300]}
        if resp.status >= 300 or payload.get("result") == "error":
            raise SupervisorError(f"{method} {path}: {resp.status} "
                                  f"{payload.get('message', '')}".strip())
        return payload.get("data") or {}


async def install_info(hass) -> dict:
    """What this home is and what it runs, according to the Supervisor.

    {"install": "haos" | "supervised" | "none", "core": {...}, "os": {...}}.
    The Supervisor's word on versions is the one used everywhere here. It is
    what installs them, and what reports them after a restart.
    """
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return {"install": "none"}
    info = await supervisor(hass, "GET", "/info", timeout=15)
    core = await supervisor(hass, "GET", "/core/info", timeout=15)
    result = {"install": "haos" if info.get("hassos") else "supervised",
              "core": {"version": core.get("version"),
                       "latest": core.get("version_latest"),
                       "update_available": bool(core.get("update_available"))}}
    if result["install"] == "haos":
        os_info = await supervisor(hass, "GET", "/os/info", timeout=15)
        result["os"] = {"version": running_os_version(os_info),
                        "latest": os_info.get("version_latest"),
                        "update_available": bool(os_info.get("update_available")),
                        "boot": os_info.get("boot"),
                        "boot_slots": os_info.get("boot_slots") or {}}
    return result


def running_os_version(os_info: dict) -> str | None:
    """The OS version actually booted: the booted slot's.

    Recent Supervisors separate `version_pending` (installed, not yet booted),
    and, depending on the Core version, `version` may report the pending one.
    An OS that is installed but not running must never read as "updated", so
    the booted slot is the authority when the Supervisor reports slots.
    """
    slot = (os_info.get("boot_slots") or {}).get(os_info.get("boot") or "")
    return (slot or {}).get("version") or os_info.get("version")


async def _installed(hass, kind: str) -> str | None:
    info = await install_info(hass)
    return (info.get(kind) or {}).get("version")


async def _safe_installed(hass, kind: str) -> str | None:
    """The installed version, retrying while the Supervisor is busy (it is
    straight after a restart), or None."""
    for _ in range(6):
        try:
            return await _installed(hass, kind)
        except (SupervisorError, *TRANSPORT_ERRORS):
            await asyncio.sleep(POLL_S)
    return None


# ── the job, on disk ────────────────────────────────────────────────────────

def _state(hass) -> dict:
    return hass.data.setdefault(DOMAIN, {}).setdefault(
        _DATA_KEY, {"job": None, "history": [], "task": None, "store": None})


def _store(hass):
    state = _state(hass)
    if state["store"] is None:
        from homeassistant.helpers.storage import Store

        state["store"] = Store(hass, STORE_VERSION, STORE_KEY)
    return state["store"]


async def _save(hass) -> None:
    state = _state(hass)
    await _store(hass).async_save({"job": state["job"], "history": state["history"]})


def current_job(hass) -> dict | None:
    return _state(hass)["job"]


def active_job(hass) -> dict | None:
    job = current_job(hass)
    return job if job and job.get("state") not in TERMINAL else None


_STATE_WORDS = {
    ACCEPTED: "accepted", BACKING_UP: "taking a full backup first",
    UPDATING: "installing", CHECKING: "checking the home is healthy",
    ROLLING_BACK: "unhealthy, rolling back", RESTORING: "restoring the backup",
    SUCCEEDED: "done, healthy", FAILED: "not applied, nothing changed",
    ROLLED_BACK: "rolled back", ROLLBACK_FAILED: "ROLLBACK FAILED, needs attention",
}


async def _step(hass, job: dict, state: str, detail: str = "", **fields) -> None:
    """Move the job, and write it to disk before anything else happens."""
    job["state"] = state
    job["detail"] = detail
    job["updated_at"] = _now()
    job.update(fields)
    if state in TERMINAL:
        job["finished_at"] = job["updated_at"]
    job.setdefault("steps", []).append({"ts": job["updated_at"], "state": state,
                                        "detail": detail[:300]})
    job["steps"] = job["steps"][-STEP_LOG:]
    if state in TERMINAL:
        history = _state(hass)["history"]
        history.insert(0, summary(job, with_steps=False))
        del history[HISTORY:]
    await _save(hass)
    # Every step, with the versions, in the homeowner's own logbook.
    maintenance.logbook(hass, f"Dartec guarded update, {_label(job)}: "
                              f"{_STATE_WORDS.get(state, state)}"
                              + (f" ({detail})" if detail else ""))
    _nudge(hass)


def _nudge(hass) -> None:
    """Send a snapshot now rather than at the next minute."""
    try:
        from homeassistant.helpers.dispatcher import async_dispatcher_send

        async_dispatcher_send(hass, SIGNAL_SNAPSHOT_NOW)
    except Exception:  # noqa: BLE001
        pass


def summary(job: dict | None, *, with_steps: bool = True) -> dict | None:
    """The job as the manager sees it: no baseline bulk."""
    if not job:
        return None
    keys = ("id", "kind", "target", "previous", "state", "detail", "rollout_id",
            "started_at", "updated_at", "finished_at", "backup_slug", "health",
            "health_after_rollback", "rollback_method", "restore_used")
    out = {k: job.get(k) for k in keys}
    if with_steps:
        out["steps"] = job.get("steps", [])[-STEP_LOG:]
    return out


def snapshot_section(hass, info: dict | None) -> dict:
    """`snapshot.ha_update`: what this home runs, what it could update to,
    whether it takes guarded updates, and the current or last job."""
    from .service_policy import guarded_enabled

    info = info or {"install": "none"}
    state = _state(hass)
    return {"install": info.get("install"),
            "core": info.get("core"), "os": info.get("os"),
            "enabled": guarded_enabled(maintenance.entry_options(hass)),
            "job": summary(state["job"]),
            "history": state["history"]}


# ── accepting a command ─────────────────────────────────────────────────────

def _no(code: str, detail: str) -> dict:
    return {"ok": False, "code": code, "detail": detail}


async def handle(hass, cmd: dict) -> dict:
    """`ha_core_update` / `ha_os_update`, already cleared by
    service_policy.check_guarded. Checks what only the home can know, takes
    the job, and answers at once. The work runs in the background."""
    kind = _ACTION_KIND[cmd["action"]]
    target = str(cmd["version"])

    running = active_job(hass)
    if running is not None:
        return _no("busy", f"another update is under way ({_label(running)}, "
                           f"{running['state']})")
    try:
        info = await install_info(hass)
    except (SupervisorError, *TRANSPORT_ERRORS) as err:
        return _no("supervisor", f"the Supervisor could not be asked: {err}")
    if info["install"] == "none":
        return _no("not_applicable", "this install has no Supervisor (Container or "
                                     "Core); Home Assistant is updated outside it")
    if kind == KIND_OS and info["install"] != "haos":
        return _no("not_applicable", "this is a Supervised install: its operating "
                                     "system is not Home Assistant OS and is not "
                                     "updated from here")
    versions = info[kind]
    current, latest = versions.get("version"), versions.get("latest")
    if current == target:
        return _no("already", f"already on {target}")
    refusal = check_upgrade(current, target)
    if refusal:
        return _no("not_upgrade", refusal)
    if not latest:
        return _no("not_offered", "the Supervisor has not reported which version "
                                  "is available")
    if version_newer(target, latest):
        return _no("not_offered", f"{target} is newer than {latest}, the newest the "
                                  "Supervisor offers this home")
    config = await _config_check(hass)
    if not config["ok"]:
        return _no("precheck", "the configuration does not pass Home Assistant's "
                               f"check before the update: {config['detail']}")

    job = {"id": str(cmd.get("job_id") or os.urandom(8).hex()), "kind": kind,
           "target": target, "previous": current,
           "rollout_id": cmd.get("rollout_id"), "started_at": _now(),
           "resumes": {}, "steps": []}
    job["baseline"] = await _baseline(hass)
    _state(hass)["job"] = job
    await _step(hass, job, ACCEPTED,
                "backup, update, health check; rolled back if unhealthy")
    _spawn(hass)
    return {"ok": True, "accepted": True, "job_id": job["id"], "previous": current,
            "target": target, "detail": f"accepted: {_label(job)}"}


# ── running it ──────────────────────────────────────────────────────────────

def _spawn(hass) -> None:
    state = _state(hass)
    task = state.get("task")
    if task is not None and not task.done():
        return
    state["task"] = hass.async_create_background_task(_drive(hass),
                                                      name="dartec_ha_update")


async def async_setup(hass) -> None:
    """Load the job from disk and pick it up where the restart left it."""
    stored = await _store(hass).async_load() or {}
    state = _state(hass)
    if state["job"] is None:
        state["job"] = stored.get("job")
        state["history"] = stored.get("history") or []
    job = active_job(hass)
    task = state.get("task")
    if job is None or (task is not None and not task.done()):
        # Nothing to resume, or the entry was reloaded under a job that is
        # still running in this process: that is not a restart.
        return
    resumes = job.setdefault("resumes", {})
    resumes[job["state"]] = resumes.get(job["state"], 0) + 1
    await _save(hass)
    _spawn(hass)


async def _drive(hass) -> None:
    job = active_job(hass)
    if job is None:
        return
    try:
        await _wait_started(hass)
        while job.get("state") not in TERMINAL:
            if job.get("resumes", {}).get(job["state"], 0) > MAX_RESUMES:
                end = (ROLLBACK_FAILED if job["state"] in (CHECKING, ROLLING_BACK, RESTORING)
                       else FAILED)
                await _step(hass, job, end, f"stopped after {MAX_RESUMES} restarts in "
                                            f"'{job['state']}'")
                break
            await _STEPS[job["state"]](hass, job)
    except asyncio.CancelledError:
        # Home Assistant is stopping, which the update itself does. The job on
        # disk says where to carry on.
        raise
    except Exception as err:  # noqa: BLE001 — record it rather than vanish
        _LOGGER.exception("guarded update failed unexpectedly")
        if job.get("state") not in TERMINAL:
            changed = job["state"] not in (ACCEPTED, BACKING_UP)
            await _step(hass, job, ROLLBACK_FAILED if changed else FAILED,
                        f"unexpected error in '{job['state']}': "
                        f"{type(err).__name__}: {err}")


async def _wait_started(hass) -> None:
    """Carry on only once Home Assistant has finished starting."""
    deadline = time.monotonic() + 2 * SETTLE_S
    while time.monotonic() <= deadline:
        if str(getattr(hass.state, "value", hass.state)).lower() == "running":
            return
        await asyncio.sleep(POLL_S)


async def _wait_to_be_stopped() -> None:
    """After asking for something that restarts Home Assistant, this process
    should not outlive the wait. If it does, the caller judges what is
    installed."""
    deadline = time.monotonic() + RESTART_EXPECTED_WITHIN_S
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_S * 3)


async def _accepted(hass, job: dict) -> None:
    await _step(hass, job, BACKING_UP)


async def _backing_up(hass, job: dict) -> None:
    try:
        data = await supervisor(hass, "POST", "/backups/new/full",
                                {"name": f"Dartec before {_label(job)}"},
                                timeout=BACKUP_TIMEOUT_S)
        slug = data.get("slug")
        if not slug:
            raise SupervisorError("the Supervisor did not return a backup")
        info = await supervisor(hass, "GET", f"/backups/{slug}/info", timeout=30)
        if info.get("slug") != slug:
            raise SupervisorError(f"backup {slug} is not there")
    except (SupervisorError, *TRANSPORT_ERRORS) as err:
        await _step(hass, job, FAILED, f"no backup, so nothing was changed: {err}")
        return
    await _step(hass, job, UPDATING, f"backup {slug} confirmed", backup_slug=slug,
                update_sent_at=None)


async def _updating(hass, job: dict) -> None:
    kind = job["kind"]
    if job.get("update_sent_at") is None:
        job["update_sent_at"] = _now()
        await _save(hass)
        body = {"version": job["target"]}
        if kind == KIND_CORE:
            body["backup"] = False       # ours is already taken and confirmed
        try:
            await supervisor(hass, "POST", "/core/update" if kind == KIND_CORE
                             else "/os/update", body, timeout=UPDATE_CALL_TIMEOUT_S)
        except SupervisorError as err:
            if await _safe_installed(hass, kind) == job["previous"]:
                await _step(hass, job, FAILED, f"the Supervisor did not apply it, "
                                               f"nothing changed: {err}")
                return
            _LOGGER.warning("update call refused, but the version moved: %s", err)
        except TRANSPORT_ERRORS as err:
            # Including this process being cut off as Core is stopped.
            _LOGGER.info("update call ended without an answer: %s", err)
        else:
            await _wait_to_be_stopped()

    # Resumed after the restart, or the wait above ran out.
    installed = await _safe_installed(hass, kind)
    if installed == job["target"]:
        await _step(hass, job, CHECKING, f"now on {installed}")
    elif installed == job["previous"]:
        # Core that will not start is put back by the Supervisor, and an OS
        # slot that will not boot is abandoned by HAOS itself.
        who = "the Supervisor" if kind == KIND_CORE else "HAOS"
        await _step(hass, job, ROLLED_BACK,
                    f"still on {installed}: the update did not take, and {who} "
                    "kept the previous version", rollback_method="automatic")
    else:
        await _step(hass, job, ROLLING_BACK, f"on unexpected version {installed}",
                    rollback_sent_at=None)


async def _checking(hass, job: dict) -> None:
    health = await check_health(hass, job, full=True)
    if health["ok"]:
        await _step(hass, job, SUCCEEDED, health["summary"], health=health)
    else:
        await _step(hass, job, ROLLING_BACK, health["summary"], health=health,
                    rollback_sent_at=None)


async def _rolling_back(hass, job: dict) -> None:
    kind = job["kind"]
    if job.get("rollback_sent_at") is None:
        # How, then that it was sent, both on disk before the call: the call
        # itself restarts Home Assistant, so nothing after it runs.
        job["rollback_method"] = await _rollback_method(hass, job)
        job["rollback_sent_at"] = _now()
        await _save(hass)
        try:
            await _send_rollback(hass, job)
        except SupervisorError as err:
            if kind == KIND_CORE and job.get("backup_slug"):
                await _step(hass, job, RESTORING, f"putting the version back was "
                                                  f"refused ({err}); restoring the backup",
                            restore_sent_at=None)
            else:
                await _step(hass, job, ROLLBACK_FAILED, f"rollback refused: {err}")
            return
        except TRANSPORT_ERRORS:
            pass
        else:
            await _wait_to_be_stopped()

    installed = await _safe_installed(hass, kind)
    if installed != job["previous"]:
        if kind == KIND_CORE and job.get("backup_slug") and not job.get("restore_sent_at"):
            await _step(hass, job, RESTORING, f"still on {installed} after the "
                                              "rollback; restoring the backup",
                        restore_sent_at=None)
        else:
            await _step(hass, job, ROLLBACK_FAILED, f"on {installed}, expected "
                                                    f"{job['previous']}")
        return
    after = await check_health(hass, job, full=False)
    if after["ok"]:
        await _step(hass, job, ROLLED_BACK, f"back on {installed} and healthy",
                    health_after_rollback=after)
    elif kind == KIND_CORE and job.get("backup_slug") and not job.get("restore_sent_at"):
        await _step(hass, job, RESTORING, f"back on {installed} but still unhealthy "
                                          f"({after['summary']}); restoring the backup",
                    restore_sent_at=None, health_after_rollback=after)
    else:
        await _step(hass, job, ROLLBACK_FAILED, f"back on {installed} but still "
                                                f"unhealthy: {after['summary']}",
                    health_after_rollback=after)


async def _rollback_method(hass, job: dict) -> str:
    """core_version, boot_slot_<A|B>, or os_version.

    HAOS keeps the previous release in its other boot slot. Booting it is the
    true rollback: no download, and exactly what ran before. It is used when
    that slot holds the version this home had; otherwise the OS is downgraded
    to it by version.
    """
    if job["kind"] == KIND_CORE:
        return "core_version"
    try:
        os_info = (await install_info(hass)).get("os") or {}
    except (SupervisorError, *TRANSPORT_ERRORS):
        return "os_version"
    other = next((name for name, slot in (os_info.get("boot_slots") or {}).items()
                  if name != os_info.get("boot")
                  and (slot or {}).get("version") == job["previous"]), None)
    return f"boot_slot_{other}" if other else "os_version"


async def _send_rollback(hass, job: dict) -> None:
    method = job["rollback_method"]
    if method == "core_version":
        await supervisor(hass, "POST", "/core/update",
                         {"version": job["previous"], "backup": False},
                         timeout=UPDATE_CALL_TIMEOUT_S)
        return
    if method.startswith("boot_slot_"):
        try:
            await supervisor(hass, "POST", "/os/boot-slot",
                             {"boot_slot": method.removeprefix("boot_slot_")}, timeout=120)
            return
        except SupervisorError as err:
            _LOGGER.warning("boot slot switch refused, downgrading instead: %s", err)
            job["rollback_method"] = "os_version"
            await _save(hass)
    await supervisor(hass, "POST", "/os/update", {"version": job["previous"]},
                     timeout=UPDATE_CALL_TIMEOUT_S)


async def _restoring(hass, job: dict) -> None:
    if job.get("restore_sent_at") is None:
        job["restore_sent_at"] = _now()
        job["restore_used"] = True
        await _save(hass)
        try:
            # The Home Assistant part only: its configuration, database and
            # version as they were at the backup. Add-ons are left alone: they
            # were not updated, and their data since then is theirs.
            await supervisor(hass, "POST",
                             f"/backups/{job['backup_slug']}/restore/partial",
                             {"homeassistant": True}, timeout=RESTORE_TIMEOUT_S)
        except SupervisorError as err:
            await _step(hass, job, ROLLBACK_FAILED, f"restoring backup "
                                                    f"{job['backup_slug']} was refused: {err}")
            return
        except TRANSPORT_ERRORS:
            pass
        else:
            await _wait_to_be_stopped()

    installed = await _safe_installed(hass, job["kind"])
    after = await check_health(hass, job, full=False)
    if installed == job["previous"] and after["ok"]:
        await _step(hass, job, ROLLED_BACK, f"backup {job['backup_slug']} restored; "
                                            f"on {installed} and healthy",
                    health_after_rollback=after)
    else:
        await _step(hass, job, ROLLBACK_FAILED, f"after restoring the backup: on "
                                                f"{installed}, {after['summary']}",
                    health_after_rollback=after)


_STEPS = {ACCEPTED: _accepted, BACKING_UP: _backing_up, UPDATING: _updating,
          CHECKING: _checking, ROLLING_BACK: _rolling_back, RESTORING: _restoring}


# ── health ──────────────────────────────────────────────────────────────────

async def _config_check(hass) -> dict:
    """Home Assistant's own configuration check, run by the Supervisor."""
    try:
        await supervisor(hass, "POST", "/core/check", timeout=300)
        return {"ok": True, "detail": "valid"}
    except SupervisorError as err:
        return {"ok": False, "detail": str(err)[:300]}
    except TRANSPORT_ERRORS as err:
        return {"ok": False, "detail": f"the check did not answer: {err}"}


def _entries(hass) -> dict[str, dict]:
    return {e.entry_id: {"domain": e.domain,
                         "state": str(getattr(e.state, "value", e.state))}
            for e in hass.config_entries.async_entries()}


def _repairs(hass) -> dict[str, str]:
    """Active, undismissed repairs, as "domain/issue_id" -> severity."""
    try:
        from homeassistant.helpers import issue_registry as ir

        return {f"{i.domain}/{i.issue_id}": str(getattr(i.severity, "value", i.severity))
                for i in ir.async_get(hass).issues.values()
                if getattr(i, "active", True) and not getattr(i, "dismissed_version", None)}
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("issue registry unreadable: %s", err)
        return {}


def _critical_logs(hass) -> list[str]:
    """CRITICAL records in the system log. It is in memory, so after the
    restart an update causes, everything in it is new."""
    try:
        store = getattr(hass.data.get("system_log"), "records", None) or {}
        found = []
        for entry in list(store.values()):
            record = entry.to_dict() if hasattr(entry, "to_dict") else {}
            if str(record.get("level", "")).upper() == "CRITICAL":
                message = record.get("message")
                if isinstance(message, list):
                    message = message[0] if message else ""
                found.append(f"{record.get('name')}: {str(message)[:120]}")
        return found
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("system log unreadable: %s", err)
        return []


def _bridges_on(hass) -> list[str]:
    """Coordinator bridges that report their connection, such as
    Zigbee2MQTT's `binary_sensor.*_connection_state`, and are connected."""
    try:
        return sorted(s.entity_id for s in hass.states.async_all("binary_sensor")
                      if s.entity_id.endswith("_connection_state") and s.state == "on")
    except Exception:  # noqa: BLE001
        return []


async def _addons_started(hass) -> list[str]:
    try:
        data = await supervisor(hass, "GET", "/addons", timeout=15)
        return sorted(a["slug"] for a in data.get("addons") or []
                      if a.get("state") == "started")
    except (SupervisorError, *TRANSPORT_ERRORS):
        return []


async def _baseline(hass) -> dict:
    return {"entries_loaded": sorted(k for k, v in _entries(hass).items()
                                     if v["state"] == "loaded"),
            "repairs": _repairs(hass), "bridges_on": _bridges_on(hass),
            "addons_started": await _addons_started(hass)}


def _link_connected(hass) -> bool:
    return any(getattr(link, "connected", False)
               for link in (hass.data.get(DOMAIN) or {}).values())


def _test_file(hass) -> str:
    try:
        path = os.path.join(hass.config.config_dir, TEST_FILE)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                return handle.read(200).strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


async def check_health(hass, job: dict, *, full: bool) -> dict:
    """The verdict after an update (`full`), or after a rollback (lighter:
    the version, Core, the configuration, and the integrations and radios
    that were up before).

    Waits for integrations to settle and the agent to reconnect first:
    judging a home thirty seconds after it booted would fail most of them.
    """
    base = job.get("baseline") or {}
    wanted = set(base.get("entries_loaded") or [])
    deadline = time.monotonic() + SETTLE_S
    while time.monotonic() < deadline:
        entries = _entries(hass)
        if not [k for k in wanted if (entries.get(k) or {}).get("state")
                in ("setup_in_progress", "not_loaded", "setup_retry", None)]:
            break
        await asyncio.sleep(POLL_S)
    if full:
        connect_deadline = time.monotonic() + CONNECT_WAIT_S
        while not _link_connected(hass) and time.monotonic() < connect_deadline:
            await asyncio.sleep(POLL_S)

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})

    expected = job["target"] if full else job["previous"]
    installed = await _safe_installed(hass, job["kind"])
    check("version", installed == expected, f"{installed} (expected {expected})")
    state = str(getattr(hass.state, "value", hass.state)).lower()
    check("core_running", state == "running", state)
    config = await _config_check(hass)
    check("config_valid", config["ok"], config["detail"])

    entries = _entries(hass)
    lost = sorted(k for k in wanted if (entries.get(k) or {}).get("state") != "loaded")
    check("integrations", not lost,
          ", ".join(f"{(entries.get(k) or {}).get('domain', k)} "
                    f"({(entries.get(k) or {}).get('state', 'gone')})" for k in lost)
          or f"{len(wanted)} still loaded")
    radios = [k for k in wanted if (entries.get(k) or {}).get("domain") in RADIO_DOMAINS]
    radios_lost = [(entries.get(k) or {}).get("domain", k) for k in radios if k in lost]
    bridges_lost = sorted(set(base.get("bridges_on") or []) - set(_bridges_on(hass)))
    present = len(radios) + len(base.get("bridges_on") or [])
    check("radios", not radios_lost and not bridges_lost,
          ", ".join(radios_lost + bridges_lost)
          or (f"{present} up" if present else "none present"))

    if full:
        connected = _link_connected(hass)
        check("agent_connected", connected,
              "reconnected" if connected else "not reconnected to the manager")
        before = base.get("repairs") or {}
        new = {k: v for k, v in _repairs(hass).items() if k not in before}
        serious = sorted(k for k, v in new.items() if v in ("error", "critical"))
        # New warnings are reported but do not fail the update: a release that
        # deprecates something raises one in nearly every home, and rolling
        # back for it would leave security fixes uninstalled.
        check("repairs", not serious,
              ", ".join(serious) or (f"{len(new)} new warning(s): {', '.join(sorted(new))}"
                                     if new else "none new"))
        critical = _critical_logs(hass)
        check("critical_logs", not critical, "; ".join(critical[:3]) or "none")
        addons_lost = sorted(set(base.get("addons_started") or [])
                             - set(await _addons_started(hass)))
        check("addons", not addons_lost, ", ".join(addons_lost) or "all still running")
        if _test_file(hass) == TEST_FAIL_HEALTH:
            check("bench_test", False, f"forced by {TEST_FILE} (bench only)")

    failed = [c for c in checks if not c["ok"]]
    return {"ok": not failed, "checked_at": _now(), "checks": checks,
            "summary": ("healthy" if not failed else
                        "unhealthy: " + "; ".join(f"{c['name']} ({c['detail']})"
                                                  for c in failed))[:600]}


HANDLERS = {"ha_core_update": handle, "ha_os_update": handle}
