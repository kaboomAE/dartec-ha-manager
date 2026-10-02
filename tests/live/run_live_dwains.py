"""Dwains Dashboard Next's card pop-up, with and without the agent's dashboard fix.

dartec-ha-manager#25: on a home running Dwains Dashboard Next with the Dartec
theme, opening Dwains' "add card" pop-up logged, three times,

    TypeError: can't access property "formatEntityState", this._formatters is undefined

from Home Assistant's thermostat control, which the pop-up renders as a
preview. The agent injects one module into every page, `www/dashboard-fix.js`
(the dark-mode notification fix), so it was a suspect. It was not: the cause
was Dwains mounting its pop-ups on `document.body`, outside the
`<home-assistant>` element whose context gives HA's controls their formatters
from 2026.7 (reported upstream as dwains-dashboard-next#18). From 0.21.0 the
same module worked around it, and from 0.21.1 and 0.21.2 two more Dwains bugs
(#19, the card editor forgetting its choices; #20, Home custom cards gone after
a reload).

**Dwains fixed all three in v1.8.1** (2026-09-30). The workarounds stay for
homes still on 1.8.0, but act only where Dwains did not: the picker is moved,
and handed its config back, only when Dwains put it on `document.body`, which
only releases before 1.8.1 do; and `home_custom_cards` is carried only where
Dwains left it out. This runner proves both halves.

For each Home Assistant version given, it starts the agent from this working
tree the way run_live.py does, pairs it, installs the frontend pieces a home
runs (Dwains Dashboard Next at `--dwains`, its whole `dist` folder since 1.10.0
loads chunks next to its entry file; card-mod and the Dartec theme pinned
below, all fetched from their repositories at those tags), creates a Dwains
dashboard, and then opens the pop-up in every combination of:

* Firefox (the owner's browser) and Chromium;
* the agent's dashboard fix as served, blocked at the network, or (with
  `--fix-file`, e.g. the copy a released agent ships) replaced by another;
* light and dark mode;
* card-mod loaded, or not;
* the Dartec theme, or Home Assistant's default.

`--bench URL` runs the same matrix against a real Home Assistant that already
has Dwains installed instead of a container: the bench, never a home. It
refuses unless the instance's location name is `--location` (default
`TEST`). There the fix "as served" is the released agent's, and a third mode,
`tree`, substitutes this working tree's copy at the network, so the new fix is
tested on the bench without installing anything. What it changes there (a
dashboard, a notification, a state-only thermostat and tick sensor, the
default themes) is put back or removed at the end; the Dwains dashboard stays.

Each combination is a fresh page load. The console and uncaught errors are
recorded, with a screenshot of the pop-up, and the table is printed. The
thermostat is given a `target_temp_step`, as real ones have, so the error
reads exactly as it did on the home. The pop-up is then closed and opened
again.

It also isolates the cause: the same Dwains card host, with the same `hass`,
mounted once under `document.body` and once inside `<home-assistant>`.

What it asserts:

* **With any fix loaded, the pop-up is clean.** No formatter error, the pop-up
  sits inside `<home-assistant>`, closing it leaves no more behind than
  without the fix, it opens again, and picking the thermostat opens Dwains'
  editor with a clean live preview. In that editor, an entity chosen survives
  a Home Assistant update and a change to another option
  (dwains-dashboard-next#19). And the fix adds no error of its own.
* **On Dwains 1.8.1 and later, upstream is clean on its own**: the same with
  the fix blocked, and a saved Home custom card is drawn after a reload.
* **On Dwains 1.8.1 and later, this tree's fix does nothing to the pop-up**:
  the picker is mounted as many times as without the fix (no second move),
  and the editor's `setConfig` is called as many times per change as without
  it (no second hand-back). The same is measured, and reported, for a
  `--fix-file` or the bench's released fix.
* **The cause is where Dwains mounts its pop-up.** The card host errors under
  `document.body` and renders cleanly inside `<home-assistant>`. Before 1.8.1
  the unfixed pop-up must error exactly when that hand-mounted host does.
* **A saved Home custom card is on the Home page after a reload** with the fix
  (dwains-dashboard-next#20).
* **The notification fix still works.** In dark mode with the Dartec theme, a
  notification row in Dwains' own notification panel reaches 4.5:1 between
  its text and its background. Dwains 1.10.0 still hardcodes the row's
  near-white background, so this part of the fix is not version-gated.
* **The brand's fonts load** (dartec-ha-manager#59). The same module declares
  them: each file is served exactly as shipped, Lateef and Plex Mono load in
  both browsers, Lateef is Arabic only at 150%, and no Dubai face exists.
* Before 1.8.1, without the fix the upstream errors are reported, not failed
  on. With `--expect-clean` they fail too.

Needs Docker (not for `--bench`), network access to GitHub, and Playwright with
Chromium and Firefox (`pip install playwright && playwright install chromium firefox`).

    python tests/live/run_live_dwains.py                                  # 2026.9.3, Dwains v1.10.0
    python tests/live/run_live_dwains.py 2026.9.3 --dwains v1.8.0 --artifacts out
    python tests/live/run_live_dwains.py --dwains v1.10.0 --fix-file released.js
    python tests/live/run_live_dwains.py --bench http://192.168.1.51 \\
        --token-file ~/.dartec-provisioner/bench-ha-token --artifacts out
"""
from __future__ import annotations

import argparse
import io
import itertools
import json
import re
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_live import (  # noqa: E402
    AGENT_DOMAIN, IMAGE, STUB_PORT, Failure, docker, ha_log, http, latest_snapshot, log,
    onboard, pair, prepare_config, start_container, version_tuple, wait_for)

# What the affected home ran on 2026-09-18, from its snapshot.
DEFAULT_VERSIONS = ["2026.9.3"]
DWAINS_REPO = "dwainscheeren/dwains-dashboard-next"
# The release tested on the bench on 2026-10-02 and proposed for approval.
DEFAULT_DWAINS = "v1.10.0"
# The release that fixed #18, #19 and #20 upstream.
FIXED_IN = (1, 8, 1)
ASSETS = {
    "card-mod.js": ("thomasloven/lovelace-card-mod", "v4.2.1", "card-mod.js"),
    "dartec.yaml": ("kaboomAE/dartec-theme", "v1.1.0", "themes/dartec.yaml"),
}
USER, PASSWORD = "livetest", "live-test-password"
# With its cache buster: the agent registers it as dashboard-fix.js?v=<hash>.
FIX_URL = re.compile(r"/dartec_branding/dashboard-fix\.js(\?|$)")
TREE_FIX = HERE.parents[1] / "custom_components" / AGENT_DOMAIN / "www" / "dashboard-fix.js"
DASHBOARD = "dwains-dashboard"
DWAINS_RESOURCE = "/local/dwains-dashboard-next/dwains-dashboard-next.js"
CARD_MOD = "/local/card-mod.js"
# Home Assistant hands cards its formatters and its config through Lit context
# from <home-assistant>. A card mounted outside it finds both undefined; which
# is read first depends on the entity (a thermostat with a target_temp_step
# never reads the config), so either message is the same failure.
CONTEXT_MISSING = re.compile(r"_formatters|formatEntity|this\._config is undefined|"
                             r"reading 'config'")
ENGINES = ("firefox", "chromium")
# The fix as the Home Assistant under test serves it; blocked; this tree's copy
# substituted (bench only); a file given with --fix-file substituted.
SERVED, BLOCKED, TREE, FILE = "served", "blocked", "tree", "file"


def upstream_fixed(dwains: str) -> bool:
    return version_tuple(dwains) >= FIXED_IN


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


def fetch_dwains(cache: Path, tag: str) -> Path:
    """Dwains' whole `dist` folder at `tag`, as HACS downloads it. From 1.10.0
    the entry file only imports `chunks/`, so the single file is not enough."""
    target = cache / f"dwains-{tag}"
    if not (target / "dwains-dashboard-next.js").exists():
        url = f"https://codeload.github.com/{DWAINS_REPO}/tar.gz/refs/tags/{tag}"
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if len(parts) > 2 and parts[1] == "dist" and member.isfile():
                    out = target.joinpath(*parts[2:])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(tar.extractfile(member).read())
    return target


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


def bench_tokens(base: str, token: str) -> dict:
    """A long-lived token shaped as the frontend keeps its tokens. It never
    expires within a run, so the frontend never tries to refresh it."""
    return {"access_token": token, "token_type": "Bearer", "expires_in": 315360000,
            "refresh_token": "", "hassUrl": base, "clientId": f"{base}/",
            "expires": int(time.time() * 1000) + 315360000 * 1000}


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

# Before any page script: count every time Dwains' picker is connected to the
# document. Dwains mounting it is one; the fix moving it is a second. Lit's
# decorator registers it through customElements.define, so that is wrapped.
PROBE = """(() => {
  const TAG = "dwains-dashboard-next-card-editor-dialog";
  window.__ddProbe = {mounted: 0, setConfig: 0};
  const define = customElements.define.bind(customElements);
  customElements.define = function (name, cls, options) {
    if (name === TAG && cls && cls.prototype) {
      const connected = cls.prototype.connectedCallback;
      cls.prototype.connectedCallback = function () {
        window.__ddProbe.mounted++;
        return connected && connected.apply(this, arguments);
      };
    }
    return define(name, cls, options);
  };
})();"""

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

# Wherever it is: Dwains before 1.8.1 put it on document.body, from 1.8.1 it
# mounts it inside <home-assistant> itself, and the fix moves the former.
# Open ones only: from 1.10.0 Home Assistant's own dialog manager keeps a
# closed dialog in the document to reuse it, with its params cleared.
FIND_PICKERS = """const pickers = (all) => {
  const tag = "dwains-dashboard-next-card-editor-dialog";
  const ha = document.querySelector("home-assistant");
  const found = [...document.querySelectorAll(tag),
                 ...((ha && ha.shadowRoot) ? ha.shadowRoot.querySelectorAll(tag) : [])];
  return all ? found : found.filter((d) => d._params);
};"""

PICKER_READY = """() => {
  %s
  const dialog = pickers()[0];
  const root = dialog && dialog.shadowRoot;
  const host = root && root.querySelector(".dd-preview-host[data-card-type=thermostat]");
  return !!(host && host.childElementCount > 0);
}""" % FIND_PICKERS

WHERE_DIALOG = """() => {
  %s
  const dialog = pickers()[0];
  if (!dialog) return null;
  const ha = document.querySelector("home-assistant");
  return {parent: dialog.parentNode === document.body ? "body"
                  : dialog.getRootNode() === (ha && ha.shadowRoot) ? "home-assistant"
                  : String(dialog.parentNode && dialog.parentNode.nodeName),
          inside_home_assistant: dialog.getRootNode() === (ha && ha.shadowRoot),
          mounted: window.__ddProbe ? window.__ddProbe.mounted : null};
}""" % FIND_PICKERS

# Closes it the way its own close button does, then counts what is left open,
# and what is left in the document at all.
CLOSE_PICKER = """async () => {
  %s
  const dialog = pickers()[0];
  if (dialog && typeof dialog.closeDialog === "function") dialog.closeDialog();
  await new Promise((r) => setTimeout(r, 800));
  return {open: pickers().length, present: pickers(true).length};
}""" % FIND_PICKERS

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
    def __init__(self, engine: str, fix: str, scheme: str, card_mod: bool, theme: str):
        self.engine, self.fix, self.scheme = engine, fix, scheme
        self.card_mod, self.theme = card_mod, theme

    @property
    def key(self) -> str:
        return (f"{self.engine}-{self.fix}-{self.scheme}-"
                f"{'cardmod' if self.card_mod else 'nocardmod'}-{self.theme.lower()}")


def new_context(browser, stored: dict, scheme: str, fix: str, fix_files: dict[str, bytes]):
    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
    context.add_init_script(f"localStorage.setItem('hassTokens', "
                            f"{json.dumps(json.dumps(stored))});")
    context.add_init_script(PROBE)
    if fix == BLOCKED:
        context.route(FIX_URL, lambda route: route.fulfill(
            status=200, content_type="application/javascript", body="/* blocked by the test */"))
    elif fix in fix_files:
        body = fix_files[fix]
        context.route(FIX_URL, lambda route: route.fulfill(
            status=200, content_type="application/javascript", body=body))
    return context


def setup_home(page, resource: str | None) -> None:
    """Resources and the dashboard, through the frontend's own connection."""
    ws = lambda msg: page.evaluate(WS, msg)  # noqa: E731
    if resource:
        ws({"type": "lovelace/resources/create", "res_type": "module", "url": resource})
    listed = ws({"type": "lovelace/dashboards/list"})
    if not any(d["url_path"] == DASHBOARD for d in listed):
        ws({"type": "lovelace/dashboards/create", "url_path": DASHBOARD,
            "title": "Dartec Dashboard", "icon": "mdi:view-dashboard-variant",
            "mode": "storage", "show_in_sidebar": True, "require_admin": False})
    # One Home custom card, stored the way Dwains' own settings page saves it.
    ws({"type": "lovelace/config/save", "url_path": DASHBOARD,
        "config": {"strategy": {"type": "custom:dwains-dashboard-next",
                                "home_custom_cards": [HOME_CARD]}}})
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


def set_theme(page, theme: str, dark: str | None = None) -> None:
    for mode, name in (("light", theme), ("dark", dark or theme)):
        page.evaluate(WS, {"type": "call_service", "domain": "frontend", "service": "set_theme",
                           "service_data": {"name": name, "mode": mode}})


# Picks the thermostat the way a click on its tile does, and waits for the
# editor's own live preview of it.
PICK_THERMOSTAT = """async () => {
  %s
  const dialog = pickers()[0];
  const tile = dialog && dialog.shadowRoot &&
      dialog.shadowRoot.querySelector(".dd-preview-host[data-card-type=thermostat]");
  const button = tile && tile.closest("button");
  if (!button) return "no thermostat tile";
  button.click();
  for (let i = 0; i < 100; i++) {
    const preview = dialog.shadowRoot.querySelector(".preview dwains-dashboard-next-card-host");
    if (preview && preview.childElementCount) return "editor";
    await new Promise((r) => setTimeout(r, 100));
  }
  return "no editor preview";
}""" % FIND_PICKERS


# In the editor Dwains opened, choose an entity the way HA's entity picker
# announces it, let a Home Assistant update redraw the editor, change an option
# the way ha-form announces it, then clear that option again. Before 1.8.1
# Dwains never handed the editor its config back (dwains-dashboard-next#19),
# so without the fix the redraw blanks the field and the option change wipes
# the entity. Each step also counts the editor's setConfig calls: the fix
# handing a config back that Dwains has already handed back is one too many.
EDITOR_STEP = """async ([step, entity]) => {
  %s
  const deep = (root, name) => { const q = [root]; while (q.length) { const n = q.shift(); if (!n) continue;
    for (const el of n.querySelectorAll("*")) { if (el.localName === name) return el;
      if (el.shadowRoot) q.push(el.shadowRoot); } } return null; };
  const d = pickers()[0];
  const ed = d && d._configEl;
  if (!ed) return {error: "no editor"};
  if (!ed.__ddCounted) {
    const original = ed.setConfig;
    ed.setConfig = function () { window.__ddProbe.setConfig++; return original.apply(this, arguments); };
    ed.__ddCounted = true;
  }
  const before = window.__ddProbe.setConfig;
  const root = ed.shadowRoot || ed;
  if (step === "entity") {
    const picker = deep(root, "ha-entity-picker");
    if (!picker) return {error: "no entity picker"};
    picker.dispatchEvent(new CustomEvent("value-changed", {detail: {value: entity}, bubbles: true, composed: true}));
  }
  if (step === "option" || step === "clear") {
    const form = deep(root, "ha-form");
    if (!form) return {error: "no form"};
    const value = {...(ed._config || {}), name: "Dartec test"};
    if (step === "clear") delete value.name;
    form.dispatchEvent(new CustomEvent("value-changed", {detail: {value}, bubbles: true, composed: true}));
  }
  await new Promise((r) => setTimeout(r, 300));
  const picker = deep(root, "ha-entity-picker");
  return {card: d._card && d._card.entity, shown: picker ? picker.value : null,
          name: d._card ? (d._card.name === undefined ? null : d._card.name) : null,
          set_config_calls: window.__ddProbe.setConfig - before};
}""" % FIND_PICKERS


class Target:
    """The Home Assistant under test: a container started here, or the bench."""

    def __init__(self, base: str, token: str, stored: dict, *, bench: bool, name: str = "",
                 climate: str = "", resource: str | None = None):
        self.base, self.token, self.stored = base, token, stored
        self.bench, self.name, self.climate, self.resource = bench, name, climate, resource

    def tick(self) -> None:
        """A Home Assistant update, as a busy home sends every few seconds."""
        http("POST", f"{self.base}/api/states/sensor.dartec_live_tick", token=self.token,
             json_body={"state": str(time.time())})


def run_combo(browser, target: Target, combo: Combo, shots: Path | None,
              fix_files: dict[str, bytes]) -> dict:
    context = new_context(browser, target.stored, combo.scheme, combo.fix, fix_files)
    page = context.new_page()
    errors: list[str] = []
    fix_served: list[int] = []
    warnings: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else
            warnings.append(m.text) if m.type == "warning" else None)
    page.on("pageerror", lambda e: errors.append(f"{e.name}: {e.message}"))
    page.on("response", lambda r: fix_served.append(len(r.body())) if
            "dashboard-fix.js" in r.url else None)
    try:
        page.goto(f"{target.base}/{DASHBOARD}", wait_until="load", timeout=60000)
        opened = page.evaluate(OPEN_PICKER)
        if opened.startswith("no element"):
            raise Failure(f"{combo.key}: could not open the add-card pop-up ({opened})")
        page.wait_for_function(PICKER_READY, timeout=30000)
        page.wait_for_timeout(3000)   # the resize controller logs its copy late
        where = page.evaluate(WHERE_DIALOG)
        if shots:
            page.screenshot(path=str(shots / f"{combo.key}.png"))
        # Close, then open again: a moved pop-up is one Dwains can no longer
        # find to clean up, so it has to be gone, and opening must still work.
        formatter = [e for e in errors if CONTEXT_MISSING.search(e)]
        after_close = page.evaluate(CLOSE_PICKER)
        page.evaluate(OPEN_PICKER)
        page.wait_for_function(PICKER_READY, timeout=30000)
        reopened = page.evaluate(f"() => {{ {FIND_PICKERS} return pickers().length; }}")
        before_pick = len(errors)
        picked = page.evaluate(PICK_THERMOSTAT)
        page.wait_for_timeout(2500)
        editor_errors = [e[:200] for e in errors[before_pick:] if CONTEXT_MISSING.search(e)]
        editor = {}
        if picked == "editor":
            climate = target.climate or page.evaluate(
                "() => Object.keys(document.querySelector('home-assistant').hass.states)"
                ".find((e) => e.startsWith('climate.'))")
            editor["chosen"] = page.evaluate(EDITOR_STEP, ["entity", climate])
            target.tick()
            page.wait_for_timeout(2500)
            editor["after_update"] = page.evaluate(EDITOR_STEP, ["look", climate])
            editor["after_option"] = page.evaluate(EDITOR_STEP, ["option", climate])
            editor["after_clear"] = page.evaluate(EDITOR_STEP, ["clear", climate])
            editor["expected"] = climate
        mounted = page.evaluate("() => window.__ddProbe && window.__ddProbe.mounted")
        return {"combo": combo.key, "engine": combo.engine, "fix": combo.fix,
                "scheme": combo.scheme, "card_mod": combo.card_mod, "theme": combo.theme,
                "formatter_errors": len(formatter),
                "formatter_messages": sorted({e[:200] for e in formatter}),
                "other_errors": sorted({e[:200] for e in errors
                                        if not CONTEXT_MISSING.search(e)}),
                "sample": formatter[0][:400] if formatter else "", "dialog": where,
                "picker": opened, "left_after_close": after_close["open"],
                "present_after_close": after_close["present"],
                "pickers_after_reopen": reopened, "mounted": mounted,
                "picked": picked, "editor_errors": editor_errors,
                "editor": editor,
                "already_open": [w for w in warnings if "already open" in w],
                # Real module: several KB. Blocked: the stub's few bytes.
                "fix_bytes": fix_served[0] if fix_served else None}
    finally:
        context.close()


def check_notification_fix(browser, target: Target, modes: list[str], shots: Path | None,
                           fix_files: dict[str, bytes]) -> dict:
    """Dark mode, Dartec theme: the notification row with each fix mode."""
    out = {}
    for fix in modes:
        context = new_context(browser, target.stored, "dark", fix, fix_files)
        page = context.new_page()
        page.goto(f"{target.base}/{DASHBOARD}", wait_until="load", timeout=60000)
        opened = page.evaluate(OPEN_NOTIFICATIONS)
        page.wait_for_timeout(1500)
        measured = page.evaluate(NOTIFICATION_CONTRAST)
        if shots:
            page.screenshot(path=str(shots / f"notifications-{fix}.png"))
        out[fix] = {"opened_with": opened, **(measured or {"row": None})}
        context.close()
    return out


# The same card Dwains' pop-up previews, mounted by hand in the two places.
MOUNT = """async ([where, entity]) => {
  const ha = document.querySelector("home-assistant");
  await customElements.whenDefined("dwains-dashboard-next-card-host");
  const box = document.createElement("div");
  const host = document.createElement("dwains-dashboard-next-card-host");
  host.setAttribute("eager", "");
  host.hass = ha.hass;
  host.config = {type: "thermostat", entity: entity || Object.keys(ha.hass.states)
                                               .find((e) => e.startsWith("climate."))};
  box.appendChild(host);
  (where === "body" ? document.body : ha.shadowRoot).appendChild(box);
  await new Promise((r) => setTimeout(r, 2500));
  return host.childElementCount;
}"""


HOME_CARD = {"id": "home-card-dartec-live",
             "card": {"type": "markdown", "content": "## Dartec live test"}}

# The Home page after a fresh load: does the stored Home custom card reach
# the layout card and get drawn? Dwains 1.8.0 dropped the key between its
# strategy and its layout card (dwains-dashboard-next#20).
HOME_CARDS_SHOWN = """async () => {
  %s
  const lay = await until(() => walk((el) => el.localName === "dwains-dashboard-next-layout-card"));
  if (!lay) return null;
  await new Promise((r) => setTimeout(r, 1500));
  return {config: ((lay.config || {}).home_custom_cards || []).length,
          drawn: lay.shadowRoot ? lay.shadowRoot.querySelectorAll(
            ".home-custom-cards-section dwains-dashboard-next-card-host").length : 0};
}""" % WALK


def check_home_cards(pw, target: Target, modes: list[str], fix_files: dict[str, bytes]) -> dict:
    out = {}
    for engine in ENGINES:
        browser = getattr(pw, engine).launch()
        for fix in modes:
            context = new_context(browser, target.stored, "light", fix, fix_files)
            page = context.new_page()
            page.goto(f"{target.base}/{DASHBOARD}", wait_until="load", timeout=60000)
            out[f"{engine}-{fix}"] = page.evaluate(HOME_CARDS_SHOWN)
            context.close()
        browser.close()
    return out


# The brand's fonts, declared by the same module (dartec-ha-manager#59):
# each face the module declares, after asking the browser for the ones a
# Dartec theme uses, in Arabic and in figures.
FONTS = """async () => {
  const clean = (s) => s.replace(/["']/g, "");
  await Promise.all([
    document.fonts.load("400 16px 'Dartec Lateef'", "بيت"),
    document.fonts.load("700 16px 'Dartec Lateef'", "بيت"),
    document.fonts.load("500 16px 'Dartec Plex Mono'", "0123456789")]);
  return [...document.fonts].filter((f) => /dartec|dubai/i.test(clean(f.family))).map((f) => ({
    family: clean(f.family), weight: f.weight, status: f.status,
    unicodeRange: f.unicodeRange, sizeAdjust: f.sizeAdjust || null}));
}"""
FONT_FILES = ("Lateef-Regular.woff2", "Lateef-Medium.woff2", "Lateef-Bold.woff2",
              "IBMPlexMono-Medium-Latin1.woff2")
FONT_DIR = HERE.parents[1] / "custom_components" / AGENT_DOMAIN / "www" / "fonts"


def check_fonts(pw, target: Target) -> dict:
    """What the agent serves, and what each browser makes of it."""
    served = {}
    for name in FONT_FILES:
        with urllib.request.urlopen(f"{target.base}/dartec_branding/fonts/{name}",
                                    timeout=30) as r:
            served[name] = {"status": r.status, "type": r.headers.get("Content-Type"),
                            "same": r.read() == (FONT_DIR / name).read_bytes()}
    out = {"served": served}
    for engine in ENGINES:
        browser = getattr(pw, engine).launch()
        context = new_context(browser, target.stored, "light", SERVED, {})
        page = context.new_page()
        page.goto(f"{target.base}/profile", wait_until="load", timeout=60000)
        out[engine] = page.evaluate(FONTS)
        context.close()
        browser.close()
    return out


def font_problems(fonts: dict) -> list[str]:
    problems = [f"{name} is not served as shipped: {got}"
                for name, got in fonts["served"].items()
                if got["status"] != 200 or not got["same"]]
    for engine in ENGINES:
        faces = fonts.get(engine) or []
        by = {(f["family"], f["weight"]): f for f in faces}
        if any("dubai" in f["family"].lower() for f in faces):
            problems.append(f"{engine}: a Dubai face is declared: {faces}")
        for key in (("Dartec Lateef", "400"), ("Dartec Lateef", "700"),
                    ("Dartec Plex Mono", "500")):
            face = next((f for (fam, w), f in by.items()
                         if fam == key[0] and key[1] in w.split()), None)
            if not face or face["status"] != "loaded":
                problems.append(f"{engine}: {key[0]} {key[1]} did not load: {faces}")
        for f in faces:
            if f["family"] != "Dartec Lateef":
                continue
            if "U+600-6FF" not in f["unicodeRange"].upper() or "U+0-" in f["unicodeRange"].upper():
                problems.append(f"{engine}: Lateef is not Arabic only: {f}")
            if f["sizeAdjust"] not in (None, "150%"):
                problems.append(f"{engine}: Lateef is not set at 150%: {f}")
    return problems


def check_cause(pw, target: Target) -> dict:
    out = {}
    for engine in ENGINES:
        browser = getattr(pw, engine).launch()
        for where in ("body", "home-assistant"):
            context = new_context(browser, target.stored, "light", BLOCKED, {})
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(e.message[:200]))
            page.goto(f"{target.base}/{DASHBOARD}", wait_until="load", timeout=60000)
            page.evaluate(OPEN_NOTIFICATIONS)   # returns once Dwains is up
            page.evaluate(MOUNT, [where, target.climate])
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


# The bench has no thermostat. A state-only one is enough for the card, which
# reads hass.states; it is removed at the end.
BENCH_CLIMATE = "climate.dartec_live_thermostat"
BENCH_CLIMATE_STATE = {
    "state": "cool",
    "attributes": {"hvac_modes": ["off", "cool", "heat_cool"], "min_temp": 16, "max_temp": 30,
                   "target_temp_step": 0.5, "current_temperature": 24.5, "temperature": 22,
                   "hvac_action": "cooling", "friendly_name": "Dartec live thermostat",
                   "supported_features": 385}}


def table(rows: list[dict]) -> str:
    lines = ["| Browser | Dashboard fix | Mode | card-mod | Theme | Pop-up in | Mounted "
             "| Formatter errors | Other errors | Open after close | Entity kept "
             "| setConfig per change |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ed = r.get("editor") or {}
        kept = (bool(ed.get("expected")) and (ed.get("after_update") or {}).get("shown")
                == ed["expected"] and (ed.get("after_option") or {}).get("card") == ed["expected"])
        calls = "/".join(str((ed.get(k) or {}).get("set_config_calls", "-"))
                         for k in ("chosen", "after_option", "after_clear"))
        lines.append(f"| {r['engine']} | {r['fix']} | "
                     f"{r['scheme']} | {'on' if r['card_mod'] else 'off'} | {r['theme']} | "
                     f"{(r['dialog'] or {}).get('parent')} | {r['mounted']} | "
                     f"{r['formatter_errors']} | {len(r['other_errors'])} | "
                     f"{r['left_after_close']} | {'yes' if kept else 'no'} | {calls} |")
    return "\n".join(lines)


def kept_entity(row: dict) -> bool:
    ed = row.get("editor") or {}
    want = ed.get("expected")
    return bool(want) and (ed.get("after_update") or {}).get("shown") == want \
        and (ed.get("after_option") or {}).get("card") == want


def calls(row: dict) -> list:
    ed = row.get("editor") or {}
    return [(ed.get(k) or {}).get("set_config_calls")
            for k in ("chosen", "after_option", "after_clear")]


def judge(rows: list[dict], dwains: str, gated: list[str], notification: dict, cause: dict,
          home_cards: dict, expect_clean: bool) -> list[str]:
    """Everything the docstring promises, as problems. `gated` names the fix
    modes that carry this tree's fix, which must do nothing on a fixed Dwains."""
    problems: list[str] = []
    fixed = upstream_fixed(dwains)
    by_key = {r["combo"]: r for r in rows}
    twin_of = lambda r: by_key[r["combo"].replace(f"-{r['fix']}-", f"-{BLOCKED}-", 1)]  # noqa: E731

    for r in rows:
        if r["fix"] == BLOCKED:
            if (r["fix_bytes"] or 0) > 100:
                problems.append(f"{r['combo']}: the fix was meant to be blocked but was served")
            continue
        if (r["fix_bytes"] or 0) < 100:
            problems.append(f"{r['combo']}: the fix was not served, so this combination "
                            "did not test it")
    for r in rows:
        # With a fix, always; without one, only once Dwains has fixed it.
        must_be_clean = r["fix"] != BLOCKED or fixed
        if not must_be_clean:
            continue
        twin = twin_of(r)
        if r["formatter_errors"]:
            problems.append(f"{r['combo']}: the pop-up errors: {r['formatter_messages']}")
        if not (r["dialog"] or {}).get("inside_home_assistant"):
            problems.append(f"{r['combo']}: the pop-up is not inside <home-assistant>: "
                            f"{r['dialog']}")
        if not kept_entity(r):
            problems.append(f"{r['combo']}: the card editor did not keep its entity: "
                            f"{r.get('editor')}")
        if r["picked"] != "editor" or r["editor_errors"]:
            problems.append(f"{r['combo']}: picking the thermostat gave {r['picked']} "
                            f"with {r['editor_errors']}")
        if r is not twin:
            new = set(r["other_errors"]) - set(twin["other_errors"])
            if new:
                problems.append(f"{r['combo']}: errors only with the fix: {sorted(new)}")
            if r["left_after_close"] > twin["left_after_close"]:
                problems.append(f"{r['combo']}: {r['left_after_close']} pop-up(s) left open "
                                f"after closing, {twin['left_after_close']} without the fix")
        if r["left_after_close"] or r["pickers_after_reopen"] != 1 or r["already_open"]:
            problems.append(f"{r['combo']}: after closing, {r['left_after_close']} pop-up(s) "
                            f"stayed open; reopening gave {r['pickers_after_reopen']} "
                            f"({r['already_open']})")
    # This tree's fix on a Dwains that fixed it: no second move, no second
    # hand-back. Anything else is the fix acting on something no longer broken.
    if fixed:
        for r in rows:
            if r["fix"] not in gated:
                continue
            twin = twin_of(r)
            if r["mounted"] != twin["mounted"]:
                problems.append(f"{r['combo']}: the picker was mounted {r['mounted']} time(s), "
                                f"{twin['mounted']} without the fix: the fix moved it")
            if calls(r) != calls(twin):
                problems.append(f"{r['combo']}: setConfig per change {calls(r)}, "
                                f"{calls(twin)} without the fix: the fix handed a config back")
    for engine in ENGINES:
        for fix in {r["fix"] for r in rows}:
            shown = home_cards.get(f"{engine}-{fix}") or {}
            if (fix != BLOCKED or fixed or expect_clean) and \
                    (shown.get("config") != 1 or shown.get("drawn") != 1):
                problems.append(f"{engine}, fix {fix}: the stored Home custom card is not on "
                                f"the Home page after a reload: {shown}")
    for engine in ENGINES:
        outside, inside = cause[f"{engine}-body"], cause[f"{engine}-home-assistant"]
        if inside["context_errors"]:
            problems.append(f"{engine}: the card host errors even inside <home-assistant>: "
                            f"{inside}; the diagnosis in dartec-ha-manager#25 needs revisiting")
        if not fixed:
            popup = any(r["formatter_errors"] for r in rows
                        if r["engine"] == engine and r["fix"] == BLOCKED)
            # Before Home Assistant moved these to context (2026.5 is clean)
            # there is nothing to explain; after it, the pop-up's error and
            # the hand-mounted one outside <home-assistant> must go together.
            if popup != bool(outside["context_errors"]):
                problems.append(f"{engine}: the unfixed pop-up {'errors' if popup else 'is clean'}"
                                f", but the card host mounted by hand under body gives "
                                f"{outside}; the diagnosis in dartec-ha-manager#25 needs "
                                "revisiting")
    if not fixed and expect_clean:
        upstream = [r["combo"] for r in rows if r["fix"] == BLOCKED and r["formatter_errors"]]
        forgets = [r["combo"] for r in rows if r["fix"] == BLOCKED and not kept_entity(r)]
        if upstream:
            problems.append(f"the upstream error still happens without the fix: {upstream}")
        if forgets:
            problems.append(f"the card editor still forgets its entity without the fix: {forgets}")
    for fix, got in notification.items():
        if fix == BLOCKED:
            continue
        if not got.get("contrast"):
            problems.append(f"no notification row to measure with fix {fix}: {got}")
        elif got["contrast"] < 4.5:
            problems.append(f"the notification row with fix {fix} measures "
                            f"{got['contrast']}:1, below 4.5:1")
    return problems


def summarise(rows: list[dict], dwains: str, label: str) -> None:
    fixed = upstream_fixed(dwains)
    blocked = [r for r in rows if r["fix"] == BLOCKED]
    errs = sum(1 for r in blocked if r["formatter_errors"])
    forgets = sum(1 for r in blocked if not kept_entity(r))
    log(f"{label}: Dwains {dwains} without the fix: the pop-up errors in {errs} of "
        f"{len(blocked)} (#18) and the editor forgets its entity in {forgets} (#19)"
        + ("" if fixed else "; the workaround is still needed for this release"))
    for fix in sorted({r["fix"] for r in rows} - {BLOCKED}):
        mine = [r for r in rows if r["fix"] == fix]
        moved = sum(1 for r in mine if r["mounted"] != (
            next(b for b in blocked if b["combo"] == r["combo"].replace(
                f"-{fix}-", f"-{BLOCKED}-", 1))["mounted"]))
        extra = sum(1 for r in mine if calls(r) != calls(next(
            b for b in blocked if b["combo"] == r["combo"].replace(f"-{fix}-", f"-{BLOCKED}-", 1))))
        log(f"{label}: fix {fix}: moved the picker in {moved} of {len(mine)}, handed an extra "
            f"config back in {extra} of {len(mine)}")


def write_artifacts(shots: Path | None, versions: dict, rows, notification, cause, home_cards,
                    fonts) -> None:
    if not shots:
        return
    (shots / "results.json").write_text(json.dumps(
        {"versions": versions, "rows": rows, "notification": notification, "cause": cause,
         "home_cards": home_cards, "fonts": fonts}, indent=1), encoding="utf-8")
    (shots / "table.md").write_text(table(rows) + "\n", encoding="utf-8")


def run_matrix(target: Target, label: str, dwains: str, modes: list[str], gated: list[str],
               card_mod: tuple[bool, ...], themes: tuple[str, ...], fix_files: dict[str, bytes],
               shots: Path | None, expect_clean: bool, restore_theme: tuple[str, str | None]
               ) -> tuple[list[str], dict]:
    from playwright.sync_api import sync_playwright

    combos = [Combo(*c) for c in itertools.product(ENGINES, modes, ("light", "dark"),
                                                   card_mod, themes)]
    rows: list[dict] = []
    with sync_playwright() as pw:
        browsers = {engine: getattr(pw, engine).launch() for engine in ENGINES}
        browser = browsers["chromium"]
        context = new_context(browser, target.stored, "light", SERVED, fix_files)
        admin = context.new_page()
        admin.goto(f"{target.base}/profile", wait_until="load")
        setup_home(admin, target.resource)
        try:
            for (with_card_mod, theme), group in itertools.groupby(
                    sorted(combos, key=lambda c: (not c.card_mod, c.theme)),
                    key=lambda c: (c.card_mod, c.theme)):
                if len(card_mod) > 1:
                    set_card_mod(admin, with_card_mod)
                set_theme(admin, theme)
                for combo in group:
                    row = run_combo(browsers[combo.engine], target, combo, shots, fix_files)
                    rows.append(row)
                    log(f"{label}: {combo.key}: {row['formatter_errors']} formatter "
                        f"error(s), {len(row['other_errors'])} other, dialog {row['dialog']}, "
                        f"setConfig {calls(row)}")
            if len(card_mod) > 1:
                set_card_mod(admin, True)
            set_theme(admin, "Dartec")
            notification = check_notification_fix(browser, target, modes, shots, fix_files)
        finally:
            set_theme(admin, *restore_theme)
            context.close()
        for each in browsers.values():
            each.close()
        cause = check_cause(pw, target)
        home_cards = check_home_cards(pw, target, modes, fix_files)
        fonts = check_fonts(pw, target)

    rows.sort(key=lambda r: (r["engine"], modes.index(r["fix"]), r["scheme"] != "light",
                             not r["card_mod"], r["theme"] != "Dartec"))
    print(table(rows))
    log(f"{label}: notification row in dark mode: {notification}")
    log(f"{label}: the card host mounted by hand: {cause}")
    log(f"{label}: the stored Home custom card after a reload: {home_cards}")
    log(f"{label}: the brand's fonts: {fonts}")
    summarise(rows, dwains, label)
    problems = font_problems(fonts)
    problems += judge(rows, dwains, gated, notification, cause, home_cards, expect_clean)
    return problems, {"rows": rows, "notification": notification, "cause": cause,
                      "home_cards": home_cards, "fonts": fonts}


def run_version(version: str, dwains: str, keep: bool, artifacts: Path | None, timeout: float,
                expect_clean: bool, fix_file: Path | None) -> list[str]:
    name = f"dartec-dwains-{version.replace('.', '-')}"
    label = f"{version}/dwains {dwains}"
    problems: list[str] = []
    cache = Path(tempfile.gettempdir()) / "dartec-live-assets"
    assets = fetch_assets(cache)
    dist = fetch_dwains(cache, dwains)
    with tempfile.TemporaryDirectory(prefix="dartec-dwains-") as tmp:
        config = prepare_config(Path(tmp))
        yaml = config / "configuration.yaml"
        yaml.write_text(yaml.read_text(encoding="utf-8")
                        + "frontend:\n  themes: !include_dir_merge_named themes\n",
                        encoding="utf-8")
        for folder, filename in (("www", "card-mod.js"), ("themes", "dartec.yaml")):
            (config / folder).mkdir(exist_ok=True)
            (config / folder / filename).write_bytes(assets[filename].read_bytes())
        for path in dist.rglob("*"):
            if path.is_file():
                out = config / "www" / "dwains-dashboard-next" / path.relative_to(dist)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(path.read_bytes())
        log(f"{label}: starting {IMAGE}:{version} as {name}")
        port = start_container(name, version, config)
    base = f"http://127.0.0.1:{port}"
    shots = None
    if artifacts:
        shots = artifacts / f"dwains-{dwains}-ha-{version}"
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
        climate = give_thermostat_a_step(base, token)
        target = Target(base, token, browser_tokens(base), bench=False, name=name,
                        climate=climate, resource=DWAINS_RESOURCE)
        log(f"{label}: paired on port {port}; {climate} is the previewed thermostat")

        # Served here is this tree's fix, so it is the gated one.
        modes, fix_files = [SERVED, BLOCKED], {}
        if fix_file:
            modes.insert(1, FILE)
            fix_files[FILE] = fix_file.read_bytes()
        found, results = run_matrix(target, label, dwains, modes, [SERVED], (True, False),
                                    ("Dartec", "default"), fix_files, shots, expect_clean,
                                    ("Dartec", None))
        problems += found

        text = ha_log(name)
        errors = [line.strip() for line in text.splitlines()
                  if re.search(r"\b(ERROR|CRITICAL)\b", line) and AGENT_DOMAIN in line]
        if errors:
            problems.append("Home Assistant logged against the agent:\n  "
                            + "\n  ".join(errors[:20]))
        write_artifacts(shots, {"home_assistant": version, "dwains": dwains,
                                **{f: ASSETS[f][1] for f in ASSETS},
                                "fix_file": str(fix_file) if fix_file else None}, **results)
    except Failure as err:
        problems.append(str(err))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
        problems.append(f"{type(err).__name__}: {err}")
    finally:
        if keep:
            log(f"{label}: left running at {base} (docker rm -f -v {name})")
        else:
            docker("rm", "-f", "-v", name, check=False)
    return problems


def run_bench(base: str, token_file: Path, location: str, artifacts: Path | None,
              expect_clean: bool) -> list[str]:
    """The same matrix against the bench, which already runs the agent and
    Dwains through HACS. Refuses anything whose location is not `location`."""
    token = token_file.expanduser().read_text(encoding="utf-8").strip()
    problems: list[str] = []
    config = http("GET", f"{base}/api/config", token=token)
    if config.get("location_name") != location:
        return [f"refusing: {base} is '{config.get('location_name')}', not the bench "
                f"('{location}')"]
    states = {s["entity_id"]: s for s in http("GET", f"{base}/api/states", token=token)}
    dwains = next((s["attributes"].get("installed_version") for e, s in states.items()
                   if e.startswith("update.") and "dwains" in e), None)
    if not dwains:
        return ["Dwains Dashboard Next is not installed through HACS on the bench"]
    label = f"bench {config.get('version')}/dwains {dwains}"
    from websockets.sync.client import connect  # the theme to put back
    with connect(base.replace("http", "ws", 1) + "/api/websocket", max_size=None) as ws:
        ws.recv()
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        ws.recv()
        ws.send(json.dumps({"id": 1, "type": "frontend/get_themes"}))
        themes = json.loads(ws.recv())["result"]
    restore = (themes.get("default_theme") or "none", themes.get("default_dark_theme") or "none")
    shots = None
    if artifacts:
        shots = artifacts / f"bench-dwains-{dwains}-ha-{config.get('version')}"
        shots.mkdir(parents=True, exist_ok=True)
    http("POST", f"{base}/api/states/{BENCH_CLIMATE}", token=token, json_body=BENCH_CLIMATE_STATE)
    target = Target(base, token, bench_tokens(base, token), bench=True, climate=BENCH_CLIMATE)
    log(f"{label}: theme to restore {restore}; {BENCH_CLIMATE} added for the preview")
    try:
        modes = [SERVED, TREE, BLOCKED]
        fix_files = {TREE: TREE_FIX.read_bytes()}
        found, results = run_matrix(target, label, dwains, modes, [TREE], (False,),
                                    ("Dartec", "default"), fix_files, shots, expect_clean,
                                    restore)
        problems += found
        write_artifacts(shots, {"home_assistant": config.get("version"), "dwains": dwains,
                                "bench": base, "fix_served": "the bench agent's",
                                "fix_tree": str(TREE_FIX)}, **results)
    except Failure as err:
        problems.append(str(err))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
        problems.append(f"{type(err).__name__}: {err}")
    finally:
        for entity in (BENCH_CLIMATE, "sensor.dartec_live_tick"):
            try:
                http("DELETE", f"{base}/api/states/{entity}", token=token)
            except urllib.error.HTTPError:
                pass
        http("POST", f"{base}/api/services/persistent_notification/dismiss", token=token,
             json_body={"notification_id": "dartec_live_door"})
        log(f"{label}: removed the test states and notification; the dashboard stays")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("versions", nargs="*", default=DEFAULT_VERSIONS)
    parser.add_argument("--dwains", default=DEFAULT_DWAINS,
                        help=f"Dwains Dashboard Next release tag (default {DEFAULT_DWAINS})")
    parser.add_argument("--fix-file", type=Path,
                        help="also run with this file served as dashboard-fix.js "
                             "(e.g. a released agent's copy)")
    parser.add_argument("--bench", metavar="URL", help="run against this existing Home Assistant")
    parser.add_argument("--token-file", type=Path, help="long-lived token for --bench")
    parser.add_argument("--location", default="TEST",
                        help="the location name --bench must have (default TEST)")
    parser.add_argument("--keep", action="store_true", help="leave containers running")
    parser.add_argument("--artifacts", type=Path, help="write screenshots and the table here")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--expect-clean", action="store_true",
                        help="also fail if the pop-up errors without the fix (upstream fixed?)")
    args = parser.parse_args()
    runs = []
    if args.bench:
        if not args.token_file:
            parser.error("--bench needs --token-file")
        runs.append((f"bench {args.bench}", lambda: run_bench(
            args.bench.rstrip("/"), args.token_file, args.location, args.artifacts,
            args.expect_clean)))
    else:
        for version in args.versions:
            runs.append((version, lambda version=version: run_version(
                version, args.dwains, args.keep, args.artifacts, args.timeout,
                args.expect_clean, args.fix_file)))
    failed = False
    for label, run in runs:
        started = time.monotonic()
        problems = run()
        took = time.monotonic() - started
        if problems:
            failed = True
            log(f"{label}: FAILED after {took:.0f}s")
            for problem in problems:
                print(f"  - {problem}")
        else:
            log(f"{label}: passed in {took:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
