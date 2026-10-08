// Runs the branding module (branding.py's rendered JS) against a small
// stand-in for Home Assistant's page and prints what the sidebar title and the
// tab title became, as JSON, for tests/test_branding_js.py.
//
//     node branding_harness.js <path to rendered branding.js> <options JSON>
//
// Options:
//   lang     the viewer's language as Home Assistant holds it (hass.language),
//            or "" for none
//   htmlLang the page's lang attribute (HA sets it from the same choice)
//   dark     true for a dark theme
//   title    the page title before the module runs
//   steps    a list of languages to switch the viewer to, one after another;
//            each is followed by a "location-changed", as on navigation
//
// Only what the module touches is modelled. It is not a DOM.
"use strict";

const fs = require("fs");
const vm = require("vm");

const [, , file, optionsJson] = process.argv;
const opts = JSON.parse(optionsJson || "{}");

function style() {
  const props = {};
  return new Proxy(props, {
    get(target, key) {
      if (key === "removeProperty") return (name) => { delete target[camel(name)]; };
      if (key === "cssText") return Object.entries(target).map(([k, v]) => `${k}:${v}`).join(";");
      return target[key];
    },
    set(target, key, value) {
      if (key === "cssText") {
        for (const part of String(value).split(";")) {
          const i = part.indexOf(":");
          if (i > 0) target[camel(part.slice(0, i).trim())] = part.slice(i + 1).trim();
        }
      } else {
        target[key] = value;
      }
      return true;
    },
  });
}

function camel(name) { return name.replace(/-([a-z])/g, (_, c) => c.toUpperCase()); }

class El {
  constructor(tag) {
    this.localName = tag;
    this.children = [];
    this.style = style();
    this.dataset = {};
    this.shadowRoot = null;
    this._text = "";
    this.parent = null;
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

class Root {
  constructor(map) { this.map = map; }
  querySelector(sel) { return this.map[sel] || null; }
  querySelectorAll() { return []; }
}

const titleNode = new El("div");
titleNode.textContent = "Home Assistant";
const sidebar = new El("ha-sidebar");
sidebar.shadowRoot = new Root({ ".menu .title": titleNode });
const main = new El("home-assistant-main");
main.shadowRoot = new Root({ "ha-sidebar": sidebar });
const ha = new El("home-assistant");
ha.shadowRoot = new Root({ "home-assistant-main": main });
ha.hass = opts.lang ? { language: opts.lang, locale: { language: opts.lang } } : undefined;

const listeners = {};
const titleEl = new El("title");
const document = {
  readyState: "complete",
  title: opts.title || "Overview",
  documentElement: { lang: opts.htmlLang || "" },
  head: new El("head"),
  body: new El("body"),
  hidden: false,
  querySelector(sel) {
    if (sel === "home-assistant") return ha;
    if (sel === "title") return titleEl;
    return null;
  },
  createElement(tag) { return new El(tag); },
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
};
const window = {
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  matchMedia: () => ({ matches: !!opts.dark }),
};
const bg = opts.dark ? "rgb(17, 17, 17)" : "rgb(250, 250, 250)";
const sandbox = {
  document, window,
  getComputedStyle: () => ({ color: bg, getPropertyValue: () => (opts.dark ? "#111111" : "#fafafa") }),
  MutationObserver: class { observe() {} },
  fetch: () => Promise.reject(new Error("offline")),
  setTimeout: () => 0,
  console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);

function state() {
  const img = titleNode.children.find((c) => c.localName === "img");
  return {
    title: document.title,
    text: titleNode._text,
    img: img ? { src: img.src, alt: img.alt, height: img.style.height } : null,
    lineHeight: titleNode.style.lineHeight || null,
    overflow: titleNode.style.overflow || null,
  };
}

const out = [state()];
for (const lang of opts.steps || []) {
  ha.hass = { language: lang, locale: { language: lang } };
  document.title = document.title;  // HA would rewrite it on navigation
  for (const fn of listeners["location-changed"] || []) fn();
  out.push(state());
}
process.stdout.write(JSON.stringify(out));
