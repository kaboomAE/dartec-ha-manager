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
// Only what the module touches is modelled. It is not a DOM; it is enough to
// see which of the corrections act.
"use strict";

const fs = require("fs");
const vm = require("vm");

const [, , file, scenario] = process.argv;

class Node {
  constructor(localName) {
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
  querySelectorAll(tag) {
    const out = [];
    const visit = (n) => n.children.forEach((c) => { if (c.localName === tag) out.push(c); visit(c); });
    visit(this);
    return out;
  }
  querySelector(tag) { return this.querySelectorAll(tag)[0] || null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  getRootNode() { let n = this; while (n.parentNode) n = n.parentNode; return n; }
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
class CSSStyleSheet { replaceSync() {} }

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

const run = async () => {
  const out = {scenario};
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
