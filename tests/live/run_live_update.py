"""Guarded Home Assistant updates, end to end inside a real Home Assistant.

Starts `ghcr.io/home-assistant/home-assistant:<version>` with the agent from
this working tree, a stand-in Supervisor (`stub_supervisor.py`, reachable as
`http://supervisor` through `--add-host`) and the stub manager, then runs:

A. **A healthy Core update.** `ha_core_update` 2026.9.2 -> 2026.9.3. Expected:
   a backup confirmed before the update, one real restart of Home Assistant,
   the job resumed from its store, the health check passed, `succeeded`.
B. **An unhealthy Core update, rolled back.** `.dartec_update_test` in the
   config directory forces the first health check to fail. 2026.9.3 -> 2026.9.4.
   Expected: two restarts (the update, then the rollback), `rolled_back`, and
   the stand-in back on 2026.9.3.
C. **An OS update on "HAOS".** 16.2 -> 16.3, the reboot being a restart.
D. **Refusals.** A version the Supervisor does not offer, and a command
   carrying an extra field, are refused, and nothing is sent to the Supervisor.

Then the homeowner's logbook must show each step with its versions, and the
log must hold no errors against the agent.

What this cannot prove is the real Supervisor installing a real image, and a
real boot slot switch: those are the bench Pi's job (docs/18-guarded-updates.md
in the manager repository has the runbook).

    python tests/live/run_live_update.py                 # 2026.9.2
    python tests/live/run_live_update.py 2026.9.2 --keep
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from run_live import (AGENT, AGENT_DOMAIN, HERE, IMAGE, STATE_DIR, STUB_PORT, TOKEN,
                      Failure, docker, ha_log, http, log, read_state, wait_for)
import shutil

SUPERVISOR_TOKEN = "live-supervisor-token"
CONFIGURATION_YAML = """\
default_config:
demo:
logger:
  default: warning
"""
JOB_TIMEOUT = 15 * 60
TERMINAL = ("succeeded", "failed", "rolled_back", "rollback_failed")


class Session:
    """An access token that outlives the half hour onboarding gives it."""

    def __init__(self, base: str, tokens: dict, client_id: str):
        self.base, self.client_id = base, client_id
        self.refresh = tokens["refresh_token"]
        self.access = tokens["access_token"]
        self.expires = time.monotonic() + tokens.get("expires_in", 1800) - 120

    @property
    def token(self) -> str:
        if time.monotonic() > self.expires:
            fresh = http("POST", f"{self.base}/auth/token", form={
                "grant_type": "refresh_token", "refresh_token": self.refresh,
                "client_id": self.client_id})
            self.access = fresh["access_token"]
            self.expires = time.monotonic() + fresh.get("expires_in", 1800) - 120
        return self.access


def start(name: str, version: str, config: Path) -> int:
    docker("rm", "-f", "-v", name, check=False)
    docker("create", "--name", name, "-p", "127.0.0.1::8123", "-e", "TZ=UTC",
           "-e", f"SUPERVISOR_TOKEN={SUPERVISOR_TOKEN}",
           "--add-host", "supervisor:127.0.0.1", f"{IMAGE}:{version}")
    docker("cp", f"{config}/.", f"{name}:/config")
    for stub in ("stub_manager.py", "stub_supervisor.py"):
        docker("cp", str(HERE / stub), f"{name}:/{stub}")
    docker("start", name)
    return int(docker("port", name, "8123/tcp").strip().splitlines()[0].rsplit(":", 1)[1])


def exec_env(name: str, *env: str) -> list[str]:
    args = ["exec", "-d"]
    for pair in env:
        args += ["-e", pair]
    return args + [name]


def write_in(name: str, path: str, text: str) -> None:
    docker("exec", name, "sh", "-c", f"mkdir -p $(dirname {path}) && cat > {path} <<'EOF'\n{text}\nEOF")


def send(name: str, command: dict) -> None:
    write_in(name, f"{STATE_DIR}/send-{command['id']}.json", json.dumps(command))


def supervisor_state(name: str) -> dict:
    return read_state(name, "supervisor.json") or {}


def job(name: str) -> dict:
    snap = read_state(name, "snapshot-latest.json") or {}
    return ((snap.get("ha_update") or {}).get("job")) or {}


def follow(name: str, job_id: str, what: str) -> dict:
    seen: list[str] = []

    def done():
        current = job(name)
        if current.get("id") != job_id:
            return None
        if not seen or seen[-1] != current.get("state"):
            seen.append(current.get("state"))
            log(f"  {what}: {current.get('state')} {current.get('detail') or ''}"[:160])
        return current if current.get("state") in TERMINAL else None

    return wait_for(f"{what} to finish", done, JOB_TIMEOUT, 3)


def run(version: str, keep: bool, artifacts: Path | None, timeout: float) -> list[str]:
    name = f"dartec-live-update-{version.replace('.', '-')}"
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dartec-live-update-") as tmp:
        config = Path(tmp) / "config"
        (config / "custom_components").mkdir(parents=True)
        (config / "configuration.yaml").write_text(CONFIGURATION_YAML, encoding="utf-8")
        shutil.copytree(AGENT, config / "custom_components" / AGENT_DOMAIN,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        log(f"{version}: starting {IMAGE}:{version} as {name}, with a stand-in Supervisor")
        port = start(name, version, config)
    base = f"http://127.0.0.1:{port}"
    started_at = datetime.now(timezone.utc)

    def expect(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)
            log(f"  PROBLEM: {message}")

    try:
        wait_for("Home Assistant to serve onboarding",
                 lambda: http("GET", f"{base}/api/onboarding") is not None, timeout)
        client_id = f"{base}/"
        created = http("POST", f"{base}/api/onboarding/users", json_body={
            "client_id": client_id, "name": "Live test", "username": "livetest",
            "password": "live-test-password", "language": "en"})
        session = Session(base, http("POST", f"{base}/auth/token", form={
            "grant_type": "authorization_code", "code": created["auth_code"],
            "client_id": client_id}), client_id)
        # The stand-in Supervisor restarts Home Assistant through its own API.
        write_in(name, f"{STATE_DIR}/ha-token", session.token)

        docker(*exec_env(name, f"SUPERVISOR_TOKEN={SUPERVISOR_TOKEN}"),
               "python3", "/stub_supervisor.py")
        docker(*exec_env(name, "DARTEC_LIVE_SCENARIO=update"), "python3", "/stub_manager.py")
        wait_for("the stub manager", lambda: docker(
            "exec", name, "curl", "-sf", f"http://127.0.0.1:{STUB_PORT}/health"), 30, 1)
        wait_for("the stand-in Supervisor", lambda: docker(
            "exec", name, "curl", "-sf", "-H", f"Authorization: Bearer {SUPERVISOR_TOKEN}",
            "http://supervisor/info"), 30, 1)
        wait_for("Home Assistant to finish starting",
                 lambda: http("GET", f"{base}/api/config",
                              token=session.token).get("state") == "RUNNING", timeout)

        flow = http("POST", f"{base}/api/config/config_entries/flow", token=session.token,
                    json_body={"handler": AGENT_DOMAIN, "show_advanced_options": False})
        http("POST", f"{base}/api/config/config_entries/flow/{flow['flow_id']}",
             token=session.token, json_body={"server_url": f"http://127.0.0.1:{STUB_PORT}",
                                             "pairing_token": TOKEN})
        section = wait_for("a snapshot with ha_update from the Supervisor", lambda: (
            ((read_state(name, "snapshot-latest.json") or {}).get("ha_update") or {})
            .get("install") not in (None, "none")
            and read_state(name, "snapshot-latest.json")["ha_update"]), timeout)
        expect(section.get("install") == "haos", f"install reported as {section.get('install')}")
        expect(section.get("enabled") is True, "guarded updates not on by default")
        expect((section.get("core") or {}).get("version") == "2026.9.2",
               f"core reported as {section.get('core')}")

        # A. healthy
        log(f"{version}: A. healthy Core update 2026.9.2 -> 2026.9.3")
        send(name, {"id": "upd-a", "action": "ha_core_update", "version": "2026.9.3",
                    "job_id": "a0a0a0a0a0a0a0a0"})
        answer = wait_for("the answer to A", lambda: read_state(name, "result-upd-a.json"), 150)
        expect(answer.get("ok") and answer.get("accepted"), f"A not accepted: {answer}")
        result = follow(name, "a0a0a0a0a0a0a0a0", "A")
        sup = supervisor_state(name)
        expect(result.get("state") == "succeeded", f"A ended {result.get('state')}: "
                                                   f"{result.get('detail')}")
        expect(sup.get("core") == "2026.9.3", f"A: Supervisor has {sup.get('core')}")
        expect(sup.get("restarts") == 1, f"A: {sup.get('restarts')} restarts, expected 1")
        calls = sup.get("calls") or []
        backup_at = next((i for i, c in enumerate(calls) if c.endswith("/backups/new/full")), None)
        update_at = next((i for i, c in enumerate(calls) if c.endswith("/core/update")), None)
        expect(backup_at is not None and update_at is not None and backup_at < update_at,
               "A: the backup was not taken before the update")
        expect(all(c.get("ok") for c in (result.get("health") or {}).get("checks", [])),
               f"A: health {result.get('health')}")

        # B. unhealthy, rolled back
        log(f"{version}: B. forced-unhealthy Core update 2026.9.3 -> 2026.9.4")
        write_in(name, "/config/.dartec_update_test", "fail_health_check")
        send(name, {"id": "upd-b", "action": "ha_core_update", "version": "2026.9.4",
                    "job_id": "b1b1b1b1b1b1b1b1"})
        result = follow(name, "b1b1b1b1b1b1b1b1", "B")
        sup = supervisor_state(name)
        expect(result.get("state") == "rolled_back", f"B ended {result.get('state')}: "
                                                     f"{result.get('detail')}")
        expect(result.get("rollback_method") == "core_version",
               f"B: rollback by {result.get('rollback_method')}")
        expect(sup.get("core") == "2026.9.3", f"B: Supervisor has {sup.get('core')}")
        expect(sup.get("restarts") == 3, f"B: {sup.get('restarts')} restarts in all, "
                                         "expected 3 (A, B's update, B's rollback)")
        failed = [c["name"] for c in (result.get("health") or {}).get("checks", [])
                  if not c.get("ok")]
        expect(failed == ["bench_test"], f"B: failed checks {failed}")
        docker("exec", name, "rm", "-f", "/config/.dartec_update_test")

        # C. OS
        log(f"{version}: C. OS update 16.2 -> 16.3")
        send(name, {"id": "upd-c", "action": "ha_os_update", "version": "16.3",
                    "job_id": "c2c2c2c2c2c2c2c2"})
        result = follow(name, "c2c2c2c2c2c2c2c2", "C")
        sup = supervisor_state(name)
        expect(result.get("state") == "succeeded", f"C ended {result.get('state')}: "
                                                   f"{result.get('detail')}")
        expect(sup.get("os") == "16.3" and sup.get("boot") == "B",
               f"C: Supervisor has OS {sup.get('os')} on slot {sup.get('boot')}")

        # D. refusals
        log(f"{version}: D. refusals")
        before = len(supervisor_state(name).get("calls") or [])
        send(name, {"id": "upd-d1", "action": "ha_core_update", "version": "2026.9.9"})
        send(name, {"id": "upd-d2", "action": "ha_core_update", "version": "2026.9.4",
                    "allow_downgrade": True})
        d1 = wait_for("D1", lambda: read_state(name, "result-upd-d1.json"), 150)
        d2 = wait_for("D2", lambda: read_state(name, "result-upd-d2.json"), 150)
        expect(d1.get("ok") is False and d1.get("code") == "not_offered", f"D1: {d1}")
        expect(d2.get("ok") is False and d2.get("code") == "invalid" and d2.get("refused"),
               f"D2: {d2}")
        after = supervisor_state(name).get("calls") or []
        changed = [c for c in after[before:] if c.startswith("POST") and "check" not in c]
        expect(not changed, f"D: refusals still changed something: {changed}")

        # The homeowner's own record. The recorder commits every few seconds,
        # so the last line written is waited for rather than read at once.
        def logbook():
            return http("GET", f"{base}/api/logbook/{started_at.isoformat()}",
                        token=session.token) or []
        try:
            wait_for("the refusal in the logbook", lambda: any(
                "Refused remote command 'ha_core_update 2026.9.4'" in (e.get("message") or "")
                for e in logbook()), 60, 3)
        except Failure:
            pass
        entries = logbook()
        lines = [e.get("message", "") for e in entries if "guarded update" in (e.get("message") or "")]
        for needle in ("2026.9.2 → 2026.9.3: done, healthy",
                       "2026.9.3 → 2026.9.4: unhealthy, rolling back",
                       "2026.9.3 → 2026.9.4: rolled back",
                       "16.2 → 16.3: done, healthy"):
            expect(any(needle in line for line in lines), f"logbook lacks '{needle}'")
        expect(any("Refused remote command 'ha_core_update 2026.9.4'" in (e.get("message") or "")
                   for e in entries), "logbook lacks the D2 refusal")

        text = ha_log(name)
        errors = [line for line in text.splitlines()
                  if ("ERROR" in line or "CRITICAL" in line) and AGENT_DOMAIN in line]
        expect(not errors, "agent logged errors:\n  " + "\n  ".join(errors[:20]))
        log(f"{version}: {len(lines)} logbook lines, "
            f"{supervisor_state(name).get('restarts')} restarts of Home Assistant")

        if artifacts:
            out = artifacts / f"update-{version}"
            out.mkdir(parents=True, exist_ok=True)
            (out / "home-assistant.log").write_text(text, encoding="utf-8")
            (out / "supervisor.json").write_text(json.dumps(supervisor_state(name), indent=1),
                                                 encoding="utf-8")
            (out / "snapshot.json").write_text(json.dumps(
                read_state(name, "snapshot-latest.json"), indent=1), encoding="utf-8")
            (out / "logbook.txt").write_text("\n".join(lines), encoding="utf-8")
    except Failure as err:
        problems.append(str(err))
    except Exception as err:  # noqa: BLE001
        problems.append(f"{type(err).__name__}: {err}")
    finally:
        if problems and artifacts:
            out = artifacts / f"update-{version}"
            out.mkdir(parents=True, exist_ok=True)
            (out / "container.log").write_text(ha_log(name), encoding="utf-8")
        if keep:
            log(f"{version}: left running at {base} (docker rm -f -v {name})")
        else:
            docker("rm", "-f", "-v", name, check=False)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("versions", nargs="*", default=["2026.9.2"])
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    failed = False
    for version in args.versions:
        started = time.monotonic()
        problems = run(version, args.keep, args.artifacts, args.timeout)
        took = time.monotonic() - started
        if problems:
            failed = True
            log(f"{version}: FAILED after {took:.0f}s")
            for problem in problems:
                print(f"  - {problem}")
        else:
            log(f"{version}: passed in {took:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
