// "My Home" - the household panel of the Dartec HA Manager integration.
//
// A plain web component with no build step and no dependencies, so it loads
// the same way in a browser and in the Home Assistant Companion app. Home
// Assistant hands it `hass` (the signed-in person's own connection), and
// every change goes through the integration's admin-only websocket commands
// (household_ws.py), which apply the rules in household.py. Nothing here is a
// security decision: the panel only explains what the server will refuse.
//
// Strings live in i18n/<lang>.json. The layout uses logical CSS properties
// only, so Arabic mirrors correctly without a second stylesheet.

const WS = "dartec_ha_manager/household";
const RTL = new Set(["ar", "he", "fa", "ur"]);
const LANGS = new Set(["en", "ar"]);
// Readable on a phone and unambiguous when read aloud: no 0/o, 1/l/i.
const ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";
const COMMON = ["password", "123456", "12345678", "qwerty", "111111", "abc123",
  "iloveyou", "admin", "welcome", "letmein", "homeassistant", "dartec"];

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function randomIndex(n) {
  // Rejection sampling, so every character is equally likely.
  const buf = new Uint8Array(1);
  const limit = 256 - (256 % n);
  for (;;) {
    crypto.getRandomValues(buf);
    if (buf[0] < limit) return buf[0] % n;
  }
}

export function generatePassword() {
  const group = () => Array.from({ length: 4 }, () => ALPHABET[randomIndex(ALPHABET.length)]).join("");
  return `${group()}-${group()}-${group()}`;
}

// 0..4. A guide for the person typing, not the rule: the server enforces its
// own floor (household.check_password).
export function strength(pw, personal = []) {
  if (!pw) return 0;
  let pool = 0;
  if (/[a-z]/.test(pw)) pool += 26;
  if (/[A-Z]/.test(pw)) pool += 26;
  if (/\d/.test(pw)) pool += 10;
  if (/[\x20-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]/.test(pw)) pool += 33;
  if (/[^\x00-\x7f]/.test(pw)) pool += 40;
  let bits = pw.length * Math.log2(Math.max(pool, 2));
  const folded = pw.toLowerCase();
  if (/(.)\1\1/.test(pw)) bits -= 12;
  if (new Set(pw).size < 4) bits = Math.min(bits, 10);
  if (COMMON.some((w) => folded.includes(w))) bits = Math.min(bits, 20);
  if (personal.some((w) => w && w.length >= 3 && folded.includes(w.toLowerCase()))) bits = Math.min(bits, 20);
  if (pw.length < 8) return 0;
  if (bits < 36) return 1;
  if (bits < 56) return 2;
  if (bits < 76) return 3;
  return 4;
}

export function suggestUsername(name) {
  const plain = String(name || "").normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().trim().replace(/\s+/g, ".").replace(/[^a-z0-9._-]/g, "")
    .replace(/^[._-]+/, "").replace(/[.]{2,}/g, ".").slice(0, 32);
  return plain.length >= 2 ? plain : "";
}

const STYLE = `
:host { display: block; min-height: 100%; background: var(--primary-background-color, #f5f5f0);
  color: var(--primary-text-color, #22251f);
  font-family: var(--ha-font-family-body, var(--paper-font-body1_-_font-family, Roboto)), "Noto Sans Arabic", "Segoe UI", Tahoma, sans-serif;
  --dt-accent: var(--primary-color, #1a6b6b);
  --dt-card: var(--card-background-color, #fffdf8);
  --dt-muted: var(--secondary-text-color, #52564c);
  --dt-line: var(--divider-color, rgba(0,0,0,.12));
  --dt-danger: var(--error-color, #dc2626);
  --dt-radius: 16px; }
* { box-sizing: border-box; }
.toolbar { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 8px;
  min-height: 56px; padding-inline: 8px 16px; background: var(--app-header-background-color, var(--dt-accent));
  color: var(--app-header-text-color, #fff); }
.toolbar h1 { margin: 0; font-size: 20px; font-weight: 500; padding-inline-start: 8px; }
.iconbtn { inline-size: 48px; block-size: 48px; border: 0; border-radius: 50%; background: transparent;
  color: inherit; display: grid; place-items: center; cursor: pointer; }
.iconbtn svg { inline-size: 24px; block-size: 24px; fill: currentColor; }
main { max-inline-size: 760px; margin-inline: auto; padding: 16px 16px 96px; }
.intro { color: var(--dt-muted); line-height: 1.5; margin: 4px 0 16px; font-size: 15px; }
button { font: inherit; }
.btn { min-block-size: 48px; padding: 10px 20px; border-radius: 24px; border: 1px solid transparent;
  cursor: pointer; font-size: 15px; font-weight: 500; }
.btn.primary { background: var(--dt-accent); color: var(--text-primary-color, #fff); }
.btn.quiet { background: transparent; color: var(--dt-accent); border-color: var(--dt-line); }
.btn.danger { background: var(--dt-danger); color: #fff; }
.btn[disabled] { opacity: .5; cursor: default; }
.btn.block { inline-size: 100%; }
.add { display: flex; align-items: center; justify-content: center; gap: 8px; inline-size: 100%; margin-block-end: 16px; }
.people { display: grid; gap: 10px; }
.card { display: flex; align-items: center; gap: 14px; inline-size: 100%; text-align: start;
  padding: 14px 16px; min-block-size: 72px; border-radius: var(--dt-radius); border: 1px solid var(--dt-line);
  background: var(--dt-card); color: inherit; cursor: pointer; }
.card.static { cursor: default; }
.card:focus-visible, .btn:focus-visible, .choice:focus-within { outline: 3px solid var(--dt-accent); outline-offset: 2px; }
.avatar { flex: none; inline-size: 44px; block-size: 44px; border-radius: 50%; display: grid; place-items: center;
  font-weight: 600; font-size: 18px; color: #fff; background: var(--dt-accent); }
.avatar.paused { background: var(--disabled-text-color, #9e9e9e); }
.who { flex: 1; min-inline-size: 0; }
.name { font-size: 17px; font-weight: 500; overflow-wrap: anywhere; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-block-start: 6px; }
.chip { font-size: 12.5px; line-height: 1; padding: 6px 10px; border-radius: 999px;
  background: color-mix(in srgb, var(--dt-accent) 12%, transparent); color: var(--dt-accent); }
.chip.owner { background: var(--dt-accent); color: var(--text-primary-color, #fff); }
.chip.guest { background: color-mix(in srgb, #8f8368 22%, transparent); color: var(--primary-text-color); }
.chip.muted { background: color-mix(in srgb, var(--dt-muted) 14%, transparent); color: var(--dt-muted); }
.chip.warn { background: color-mix(in srgb, var(--warning-color, #d97706) 18%, transparent); color: var(--primary-text-color); }
.sub { color: var(--dt-muted); font-size: 13px; margin-block-start: 4px; }
.chev { flex: none; color: var(--dt-muted); }
:host([dir="rtl"]) .chev { transform: scaleX(-1); }
.note { color: var(--dt-muted); font-size: 13.5px; line-height: 1.55; padding: 14px 16px; margin-block: 16px;
  border-radius: var(--dt-radius); border: 1px dashed var(--dt-line); }
section h2 { font-size: 16px; font-weight: 600; margin: 28px 0 10px; }
.activity { list-style: none; padding: 0; margin: 0; border-radius: var(--dt-radius); overflow: hidden;
  border: 1px solid var(--dt-line); background: var(--dt-card); }
.activity li { padding: 12px 16px; border-block-end: 1px solid var(--dt-line); font-size: 14px; line-height: 1.4; }
.activity li:last-child { border-block-end: 0; }
.activity time { display: block; color: var(--dt-muted); font-size: 12px; margin-block-start: 2px; }
details.about { margin-block-start: 20px; border-radius: var(--dt-radius); border: 1px solid var(--dt-line);
  background: var(--dt-card); padding: 4px 16px; }
details.about summary { min-block-size: 48px; display: flex; align-items: center; cursor: pointer; font-weight: 500; }
details.about p { color: var(--dt-muted); line-height: 1.55; font-size: 14px; }
.center { text-align: center; padding: 48px 16px; color: var(--dt-muted); }
.error { color: var(--dt-danger); font-size: 14px; margin: 8px 0; line-height: 1.4; }
/* Sheets: full screen on a phone, a centred dialog on a wide screen. */
.scrim { position: fixed; inset: 0; z-index: 10; background: rgba(0,0,0,.45); display: flex;
  align-items: flex-end; justify-content: center; }
.sheet { inline-size: 100%; max-inline-size: 560px; max-block-size: 92vh; overflow: auto;
  background: var(--dt-card); color: var(--primary-text-color); border-radius: 20px 20px 0 0;
  padding: 20px 20px calc(20px + env(safe-area-inset-bottom)); }
@media (min-width: 700px) { .scrim { align-items: center; } .sheet { border-radius: 20px; } }
.sheet h3 { font-size: 20px; font-weight: 600; margin: 0 0 6px; overflow-wrap: anywhere; }
.sheet p.lead { color: var(--dt-muted); margin: 0 0 16px; line-height: 1.5; }
.field { display: block; margin-block: 14px; }
.field > span.label { display: block; font-weight: 500; margin-block-end: 6px; }
.field small { display: block; color: var(--dt-muted); margin-block-start: 6px; line-height: 1.4; }
input[type=text], input[type=password], select { inline-size: 100%; min-block-size: 48px; padding: 10px 14px; font: inherit;
  font-size: 16px; border-radius: 12px; border: 1px solid var(--dt-line); background: var(--primary-background-color, #fff);
  color: var(--primary-text-color); }
input.ltr { direction: ltr; text-align: start; }
:host([dir="rtl"]) input.ltr { text-align: end; }
.pwrow { display: flex; gap: 8px; }
.pwrow input { flex: 1; min-inline-size: 0; font-family: ui-monospace, Menlo, Consolas, monospace; }
.meter { display: flex; gap: 4px; margin-block-start: 8px; }
.meter i { flex: 1; block-size: 6px; border-radius: 3px; background: var(--dt-line); }
.meter[data-s="1"] i:nth-child(-n+1) { background: var(--dt-danger); }
.meter[data-s="2"] i:nth-child(-n+2) { background: var(--warning-color, #d97706); }
.meter[data-s="3"] i:nth-child(-n+3) { background: var(--success-color, #047857); }
.meter[data-s="4"] i { background: var(--success-color, #047857); }
.choices { display: grid; gap: 8px; }
.choice { display: flex; gap: 12px; align-items: flex-start; padding: 14px; border-radius: 14px; border: 1px solid var(--dt-line); cursor: pointer; }
.choice input { margin-block-start: 3px; inline-size: 20px; block-size: 20px; accent-color: var(--dt-accent); flex: none; }
.choice b { display: block; font-weight: 500; }
.choice span { display: block; color: var(--dt-muted); font-size: 13.5px; line-height: 1.45; margin-block-start: 2px; }
.choice.on { border-color: var(--dt-accent); background: color-mix(in srgb, var(--dt-accent) 7%, transparent); }
.choice.off { opacity: .55; cursor: default; }
.switchrow { display: flex; gap: 12px; align-items: flex-start; padding: 12px 0; cursor: pointer; }
.switchrow input { inline-size: 22px; block-size: 22px; accent-color: var(--dt-accent); flex: none; margin-block-start: 2px; }
.honest { font-size: 13.5px; line-height: 1.5; padding: 10px 12px; border-radius: 12px;
  background: color-mix(in srgb, var(--warning-color, #d97706) 12%, transparent); margin-block: 8px; }
.actions { display: grid; gap: 8px; margin-block-start: 16px; }
.actionbtn { display: flex; align-items: center; inline-size: 100%; min-block-size: 52px; padding: 12px 16px; border-radius: 14px;
  border: 1px solid var(--dt-line); background: transparent; color: inherit; font-size: 16px; text-align: start; cursor: pointer; }
.actionbtn.danger { color: var(--dt-danger); }
.actionbtn[disabled] { opacity: .5; cursor: default; }
.why { color: var(--dt-muted); font-size: 13px; margin: -2px 4px 4px; line-height: 1.4; }
.buttons { display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; margin-block-start: 20px; }
.buttons .btn { flex: 1 1 140px; }
.creds { display: grid; gap: 8px; margin-block: 12px; }
.cred { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 12px; border: 1px solid var(--dt-line); }
.cred span { color: var(--dt-muted); font-size: 13px; min-inline-size: 90px; }
.cred code { flex: 1; direction: ltr; text-align: start; font-size: 17px; overflow-wrap: anywhere; }
.toast { position: fixed; inset-inline: 16px; inset-block-end: calc(20px + env(safe-area-inset-bottom)); z-index: 20; margin-inline: auto;
  max-inline-size: 420px; padding: 14px 18px; border-radius: 14px; background: #22251f; color: #fff; font-size: 15px;
  box-shadow: 0 8px 30px rgba(0,0,0,.25); }
`;

const ICON_MENU = '<svg viewBox="0 0 24 24"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg>';
const ICON_ADD = '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg>';
const ICON_CHEV = '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg>';

class DartecHouseholdPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._strings = null;
    this._lang = null;
    this._data = null;
    this._error = null;
    this._sheet = null;
    this._busy = false;
    this._toastTimer = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const lang = this._pickLang(hass);
    if (lang !== this._lang) {
      this._lang = lang;
      this._loadStrings().then(() => { this._render(); if (first) this._load(); });
    }
  }

  get hass() { return this._hass; }

  set narrow(value) { this._narrow = value; if (this._strings) this._renderToolbar(); }

  set panel(value) { this._panel = value; }

  _pickLang(hass) {
    const lang = String(hass?.locale?.language || hass?.language || "en").toLowerCase().split("-")[0];
    return LANGS.has(lang) ? lang : "en";
  }

  async _loadStrings() {
    const cfg = this._panel?.config || {};
    const base = cfg.static || "/dartec_household";
    const stamp = cfg.stamp ? `?v=${encodeURIComponent(cfg.stamp)}` : "";
    const fetchLang = async (lang) => {
      const resp = await fetch(`${base}/i18n/${lang}.json${stamp}`, { credentials: "same-origin" });
      if (!resp.ok) throw new Error(`strings ${resp.status}`);
      return resp.json();
    };
    let english = {};
    try { english = await fetchLang("en"); } catch (e) { /* the keys still show */ }
    let local = {};
    if (this._lang !== "en") { try { local = await fetchLang(this._lang); } catch (e) { /* English then */ } }
    this._strings = { en: english, local };
    const rtl = RTL.has(this._lang) || !!this._hass?.translationMetadata?.translations?.[this._lang]?.isRTL;
    this.setAttribute("dir", rtl ? "rtl" : "ltr");
    this.setAttribute("lang", this._lang);
  }

  t(key, vars = {}) {
    const find = (table) => key.split(".").reduce((node, part) => (node && typeof node === "object" ? node[part] : undefined), table);
    let text = find(this._strings?.local) ?? find(this._strings?.en) ?? key;
    if (typeof text !== "string") return key;
    for (const [name, value] of Object.entries(vars)) text = text.split(`{${name}}`).join(String(value ?? ""));
    return text;
  }

  // ── Data ───────────────────────────────────────────────────────────────

  async _load() {
    try {
      this._data = await this._hass.callWS({ type: `${WS}/list` });
      this._error = null;
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._render();
  }

  async _call(type, payload) {
    this._busy = true;
    this._renderSheet();
    try {
      this._data = await this._hass.callWS({ type: `${WS}/${type}`, ...payload });
      return true;
    } catch (err) {
      if (this._sheet) this._sheet.error = this._errorText(err);
      return false;
    } finally {
      this._busy = false;
    }
  }

  _errorText(err) {
    const code = err?.code;
    if (code && this.t(`errors.${code}`) !== `errors.${code}`) return this.t(`errors.${code}`);
    return this.t("errors.generic", { message: err?.message || String(err) });
  }

  _person(id) { return (this._data?.people || []).find((p) => p.id === id); }

  _dashTitle(urlPath) {
    const board = (this._data?.dashboards || []).find((d) => d.url_path === urlPath);
    if (!board) return urlPath;
    const title = board.title;
    // Built-in pages carry a translation key or no title at all; Home
    // Assistant's own words for them are the ones the person knows. The
    // untitled default dashboard is what the sidebar calls "Overview".
    if (!title || board.builtin) {
      const key = !title && urlPath === "lovelace" ? "panel.states" : `panel.${title || urlPath}`;
      const localized = this._hass?.localize?.(key);
      if (localized) return localized;
    }
    return title || urlPath;
  }

  // ── Rendering ──────────────────────────────────────────────────────────

  _render() {
    if (!this._strings) return;
    const root = this.shadowRoot;
    root.innerHTML = `<style>${STYLE}</style>
      <div class="toolbar" part="toolbar"></div>
      <main>${this._mainHtml()}</main>
      <div id="sheet"></div><div id="toast"></div>`;
    this._renderToolbar();
    root.querySelector("main").addEventListener("click", (ev) => this._onMainClick(ev));
    this._renderSheet();
  }

  _renderToolbar() {
    const bar = this.shadowRoot.querySelector(".toolbar");
    if (!bar) return;
    bar.innerHTML = `${this._narrow ? `<button class="iconbtn" id="menu" aria-label="${esc(this.t("menu"))}">${ICON_MENU}</button>` : ""}
      <h1>${esc(this.t("title"))}</h1>`;
    bar.querySelector("#menu")?.addEventListener("click", () => {
      // What Home Assistant's own menu button fires to open the sidebar.
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
  }

  _mainHtml() {
    if (this._error && !this._data) {
      return `<div class="center"><p>${esc(this.t("load_failed"))}</p><p class="error">${esc(this._error)}</p>
        <button class="btn quiet" data-act="retry">${esc(this.t("retry"))}</button></div>`;
    }
    if (!this._data) return `<div class="center">${esc(this.t("loading"))}</div>`;
    const d = this._data;
    const canManage = d.me?.can_manage !== false;
    const people = d.people.map((p) => this._cardHtml(p)).join("");
    const activity = d.activity.length
      ? d.activity.map((a) => `<li>${esc(this._activityText(a))}<time datetime="${esc(a.at)}">${esc(this._when(a.at))}</time></li>`).join("")
      : `<li>${esc(this.t("activity.empty"))}</li>`;
    return `
      <p class="intro">${esc(this.t("intro"))}</p>
      ${canManage ? `<button class="btn primary add" data-act="add">${ICON_ADD}<span>${esc(this.t("add"))}</span></button>` : `<p class="error">${esc(this.t("errors.actor_maintenance"))}</p>`}
      <div class="people">${people}</div>
      ${d.maintenance_account ? `<p class="note">${esc(this.t("maintenance_note"))}</p>` : ""}
      <section><h2>${esc(this.t("activity.title"))}</h2><ul class="activity">${activity}</ul></section>
      <details class="about"><summary>${esc(this.t("about.title"))}</summary>
        <p>${esc(this.t("about.roles"))}</p><p>${esc(this.t("guest_honest"))}</p>
        <p>${esc(this.t("about.dashboards"))}</p><p>${esc(this.t("about.log"))}</p></details>`;
  }

  _initials(name) {
    const parts = String(name || "?").trim().split(/\s+/);
    return esc(((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase());
  }

  _cardHtml(p) {
    const chips = [`<span class="chip ${p.role === "owner" ? "owner" : p.role === "guest" ? "guest" : ""}">${esc(this.t(`roles.${p.role}`))}</span>`];
    if (p.is_me) chips.push(`<span class="chip muted">${esc(this.t("you"))}</span>`);
    if (!p.is_active) chips.push(`<span class="chip warn">${esc(this.t("paused"))}</span>`);
    if (p.local_only) chips.push(`<span class="chip muted">${esc(this.t("home_only"))}</span>`);
    if (!p.has_login) chips.push(`<span class="chip muted">${esc(this.t("no_login_chip"))}</span>`);
    const sub = p.kind === "owner" ? this.t("owner_note")
      : p.dashboard ? this.t("first_dashboard", { name: this._dashTitle(p.dashboard) }) : "";
    const interactive = p.kind === "person" || p.is_me;
    const tag = interactive ? "button" : "div";
    return `<${tag} class="card ${interactive ? "" : "static"}" ${interactive ? `data-act="open" data-id="${esc(p.id)}"` : ""}>
      <span class="avatar ${p.is_active ? "" : "paused"}" aria-hidden="true">${this._initials(p.name)}</span>
      <span class="who"><span class="name">${esc(p.name)}</span>
        <span class="chips">${chips.join("")}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</span>
      ${interactive ? `<span class="chev" aria-hidden="true">${ICON_CHEV}</span>` : ""}</${tag}>`;
  }

  _activityText(a) {
    const vars = { by: a.by, target: a.target, role: this.t(`roles.${a.detail?.role || "family"}`) };
    if (a.action === "update") {
      const d = a.detail || {};
      if ("is_active" in d && Object.keys(d).length === 1) return this.t(d.is_active ? "activity.resume" : "activity.pause", vars);
      if ("role" in d) return this.t("activity.role", vars);
      if ("is_active" in d) return this.t(d.is_active ? "activity.resume" : "activity.pause", vars);
      return this.t("activity.update", vars);
    }
    return this.t(`activity.${a.action}`, vars);
  }

  _when(iso) {
    try {
      return new Intl.DateTimeFormat(this._lang, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
    } catch (e) { return iso; }
  }

  _onMainClick(ev) {
    const el = ev.target.closest("[data-act]");
    if (!el) return;
    const act = el.dataset.act;
    if (act === "retry") { this._error = null; this._render(); this._load(); }
    if (act === "add") this._openSheet({ type: "add", role: "family", local_only: false, dashboard: "", password: "", username: "", name: "", userTouched: false });
    if (act === "open") this._openSheet({ type: "person", id: el.dataset.id });
  }

  // ── Sheets ─────────────────────────────────────────────────────────────

  _openSheet(sheet) {
    this._sheet = { error: null, ...sheet };
    this._lastFocus = this.shadowRoot.activeElement;
    this._renderSheet();
  }

  _closeSheet() {
    this._sheet = null;
    this._render();
  }

  _renderSheet() {
    const host = this.shadowRoot.getElementById("sheet");
    if (!host) return;
    const s = this._sheet;
    if (!s) { host.innerHTML = ""; return; }
    const body = {
      add: () => this._addHtml(s),
      person: () => this._personHtml(s),
      edit: () => this._editHtml(s),
      password: () => this._passwordHtml(s),
      dashboard: () => this._dashboardHtml(s),
      confirm: () => this._confirmHtml(s),
      done: () => this._doneHtml(s),
    }[s.type]();
    host.innerHTML = `<div class="scrim" data-scrim><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">${body}</div></div>`;
    const scrim = host.querySelector("[data-scrim]");
    scrim.addEventListener("click", (ev) => {
      if (ev.target === scrim && !this._busy) this._closeSheet();
      const el = ev.target.closest("[data-sact]");
      if (el && !el.disabled) this._onSheetAction(el.dataset.sact, el);
    });
    scrim.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !this._busy) this._closeSheet(); });
    scrim.addEventListener("input", (ev) => this._onInput(ev));
    scrim.addEventListener("change", (ev) => this._onInput(ev));
    const focus = host.querySelector("[autofocus]") || host.querySelector("h3");
    if (focus) { if (focus.tagName === "H3") focus.tabIndex = -1; focus.focus({ preventScroll: true }); }
  }

  _errorHtml(s) { return s.error ? `<p class="error" role="alert">${esc(s.error)}</p>` : ""; }

  _buttons(primaryLabel, primaryAct, { danger = false, disabled = false } = {}) {
    return `<div class="buttons">
      <button class="btn quiet" data-sact="close" ${this._busy ? "disabled" : ""}>${esc(this.t("form.cancel"))}</button>
      <button class="btn ${danger ? "danger" : "primary"}" data-sact="${primaryAct}" ${this._busy || disabled ? "disabled" : ""}>${esc(this._busy ? this.t("form.saving") : primaryLabel)}</button></div>`;
  }

  _roleChoices(s, { lockedSelf = false } = {}) {
    return ["admin", "family", "guest"].map((role) => `
      <label class="choice ${s.role === role ? "on" : ""} ${lockedSelf ? "off" : ""}">
        <input type="radio" name="role" value="${role}" ${s.role === role ? "checked" : ""} ${lockedSelf ? "disabled" : ""}>
        <span><b>${esc(this.t(`roles.${role}`))}</b><span>${esc(this.t(`role_help.${role}`))}</span></span></label>`).join("");
  }

  _localOnlyHtml(s, { self = false } = {}) {
    const guest = s.role === "guest";
    const help = guest ? this.t("form.local_only_guest") : self ? this.t("form.local_only_self") : this.t("form.local_only_help");
    return `<label class="switchrow"><input type="checkbox" name="local_only" ${guest || s.local_only ? "checked" : ""} ${guest || self ? "disabled" : ""}>
      <span><b>${esc(this.t("form.local_only"))}</b><small style="display:block;color:var(--dt-muted);margin-top:4px">${esc(help)}</small></span></label>`;
  }

  _dashboardOptions(selected) {
    const boards = this._data?.dashboards || [];
    return `<option value="">${esc(this.t("form.dashboard_default"))}</option>` + boards.map((b) =>
      `<option value="${esc(b.url_path)}" ${selected === b.url_path ? "selected" : ""}>${esc(this._dashTitle(b.url_path))}${b.from_dartec ? ` · ${esc(this.t("form.from_dartec"))}` : ""}</option>`).join("");
  }

  _passwordField(s, personal) {
    const score = strength(s.password, personal);
    return `<label class="field"><span class="label">${esc(this.t("form.password"))}</span>
      <div class="pwrow"><input class="ltr" name="password" type="${s.showPw ? "text" : "password"}" autocomplete="new-password"
        autocapitalize="off" spellcheck="false" value="${esc(s.password)}" dir="ltr">
        <button class="btn quiet" type="button" data-sact="togglepw">${esc(s.showPw ? this.t("form.hide") : this.t("form.show"))}</button></div>
      <div class="meter" data-s="${score}" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
      <small id="pwlabel">${esc(this.t("strength.label", { level: this.t(`strength.${score}`) }))} · ${esc(this.t("form.password_hint", { min: this._data?.limits?.min_password || 8 }))}</small>
      <button class="btn quiet" type="button" data-sact="generate" style="margin-top:8px">${esc(this.t("form.generate"))}</button></label>`;
  }

  _addHtml(s) {
    return `<h3 id="sheet-title">${esc(this.t("form.add_title"))}</h3>
      <label class="field"><span class="label">${esc(this.t("form.name"))}</span>
        <input type="text" name="name" autocomplete="off" value="${esc(s.name)}" autofocus><small>${esc(this.t("form.name_hint"))}</small></label>
      <label class="field"><span class="label">${esc(this.t("form.username"))}</span>
        <input class="ltr" type="text" name="username" dir="ltr" autocomplete="off" autocapitalize="off" spellcheck="false" value="${esc(s.username)}">
        <small>${esc(this.t("form.username_hint"))}</small></label>
      ${this._passwordField(s, [s.username, ...String(s.name).split(/\s+/)])}
      <div class="field"><span class="label">${esc(this.t("form.role"))}</span><div class="choices">${this._roleChoices(s)}</div>
        ${s.role === "guest" ? `<p class="honest">${esc(this.t("guest_honest"))}</p>` : ""}</div>
      ${this._localOnlyHtml(s)}
      <label class="field"><span class="label">${esc(this.t("form.dashboard"))}</span>
        <select name="dashboard">${this._dashboardOptions(s.dashboard)}</select>
        <small>${esc(this.t("form.dashboard_note"))}</small></label>
      ${this._errorHtml(s)}
      ${this._buttons(this.t("form.add_button"), "create")}`;
  }

  _personHtml(s) {
    const p = this._person(s.id);
    if (!p) return `<h3 id="sheet-title">${esc(this.t("errors.not_found"))}</h3>${this._buttons(this.t("form.close"), "close")}`;
    const me = this._data.me || {};
    const self = p.is_me;
    const canManage = me.can_manage !== false;
    const btn = (act, label, { danger = false, disabled = false, why = "" } = {}) =>
      `<button class="actionbtn ${danger ? "danger" : ""}" data-sact="${act}" ${disabled || !canManage ? "disabled" : ""}>${esc(label)}</button>${why ? `<p class="why">${esc(why)}</p>` : ""}`;
    let pwWhy = "";
    if (self) pwWhy = this.t("actions.password_self");
    else if (!me.is_owner) pwWhy = this.t("actions.password_owner_only");
    else if (!p.has_login) pwWhy = this.t("errors.no_login");
    const chips = [this.t(`roles.${p.role}`), p.is_active ? null : this.t("paused"),
      p.local_only ? this.t("home_only") : this.t("anywhere")].filter(Boolean).join(" · ");
    return `<h3 id="sheet-title">${esc(p.name)}</h3>
      <p class="lead">${esc(chips)}${p.username ? `<br><span dir="ltr">${esc(p.username)}</span>` : ""}</p>
      ${self ? `<p class="why">${esc(this.t(p.kind === "owner" ? "actions.self_owner" : "actions.self_limits"))}</p>` : ""}
      ${p.role === "view_only" ? `<p class="why">${esc(this.t("view_only_help"))}</p>` : ""}
      ${this._errorHtml(s)}
      <div class="actions">
        ${p.kind === "person" ? btn("edit", this.t("actions.edit")) : ""}
        ${btn("dashboard", this.t("actions.dashboard"))}
        ${p.kind === "person" ? btn("password", this.t("actions.password"), { disabled: !!pwWhy, why: pwWhy }) : ""}
        ${self ? "" : p.is_active ? btn("pause", this.t("actions.pause"), { danger: true }) : btn("resume", this.t("actions.resume"))}
        ${self ? "" : btn("remove", this.t("actions.remove"), { danger: true })}
      </div>
      <div class="buttons"><button class="btn quiet" data-sact="close">${esc(this.t("form.close"))}</button></div>`;
  }

  _editHtml(s) {
    const p = this._person(s.id);
    return `<h3 id="sheet-title">${esc(this.t("form.edit_title", { name: p?.name }))}</h3>
      <label class="field"><span class="label">${esc(this.t("form.name"))}</span>
        <input type="text" name="name" value="${esc(s.name)}" autofocus></label>
      <div class="field"><span class="label">${esc(this.t("form.role"))}</span>
        <div class="choices">${this._roleChoices(s, { lockedSelf: p?.is_me })}</div>
        ${p?.is_me ? `<p class="why">${esc(this.t("form.role_self"))}</p>` : ""}
        ${s.role === "guest" ? `<p class="honest">${esc(this.t("guest_honest"))}</p>` : ""}</div>
      ${this._localOnlyHtml(s, { self: p?.is_me })}
      ${this._errorHtml(s)}
      ${this._buttons(this.t("form.save"), "save")}`;
  }

  _passwordHtml(s) {
    const p = this._person(s.id);
    return `<h3 id="sheet-title">${esc(this.t("password.title", { name: p?.name }))}</h3>
      ${this._passwordField(s, [p?.username, ...String(p?.name || "").split(/\s+/)])}
      <label class="switchrow"><input type="checkbox" name="sign_out" ${s.sign_out ? "checked" : ""}>
        <span><b>${esc(this.t("password.sign_out"))}</b><small style="display:block;color:var(--dt-muted);margin-top:4px">${esc(this.t("password.sign_out_help"))}</small></span></label>
      ${this._errorHtml(s)}
      ${this._buttons(this.t("form.save"), "askpassword")}`;
  }

  _dashboardHtml(s) {
    const p = this._person(s.id);
    const boards = this._data?.dashboards || [];
    const choice = (value, label, extra = "") => `<label class="choice ${s.dashboard === value ? "on" : ""}">
      <input type="radio" name="board" value="${esc(value)}" ${s.dashboard === value ? "checked" : ""}>
      <span><b>${esc(label)}</b>${extra ? `<span>${esc(extra)}</span>` : ""}</span></label>`;
    return `<h3 id="sheet-title">${esc(this.t("dashboard.title", { name: p?.name }))}</h3>
      <p class="honest">${esc(this.t("form.dashboard_note"))}</p>
      <div class="choices">${choice("", this.t("form.dashboard_default"))}
        ${boards.map((b) => choice(b.url_path, this._dashTitle(b.url_path), b.from_dartec ? this.t("form.from_dartec") : "")).join("")}</div>
      ${this._errorHtml(s)}
      ${this._buttons(this.t("form.save"), "savedashboard")}`;
  }

  _confirmHtml(s) {
    const p = this._person(s.id);
    const name = p?.name || "";
    const copy = {
      pause: [this.t("confirm.pause_title", { name }), this.t("confirm.pause_body", { name }), this.t("confirm.pause_button")],
      remove: [this.t("confirm.remove_title", { name }), this.t("confirm.remove_body", { name }), this.t("confirm.remove_button", { name })],
      password: [this.t("password.confirm_title", { name }), this.t("password.confirm_body"), this.t("password.confirm_button")],
    }[s.what];
    return `<h3 id="sheet-title">${esc(copy[0])}</h3><p class="lead">${esc(copy[1])}</p>
      ${this._errorHtml(s)}${this._buttons(copy[2], "confirm", { danger: s.what !== "password" })}`;
  }

  _doneHtml(s) {
    const cred = (label, value, key) => `<div class="cred"><span>${esc(label)}</span><code>${esc(value)}</code>
      <button class="btn quiet" data-sact="copy" data-key="${key}">${esc(s.copied === key ? this.t("done.copied") : this.t("done.copy"))}</button></div>`;
    return `<h3 id="sheet-title">${esc(this.t(s.title, { name: s.name }))}</h3>
      <p class="lead">${esc(this.t("done.share"))}</p>
      <div class="creds">${s.username ? cred(this.t("done.username"), s.username, "username") : ""}${cred(this.t("done.password"), s.password, "password")}</div>
      <p class="why">${esc(this.t("done.app_hint"))}</p>
      <div class="buttons"><button class="btn primary" data-sact="close" autofocus>${esc(this.t("form.close"))}</button></div>`;
  }

  // ── Sheet input and actions ────────────────────────────────────────────

  _onInput(ev) {
    const s = this._sheet;
    const el = ev.target;
    if (!s || !el.name) return;
    if (el.name === "name") {
      s.name = el.value;
      if (s.type === "add" && !s.userTouched) {
        s.username = suggestUsername(el.value);
        const u = this.shadowRoot.querySelector('input[name="username"]');
        if (u) u.value = s.username;
      }
      this._refreshMeter();
      return;
    }
    if (el.name === "username") { s.username = el.value; s.userTouched = true; this._refreshMeter(); return; }
    if (el.name === "password") { s.password = el.value; this._refreshMeter(); return; }
    if (el.name === "dashboard") { s.dashboard = el.value; return; }
    if (el.name === "sign_out") { s.sign_out = el.checked; return; }
    if (el.name === "local_only") { s.local_only = el.checked; return; }
    if (ev.type !== "change") return;
    // Choices re-render, so the explanations and the guest note follow them.
    if (el.name === "role") { s.role = el.value; this._renderSheet(); }
    if (el.name === "board") { s.dashboard = el.value; this._renderSheet(); }
  }

  _refreshMeter() {
    const s = this._sheet;
    const meter = this.shadowRoot.querySelector(".meter");
    if (!s || !meter) return;
    const p = s.id ? this._person(s.id) : null;
    const personal = p ? [p.username, ...String(p.name).split(/\s+/)] : [s.username, ...String(s.name).split(/\s+/)];
    const score = strength(s.password, personal);
    meter.dataset.s = score;
    const label = this.shadowRoot.getElementById("pwlabel");
    if (label) label.textContent = `${this.t("strength.label", { level: this.t(`strength.${score}`) })} · ${this.t("form.password_hint", { min: this._data?.limits?.min_password || 8 })}`;
  }

  _passwordOk(s, personal) {
    if (strength(s.password, personal) < 2) { s.error = this.t("errors.password_weak"); this._renderSheet(); return false; }
    return true;
  }

  async _onSheetAction(act, el) {
    const s = this._sheet;
    if (!s || this._busy) return;
    const p = s.id ? this._person(s.id) : null;
    if (act === "close") { this._closeSheet(); return; }
    if (act === "togglepw") { s.showPw = !s.showPw; this._renderSheet(); return; }
    if (act === "generate") { s.password = generatePassword(); s.showPw = true; s.error = null; this._renderSheet(); return; }
    if (act === "copy") { this._copy(s[el.dataset.key], el.dataset.key); return; }
    if (act === "edit") { this._openSheet({ type: "edit", id: p.id, name: p.name, role: p.role === "view_only" ? "" : p.role, local_only: p.local_only }); return; }
    if (act === "dashboard") { this._openSheet({ type: "dashboard", id: p.id, dashboard: p.dashboard || "" }); return; }
    if (act === "password") { this._openSheet({ type: "password", id: p.id, password: "", sign_out: false }); return; }
    if (act === "pause" || act === "remove") { this._openSheet({ type: "confirm", what: act, id: p.id }); return; }
    if (act === "resume") {
      if (await this._call("update", { user_id: p.id, is_active: true })) this._done(this.t("toast.resumed", { name: p.name }));
      else this._renderSheet();
      return;
    }
    if (act === "create") {
      if (!this._passwordOk(s, [s.username, ...String(s.name).split(/\s+/)])) return;
      const payload = { name: s.name, username: s.username.trim().toLowerCase(), password: s.password,
        role: s.role, local_only: s.role === "guest" ? true : !!s.local_only };
      if (s.dashboard) payload.dashboard = s.dashboard;
      if (await this._call("create", payload)) {
        this._openSheet({ type: "done", title: "done.added_title", name: s.name.trim(), username: payload.username, password: s.password });
        this._renderMain();
      } else this._renderSheet();
      return;
    }
    if (act === "save") {
      const payload = { user_id: p.id };
      if (s.name.trim() !== p.name) payload.name = s.name;
      if (s.role && s.role !== p.role) payload.role = s.role;
      const local = s.role === "guest" ? true : !!s.local_only;
      if (local !== !!p.local_only && !p.is_me) payload.local_only = local;
      if (Object.keys(payload).length === 1) { this._closeSheet(); return; }
      if (await this._call("update", payload)) this._done(this.t("toast.saved"));
      else this._renderSheet();
      return;
    }
    if (act === "askpassword") {
      if (!this._passwordOk(s, [p.username, ...String(p.name).split(/\s+/)])) return;
      this._openSheet({ type: "confirm", what: "password", id: p.id, password: s.password, sign_out: s.sign_out });
      return;
    }
    if (act === "savedashboard") {
      if (await this._call("set_dashboard", { user_id: p.id, url_path: s.dashboard || null })) this._done(this.t("toast.dashboard"));
      else this._renderSheet();
      return;
    }
    if (act === "confirm") {
      if (s.what === "pause") {
        if (await this._call("update", { user_id: p.id, is_active: false })) this._done(this.t("toast.paused", { name: p.name }));
        else this._renderSheet();
      } else if (s.what === "remove") {
        const name = p.name;
        if (await this._call("remove", { user_id: p.id })) this._done(this.t("toast.removed", { name }));
        else this._renderSheet();
      } else if (s.what === "password") {
        if (await this._call("set_password", { user_id: p.id, password: s.password, sign_out: !!s.sign_out })) {
          this._openSheet({ type: "done", title: "done.password_title", name: p.name, username: p.username, password: s.password });
          this._renderMain();
        } else this._renderSheet();
      }
    }
  }

  _renderMain() {
    const main = this.shadowRoot.querySelector("main");
    if (main) main.innerHTML = this._mainHtml();
  }

  _done(message) {
    this._sheet = null;
    this._render();
    this._toast(message);
  }

  _toast(message) {
    const host = this.shadowRoot.getElementById("toast");
    if (!host) return;
    host.innerHTML = `<div class="toast" role="status">${esc(message)}</div>`;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { host.innerHTML = ""; }, 3500);
  }

  async _copy(text, key) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
      // The Companion app over plain http has no clipboard API.
      const area = document.createElement("textarea");
      area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
      this.shadowRoot.appendChild(area); area.select();
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      area.remove();
    }
    if (ok && this._sheet) { this._sheet.copied = key; this._renderSheet(); }
  }
}

if (!customElements.get("dartec-household-panel")) {
  customElements.define("dartec-household-panel", DartecHouseholdPanel);
}
