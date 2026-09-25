"""Run room panel accounts inside real Home Assistant containers.

For each Home Assistant version given, this starts the agent from this working
tree (the same way run_live.py does), onboards, pairs it to the stub manager in
its `drive` scenario, and runs `panels_driver.py` inside the container. The
driver sends the manager's commands through the stub, and checks through Home
Assistant's own commands, signed in as the owner and as the panel account
itself, that:

* room dashboards made with `lovelace_create` are storage dashboards, out of
  the sidebar and not admin-only; one made with `require_admin: true` is
  admin-only, and one with `require_admin: "false"` is refused, not made;
* `panel_setup` is refused without the home's consent (commissioning closed,
  no window) and creates nothing; with a window the homeowner opened, it makes
  a non-admin, `local_only` account with no person, that signs in through the
  login flow;
* that account's **own** `frontend/get_user_data` has the room as
  `core.default_panel`, and `sidebar.hiddenPanels` is exactly every panel Home
  Assistant sends it except the room (`lovelace`, `logbook`, `history` and the
  other room among them), and its own `lovelace/config` for the room loads;
* setup again is idempotent: the same account, put back to `local_only`, with
  the new password working and the old one not;
* an administrator's `panel-` username, an admin-only or missing dashboard and
  a short password are refused;
* `panel_status` says it signed in, and when (after the sign-in), and nothing
  about addresses; `panel_update` moves it to another room; `panel_remove`
  deletes it, refuses anyone who is not a panel, and says "already gone" the
  second time;
* `language` and `theme` land in the panel account's **own** `language` and
  `theme` user data (the locale shape that turns it right to left; the
  profile page's `{"theme": name}`), are left alone when not sent, clear with
  `null`, and an unknown language or a theme the home does not have is
  refused before anything is created (#49);
* the snapshot carries `panels` and `core.language`, and `household` does not
  count the panel; My Home neither lists it nor lets it be changed, offers no
  room dashboard, and refuses a new person a `panel-` username;
* the logbook has the refusal, the setup, the update and the removal, and no
  password is in any answer, the snapshot, the logbook or Home Assistant's log.

It also fails on any error Home Assistant logged against the agent.

    python tests/live/run_live_panels.py                 # 2026.8.3 and 2026.9.2
    python tests/live/run_live_panels.py 2026.9.2 --keep # leave it up
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

# The same made-up passwords as panels_driver.py (which cannot be imported
# here: it needs aiohttp, which only the container has).
PASSWORD_1 = "kq7m-wd3p-zr9h-x2fa"
PASSWORD_2 = "tb4n-8vce-hm2j-q6ys"

# One real theme, so `theme` is checked against what Home Assistant loaded
# rather than only against its built-in `default`. Same name as panels_driver.
THEME = "Dartec Glass Lite"
THEME_YAML = f"""\
{THEME}:
  primary-color: "#1a6b6b"
  ha-card-backdrop-filter: none
"""


def add_theme(config: Path) -> None:
    (config / "themes").mkdir()
    (config / "themes" / "dartec.yaml").write_text(THEME_YAML, encoding="utf-8")
    with (config / "configuration.yaml").open("a", encoding="utf-8") as handle:
        handle.write("frontend:\n  themes: !include_dir_merge_named themes\n")


def run_version(version: str, keep: bool, artifacts: Path | None, timeout: float) -> list[str]:
    name = f"dartec-panels-{version.replace('.', '-')}"
    with tempfile.TemporaryDirectory(prefix="dartec-panels-") as tmp:
        config = prepare_config(Path(tmp))
        add_theme(config)
        log(f"{version}: starting {IMAGE}:{version} as {name}")
        port = start_container(name, version, config)
    docker("cp", str(HERE / "panels_driver.py"), f"{name}:/panels_driver.py")
    base = f"http://127.0.0.1:{port}"
    problems: list[str] = []
    evidence: dict = {}
    try:
        wait_for("Home Assistant to serve onboarding",
                 lambda: http("GET", f"{base}/api/onboarding") is not None, timeout)
        token = onboard(base)
        docker("exec", "-d", "-e", "DARTEC_LIVE_SCENARIO=drive", name,
               "python3", "/stub_manager.py")
        wait_for("the stub manager", lambda: docker(
            "exec", name, "curl", "-sf", f"http://127.0.0.1:{STUB_PORT}/health"), 30, 1)
        wait_for("Home Assistant to finish starting",
                 lambda: http("GET", f"{base}/api/config", token=token).get("state") == "RUNNING",
                 timeout)
        pair(base, token)
        log(f"{version}: onboarded and paired on port {port}")
        wait_for("the first snapshot", lambda: latest_snapshot(name)[0] >= 1, timeout)

        out = docker("exec", name, "python3", "/panels_driver.py", token)
        result = json.loads(out.strip().splitlines()[-1])
        problems += result["problems"]
        evidence = result["evidence"]
        log(f"{version}: driver finished, {len(result['problems'])} problem(s), "
            f"{len(evidence.get('answers', []))} command(s) answered, sidebar hides "
            f"{len((evidence.get('sidebar') or {}).get('hiddenPanels') or [])}")

        text = ha_log(name)
        errors = [line.strip() for line in text.splitlines()
                  if re.search(r"\b(ERROR|CRITICAL)\b", line) and AGENT_DOMAIN in line]
        reported = [line.strip() for line in text.splitlines()
                    if re.search(rf"integration '{AGENT_DOMAIN}'", line)]
        if errors or reported:
            problems.append("Home Assistant logged against the agent:\n  "
                            + "\n  ".join((errors + reported)[:20]))
        if PASSWORD_1 in text or PASSWORD_2 in text:
            problems.append("a panel password is in Home Assistant's log")
        if artifacts:
            folder = artifacts / f"panels-{version}"
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
            folder = artifacts / f"panels-{version}"
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
