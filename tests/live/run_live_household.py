"""Run "My Home", the household panel, inside real Home Assistant containers.

For each Home Assistant version given, this starts the agent from this working
tree (the same way run_live.py does), onboards, pairs it to the stub manager,
and then runs `household_driver.py` inside the container. The driver signs in
as the owner, an admin, a regular user, a guest and Dartec's own account, and
checks through Home Assistant's own commands that:

* the panel is registered admin-only and its files are served; a regular
  user is not sent it and every command refuses them;
* adding a person creates the user, the login, the right group and a linked
  person; a guest is always local-only; duplicate and reserved usernames are
  refused;
* editing renames the user and their person; pausing signs them out and stops
  them signing in; resuming lets them back; a new password works and the old
  one does not;
* the first dashboard is what the person themselves reads back from their own
  frontend settings, and an admin-only dashboard cannot be chosen;
* removing takes the user and the person away;
* the owner, Dartec's account, your own role and your own pause are refused;
  an admin who is not the owner cannot set a password; Dartec's account
  cannot change anything;
* the logbook names who did each thing and never carries a password;
* the snapshot carries only counts of people by role.

It also fails on any error Home Assistant logged against the agent.

    python tests/live/run_live_household.py                 # 2026.8.3 and 2026.9.2
    python tests/live/run_live_household.py 2026.9.2 --keep # leave it up for screenshots
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_live import (  # noqa: E402
    AGENT_DOMAIN, DEFAULT_VERSIONS, IMAGE, STUB_PORT, Failure, docker, ha_log, http,
    latest_snapshot, log, onboard, pair, prepare_config, start_container, wait_for)

COUNT_KEYS = {"owner", "admin", "family", "guest", "view_only", "paused", "total"}


def mentions(node, pattern, path="") -> list[str]:
    """Every place in the snapshot where a household member is named."""
    if isinstance(node, dict):
        return [hit for key, value in node.items()
                for hit in ([f"{path} has key {key!r}"] if pattern.search(str(key)) else [])
                + mentions(value, pattern, f"{path}.{key}")]
    if isinstance(node, list):
        return [hit for index, value in enumerate(node)
                for hit in mentions(value, pattern, f"{path}[{index}]")]
    return [f"{path} = {node!r}"] if isinstance(node, str) and pattern.search(node) else []


def run_version(version: str, keep: bool, artifacts: Path | None, timeout: float) -> list[str]:
    name = f"dartec-household-{version.replace('.', '-')}"
    with tempfile.TemporaryDirectory(prefix="dartec-household-") as tmp:
        config = prepare_config(Path(tmp))
        log(f"{version}: starting {IMAGE}:{version} as {name}")
        port = start_container(name, version, config)
    docker("cp", str(HERE / "household_driver.py"), f"{name}:/household_driver.py")
    base = f"http://127.0.0.1:{port}"
    problems: list[str] = []
    try:
        wait_for("Home Assistant to serve onboarding",
                 lambda: http("GET", f"{base}/api/onboarding") is not None, timeout)
        token = onboard(base)
        docker("exec", "-d", name, "python3", "/stub_manager.py")
        wait_for("the stub manager", lambda: docker(
            "exec", name, "curl", "-sf", f"http://127.0.0.1:{STUB_PORT}/health"), 30, 1)
        wait_for("Home Assistant to finish starting",
                 lambda: http("GET", f"{base}/api/config", token=token).get("state") == "RUNNING",
                 timeout)
        pair(base, token)
        log(f"{version}: onboarded and paired on port {port}")
        wait_for("the first snapshot", lambda: latest_snapshot(name)[0] >= 1, timeout)

        out = docker("exec", name, "python3", "/household_driver.py", token)
        result = json.loads(out.strip().splitlines()[-1])
        problems += result["problems"]
        evidence = result["evidence"]
        log(f"{version}: driver finished, {len(result['problems'])} problem(s), "
            f"{len(evidence.get('logbook', []))} logbook line(s)")

        # A snapshot taken after the driver: owner, Omar (admin), Layla
        # (family). Sam was removed; Dartec's account is not counted.
        driven_at, _ = latest_snapshot(name)

        def counted():
            number, snap = latest_snapshot(name)
            return snap if number > driven_at and snap else None

        snap = wait_for("a snapshot after the changes", counted, timeout)
        section = snap.get("household")
        expected = {"owner": 1, "admin": 1, "family": 1, "guest": 0, "view_only": 0,
                    "paused": 0, "total": 3}
        if section != expected:
            problems.append(f"snapshot household is {section}, expected {expected}")
        if not isinstance(section, dict) or set(section) != COUNT_KEYS:
            problems.append(f"snapshot household carries more than counts: {section}")
        # Names reach the manager today through two older sections this
        # feature does not add to: the entity list (a `person.*` entity is
        # named after its person) and Home Assistant's own log lines.
        # dartec-ha-manager#20 asks the owner what to do about those. What
        # is checked here is that nothing else names anyone.
        older = re.compile(r"^\.(entities\[\d+\]\.(entity_id|name)|logs\[\d+\]\.message) ")
        for where in mentions(snap, re.compile(r"(layla|omar)", re.I)):
            if not older.match(where):
                problems.append(f"the snapshot names a person at {where}")

        text = ha_log(name)
        errors = [line.strip() for line in text.splitlines()
                  if re.search(r"\b(ERROR|CRITICAL)\b", line) and AGENT_DOMAIN in line]
        reported = [line.strip() for line in text.splitlines()
                    if re.search(rf"integration '{AGENT_DOMAIN}'", line)]
        if errors or reported:
            problems.append("Home Assistant logged against the agent:\n  "
                            + "\n  ".join((errors + reported)[:20]))
        if artifacts:
            folder = artifacts / f"household-{version}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "evidence.json").write_text(json.dumps(evidence, indent=1,
                                                             ensure_ascii=False), encoding="utf-8")
            (folder / "home-assistant.log").write_text(text, encoding="utf-8")
    except Failure as err:
        problems.append(str(err))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
        problems.append(f"{type(err).__name__}: {err}")
    finally:
        if problems and artifacts:
            folder = artifacts / f"household-{version}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "container.log").write_text(ha_log(name), encoding="utf-8")
        if keep:
            log(f"{version}: left running at {base} (docker rm -f -v {name})")
        else:
            docker("rm", "-f", "-v", name, check=False)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("versions", nargs="*", default=DEFAULT_VERSIONS)
    parser.add_argument("--keep", action="store_true", help="leave containers running")
    parser.add_argument("--artifacts", type=Path, help="write logs and evidence here")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    failed = False
    for version in args.versions:
        started = time.monotonic()
        problems = run_version(version, args.keep, args.artifacts, args.timeout)
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
