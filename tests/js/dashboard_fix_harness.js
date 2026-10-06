// Runs www/dashboard-fix.js against a small stand-in for the browser and
// prints what it did, as JSON, for tests/test_dashboard_fix.py.
//
//     node dashboard_fix_harness.js <path to dashboard-fix.js> <scenario>
//
// Scenarios, each a Dwains release's behaviour:
//   old-picker   Dwains 1.8.0: the Add card picker is appended to
//                document.body, and its editor is never handed its config.
//   new-picker   Dwains 1.8.1 and later: the picker is mounted inside
//                <home-assistant>'s shadow root, and Dwains hands the config
//                back itself.
//   old-strategy Dwains 1.8.0's generators leave home_custom_cards out.
//   new-strategy Dwains 1.8.1 and later pass it, an empty list when none.
//
// And for the right-to-left unit correction (#55), Home Assistant's own
// elements as Lit renders them, with the page in a given direction:
//   units-rtl     Arabic: document.dir is "rtl".
//   units-ltr     English: document.dir is "ltr".
//   units-switch  Arabic, then English, then Arabic again, on the same page,
//                 with Home Assistant writing a new value in between.
//
// Only what the module touches is modelled. It is not a DOM; it is enough to
// see which of the corrections act.
"use strict";

const fs = require("fs");
const vm = require("vm");

const [, , file, scenario] = process.argv;

class Node {
  constructor(localName) {
    this.nodeType = 1;
    this.className = "";
    this.localName = localName;
    this.parentNode = null;
    this.children = [];
    this.shadowRoot = null;
    this.listeners = {};
  }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    observers.forEach((o) => o.notice(this, child));
    return child;
  }
  removeChild(child) {
    this.children = this.children.filter((c) => c !== child);
    child.parentNode = null;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get childNodes() { return this.children; }
  // A tag name, or a single ".class".
  querySelectorAll(sel) {
    const hit = sel.startsWith(".")
      ? (c) => (c.className || "").split(" ").includes(sel.slice(1))
      : (c) => c.localName === sel;
    const out = [];
    const visit = (n) => (n.children || []).forEach((c) => { if (hit(c)) out.push(c); visit(c); });
    visit(this);
    return out;
  }
  querySelector(tag) { return this.querySelectorAll(tag)[0] || null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  getRootNode() { let n = this; while (n.parentNode) n = n.parentNode; return n; }
}

// A text node, which is what Lit writes a string into.
class Text {
  constructor(data) { this.nodeType = 3; this.data = data; this.parentNode = null; }
}

const observers = [];
class MutationObserver {
  constructor(callback) { this.callback = callback; }
  observe(target) { this.target = target; observers.push(this); }
  notice(parent, child) {
    // subtree: true, but a shadow root is a separate tree, as in a browser.
    let n = parent;
    while (n && n !== this.target) n = n.parentNode;
    if (n !== this.target) return;
    this.callback([{target: parent, addedNodes: [child]}]);
  }
}

const document = new Node("#document");
const head = document.appendChild(new Node("head"));
const body = document.appendChild(new Node("body"));
const ha = body.appendChild(new Node("home-assistant"));
ha.shadowRoot = new Node("#shadow-root");
document.body = body;
document.head = head;
document.documentElement = document;
document.getElementById = () => null;
document.dir = "";
document.getAttribute = () => null;
document.createElement = (tag) => new Node(tag);
document.querySelector = (tag) => document.querySelectorAll(tag)[0] || null;

const windowListeners = {};
const defined = {};
const window = {
  addEventListener(type, fn, capture) {
    (windowListeners[type] = windowListeners[type] || []).push({fn, capture});
  },
};
const waiting = {};
const customElements = {
  get: (tag) => defined[tag],
  // Resolves when the tag is defined, and never otherwise, as in a browser.
  whenDefined: (tag) => (defined[tag] ? Promise.resolve()
    : new Promise((resolve) => (waiting[tag] = waiting[tag] || []).push(resolve))),
};
const define = (tag, cls) => {
  defined[tag] = cls;
  (waiting[tag] || []).forEach((resolve) => resolve());
};
class CSSStyleSheet { replaceSync(css) { this.css = css; } }

const context = vm.createContext({
  window, document, customElements, CSSStyleSheet, MutationObserver, console,
  requestAnimationFrame: (fn) => setTimeout(fn, 0), queueMicrotask, setTimeout, Promise,
});
vm.runInContext(fs.readFileSync(file, "utf-8"), context, {filename: "dashboard-fix.js"});

// A config-changed from the editor, the way Home Assistant's card editors
// send it: the window's capture listeners first, then Dwains' own, on the
// editor, which merges the change into its card (and, from 1.8.1, hands it
// back with setConfig).
const configChanged = (dialog, editor, config, dwainsHandsBack) => {
  const ev = {composedPath: () => [editor, dialog.shadowRoot, dialog, dialog.parentNode]};
  (windowListeners["config-changed"] || []).filter((l) => l.capture).forEach((l) => l.fn(ev));
  dialog._card = {...config};
  if (dwainsHandsBack) editor.setConfig(dialog._card);
};

const picker = () => {
  const dialog = new Node("dwains-dashboard-next-card-editor-dialog");
  dialog.shadowRoot = new Node("#shadow-root");
  const editor = new Node("hui-thermostat-card-editor");
  editor.calls = [];
  editor.setConfig = (config) => editor.calls.push(config);
  dialog._configEl = editor;
  return {dialog, editor};
};

const where = (node) => (node.parentNode === body ? "body"
  : node.getRootNode() === ha.shadowRoot ? "home-assistant" : String(node.parentNode));

let writes = 0;
// Counts every property the module writes onto what Dwains built.
const watched = (target) => new Proxy(target, {
  set(obj, key, value) { writes++; obj[key] = value; return true; },
});

const strategy = (keyPassed) => {
  // Dwains' dashboard and view strategies, as its generators build them.
  const cards = [{id: "c1", card: {type: "markdown"}}];
  define("ll-strategy-dashboard-dwains-dashboard-next", {
    generate: async (config) => ({views: [{strategy: watched(keyPassed
      ? {type: "custom:dwains-dashboard-next-view", home_custom_cards: config.home_custom_cards || []}
      : {type: "custom:dwains-dashboard-next-view"})}]}),
  });
  define("ll-strategy-view-dwains-dashboard-next-view", {
    generate: async (config) => ({cards: [watched(keyPassed
      ? {type: "custom:dwains-dashboard-next-layout-card", home_custom_cards: config.home_custom_cards || []}
      : {type: "custom:dwains-dashboard-next-layout-card"})]}),
  });
  return cards;
};

// Home Assistant's elements, as far as the unit correction sees them: Lit
// elements that write each string part into its own text node, writing a
// node again only when its value changed, and then call updated(). The
// base class owns updated(), as LitElement does; ha-tile-info overrides it.
class LitElement extends Node {
  updated() { this.updates = (this.updates || 0) + 1; }
}
class StateDisplay extends LitElement {}
class TileInfo extends LitElement {
  updated(changed) { super.updated(changed); this.ownUpdated = true; }
}
class NumberButtons extends LitElement {}

// Renders `values` into `parent`'s text nodes, as Lit's child parts do.
const render = (el, parent, values) => {
  parent.parts = parent.parts || [];
  values.forEach((value, i) => {
    if (!parent.parts[i]) {
      parent.parts[i] = {node: parent.appendChild(new Text(value)), committed: value};
    } else if (parent.parts[i].committed !== value) {
      parent.parts[i].node.data = value;
      parent.parts[i].committed = value;
    }
  });
  el.updated(new Map());
};

const units = async (directions) => {
  define("state-display", StateDisplay);
  define("ha-tile-info", TileInfo);
  define("ha-control-number-buttons", NumberButtons);
  await new Promise((r) => setTimeout(r, 5));

  const card = ha.shadowRoot.appendChild(new Node("hui-tile-card"));
  card.shadowRoot = new Node("#shadow-root");
  const make = (Cls, tag) => {
    const el = new Cls(tag);
    card.shadowRoot.appendChild(el);
    return el;
  };
  // <state-display> renders into its own light DOM.
  const displays = {
    sensor: ["22.0 °C"],
    climate: ["تبريد", " · ", "23.5 °C"],
    climate_en: ["Cool", " · ", "23.5 °C"],
    upstream: ["\u206822.0 °C\u2069"],
    arabic_digits: ["٢٢٫٥ °C"],
    negative: ["-3.5 °C"],
    energy: ["1,234.5 kWh"],
    words: ["On"],
    name: ["Bedroom 2"],
  };
  const els = {};
  Object.keys(displays).forEach((k) => { els[k] = make(StateDisplay, "state-display"); });
  // <ha-tile-info>: primary and secondary spans inside its shadow root.
  const info = make(TileInfo, "ha-tile-info");
  info.shadowRoot = new Node("#shadow-root");
  const primarySlot = info.shadowRoot.appendChild(new Node("slot"));
  primarySlot.className = "primary";
  const primary = primarySlot.appendChild(new Node("span"));
  const secondarySlot = info.shadowRoot.appendChild(new Node("slot"));
  secondarySlot.className = "secondary";
  const secondary = secondarySlot.appendChild(new Node("span"));
  // <ha-control-number-buttons>, with a stylesheet of its own already.
  const buttons = make(NumberButtons, "ha-control-number-buttons");
  buttons.shadowRoot = new Node("#shadow-root");
  const own = new CSSStyleSheet();
  buttons.shadowRoot.adoptedStyleSheets = [own];

  const paint = (sensorValue) => {
    Object.entries(displays).forEach(([k, values]) =>
      render(els[k], els[k], k === "sensor" ? [sensorValue] : values));
    primary.parts = primary.parts || [];
    render(info, primary, ["Kitchen 230 W"]);
    render(info, secondary, ["22.0 °C · 45%"]);
    buttons.updated(new Map());
  };
  const look = () => ({
    displays: Object.fromEntries(Object.keys(displays).map((k) =>
      [k, els[k].childNodes.map((n) => n.data).join("")])),
    tile_primary: primary.childNodes.map((n) => n.data).join(""),
    tile_secondary: secondary.childNodes.map((n) => n.data).join(""),
    stepper_sheets: buttons.shadowRoot.adoptedStyleSheets.map((sh) =>
      sh === own ? "home-assistant" : sh.css),
    tile_info_own_updated: Boolean(info.ownUpdated),
    updates: els.sensor.updates,
  });

  const steps = [];
  for (const [i, step] of directions.entries()) {
    document.dir = step.dir;
    paint(step.value);
    steps.push({dir: step.dir, value: step.value, ...look()});
    if (i === 0) {  // a second render with nothing changed: no double isolation
      paint(step.value);
      steps[0].repainted = look();
    }
  }
  return steps;
};

const run = async () => {
  const out = {scenario};
  if (scenario === "units-rtl") out.steps = await units([{dir: "rtl", value: "22.0 °C"}]);
  if (scenario === "units-ltr") out.steps = await units([{dir: "ltr", value: "22.0 °C"}]);
  if (scenario === "units-switch") {
    out.steps = await units([
      {dir: "rtl", value: "22.0 °C"},
      {dir: "ltr", value: "22.0 °C"},  // the language changes; the value does not
      {dir: "ltr", value: "22.5 °C"},  // Home Assistant writes a new value
      {dir: "rtl", value: "23.0 °C"},  // and back to Arabic, with another
    ]);
  }
  if (scenario === "old-picker" || scenario === "new-picker") {
    const {dialog, editor} = picker();
    const old = scenario === "old-picker";
    let mounts = 0;
    const append = Node.prototype.appendChild;
    Node.prototype.appendChild = function (child) {
      if (child === dialog) mounts++;
      return append.call(this, child);
    };
    (old ? body : ha.shadowRoot).appendChild(dialog);
    out.where = where(dialog);
    out.mounts = mounts;
    configChanged(dialog, editor, {type: "thermostat", entity: "climate.hall"}, !old);
    configChanged(dialog, editor, {type: "thermostat", entity: "climate.hall", name: "Hall"}, !old);
    await new Promise((r) => setTimeout(r, 5));
    out.set_config_calls = editor.calls.length;
    out.last_config = editor.calls[editor.calls.length - 1] || null;
    // A change without a card type: Dwains 1.8.1 deliberately does not hand
    // that back. Nothing else may either.
    const before = editor.calls.length;
    const ev = {composedPath: () => [editor, dialog]};
    (windowListeners["config-changed"] || []).forEach((l) => l.fn(ev));
    await new Promise((r) => setTimeout(r, 5));
    out.untyped_change_calls = editor.calls.length - before;
  }
  if (scenario === "old-strategy" || scenario === "new-strategy") {
    const cards = strategy(scenario === "new-strategy");
    await new Promise((r) => setTimeout(r, 10));
    const config = {type: "custom:dwains-dashboard-next", home_custom_cards: cards};
    const dash = await defined["ll-strategy-dashboard-dwains-dashboard-next"].generate(config, {});
    const view = await defined["ll-strategy-view-dwains-dashboard-next-view"].generate(
      {home_custom_cards: cards}, {});
    out.view_strategy = dash.views[0].strategy;
    out.layout_card = view.cards[0];
    out.writes = writes;
  }
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
};

run().catch((err) => { process.stderr.write(String(err && err.stack || err)); process.exit(1); });
