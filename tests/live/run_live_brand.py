"""The brand's old spelling, found and put right, inside real Home Assistant.

dartec-ha-manager#25: a home read "DarTec Dashboard". The brand is Dartec.
Old spellings live in data on the home, not in code, so this stages each one
the way a home that predates the rename has it, and checks the agent:

* reports the default theme as **stored**, next to the one running and the
  themes loaded. The home is started with `DarTec` stored as its default,
  as dartec-theme v1.0.0 named it, and only `Dartec` loaded (v1.1.0). Home
  Assistant then quietly runs its own default, so the report must say
  `DarTec` is stored, `default` is running, and `Dartec` is what exists;
* reports the sidebar branding and its title;
* corrects its own config entry title from "DarTec: ..." when it starts
  (reloaded here, as at a restart), and nothing else about the entry;
* renames a dashboard titled "DarTec Dashboard" through `lovelace_update`,
  sent by the stub manager as the real one would, answers with the previous
  title, and leaves a line in the household's logbook; and the next
  snapshot shows the new title;
* logs nothing against itself on the way.

    python tests/live/run_live_brand.py                 # 2026.8.3 and 2026.9.2
    python tests/live/run_live_brand.py 2026.9.3 --keep
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
    AGENT_DOMAIN, DEFAULT_VERSIONS, IMAGE, STATE_DIR, STUB_PORT, Failure, docker, ha_log,
    http, latest_snapshot, log, onboard, pair, prepare_config, read_state, start_container,
    wait_for)

THEME_YAML = 'Dartec:\n  primary-color: "#0f766e"\n'
# What frontend.set_theme leaves in .storage on a home set up with v1.0.0.
STORED_THEME = {"version": 1, "minor_version": 1, "key": "frontend_theme",
                "data": {"frontend_default_theme": "DarTec",
                         "frontend_default_dark_theme": None}}
DASHBOARD = "dartec-brandtest"


def driver(name: str, token: str, step: str) -> dict:
    out = docker("exec", name, "python3", "/brand_driver.py", token, step)
    return json.loads(out.strip().splitlines()[-1])


def send(name: str, command: dict) -> None:
    """Queue a command for the stub manager to send on the next snapshot."""
    body = json.dumps(command).replace("'", "'\"'\"'")
    docker("exec", name, "sh", "-c",
           f"printf '%s' '{body}' > {STATE_DIR}/.q && mv {STATE_DIR}/.q "
           f"{STATE_DIR}/send-{command['id']}.json")


def run_version(version: str, keep: bool, timeout: float) -> list[str]:
    name = f"dartec-brand-{version.replace('.', '-')}"
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dartec-brand-") as tmp:
        config = prepare_config(Path(tmp))
        yaml = config / "configuration.yaml"
        yaml.write_text(yaml.read_text(encoding="utf-8")
                        + "frontend:\n  themes: !include_dir_merge_named themes\n",
                        encoding="utf-8")
        (config / "themes").mkdir()
        (config / "themes" / "dartec.yaml").write_text(THEME_YAML, encoding="utf-8")
        (config / ".storage").mkdir()
        (config / ".storage" / "frontend_theme").write_text(json.dumps(STORED_THEME),
                                                          encoding="utf-8")
        log(f"{version}: starting {IMAGE}:{version} as {name}")
        port = start_container(name, version, config)
    docker("cp", str(HERE / "brand_driver.py"), f"{name}:/brand_driver.py")
    base = f"http://127.0.0.1:{port}"
    try:
        wait_for("Home Assistant to serve onboarding",
                 lambda: http("GET", f"{base}/api/onboarding") is not None, timeout)
        token = onboard(base)
        docker("exec", "-d", "-e", "DARTEC_LIVE_SCENARIO=update", name,
               "python3", "/stub_manager.py")
        wait_for("the stub manager", lambda: docker(
            "exec", name, "curl", "-sf", f"http://127.0.0.1:{STUB_PORT}/health"), 30, 1)
        wait_for("Home Assistant to finish starting",
                 lambda: http("GET", f"{base}/api/config", token=token).get("state") == "RUNNING",
                 timeout)
        pair(base, token)
        wait_for("the first snapshot", lambda: latest_snapshot(name)[0] >= 1, timeout)
        log(f"{version}: paired on port {port}")

        _, first = latest_snapshot(name)
        theme = (first or {}).get("frontend_theme") or {}
        expected = {"stored_default": "DarTec", "default": "default"}
        if {k: theme.get(k) for k in expected} != expected or \
                "Dartec" not in (theme.get("available") or []):
            problems.append(f"frontend_theme is {theme}; expected {expected} with 'Dartec' "
                            "available (a default stored under the old name, which Home "
                            "Assistant is not running)")
        branding = (first or {}).get("branding")
        if branding != {"enabled": False, "title": "Dartec"}:
            problems.append(f"branding is {branding}, expected the defaults "
                            "{'enabled': False, 'title': 'Dartec'}")

        staged = driver(name, token, "stage")
        log(f"{version}: staged {staged}")
        if not (staged.get("dashboard_created") and staged.get("entry_retitled")):
            problems.append(f"could not stage the old spellings: {staged}")
        if staged.get("entry_title_after_reload") != "Dartec: Live test / Integration rig":
            problems.append(f"after a reload the agent's entry is titled "
                            f"{staged.get('entry_title_after_reload')!r}, not corrected to "
                            "'Dartec: Live test / Integration rig'")

        send(name, {"id": "rename", "action": "lovelace_update", "url_path": DASHBOARD,
                    "title": "Dartec Dashboard"})
        renamed = wait_for("the rename's answer", lambda: read_state(name, "result-rename.json"),
                           timeout)
        if not (renamed.get("ok") and renamed.get("previous_title") == "DarTec Dashboard"):
            problems.append(f"lovelace_update answered {renamed}")
        send(name, {"id": "missing", "action": "lovelace_update", "url_path": "no-such-board",
                    "title": "X"})
        missing = wait_for("the missing dashboard's answer",
                           lambda: read_state(name, "result-missing.json"), timeout)
        if missing.get("ok") is not False or "not found" not in str(missing.get("detail")):
            problems.append(f"renaming a dashboard that does not exist answered {missing}")

        renamed_at, _ = latest_snapshot(name)

        def after():
            number, snap = latest_snapshot(name)
            return snap if number > renamed_at and snap else None

        snap = wait_for("a snapshot after the rename", after, timeout)
        titles = {d.get("url_path"): d.get("title") for d in snap.get("dashboards") or []}
        if titles.get(DASHBOARD) != "Dartec Dashboard":
            problems.append(f"the snapshot's dashboards say {titles.get(DASHBOARD)!r} for "
                            f"{DASHBOARD}")
        ours = [e.get("title") for e in snap.get("integrations") or []
                if e.get("domain") == AGENT_DOMAIN]
        if ours != ["Dartec: Live test / Integration rig"]:
            problems.append(f"the snapshot's integration title is {ours}")
        misspelt = [f"{path}" for path in re.findall(r"Dar[ -]?Tec[^\"]*", json.dumps(
            {k: snap.get(k) for k in ("dashboards", "integrations", "branding")}))]
        if misspelt:
            problems.append(f"the snapshot still carries {misspelt}")

        read = driver(name, token, "read")
        if read.get("dashboard_title") != "Dartec Dashboard":
            problems.append(f"Home Assistant says the dashboard is {read.get('dashboard_title')!r}")
        if not any("renamed dashboard" in line and "'DarTec Dashboard'" in line
                   for line in read.get("logbook") or []):
            problems.append(f"no logbook line for the rename: {read.get('logbook')}")

        text = ha_log(name)
        errors = [line.strip() for line in text.splitlines()
                  if re.search(r"\b(ERROR|CRITICAL)\b", line) and AGENT_DOMAIN in line]
        if errors:
            problems.append("Home Assistant logged against the agent:\n  "
                            + "\n  ".join(errors[:20]))
    except Failure as err:
        problems.append(str(err))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
        problems.append(f"{type(err).__name__}: {err}")
    finally:
        if keep:
            log(f"{version}: left running at {base} (docker rm -f -v {name})")
        else:
            docker("rm", "-f", "-v", name, check=False)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("versions", nargs="*", default=DEFAULT_VERSIONS)
    parser.add_argument("--keep", action="store_true", help="leave containers running")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    failed = False
    for version in args.versions:
        started = time.monotonic()
        problems = run_version(version, args.keep, args.timeout)
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
