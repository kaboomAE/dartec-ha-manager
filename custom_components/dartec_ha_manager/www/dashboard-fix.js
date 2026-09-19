// Make the Dwains dashboard's notification panel readable on a dark theme.
//
// The panel hardcodes `background: rgba(255, 255, 255, 0.78)` on every
// notification row. There is no dark variant and no CSS variable, so on any
// dark theme the row is white while the text on it comes from
// --primary-text-color, which is near-white. Measured on Home Assistant
// 2026.9.1 with the Dartec theme: 1.25:1, where WCAG AA asks for 4.5:1. The
// customer sees a notification they cannot read, about their own house.
//
// A theme cannot fix this — no variable is involved — so the correction is
// injected here, into the shadow root that owns the rule. It is written in
// terms of theme variables rather than fixed colours, so it stays right in
// light mode too (where it changes nothing perceptible: the row was already
// white and --secondary-background-color is the theme's own near-white).
//
// Deliberately narrow. The bundle carries 189 hardcoded light surfaces; this
// fixes the one that makes text unreadable, and leaves the rest to upstream.
// Restyling somebody else's dashboard wholesale would break on their next
// release and leave nobody able to say what had been changed.
//
// Defensive throughout, like branding.js: if the dashboard is not installed,
// or its internals move, this does nothing at all rather than risk the
// customer's UI.
//
// A second correction, just as narrow: Dwains' "Add card" picker
// (dartec-ha-manager#25, reported upstream as dwains-dashboard-next#18).
// Dwains mounts its pop-ups on document.body. From Home Assistant 2026.7,
// HA's own card controls take their formatters and config from context that
// <home-assistant> provides, so a card previewed in that pop-up throws
// "this._formatters is undefined" and draws blank. The picker is moved to
// where Home Assistant mounts its own dialogs, inside <home-assistant>'s
// shadow root, before Dwains opens it, and removed when it closes, because
// Dwains' own clean-up only looks in document.body. Only that one dialog:
// it is the one that renders Home Assistant cards. Once Dwains fixes this
// upstream the picker is no longer on document.body and this does nothing.
//
// A third, in the same dialog: its card editor forgets what was chosen in it
// after a few seconds (dwains-dashboard-next#19). See the listener below.
(() => {
  const HOST = "dwains-dashboard-next-layout-card";
  const CSS = `
    .notification-row {
      background: var(--secondary-background-color) !important;
      border-color: var(--divider-color) !important;
    }
    .notification-dismiss {
      background: var(--divider-color) !important;
    }
  `;

  let sheet = null;
  try {
    sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
  } catch (err) {
    return; // No constructable stylesheets: leave everything alone.
  }

  const patch = (root) => {
    try {
      if (!root || !("adoptedStyleSheets" in root)) return;
      if (root.adoptedStyleSheets.includes(sheet)) return;
      root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
    } catch (err) {
      /* a root that will not take a sheet is not worth breaking the page for */
    }
  };

  // The dashboard mounts its cards well after this module loads, and remounts
  // them when the view changes, so finding them once is not enough.
  const sweep = (node) => {
    if (!node) return;
    try {
      if (node.localName === HOST) patch(node.shadowRoot);
      if (node.shadowRoot) [...node.shadowRoot.children].forEach(sweep);
      if (node.children) [...node.children].forEach(sweep);
    } catch (err) {
      /* ignore anything that will not walk */
    }
  };

  // Coalesced. A sweep costs well under a millisecond on this page, but the
  // observer fires on every DOM change and a card editor changes the DOM
  // constantly — so the work is collapsed to at most one sweep per frame,
  // and only when nodes were actually added. Measured before this: 340 nodes,
  // 0.7 ms, zero mutations while idle. It was never the bottleneck; this is
  // so it can never become one on a busier page than the one I could test.
  let queued = false;
  const run = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; sweep(document.body); });
  };

  // Dwains' card picker, moved inside <home-assistant> (see the top). This
  // has to happen here, in the observer's own callback: Dwains appends the
  // dialog and opens it on the next animation frame, and a mutation callback
  // runs before that frame, so the dialog is opened where it will stay.
  const PICKER = "dwains-dashboard-next-card-editor-dialog";
  const rehome = (node) => {
    try {
      if (node.localName !== PICKER || node.parentNode !== document.body) return;
      const ha = document.querySelector("home-assistant");
      const root = ha && ha.shadowRoot;
      if (!root) return;
      // One that closed without saying so (a navigation mid-edit) would
      // otherwise stay, since Dwains cannot see it here to remove it.
      root.querySelectorAll(PICKER).forEach((old) => old.remove());
      root.appendChild(node);
      node.addEventListener("dialog-closed", () => {
        if (node.parentNode === root) node.remove();
      }, { once: true });
    } catch (err) {
      /* left where Dwains put it: the upstream behaviour, nothing worse */
    }
  };

  // Keep Dwains' card editor in step with what was chosen in it
  // (reported upstream as dwains-dashboard-next#19). Home Assistant's card
  // editors do not keep their own changes: they announce them with
  // config-changed and expect the host to hand the result back with
  // setConfig. Dwains merges the change into its card but never hands it
  // back, so every Home Assistant update redraws the editor from the first,
  // empty config, and the next change sends that empty entity and wipes the
  // one chosen. Reproduced with the agent removed entirely, so it is not
  // caused by anything here. After Dwains has merged (its own listener runs
  // at the editor; this one is a capture listener, so it defers a
  // microtask), Dwains' own card is given back to Dwains' own editor. Nothing
  // happens outside that dialog, or if its internals are not as expected.
  // Once Dwains does this itself, it is a harmless repeat.
  try {
    window.addEventListener("config-changed", (ev) => {
      const dialog = ev.composedPath().find((n) => n && n.localName === PICKER);
      if (!dialog) return;
      queueMicrotask(() => {
        try {
          const editor = dialog._configEl, card = dialog._card;
          if (editor && card && card.type && typeof editor.setConfig === "function") {
            editor.setConfig(card);
          }
        } catch (err) {
          /* left as Dwains has it: the upstream behaviour, nothing worse */
        }
      });
    }, true);
  } catch (err) {
    /* no listener, no correction */
  }

  sweep(document.body);
  try {
    new MutationObserver((records) => {
      let added = false;
      for (const record of records) {
        if (!record.addedNodes || !record.addedNodes.length) continue;
        added = true;
        if (record.target === document.body) record.addedNodes.forEach(rehome);
      }
      if (added) run();
    }).observe(document.body, { childList: true, subtree: true });
  } catch (err) {
    // Without an observer, catch the common case of a later mount.
    [400, 1500, 4000].forEach((ms) => setTimeout(run, ms));
  }
})();

// A fourth: Home custom cards that are saved but gone after a reload
// (dartec-ha-manager#25, reported upstream as dwains-dashboard-next#20).
// Dwains stores them in its strategy config as `home_custom_cards`, but both
// of its generators copy a fixed list of keys onward, the Home view's
// strategy and then the layout card, and that key is not on either list. So
// the card is saved, shows until the page reloads, and then is never drawn.
// Reproduced on a clean install with only Dwains. The generators are wrapped
// to carry that one key through, only where Dwains left it out, so this
// does nothing once Dwains passes it itself. Its own IIFE, so it does not
// depend on the stylesheet above, and it runs as the module loads, before
// Home Assistant asks the strategy to build the dashboard.
(() => {
  const DASHBOARDS = ["ll-strategy-dashboard-dwains-dashboard-next", "ll-strategy-dashboard-dwains"];
  const VIEWS = ["ll-strategy-view-dwains-dashboard-next-view", "ll-strategy-view-dwains-view"];
  const LAYOUT = "custom:dwains-dashboard-next-layout-card";

  const wrap = (tag, carry) => {
    try {
      customElements.whenDefined(tag).then(() => {
        const cls = customElements.get(tag);
        if (!cls || typeof cls.generate !== "function" || cls.__dartecHomeCards) return;
        const original = cls.generate;
        cls.generate = async function (config, hass) {
          const out = await original.call(this, config, hass);
          try {
            const cards = config && config.home_custom_cards;
            if (Array.isArray(cards) && out) carry(cards, out);
          } catch (err) {
            /* as Dwains built it: the upstream behaviour, nothing worse */
          }
          return out;
        };
        cls.__dartecHomeCards = true;
      });
    } catch (err) {
      /* no custom elements registry: nothing to do */
    }
  };

  DASHBOARDS.forEach((tag) => wrap(tag, (cards, out) => {
    (out.views || []).forEach((view) => {
      if (view && view.strategy && !("home_custom_cards" in view.strategy)) {
        view.strategy.home_custom_cards = cards;
      }
    });
  }));
  VIEWS.forEach((tag) => wrap(tag, (cards, out) => {
    (out.cards || []).forEach((card) => {
      if (card && card.type === LAYOUT && !("home_custom_cards" in card)) {
        card.home_custom_cards = cards;
      }
    });
  }));
})();
