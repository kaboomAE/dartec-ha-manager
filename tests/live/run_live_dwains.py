"""Dwains Dashboard Next's card pop-up, with and without the agent's dashboard fix.

dartec-ha-manager#25: on a home running Dwains Dashboard Next with the Dartec
theme, opening Dwains' "add card" pop-up logged, three times,

    TypeError: can't access property "formatEntityState", this._formatters is undefined

from Home Assistant's thermostat control, which the pop-up renders as a
preview. The agent injects one module into every page, `www/dashboard-fix.js`
(the dark-mode notification fix), so it was a suspect. This runs the question
rather than arguing it.

For each Home Assistant version given, it starts the agent from this working
tree the way run_live.py does, pairs it, installs the exact frontend pieces the
affected home runs (pinned below: Dwains Dashboard Next, card-mod and the
Dartec theme, fetched from their repositories at those tags), creates a Dwains
dashboard, and then, in Chromium, opens the pop-up in every combination of:

* Firefox (the owner's browser) and Chromium;
* the agent's dashboard fix loaded, or blocked at the network;
* light and dark mode;
* card-mod loaded, or not;
* the Dartec theme, or Home Assistant's default.

Each combination is a fresh page load. The console and uncaught errors are
recorded, with a screenshot of the pop-up, and the table is printed. The
demo thermostat is given a `target_temp_step`, as real ones have, so the
error reads exactly as it did on the home.

It then isolates the cause: the same Dwains card host, with the same `hass`,
mounted once under `document.body` (where Dwains puts its pop-ups) and once
inside `<home-assistant>`.

What it asserts:

* **The fix never causes an error of its own.** For every combination, the
  errors with the fix loaded are exactly the errors without it. This is the
  regression check the issue asked for.
* **The cause is where Dwains mounts its pop-up.** The card host errors under
  `document.body` and renders cleanly inside `<home-assistant>`, in both
  browsers, with the fix blocked. If that stops being true the diagnosis in
  #25 is out of date, and the test says so.
* **The fix still works.** In dark mode with the Dartec theme, a notification
  row in Dwains' own notification panel reaches 4.5:1 between its text and
  its background.
* With `--expect-clean`, no combination logs the formatter error at all. That
  is the check to turn on once Dwains is fixed upstream; until then the error
  is reported, not failed on, because it is not the agent's.

Needs Docker, network access to GitHub, and Playwright with Chromium
(`pip install playwright && playwright install chromium`).

    python tests/live/run_live_dwains.py                          # 2026.9.3
    python tests/live/run_live_dwains.py 2026.9.3 --artifacts out # screenshots + table
    python tests/live/run_live_dwains.py 2026.9.3 --keep
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_live import (  # noqa: E402
    AGENT_DOMAIN, IMAGE, STUB_PORT, Failure, docker, ha_log, http, latest_snapshot, log,
    onboard, pair, prepare_config, start_container, wait_for)

# What the affected home ran on 2026-09-18, from its snapshot.
DEFAULT_VERSIONS = ["2026.9.3"]
ASSETS = {
    "dwains-dashboard-next.js": ("dwainscheeren/dwains-dashboard-next", "v1.8.0",
                                 "dist/dwains-dashboard-next.js"),
    "card-mod.js": ("thomasloven/lovelace-card-mod", "v4.2.1", "card-mod.js"),
    "dartec.yaml": ("kaboomAE/dartec-theme", "v1.1.0", "themes/dartec.yaml"),
}
USER, PASSWORD = "livetest", "live-test-password"
FIX_URL = "**/dartec_branding/dashboard-fix.js"
DASHBOARD = "dwains-dashboard"
CARD_MOD = "/local/card-mod.js"
# Home Assistant hands cards its formatters and its config through Lit context
# from <home-assistant>. A card mounted outside it finds both undefined; which
# is read first depends on the entity (a thermostat with a target_temp_step
# never reads the config), so either message is the same failure.
CONTEXT_MISSING = re.compile(r"_formatters|formatEntity|this\._config is undefined|"
                             r"reading 'config'")
ENGINES = ("firefox", "chromium")


def fetch_assets(cache: Path) -> dict[str, Path]:
    cache.mkdir(parents=True, exist_ok=True)
    paths = {}
    for filename, (repo, tag, path) in ASSETS.items():
        target = cache / f"{repo.replace('/', '_')}-{tag}-{filename}"
        if not target.exists():
            url = f"https://raw.githubusercontent.com/{repo}/{tag}/{path}"
            with urllib.request.urlopen(url, timeout=60) as response:
                target.write_bytes(response.read())
        paths[filename] = target
    return paths


def post(url: str, **kwargs) -> dict:
    return http("POST", url, **kwargs) or {}


def browser_tokens(base: str) -> dict:
    """Signs in through the login flow in code and returns tokens shaped the way
    the frontend keeps them, so nothing is typed into a sign-in form."""
    client_id = f"{base}/"
    flow = post(f"{base}/auth/login_flow", json_body={
        "client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id})
    step = post(f"{base}/auth/login_flow/{flow['flow_id']}", json_body={
        "username": USER, "password": PASSWORD, "client_id": client_id})
    got = post(f"{base}/auth/token", form={"grant_type": "authorization_code",
                                            "code": step["result"], "client_id": client_id})
    return {**got, "hassUrl": base, "clientId": client_id,
            "expires": int(time.time() * 1000) + got["expires_in"] * 1000}


def finish_onboarding(base: str, token: str) -> None:
    """The live test only creates the owner; the frontend sends anyone to
    onboarding until the other steps are done too."""
    for step, body in (("core_config", {}), ("analytics", {}),
                       ("integration", {"client_id": f"{base}/", "redirect_uri": f"{base}/"})):
        try:
            post(f"{base}/api/onboarding/{step}", json_body=body, token=token)
        except urllib.error.HTTPError as err:
            if err.code != 403:  # that step is already done
                raise


# ---------------------------------------------------------------- the browser

WS = """async (msg) => {
  let ha = document.querySelector("home-assistant");
  for (let i = 0; i < 150 && !(ha && ha.hass && ha.hass.connection); i++) {
    await new Promise((r) => setTimeout(r, 100));
    ha = document.querySelector("home-assistant");
  }
  return await ha.hass.connection.sendMessagePromise(msg);
}"""

# A breadth-first walk through every shadow root, shared by the probes below.
# Dwains' markup is not an API and neither is its nesting, so nothing here
# depends on where an element sits, only on what it is.
WALK = """const walk = (test) => {
  const seen = new Set(); const queue = [document];
  while (queue.length) {
    const root = queue.shift();
    for (const el of root.querySelectorAll("*")) {
      if (test(el)) return el;
      if (el.shadowRoot && !seen.has(el)) { seen.add(el); queue.push(el.shadowRoot); }
    }
  }
  return null;
};
const until = async (fn, tries = 150) => {
  for (let i = 0; i < tries; i++) { const v = fn(); if (v) return v;
    await new Promise((r) => setTimeout(r, 100)); }
  return null;
};"""

# In edit mode each area has an "Add card" button that calls the layout card's
# _addCard. Unless Home Assistant's own card picker has already been loaded
# on the page, that opens Dwains' own picker, the one in the reported trace
# (showDialog -> _mountPreviews). Calling _addCard is pressing that button
# without depending on where Dwains draws it.
OPEN_PICKER = """async () => {
  %s
  const host = await until(() => walk((el) => el.localName === "dwains-dashboard-next-layout-card"
                                                && typeof el._addCard === "function"));
  if (!host) return "no element with _addCard";
  const area = Object.keys(host.hass.areas || {})[0] || "living_room";
  host._addCard(area, "bottom", 0);
  return customElements.get("hui-dialog-create-card") ? "native" : "dwains";
}""" % WALK

PICKER_READY = """() => {
  const dialog = document.querySelector("dwains-dashboard-next-card-editor-dialog");
  const root = dialog && dialog.shadowRoot;
  const host = root && root.querySelector(".dd-preview-host[data-card-type=thermostat]");
  return !!(host && host.childElementCount > 0);
}"""

WHERE_DIALOG = """() => {
  const dialog = document.querySelector("dwains-dashboard-next-card-editor-dialog");
  if (!dialog) return null;
  return {parent: dialog.parentNode && (dialog.parentNode.localName || "#document"),
          inside_home_assistant: !!dialog.closest("home-assistant")};
}"""

# The fix's own target: Dwains' notification panel draws each row with a
# hardcoded near-white background. Measured the way a reader meets it, the
# row's background against the colour of its text.
NOTIFICATION_CONTRAST = """async () => {
  %s
  const row = await until(() => walk((el) => el.classList && el.classList.contains("notification-row")), 50);
  if (!row) return null;
  const rgb = (s) => (s.match(/[\\d.]+/g) || []).slice(0, 3).map(Number);
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const leaf = [...row.querySelectorAll("*")].find((el) => el.children.length === 0 &&
                                                   el.textContent.trim()) || row;
  const bg = getComputedStyle(row).backgroundColor, fg = getComputedStyle(leaf).color;
  const [a, b] = [lum(rgb(bg)), lum(rgb(fg))].sort((x, y) => y - x);
  return {background: bg, color: fg, contrast: Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100};
}""" % WALK

# The panel the bell opens: the same two fields its button sets.
OPEN_NOTIFICATIONS = """async () => {
  %s
  const host = await until(() => walk((el) => el.localName === "dwains-dashboard-next-layout-card"
                                                && "_notificationsOpen" in el));
  if (!host) return null;
  if (typeof host._loadPersistentNotifications === "function")
    await host._loadPersistentNotifications();
  host._notificationsOpen = true;
  host.requestUpdate && host.requestUpdate();
  return host.localName;
}""" % WALK


class Combo:
    def __init__(self, engine: str, fix: bool, scheme: str, card_mod: bool, theme: str):
        self.engine, self.fix, self.scheme = engine, fix, scheme
        self.card_mod, self.theme = card_mod, theme

    @property
    def key(self) -> str:
        return (f"{self.engine}-{'fix' if self.fix else 'nofix'}-{self.scheme}-"
                f"{'cardmod' if self.card_mod else 'nocardmod'}-{self.theme.lower()}")


def new_context(browser, stored: dict, scheme: str, fix: bool):
    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
    context.add_init_script(f"localStorage.setItem('hassTokens', "
                            f"{json.dumps(json.dumps(stored))});")
    if not fix:
        context.route(FIX_URL, lambda route: route.fulfill(
            status=200, content_type="application/javascript", body="/* blocked by the test */"))
    return context


def setup_home(page) -> None:
    """Resources and the dashboard, through the frontend's own connection."""
    ws = lambda msg: page.evaluate(WS, msg)  # noqa: E731
    ws({"type": "lovelace/resources/create", "res_type": "module",
        "url": "/local/dwains-dashboard-next.js"})
    ws({"type": "lovelace/dashboards/create", "url_path": DASHBOARD,
        "title": "Dartec Dashboard", "icon": "mdi:view-dashboard-variant",
        "mode": "storage", "show_in_sidebar": True, "require_admin": False})
    ws({"type": "lovelace/config/save", "url_path": DASHBOARD,
        "config": {"strategy": {"type": "custom:dwains-dashboard-next"}}})
    ws({"type": "call_service", "domain": "persistent_notification", "service": "create",
        "service_data": {"title": "Front door", "message": "The front door was left open.",
                         "notification_id": "dartec_live_door"}})


def set_card_mod(page, on: bool) -> None:
    listed = page.evaluate(WS, {"type": "lovelace/resources"})
    present = [r for r in listed if r["url"] == CARD_MOD]
    if on and not present:
        page.evaluate(WS, {"type": "lovelace/resources/create", "res_type": "module",
                           "url": CARD_MOD})
    if not on:
        for row in present:
            page.evaluate(WS, {"type": "lovelace/resources/delete", "resource_id": row["id"]})


def set_theme(page, theme: str) -> None:
    for mode in ("light", "dark"):
        page.evaluate(WS, {"type": "call_service", "domain": "frontend", "service": "set_theme",
                           "service_data": {"name": theme, "mode": mode}})


def run_combo(browser, base: str, stored: dict, combo: Combo, shots: Path | None) -> dict:
    context = new_context(browser, stored, combo.scheme, combo.fix)
    page = context.new_page()
    errors: list[str] = []
    fix_served: list[int] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"{e.name}: {e.message}"))
    page.on("response", lambda r: fix_served.append(len(r.body())) if
            "dashboard-fix.js" in r.url else None)
    try:
        page.goto(f"{base}/{DASHBOARD}", wait_until="load")
        opened = page.evaluate(OPEN_PICKER)
        if opened.startswith("no element"):
            raise Failure(f"{combo.key}: could not open the add-card pop-up ({opened})")
        page.wait_for_function(PICKER_READY, timeout=30000)
        page.wait_for_timeout(3000)   # the resize controller logs its copy late
        where = page.evaluate(WHERE_DIALOG)
        if shots:
            page.screenshot(path=str(shots / f"{combo.key}.png"))
        formatter = [e for e in errors if CONTEXT_MISSING.search(e)]
        return {"combo": combo.key, "engine": combo.engine, "fix_loaded": combo.fix,
                "scheme": combo.scheme, "card_mod": combo.card_mod, "theme": combo.theme,
                "formatter_errors": len(formatter),
                "formatter_messages": sorted({e[:200] for e in formatter}),
                "other_errors": sorted({e[:200] for e in errors
                                        if not CONTEXT_MISSING.search(e)}),
                "sample": formatter[0][:400] if formatter else "", "dialog": where,
                "picker": opened,
                # Real module: several KB. Blocked: the stub's few bytes.
                "fix_bytes": fix_served[0] if fix_served else None}
    finally:
        context.close()


def check_notification_fix(browser, base: str, stored: dict, shots: Path | None) -> dict:
    """Dark mode, Dartec theme: the notification row with the fix and without it."""
    out = {}
    for fix in (True, False):
        context = new_context(browser, stored, "dark", fix)
        page = context.new_page()
        page.goto(f"{base}/{DASHBOARD}", wait_until="load")
        opened = page.evaluate(OPEN_NOTIFICATIONS)
        page.wait_for_timeout(1500)
        measured = page.evaluate(NOTIFICATION_CONTRAST)
        if shots:
            page.screenshot(path=str(shots / f"notifications-{'fix' if fix else 'nofix'}.png"))
        out["fix" if fix else "nofix"] = {"opened_with": opened, **(measured or {"row": None})}
        context.close()
    return out


# The same card Dwains' pop-up previews, mounted by hand in the two places.
MOUNT = """async (where) => {
  const ha = document.querySelector("home-assistant");
  await customElements.whenDefined("dwains-dashboard-next-card-host");
  const box = document.createElement("div");
  const host = document.createElement("dwains-dashboard-next-card-host");
  host.setAttribute("eager", "");
  host.hass = ha.hass;
  host.config = {type: "thermostat", entity: Object.keys(ha.hass.states)
                                               .find((e) => e.startsWith("climate."))};
  box.appendChild(host);
  (where === "body" ? document.body : ha.shadowRoot).appendChild(box);
  await new Promise((r) => setTimeout(r, 2500));
  return host.childElementCount;
}"""


def check_cause(pw, base: str, stored: dict) -> dict:
    out = {}
    for engine in ENGINES:
        browser = getattr(pw, engine).launch()
        for where in ("body", "home-assistant"):
            context = new_context(browser, stored, "light", False)
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(e.message[:200]))
            page.goto(f"{base}/{DASHBOARD}", wait_until="load")
            page.evaluate(OPEN_NOTIFICATIONS)   # returns once Dwains is up
            page.evaluate(MOUNT, where)
            out[f"{engine}-{where}"] = {
                "context_errors": sum(1 for e in errors if CONTEXT_MISSING.search(e)),
                "sample": errors[0] if errors else ""}
            context.close()
        browser.close()
    return out


def give_thermostat_a_step(base: str, token: str) -> str:
    """Real thermostats report their step; the demo's do not."""
    states = http("GET", f"{base}/api/states", token=token)
    climate = next(s for s in states if s["entity_id"].startswith("climate."))
    attributes = {**climate["attributes"], "target_temp_step": 0.5}
    http("POST", f"{base}/api/states/{climate['entity_id']}", token=token,
         json_body={"state": climate["state"], "attributes": attributes})
    return climate["entity_id"]


def table(rows: list[dict]) -> str:
    lines = ["| Browser | Dashboard fix | Mode | card-mod | Theme | Formatter errors "
             "| Other errors |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['engine']} | {'loaded' if r['fix_loaded'] else 'blocked'} | "
                     f"{r['scheme']} | "
                     f"{'on' if r['card_mod'] else 'off'} | {r['theme']} | "
                     f"{r['formatter_errors']} | {len(r['other_errors'])} |")
    return "\n".join(lines)


def run_version(version: str, keep: bool, artifacts: Path | None, timeout: float,
                expect_clean: bool) -> list[str]:
    from playwright.sync_api import sync_playwright

    name = f"dartec-dwains-{version.replace('.', '-')}"
    problems: list[str] = []
    assets = fetch_assets(Path(tempfile.gettempdir()) / "dartec-live-assets")
    with tempfile.TemporaryDirectory(prefix="dartec-dwains-") as tmp:
        config = prepare_config(Path(tmp))
        yaml = config / "configuration.yaml"
        yaml.write_text(yaml.read_text(encoding="utf-8")
                        + "frontend:\n  themes: !include_dir_merge_named themes\n",
                        encoding="utf-8")
        for folder, filename in (("www", "dwains-dashboard-next.js"), ("www", "card-mod.js"),
                                 ("themes", "dartec.yaml")):
            (config / folder).mkdir(exist_ok=True)
            (config / folder / filename).write_bytes(assets[filename].read_bytes())
        log(f"{version}: starting {IMAGE}:{version} as {name}")
        port = start_container(name, version, config)
    base = f"http://127.0.0.1:{port}"
    shots = None
    if artifacts:
        shots = artifacts / f"dwains-{version}"
        shots.mkdir(parents=True, exist_ok=True)
    try:
        wait_for("Home Assistant to serve onboarding",
                 lambda: http("GET", f"{base}/api/onboarding") is not None, timeout)
        token = onboard(base)
        # The update scenario sends nothing unprompted; this test needs only a
        # paired agent, so that its modules are served the way a home serves them.
        docker("exec", "-d", "-e", "DARTEC_LIVE_SCENARIO=update", name,
               "python3", "/stub_manager.py")
        wait_for("the stub manager", lambda: docker(
            "exec", name, "curl", "-sf", f"http://127.0.0.1:{STUB_PORT}/health"), 30, 1)
        wait_for("Home Assistant to finish starting",
                 lambda: http("GET", f"{base}/api/config", token=token).get("state") == "RUNNING",
                 timeout)
        pair(base, token)
        wait_for("the first snapshot", lambda: latest_snapshot(name)[0] >= 1, timeout)
        finish_onboarding(base, token)
        stored = browser_tokens(base)
        climate = give_thermostat_a_step(base, token)
        log(f"{version}: paired on port {port}; {climate} is the previewed thermostat")

        combos = [Combo(*c) for c in itertools.product(
            ENGINES, (True, False), ("light", "dark"), (True, False), ("Dartec", "default"))]
        rows: list[dict] = []
        with sync_playwright() as pw:
            browsers = {engine: getattr(pw, engine).launch() for engine in ENGINES}
            browser = browsers["chromium"]
            context = new_context(browser, stored, "light", True)
            admin = context.new_page()
            admin.goto(f"{base}/profile", wait_until="load")
            setup_home(admin)
            for (card_mod, theme), group in itertools.groupby(
                    sorted(combos, key=lambda c: (not c.card_mod, c.theme)),
                    key=lambda c: (c.card_mod, c.theme)):
                set_card_mod(admin, card_mod)
                set_theme(admin, theme)
                for combo in group:
                    row = run_combo(browsers[combo.engine], base, stored, combo, shots)
                    rows.append(row)
                    log(f"{version}: {combo.key}: {row['formatter_errors']} formatter "
                        f"error(s), {len(row['other_errors'])} other, dialog {row['dialog']}")
            set_card_mod(admin, True)
            set_theme(admin, "Dartec")
            notification = check_notification_fix(browser, base, stored, shots)
            context.close()
            for each in browsers.values():
                each.close()
            cause = check_cause(pw, base, stored)

        rows.sort(key=lambda r: (r["engine"], not r["fix_loaded"], r["scheme"] != "light",
                                 not r["card_mod"], r["theme"] != "Dartec"))
        print(table(rows))
        log(f"{version}: notification row in dark mode: {notification}")
        log(f"{version}: the card host mounted by hand: {cause}")
        for engine in ENGINES:
            outside, inside = cause[f"{engine}-body"], cause[f"{engine}-home-assistant"]
            popup = any(r["formatter_errors"] for r in rows if r["engine"] == engine)
            # Before Home Assistant moved these to context (2026.5 is clean)
            # there is nothing to explain; after it, the pop-up's error and
            # the hand-mounted one outside <home-assistant> must go together.
            if inside["context_errors"] or popup != bool(outside["context_errors"]):
                problems.append(f"{engine}: the pop-up {'errors' if popup else 'is clean'}, "
                                f"but the card host mounted by hand gives body {outside}, "
                                f"inside {inside}; the diagnosis in "
                                "dartec-ha-manager#25 needs revisiting")

        by_key = {r["combo"]: r for r in rows}
        for r in rows:
            if not r["fix_loaded"]:
                if (r["fix_bytes"] or 0) > 100:
                    problems.append(f"{r['combo']}: the fix was meant to be blocked but was "
                                    "served")
                continue
            if (r["fix_bytes"] or 0) < 100:
                problems.append(f"{r['combo']}: the fix was not served, so this combination "
                                "did not test it")
            twin = by_key[r["combo"].replace("-fix-", "-nofix-", 1)]
            # Which errors, not how many: the resize observer adds a copy when it
            # happens to fire before the pop-up settles, fix or no fix.
            if set(r["formatter_messages"]) != set(twin["formatter_messages"]) or \
                    set(r["other_errors"]) != set(twin["other_errors"]):
                problems.append(f"{r['combo']}: the fix changes the errors "
                                f"({r['formatter_messages']} vs {twin['formatter_messages']}"
                                f"; others {r['other_errors']} vs "
                                f"{twin['other_errors']})")
        if expect_clean:
            dirty = [r["combo"] for r in rows if r["formatter_errors"]]
            if dirty:
                problems.append(f"the formatter error still happens in: {dirty}")

        with_fix = notification.get("fix") or {}
        if not with_fix.get("contrast"):
            problems.append(f"no notification row to measure with the fix: {with_fix}")
        elif with_fix["contrast"] < 4.5:
            problems.append(f"the notification row with the fix measures "
                            f"{with_fix['contrast']}:1, below 4.5:1")

        text = ha_log(name)
        errors = [line.strip() for line in text.splitlines()
                  if re.search(r"\b(ERROR|CRITICAL)\b", line) and AGENT_DOMAIN in line]
        if errors:
            problems.append("Home Assistant logged against the agent:\n  "
                            + "\n  ".join(errors[:20]))
        if shots:
            (shots / "results.json").write_text(json.dumps(
                {"versions": {"home_assistant": version,
                              **{f: ASSETS[f][1] for f in ASSETS}},
                 "rows": rows, "notification": notification, "cause": cause},
                indent=1), encoding="utf-8")
            (shots / "table.md").write_text(table(rows) + "\n", encoding="utf-8")
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
    parser.add_argument("--artifacts", type=Path, help="write screenshots and the table here")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--expect-clean", action="store_true",
                        help="also fail if Dwains' pop-up logs the formatter error at all")
    args = parser.parse_args()
    failed = False
    for version in args.versions:
        started = time.monotonic()
        problems = run_version(version, args.keep, args.artifacts, args.timeout,
                               args.expect_clean)
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
