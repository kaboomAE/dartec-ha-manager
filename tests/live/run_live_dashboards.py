"""Render the generated dashboards in real Home Assistant, at wall-panel and
phone sizes, in English and Arabic (recommendation R13, dartec-ha-manager#56).

For each Home Assistant version given, this:

1. starts the agent from this working tree in a fresh container and pairs it
   to the stub manager in its `drive` scenario, as run_live_panels.py does;
2. builds the dashboard test villa from Home Assistant's own helpers
   (`dashboards_driver.py`, inside the container): two floors, seven
   Arabic-named rooms, six ACs, lights, curtains, a leak sensor, raw-named
   relays, a room that is offline and a 26-circuit guest suite, the villa
   the dashboards guide was researched on;
3. applies every dashboard in `docs/dashboards/prototypes/` (today's
   generator and the recommended home, room and bedroom-panel dashboards, in
   English and Arabic) **through the agent**, as the manager does, with
   `lovelace_create`, and fails on any entity or action a dashboard names
   that Home Assistant does not have;
4. opens every view of every dashboard in Chromium at four sizes, phone
   (390 x 844), tablet (800 x 1333, a Galaxy Tab A7 upright), wall
   (1280 x 800) and NSPanel (480 x 480), in the dashboard's language (the
   Arabic ones with the account set to Arabic, so Home Assistant draws right
   to left), and fails on any error card, any "entity not available" warning
   and any view that draws no card at all;
5. measures, on every view, where each "°C" and "%" is drawn against its
   number in the places Home Assistant drew "C° 22.0" in Arabic (tiles,
   badges, heading badges, the area card, the target-temperature stepper),
   and fails if a unit lands before its number in Arabic, if a state word
   the agent translates is still English, or if the agent changed anything
   in English (dartec-ha-manager#55);
6. keeps every screenshot, with the evidence and Home Assistant's log, under
   `--artifacts` (CI uploads them as `live-dashboards-<version>`), so a person
   can look at what a release changed before the fleet takes it.

What this does **not** measure is speed. Headless Chromium shows no cost for
blur or anything else a tablet's GPU pays for, so a timing here would say
nothing about a wall panel. The screenshots are for looking at; the checks
are for what is broken.

Needs Docker, and Playwright with Chromium on the host (`pip install
playwright && python -m playwright install chromium`).

    python tests/live/run_live_dashboards.py                     # 2026.8.3 and 2026.9.2
    python tests/live/run_live_dashboards.py 2026.9.2 --artifacts out --schemes light,dark
    python tests/live/run_live_dashboards.py 2026.9.2 --keep     # leave it up
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
    AGENT_DOMAIN, DEFAULT_VERSIONS, IMAGE, REPO, STUB_PORT, Failure, docker, ha_log, http,
    latest_snapshot, log, onboard, pair, prepare_config, start_container, wait_for)

PROTOTYPES = REPO / "docs" / "dashboards" / "prototypes"

# name: (width, height, device scale, touch phone). The guide's own sizes
# (docs/dashboards/screenshots), so a run can be compared with them.
SIZES = {
    "phone": (390, 844, 2, True),
    "tablet": (800, 1333, 1.5, True),
    "wall": (1280, 800, 1, False),
    "nspanel": (480, 480, 1, True),
}

# Anything in the rendered page that means a card is broken or empty: a card
# that failed to build (`hui-error-card`), and the warning a card shows for an
# entity Home Assistant does not have (`hui-warning`). Searched through every
# shadow root, because that is where Lovelace draws.
FIND_BROKEN = """() => {
  const found = [];
  let cards = 0;
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      const tag = el.tagName.toLowerCase();
      if (tag === 'hui-error-card' || tag === 'hui-warning') {
        const text = (el.textContent || '') + ' ' + (el.shadowRoot ? el.shadowRoot.textContent : '');
        found.push({tag, text: text.replace(/\\s+/g, ' ').trim().slice(0, 300)});
      }
      if (tag === 'ha-card') cards += 1;
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  const ha = document.querySelector('home-assistant');
  const hass = ha && ha.hass;
  return {found, cards, language: hass ? hass.language : null,
          dir: document.documentElement.getAttribute('dir') || document.dir || null,
          view: location.pathname};
}"""


# Where Home Assistant draws a value and its unit in the elements the agent's
# right-to-left correction looks after (www/dashboard-fix.js, #55), and
# whether the unit came out after the number. A "22.0 °C" drawn as "C° 22.0"
# has its "C" to the left of its digits. Also: the agent's isolates and
# stylesheet, so English can be checked for any trace of them, and one of the
# agent's Arabic state words as the frontend has it.
MEASURE_UNITS = r"""() => {
  const runs = [], traces = [];
  const RUN = /([0-9\u0660-\u0669][0-9\u0660-\u0669.,\u066B\u066C]*)[ \u00A0\u202F]?(°C|%)/g;
  const rect = (node, start, end) => {
    const r = document.createRange();
    r.setStart(node, start); r.setEnd(node, end);
    const b = r.getBoundingClientRect();
    return b.width || b.height ? b : null;
  };
  const measure = (where, node) => {
    if (node.nodeType !== 3) return;
    if (/[\u2066-\u2069]/.test(node.data)) traces.push(where + ": " + node.data);
    for (const m of node.data.matchAll(RUN)) {
      const digit = rect(node, m.index, m.index + 1);
      const end = m.index + m[0].length;
      const unit = rect(node, end - 1, end);
      if (!digit || !unit) continue;  // not drawn (hidden, scrolled away)
      runs.push({where, text: m[0], after: unit.left > digit.left});
    }
  };
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      const tag = el.localName;
      if (tag === 'state-display') el.childNodes.forEach((n) => measure(tag, n));
      if (tag === 'ha-tile-info' && el.shadowRoot) {
        el.shadowRoot.querySelectorAll('.secondary').forEach((line) => {
          line.childNodes.forEach((n) => measure(tag, n));
          [...line.children].forEach((span) => span.childNodes.forEach((n) => measure(tag, n)));
        });
      }
      if (tag === 'ha-control-number-buttons' && el.shadowRoot) {
        const box = el.shadowRoot.querySelector('.value');
        const unit = box && box.querySelector('.unit');
        const number = box && [...box.childNodes].find((n) => n.nodeType === 3 && /\d/.test(n.data));
        if (el.shadowRoot.adoptedStyleSheets.length > 1) traces.push(tag + ": extra stylesheet");
        if (number && unit && unit.offsetWidth) {
          const d = rect(number, 0, number.data.length), u = unit.getBoundingClientRect();
          if (d) runs.push({where: tag, text: number.data.trim() + unit.textContent,
                            after: u.left > d.left});
        }
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  const ha = document.querySelector('home-assistant');
  const hass = ha && ha.hass;
  const on = hass && hass.localize ? hass.localize('component.light.entity_component._.state.on') : null;
  return {runs, traces, light_on: on};
}"""

# The agent's word for a light that is on, from the agent itself, and HA's English.
sys.path.insert(0, str(REPO / "custom_components" / AGENT_DOMAIN))
from arabic_states import ON  # noqa: E402

ENGLISH_ON, ARABIC_ON = ON


def screenshots(base: str, dashboards: list[dict], language: str, out: Path,
                schemes: list[str], sizes: list[str]) -> tuple[list[str], list[dict]]:
    """Open every view of the dashboards in `language` at every size. The
    account's own language was set to it before this runs."""
    from household_screenshots import tokens
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    seen: list[dict] = []
    stored = tokens(base)
    boards = [d for d in dashboards if d["language"] == language]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for size in sizes:
            width, height, scale, touch = SIZES[size]
            for scheme in schemes:
                context = browser.new_context(
                    viewport={"width": width, "height": height}, device_scale_factor=scale,
                    is_mobile=touch, has_touch=touch, color_scheme=scheme,
                    locale="ar-AE" if language == "ar" else "en-GB")
                # The panel look: sidebar hidden, as a technician sets it on
                # a wall tablet. A phone keeps Home Assistant's own default.
                sidebar = '"docked"' if size == "phone" else '"always_hidden"'
                context.add_init_script(
                    f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(stored))});"
                    f"localStorage.setItem('dockedSidebar', {json.dumps(sidebar)});")
                page = context.new_page()
                errors: list[str] = []
                page.on("console", lambda msg, errors=errors: errors.append(msg.text)
                        if msg.type == "error" else None)
                page.on("pageerror", lambda err, errors=errors: errors.append(str(err)))
                for board in boards:
                    folder = out / board["url_path"]
                    folder.mkdir(parents=True, exist_ok=True)
                    for view in board["views"]:
                        errors.clear()
                        page.goto(f"{base}/{board['url_path']}/{view}", wait_until="networkidle")
                        page.wait_for_timeout(2500)
                        state = page.evaluate(FIND_BROKEN)
                        units = page.evaluate(MEASURE_UNITS)
                        name = f"{view}-{size}-{scheme}.png"
                        page.screenshot(path=str(folder / name))
                        where = f"{board['url_path']}/{view} at {size} ({scheme})"
                        before = [r for r in units["runs"] if not r["after"]]
                        if language == "ar":
                            if before:
                                problems.append(f"{where}: unit drawn before its number: " + "; ".join(
                                    f"{r['where']} {r['text']!r}" for r in before[:5]))
                            if units["light_on"] not in (None, ARABIC_ON):
                                problems.append(f"{where}: a light that is on reads "
                                                f"{units['light_on']!r}, not {ARABIC_ON!r}")
                        else:
                            if units["traces"]:
                                problems.append(f"{where}: the right-to-left correction acted "
                                                f"in English: {units['traces'][:3]}")
                            if units["light_on"] not in (None, ENGLISH_ON):
                                problems.append(f"{where}: a light that is on reads "
                                                f"{units['light_on']!r} in English")
                        if state["found"]:
                            problems.append(f"{where}: " + "; ".join(
                                f"{f['tag']}: {f['text']}" for f in state["found"][:5]))
                        if not state["cards"]:
                            problems.append(f"{where}: no card was drawn")
                        if state["language"] != language:
                            problems.append(f"{where}: Home Assistant drew it in "
                                            f"{state['language']!r}, not {language!r}")
                        seen.append({"view": where, "file": f"{board['url_path']}/{name}",
                                     "cards": state["cards"], "dir": state["dir"],
                                     "units_measured": len(units["runs"]),
                                     "units_before_number": len(before),
                                     "light_on": units["light_on"],
                                     "console_errors": list(errors)})
                context.close()
        browser.close()
    return problems, seen


def run_version(version: str, keep: bool, artifacts: Path | None, timeout: float,
                schemes: list[str], sizes: list[str]) -> list[str]:
    name = f"dartec-dashboards-{version.replace('.', '-')}"
    with tempfile.TemporaryDirectory(prefix="dartec-dashboards-") as tmp:
        config = prepare_config(Path(tmp))
        log(f"{version}: starting {IMAGE}:{version} as {name}")
        port = start_container(name, version, config)
    docker("cp", str(HERE / "dashboards_driver.py"), f"{name}:/dashboards_driver.py")
    docker("cp", f"{PROTOTYPES}{'/'}.", f"{name}:/prototypes")
    base = f"http://127.0.0.1:{port}"
    problems: list[str] = []
    evidence: dict = {}
    out = (artifacts or Path(tempfile.gettempdir()) / "dartec-dashboards") / f"dashboards-{version}"
    out.mkdir(parents=True, exist_ok=True)
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
        from household_screenshots import finish_onboarding
        finish_onboarding(base, token)
        log(f"{version}: onboarded and paired on port {port}")
        wait_for("the first snapshot", lambda: latest_snapshot(name)[0] >= 1, timeout)

        built = json.loads(docker("exec", name, "python3", "/dashboards_driver.py", "build",
                                  token).strip().splitlines()[-1])
        problems += built["problems"]
        evidence.update(built["evidence"])
        dashboards = evidence["dashboards"] = built["dashboards"]
        log(f"{version}: villa built ({evidence.get('villa', {}).get('entries')} helpers, "
            f"{evidence.get('villa', {}).get('seconds')}s), {len(dashboards)} dashboards applied "
            f"through the agent, {len(built['problems'])} problem(s)")

        shots = []
        for language in ("en", "ar"):
            docker("exec", name, "python3", "/dashboards_driver.py", "language", token, language)
            found, seen = screenshots(base, dashboards, language, out / "screenshots", schemes,
                                      sizes)
            problems += found
            shots += seen
            measured = sum(s["units_measured"] for s in seen)
            # Nothing measured would mean the check checked nothing: the villa
            # has temperatures on every dashboard, so that is a broken check.
            if not measured:
                problems.append(f"{language}: no value with a unit was found to measure")
            log(f"{version}: {len(seen)} screenshots in {language}, {measured} values with "
                f"a unit measured, {len(found)} problem(s)")
        evidence["screenshots"] = shots

        text = ha_log(name)
        errors = [line.strip() for line in text.splitlines()
                  if re.search(r"\b(ERROR|CRITICAL)\b", line) and AGENT_DOMAIN in line]
        if errors:
            problems.append("Home Assistant logged against the agent:\n  "
                            + "\n  ".join(errors[:20]))
        (out / "home-assistant.log").write_text(text, encoding="utf-8")
    except Failure as err:
        problems.append(str(err))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
        problems.append(f"{type(err).__name__}: {err}")
    finally:
        evidence["problems"] = problems
        (out / "evidence.json").write_text(json.dumps(evidence, indent=1, ensure_ascii=False),
                                           encoding="utf-8")
        if problems:
            (out / "container.log").write_text(ha_log(name), encoding="utf-8")
        if keep:
            log(f"{version}: left running at {base} (docker rm -f -v {name})")
        else:
            docker("rm", "-f", "-v", name, check=False)
        log(f"{version}: screenshots and evidence in {out}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("versions", nargs="*", default=DEFAULT_VERSIONS)
    parser.add_argument("--keep", action="store_true", help="leave containers running")
    parser.add_argument("--artifacts", type=Path, help="write screenshots and evidence here")
    parser.add_argument("--schemes", default="light",
                        help="comma-separated colour schemes to shoot (light, dark)")
    parser.add_argument("--sizes", default=",".join(SIZES),
                        help=f"comma-separated sizes to open ({', '.join(SIZES)})")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    schemes = [s for s in args.schemes.split(",") if s in ("light", "dark")] or ["light"]
    sizes = [s for s in args.sizes.split(",") if s in SIZES] or list(SIZES)
    failed = False
    for version in args.versions:
        started = time.monotonic()
        problems = run_version(version, args.keep, args.artifacts, args.timeout, schemes,
                               sizes)
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
