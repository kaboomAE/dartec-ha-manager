# Dartec HA Manager Agent — Handover

**Written**: 2026-08-27 · **Repo**: `kaboomAE/dartec-ha-manager` (**public**)
**Current version**: 0.25.0 in `manifest.json`, **not yet released** (0.24.0,
released 2026-09-28, is what HACS installs)

The [README](README.md) is for people installing this. This document is for
whoever maintains it. The manager side has its own handover in the private
server repo.

---

## 1. What it is

A Home Assistant **custom integration**, distributed through HACS, that links one
home to the Dartec fleet dashboard.

**Not an add-on, deliberately.** Add-ons only work on HA OS and Supervised
installs; a custom integration runs on all four install types including
Container and Core. That decision is written up in the server repo's
`docs/03-distribution.md`.

**The security shape, which is the whole design:**

- The agent opens **one outbound WebSocket** to the manager. Nothing listens,
  nothing is forwarded, no VPN, no ports opened at the customer's house.
- Home Assistant credentials **never leave the home**. The manager holds only a
  per-home pairing token, which it can rotate.
- Remote commands are checked against an **allowlist inside the agent**. The
  manager cannot run arbitrary code in a customer's home even if the manager
  itself is compromised. Both ends enforce it; the agent's copy is the one that
  matters.
- For loopback work the agent mints itself a **5-minute owner token** and revokes
  it in a `finally` block.

---

## 2. Module map

Everything lives in `custom_components/dartec_ha_manager/`.

| Module | Responsibility |
|---|---|
| `__init__.py` | Entry setup/unload. Restores branding from entry options, starts the cloud link |
| `config_flow.py` | The setup dialog — server URL + enrolment code (redeemed for a pairing token) or pairing token |
| `const.py` | `DOMAIN`, config keys, `SNAPSHOT_INTERVAL_S = 60`, reconnect backoff bounds |
| `cloud_link.py` | The outbound WebSocket: connect, auth, push a snapshot every 60 s, handle inbound commands, reconnect with backoff |
| `collector.py` | Builds the snapshot. Core version, integrations, add-ons, HACS, automations, dashboards, logs, host metrics, backups, areas, devices, entities |
| `device_health.py` | Batteries (one row per device, lowest first) and devices that stopped answering, summarised for the manager's alerts; **the rules for telling an expected-unavailable entity from a dead device live in its docstring**. No module-level HA imports |
| `commands.py` | Command dispatch — routes an inbound action to its handler |
| `ws_bridge.py` | Talks to HA's own websocket/REST over loopback with a short-lived self-minted token |
| `machine.py` | Host metrics; includes the ARM64 CPU-model decoding. Also the slow-cadence machine identity (Supervisor board, machine, MAC, the data disk) and disk health, cached so a snapshot only copies a dictionary. **Not called `hardware.py`**: Home Assistant reserves that name for hardware platforms, and 2026.5–2026.6 log an error for any `hardware.py` without `async_info` (#27) |
| `disk_health.py` | What the disk and the machine's temperatures can be read from **without new privileges**: the Supervisor API, the host's UDisks2 over D-Bus (read-only, SMART for NVMe and ATA), and sysfs (hwmon sensors, eMMC life time). No module-level HA imports |
| `lovelace_cmds.py` | Dashboard read/create/save |
| `registry_cmds.py` | Areas, floors, device and entity assignment |
| `user_cmds.py` | Home Assistant user accounts |
| `hacs_cmds.py` | HACS install/list: **an exact `version` on every install** (`LATEST_ALLOWED` is the one exception) and the downgrade guard |
| `hacs_token.py` | The GitHub token in HACS's config entry: its fingerprint for the snapshot, and `hacs_token_set`. No HA imports |
| `home_cmds.py` | Themes, branding, agent self-update, HA restart |
| `backup_cmds.py` | Backup list/create/delete/schedule, and upload to Dartec storage |
| `tunnel_cmds.py` | The Cloudflare tunnel: `tunnel_status` and `tunnel_stop` only, to find and take down one a home still has. **Setting one up is retired** (0.24.0, owner's decision 2026-09-27: Dartec Link replaces it); `tunnel_setup` is refused by name through `service_policy.RETIRED_ACTIONS` |
| `branding.py` | Installer branding in the sidebar and tab title, plus its config endpoint |
| `arabic_states.py` | Dartec's formal Arabic for the HA state words HA's Arabic leaves in English ("On", "Cool", "Open"), written into Home Assistant's own Arabic translation cache at setup, only for listed keys still in English (or the one listed mistranslation), and put back on unload (#55). **The words are the owner's** (kaboomAE/dartec-ops#15): change them only with his say. No module-level HA imports |
| `ha_update.py` | Guarded Core/OS updates: backup, update, resume after restart, health check, rollback, restore. The job lives in a `Store` and is reported in `snapshot.ha_update`. Its policy (`GUARDED_ACTIONS`, the opt-out) is in `service_policy.py` |
| `trust.py` | **What the agent accepts from the manager**: the fixed host allowlists (`MANAGER_HOSTS`, `LINK_HOSTS`), `check_url` and hand-followed redirects, the id patterns for anything put into a path, and `bounded_mb`. No HA imports. The rules it serves are in [docs/trust-boundary.md](docs/trust-boundary.md); read that before adding a command |
| `version.py` | Version comparison, and the agent's own running version (from HA's loader). No module-level HA imports, so CI can test it directly |
| `registry_paging.py` | Filtering, counting and paging a registry listing, and `inventory_digest` (the hash the manager compares to decide whether its full entity list is stale). No HA imports |
| `registry_access.py` | Enumerating the device registry in a way that works on both its pre- and post-2026.9 shapes. No HA imports |
| `privacy.py` | Household names never leave the home (#20): every snapshot and command reply is scrubbed in `cloud_link` so each household member's name becomes a stable id (`hm_` + 10 hex, an HMAC under a secret in `.storage/dartec_ha_manager.privacy`), and every inbound command has the ids turned back into names. Core has no HA imports; `tests/test_privacy.py`. The manager's copy is `server/app/household_privacy.py`, and the two must agree |
| `household.py` | **"My Home"**, the homeowner's household panel: every rule about who may add, change, pause, reset or remove whom (owner, `dartec` and system accounts untouchable; always an active admin left; nobody changes their own role; only the owner sets another's password, as in HA itself). Plain functions over dicts, no HA imports, unit-tested in `tests/test_household.py` |
| `household_ws.py` | Registers the "My Home" sidebar panel (`/dartec-household`, `require_admin`) and its admin-only websocket commands (`dartec_ha_manager/household/*`; `set_language` and `create`'s `language` since #49, the same rule as the first dashboard: yourself or a person here), applies `household.py` using HA's own auth, person and per-user frontend storage, writes the logbook ("done by") and a local activity list, and gives the snapshot its `household` counts. Refuses the agent's own loopback token. **No remote command reaches it** |
| `user_prefs.py` | A person's own `language` (the whole locale) and `theme` in the frontend's per-user store, as room panels and My Home set them for someone else. No HA imports, unit-tested in `tests/test_user_prefs.py` |
| `panels.py` | **Room panels**: who is a panel account (a `panel-` username on an account that is not the owner, not a system account and not an administrator; all four parts matter), the username and password rules, the hidden-sidebar list, the status row. Plain functions over dicts, no HA imports, unit-tested in `tests/test_panels.py`. **Its docstring says what a panel account is not: a security boundary** |
| `panel_cmds.py` | `panel_setup` / `panel_update` / `panel_remove` / `panel_status` and the snapshot's `panels`: the account through HA's own `config/auth/*` commands over the loopback, the first dashboard and hidden sidebar through the frontend's per-user store (as My Home does) |
| `www/` | Brand SVGs served as static assets; `dashboard-fix.js`, the injected corrections (Dwains, the brand fonts, and a value and its unit in right-to-left languages, #55); `www/fonts/` the brand fonts `dashboard-fix.js` declares (Lateef, IBM Plex Mono; unmodified OFL files, never Dubai: see its README); `www/household/` is the panel (one dependency-free web component) and its `i18n/en.json` / `ar.json` strings |

`tests/` holds the unit tests that need no Home Assistant. `tests/live/` runs
this working tree inside real Home Assistant containers (§4). Deeper command
behaviour is tested from the server repo's end-to-end suite against a real
Home Assistant.

---

## 3. Releasing — the part that bites

**HACS installs the latest GitHub _Release_, not the latest commit and not the
latest tag.** A pushed tag with no Release attached is invisible to HACS.

This has already caused one real failure: v0.10.0 through v0.10.3 were tagged and
released, then v0.10.4 was committed and tagged but *not* released — so Fleet
maintenance would have installed 0.10.3 over a running 0.10.4 and restarted the
customer's Home Assistant for nothing.

**Release checklist:**

1. Bump `"version"` in `manifest.json`. HACS and the fleet view both read it.
2. Commit and push.
3. Tag: `git tag -a v0.10.5 -m "..."` and `git push origin v0.10.5`.
4. **Publish a GitHub Release from that tag.** `gh release create v0.10.5
   --generate-notes`, or the web UI. Do not skip this.
5. Confirm CI is green (four jobs, below).
6. Roll out from the manager: Admin → Fleet maintenance.

**Version numbers are compared by value, not text.** `version.py` exists because
`"0.10.4" < "0.9.0"` as strings — every release past `.9` reads as a downgrade
under naive comparison. `tests/test_version.py` covers exactly that trap.

---

## 4. CI

`.github/workflows/validate.yml`, on every push and pull request, and weekly
on Monday.

| Job | Checks |
|---|---|
| `hassfest` | Home Assistant's own manifest/structure validation against current dev |
| `hacs` | That the repo is HACS-installable (`brands` ignored — needs a merged PR to home-assistant/brands, only required for the default store) |
| `unit` | `pytest tests` — the HA-free modules |
| `live` | `tests/live/run_live.py` against real `ghcr.io/home-assistant/home-assistant` images: **2026.8.3 and 2026.9.2** on every run, plus **`stable` and `beta`** on the weekly and manual runs |

**What `live` proves, per version** (added 2026-09-17, first run against
0.17.0):

- A fresh container with `default_config`, the `demo` integration (61 devices,
  no network), and the agent copied from the checkout. It is onboarded through
  the real onboarding API, one device is put in an area, and the agent is
  paired through its **real config flow** to a stub manager
  (`tests/live/stub_manager.py`) running on loopback inside the container —
  loopback because the config flow refuses any other plain-http URL.
- The stub takes snapshots and sends `registry_query`. The device ids, names,
  manufacturers and area names in both are compared with Home Assistant's own
  `config/device_registry/list`, and `device_count` with its length. This is
  what covers the **id-resolving path on 2026.8**, which until now had only run
  against fakes: on 2026.8.3 iterating the registry really does yield ids.
- The snapshot's `core.version` matches the running Home Assistant and
  `agent_version` matches `manifest.json` (the loader path in `version.py`).
- The Home Assistant log has **nothing reported against `dartec_ha_manager`**:
  no `device_registry.devices` mapping deprecation, no read of a device's
  deprecated `config_entries` (2026.10), no blocking call on
  `manifest.json`, no other `Detected ...` report, no `ERROR` line naming it.
- **A HACS token swap** (added 2026-09-17, #8): the stub manager sends
  `hacs_token_set` to a stand-in HACS (`tests/live/hacs_stub/`) that registers
  HACS's reload-from-update-listener and writes to disk before it unloads. The
  run fails unless the swap answers `ok`, the entry is still `loaded`, the
  running setup has the new token, a snapshot reports it, the listener never
  fired, and the log shows no `failed_unload`, `OperationNotAllowed` or
  unretrieved task exception. The stub also replaces the agent's GitHub check,
  since the container must not depend on GitHub accepting a made-up token.
- **The full entity inventory** (added 2026-09-18, 0.19.0): after the HACS swap the stub reads every entity the way the manager does (`registry_query` with `include_unregistered`, a 1,000-row page). The run fails unless it holds every entity Home Assistant's REST API lists, its unregistered rows are exactly the snapshot's `unregistered_entities`, the registry count plus the unregistered count is its total, and its digest is the snapshot's.
- **Batteries and offline devices** (added 2026-09-18, 0.18.0): `device_health_setup.py` takes demo devices down, labels one `dartec_expected_offline` and drains a battery to 4%, all through Home Assistant's own APIs. The run fails unless `offline_devices` is exactly the one fully-down device — not the labelled one, not the device with one dead entity out of two, not Push's never-pressed button, not the Backup service device — and `batteries` equals every registered battery-percentage sensor Home Assistant shows, lowest first, with the demo's `battery_charging` sensor left out.
- **"My Home"** (added 2026-09-18, 0.20.0) has its own runner, `run_live_household.py` (driver: `household_driver.py`, run inside the container). It signs in as the owner, an admin, a regular user, a guest and the `dartec` account and checks through HA's own commands (`config/auth/list`, `person/list`, `frontend/get_user_data` as the person, the logbook) that create, edit, pause (signs them out), resume, password reset, first dashboard and remove all do what they say, that every guard refuses, that a regular user is neither sent the panel nor answered, and that the snapshot's `household` is counts only. `household_screenshots.py` regenerates the guide's pictures from a `--keep` container (needs Playwright).
- **Room panels** (added 2026-09-19, 0.23.0) have their own runner, `run_live_panels.py` (driver: `panels_driver.py`, in the container). The stub manager runs in its `drive` scenario, where it watches its outbox continuously, so the driver sends each command the way the manager would and gets the answer in well under a second. It closes commissioning and checks `panel_setup` is refused with `code: "consent"` and creates nothing, opens a window with the homeowner's own `allow_maintenance` service, then signs in **as the panel account** through the login flow and checks, through Home Assistant's own commands, that it is a non-admin, `local_only` account with no person; that its own `frontend/get_user_data` has the room as `core.default_panel` and `sidebar.hiddenPanels` equal to every panel `get_panels` sends it except the room; that its own `lovelace/config` for the room loads; that setup again keeps the user id, restores `local_only`, and makes the old password stop working; the refusals (`username_taken` for an administrator's `panel-` username, `no_dashboard`, `not_panel`, `invalid`); `panel_status` (signed in, `last_used_at` after the sign-in); `panel_update`; `panel_remove` and its "already gone"; the snapshot's `panels`, `core.language` and uncounted `household`; My Home not listing or changing it; the logbook lines; and that neither password appears in any answer, the snapshot, the logbook or Home Assistant's log. Checked by breaking it: `local_only: False` on create, and the room left in its own hidden list, each fail it.
- **The Dwains dashboard fix** (added 2026-09-19, dartec-ha-manager#25; gated 2026-10-02) has its own runner, `run_live_dwains.py` (needs Playwright with Chromium and Firefox). It installs a home's frontend (Dwains Dashboard Next at `--dwains`, default v1.10.0, its whole `dist` folder because 1.10.0 loads `chunks/`; card-mod v4.2.1 and dartec-theme v1.1.0, fetched at those tags), opens Dwains' **Add card** pop-up in 32 combinations (browser, `www/dashboard-fix.js` served or blocked at the network, light/dark, card-mod on/off, Dartec/default theme; `--fix-file` adds a third copy, e.g. a released agent's), and checks: with any fix, the pop-up is clean, sits inside `<home-assistant>`, leaves no more open than without the fix, opens again, and opens the thermostat in Dwains' editor with a clean preview that keeps a chosen entity through an update and an option change; the fix adds no error of its own; the notification row in dark mode reaches 4.5:1 (12.59:1; 1.25:1 without, still so on Dwains 1.10.0). On Dwains 1.8.1 and later it also requires upstream to be clean **without** the fix, and this tree's fix to do nothing to the pop-up: the picker mounted as often as without it (no second move), and the editor's `setConfig` called as often per change (no second hand-back), both counted in the page. A saved Home custom card must be drawn after a reload. It also mounts the previewed thermostat by hand under `document.body` and inside `<home-assistant>`, which is the cause of #18 (HA 2026.7+ gives cards their formatters only inside it). `--bench URL --token-file F` runs the same against the bench (refuses unless the location is `TEST`), with a `tree` mode that substitutes this working tree's fix at the network, and puts back what it changed. CI runs it against Dwains v1.8.0 and v1.10.0. The same module declares the brand fonts (#59); the runner checks each file is served as shipped and that Lateef and Plex Mono load in both browsers, Lateef Arabic only at 150%, with no Dubai face.
- **The brand's old spelling** (added 2026-09-19, 0.21.0, dartec-ha-manager#25) has its own runner, `run_live_brand.py` (driver: `brand_driver.py`, in the container). It starts HA with `DarTec` stored as the default theme and only `Dartec` loaded, as a home themed under dartec-theme v1.0.0 is, and checks that the snapshot's `frontend_theme` says so (`stored_default: DarTec`, running `default`, `Dartec` available) and that `branding` carries the sidebar title. It then retitles the agent's entry "DarTec: ...", reloads it, and checks the agent corrected it; renames a "DarTec Dashboard" with `lovelace_update` and checks the answer, the logbook line, Home Assistant's own dashboard list and the next snapshot.
- **The dashboards the fleet is given** (added 2026-09-26, #56, recommendation R13) have their own runner, `run_live_dashboards.py` (driver: `dashboards_driver.py`, in the container; needs Playwright with Chromium on the host). It builds the dashboards guide's test villa from Home Assistant's own helpers, through its config flows (two floors, seven Arabic-named rooms, six generic-thermostat ACs with presets, lights, curtains, a leak sensor, raw-named relays, an offline kids' room, a 26-circuit suite: 84 helpers, a few seconds), sets the bench's states, and applies every dashboard in `docs/dashboards/prototypes/` (today's generator and the recommended home, room and bedroom-panel dashboards, English and Arabic) **through the agent's `lovelace_create`**, as the manager does. It fails on any entity or action a dashboard names that Home Assistant lacks, then opens every view (17 per language) in Chromium at phone 390x844, tablet 800x1333, wall 1280x800 and NSPanel 480x480, the Arabic ones with the account's own language set to Arabic, and fails on any `hui-error-card`, any `hui-warning` (the "entity not found" box), a view with no card, or a view not drawn in its language. The 136 screenshots, the evidence and Home Assistant's log are uploaded as `live-dashboards-<version>` on every run, pass or fail, for a person to compare across releases; `--schemes light,dark` adds dark, `--sizes wall` shortens a local run. Checked by breaking it: a tile pointed at a missing entity and a card of a type that does not exist fail it twice over (the entity check, and the error card and warning in the page). **It takes no timings**: headless Chromium shows no cost for blur or anything a tablet's GPU pays for. The CI job installs `fonts-noto-core` so Arabic is drawn in Noto, as on an Android tablet. **Units in Arabic** (added 2026-10-06, #55): on every view it measures where each "°C" and "%" is drawn against its number in `state-display` (tiles, badges, heading badges), `ha-tile-info`'s secondary line (the area card) and `ha-control-number-buttons` (the stepper), and fails if a unit lands before its number in Arabic, if a light that is on does not read the agent's «مُشغَّل» in Arabic or "On" in English, if no value with a unit was found to measure, or if any trace of the correction (an isolate in those text nodes, an extra stylesheet on the stepper) is found in English.
- **A canary integration** (`tests/live/canary/`) deliberately does every
  wrong thing above, and the run fails unless Home Assistant reports the canary
  for them on exactly the versions that should (the mapping one only from
  2026.9, a device's `config_entries` read only from 2026.10). Without it a
  clean log could mean the warning's wording changed. It is never installed
  outside the container.
- **Arabic state words** (added 2026-10-06, 0.25.0, #55): `arabic_states_check.py`, in the
  container, asks `frontend/get_translations` for `entity_component` in English and Arabic,
  one integration at a time, exactly as the frontend does on every page load. The run fails
  unless every key `arabic_states.py` corrects still exists in Home Assistant's English with
  the English the table expects (a key HA renames or rewords is skipped by the agent, so it
  would silently go back to English; this is where that shows), each reads Dartec's Arabic
  unless Home Assistant has its own, and what HA translates itself (the climate preset
  "Home", في المنزل) is unchanged.

It was checked by breaking the agent three ways — `.values()` in
`registry_access` (caught on 2026.9.2), ids left unresolved (caught on
2026.8.3: `registry_query` fails and `device_count` is missing, because the
collector swallows the error), and a `manifest.json` read in `version.py`
(caught as a blocking call).

**Why it runs on every push, not only on a schedule:** each version takes about
15 seconds once its image is pulled; the pull dominates the job. `stable` and
`beta` stay off pushes so a new Home Assistant release cannot turn an unrelated
PR red — the weekly run is where that shows up. On failure the job uploads the
Home Assistant log, snapshot and registry dumps as `live-<version>`.

Run it locally the same way (needs Docker and Python 3.10+, nothing else):

```bash
python tests/live/run_live.py                    # 2026.8.3 and 2026.9.2
python tests/live/run_live.py 2026.9.2 --keep    # leave the container up to poke at
```

When a new Home Assistant release changes something the agent depends on, pin
the last release before it and the first release with it in the matrix, the
way 2026.8.3/2026.9.2 bracket the registry change.

**Run hassfest locally before pushing** — it is the same container CI uses:

```bash
docker run --rm -v "${PWD}:/github/workspace" ghcr.io/home-assistant/hassfest
```

**Manifest dependencies matter.** `branding.py` imports from the `http` and
`frontend` components at module load, so both must be declared in
`manifest.json`'s `dependencies`. hassfest reports only the first offender per
run, so fix them all at once or you will fail twice.

---

## 5. Developing against the test rig

Never develop against a customer's home. The rig is a Docker container:

```bash
docker run -d --name dartec-ha-test -p 8123:8123 \
  ghcr.io/home-assistant/home-assistant:stable
```

Deploy a working copy and restart:

```bash
for f in custom_components/dartec_ha_manager/*.py; do
  docker cp "$f" "dartec-ha-test:/config/custom_components/dartec_ha_manager/$(basename $f)"
done
docker cp custom_components/dartec_ha_manager/manifest.json \
  dartec-ha-test:/config/custom_components/dartec_ha_manager/manifest.json
docker restart dartec-ha-test
```

Rig login is `dartec` / `dartec-test-2026` — a throwaway rig credential, never a
customer's.

**Watch out**: a HACS download **overwrites the whole integration directory**. A
hand-copied working version disappears the moment HACS installs a release, and
files that do not exist in that release (a new module you just added) are
deleted rather than merged.

---

## 6. Command surface

Dispatch lives in `commands.py` and has two layers, which matters when you add
a command:

- **Special-cased first**: `addon_start` / `addon_stop` / `addon_restart` (via an
  `_ADDON_ACTIONS` map) and `call_service`.
- **Then six per-module `HANDLERS` dicts**, tried in order: lovelace → hacs →
  home → registry → user → tunnel. Backup handlers are imported alongside them.

29 actions live in those dicts; with the four special cases the surface is 33,
matching the manager's `ALLOWED_ACTIONS` exactly. **Both lists have to be edited
together** — the manager rejects anything not in its copy, and the agent rejects
anything not in its own.

The full surface as of 0.10.4:

```
addon_restart, addon_start, addon_stop, call_service,
lovelace_get, lovelace_save, lovelace_create, lovelace_update,
hacs_install, hacs_list, hacs_token_set, theme_set, automation_create, branding_set,
floor_upsert, floor_delete, area_upsert, area_delete,
devices_assign, entities_assign,
users_list, user_create, user_update, user_set_password, user_delete,
agent_update, ha_restart,
tunnel_status, tunnel_setup, tunnel_stop,   (tunnel_setup retired in 0.24.0)
backup_list, backup_create, backup_delete, backup_schedule, backup_upload
```

`lovelace_create` takes optional `show_in_sidebar` and `require_admin` (booleans; anything else is refused and nothing is created). Left out, they are what they always were: in the sidebar, open to everyone. `require_admin: true` is how the manager makes an admin-only preview (#65). The answer carries both as applied.

`lovelace_update` (0.21.0) renames a storage dashboard: payload `{url_path, title, icon?}`, answered with `previous_title`, and a line in the household's logbook. Rollout sets a title only when it creates a dashboard, so this is the one way to correct one afterwards (dartec-ha-manager#25). Like the mutating blueprint commands it is not in the manager's generic `ALLOWED_ACTIONS`: it has its own audited route, `PATCH /api/instances/{id}/lovelace`.

**Room panels (0.23.0).** Four commands, the manager's side and the contract are in dartec-ha-manager-server `docs/23-room-panels.md`; rules in `panels.py`:

- `panel_setup {name, username: "panel-<slug>", password (>= 12), url_path}` — **sensitive** (standing access; the same consent as `user_create`). Creates the account (`system-users`, `local_only`, a `homeassistant` login, rolled back if the login fails, no person), or, if a panel account already has that username, puts it back into that shape with the new password (`created: false`; the manager relies on this being retry-safe). The username held by anyone who is not a panel account is `code: "username_taken"`; a `url_path` that is not one of the home's Lovelace dashboards, or is admin-only (a sidebar override counts), is `no_dashboard`; malformed input is `invalid`. Then writes the account's own frontend data: `core.default_panel = url_path` (other `core` keys kept) and `sidebar = {hiddenPanels: every panel a non-admin is sent except url_path, panelOrder: [url_path]}`. Answers `{user_id, username, created, url_path, hidden_panels}`. The password is never logged or answered; errors from HA are scrubbed of it, and unexpected failures are reported by exception type only.
- `language` and `theme` (#49, not yet released), optional on both `panel_setup` and `panel_update`: `language` is `en` or `ar`, written to the panel account's own `language` user data as the whole locale (`{language, number_format: "language", …}`, the shape the profile page writes, which turns the tablet right to left); `theme` must be a theme loaded on the home (or `default`), written as `{theme: name}` to its own `theme` user data (per-person themes, HA 2026.2+). Left out, each is left as it is; `null` clears it. An unknown language is `invalid`, an unloaded theme `no_theme`, both before anything is created. Answers, `panel_status` rows and the snapshot's `panels` carry `language` and `theme`. Rules in `user_prefs.py` (no HA imports), shared with My Home.
- `panel_update {user_id, url_path?, name?, language?, theme?}` — routine. Only for a panel account (`not_panel` otherwise). Rewrites the first dashboard and the hidden list; with no `url_path` it keeps the current one, which is how a dashboard added to the home after setup gets hidden too (**hiddenPanels is a list, not a rule: a new dashboard is visible to existing panels until `panel_update` runs**).
- `panel_remove {user_id}` — routine. Deletes a panel account, refuses anyone else, `{ok: true, gone: true}` when it is already gone.
- `panel_status {user_id?}` — routine, read only: `{user_id, username, name, is_active, local_only, default_panel, signed_in, last_used_at}` per panel. `signed_in` means a `normal` refresh token exists (a login); `last_used_at` is the latest across them. No addresses.

Why the last three are routine is in `service_policy.py`. Setup, update and removal are logbooked, setup with the account and dashboard. The snapshot gains `panels` (the `panel_status` list) and `core.language`. In My Home a panel account is kind `panel`: not listed, not counted, not changeable (`Refused` code `panel`), one read-only line saying how many exist, and `panel-` is reserved for new people (`username_panel`). Room dashboards (`dartec-room-*`) are never offered as a first dashboard. **Not a security boundary** — HA has no per-user device permissions, so a panel account can control every device through the API; "Always hide the sidebar" is per browser and set on the tablet by the technician. `docs/my-home/README.md` tells the homeowner the same.

`hacs_token_set` (0.17.0, fixed in 0.17.1) replaces the GitHub token in the
home's existing HACS config entry — payload `{token, fingerprint}`, where the
fingerprint is the first 16 hex characters of the token's SHA-256 and must
match it. It never creates a HACS entry and never leaves a home with a worse
token than it had: the new token is first checked with an authenticated GET to
GitHub; then, only if HACS is loaded and its queue is idle, HACS is **unloaded
through Home Assistant**, only `data["token"]` changes, HACS is **set up**
again and must reach LOADED within 30 s. If it does not, the previous data
(held in memory) goes back the same way. It answers
`{ok: true, changed, fingerprint}` or `{ok: false, reason}`: `invalid_token`,
`no_hacs_entry`, `token_rejected`, `github_unreachable`, `hacs_not_loaded` and
`hacs_busy` (nothing touched), `rolled_back` (swap undone, HACS loaded on the
old token), `reload_failed` (the rollback failed too).

**Never write HACS's entry while it is loaded and then reload it.** HACS
registers an update listener that unloads and sets itself up by hand, outside
Home Assistant's state machine, so the write starts a reload of its own. 0.17.0
did exactly that, followed by `async_reload`; on the bench Pi the two reloads
collided and HACS sat in `failed_unload` until Core restarted, with the
rollback refused the same way (#8). Unloading first runs HACS's
`async_on_unload` callbacks, which remove the listener. The unit fakes now
model the listener, and the live test runs the swap against a stand-in HACS
(`tests/live/hacs_stub/`) that reloads the way HACS does and counts every
listener reload; 0.17.0 fails it. Swaps
and rollbacks are logbooked by fingerprint. The snapshot reports
`hacs_token: {token_fingerprint}` — its own key, because `hacs` is the
repository list. The token itself is never in a response, a log line or the
logbook. It is **routine** (no consent), confirmed by the owner on
2026-09-17; see the comment in `service_policy.py`, where switching it to
sensitive is one line.

`maintenance_status`, `maintenance_request` and (0.16.0) `commissioning_complete`
are answered before the consent gate, because none of them grants anything —
the last one only ever ends commissioning, and reads nothing from the command.

**When adding a command:** add the handler, register it in `commands.py`, add it
to the manager's `ALLOWED_ACTIONS`, and — if it changes anything in the home —
add it to the server's read-only-freeze sweep in `tests/test_safety.py`. That
parametrised list is what catches a mutating route written without the freeze
check.

---

## 7. Gotchas

**Privacy**
- **Nothing reaches the manager except through `cloud_link`, and `cloud_link`
  scrubs it** (`privacy.py`). A new outbound path (an HTTP post, a second
  socket message type) must go through `Pseudonymiser.scrub` too, or it will
  leak household names. The snapshot says `privacy: {"version": 1}`; the
  manager refuses to send `hm_` ids to a home that does not.
- A reply is scrubbed **once**, with the names before and after the command
  merged (`merged`), because an inventory digest in it is folded with the
  names and has to match the snapshot's.

**Collector**
- Entities inherit their area from their **device**. Reading only the entity
  registry made 1,265 entities look room-less (fixed 0.4.0).
- `core_stats` measures the Core container, not the host — host metrics need
  psutil (0.3.x).
- Mainline ARM64 `/proc/cpuinfo` has no `model name`; the CPU shows as null on
  an HA Green unless implementer/part codes are decoded (0.3.2).
- **Never block the event loop.** File and process reads go through an executor
  and are cached (0.3.1). The agent's own version comes from
  `homeassistant.loader`, not a read of `manifest.json` — that read ran every
  snapshot and HA 2026.9 logs it as a blocking call.
- **Never use `device_registry.devices` as a mapping** (`.values()`, `.get()`,
  `[id]`, `id in`). HA 2026.9 logs it and 2027.9 breaks it. Enumerate with
  `registry_access.all_devices`, look one up with `registry.async_get(id)`;
  `tests/test_registry_access.py` sweeps the package for the old form, and the
  `live` CI job checks the real log on 2026.8.3 and 2026.9.2.
- **Never read a device's `config_entries`, `config_entries_subentries` or
  `primary_config_entry`.** From 2026.8 a device has one
  `config_entry_id` and those are shims; 2026.10 logs every read and 2027.10
  removes them. Before 2026.8 `config_entry_id` does not exist. Use
  `registry_access.device_config_entries(device)`; the same test file sweeps
  for the shims, and `live` checks the real log on 2026.10 (the weekly `beta`
  and `stable` runs, or by hand).

**Dashboards**
- The live `DashboardsCollection` is a setup-local variable and cannot be reached
  from a command. Use the official `lovelace/dashboards/create` over the loopback
  websocket (0.2.1).

**HACS**
- **Every `hacs_install` names its version** (#54, owner decision 2026-09-26).
  `version` is a release tag as HACS names it (`v1.8.0`, `5.0.15`) or a commit
  hash, never a branch; it goes to HACS's own `hacs/repository/download`
  unchanged. Without one the answer is `code: "version_required"` and HACS is
  not even told about the repository, except `only_if_missing` on something
  already installed, which downloads nothing. An older version is
  `code: "downgrade"` (unless `allow_downgrade`); HACS recording another
  release after the download is `code: "version_mismatch"` (`changed: true`).
  The one exception is `agent_update`, which takes this repository's latest
  release under the guarded-update decision of 2026-09-18; it is a keyword
  argument (`latest_ok`) no command can set, and `hacs_cmds.LATEST_ALLOWED`
  lists it with who decided it. The manager's catalogues must send versions
  before this reaches homes: dartec-ha-manager-server#57.
- HACS records the tag it was given as `installed_version` (its
  `async_download_repository` sets `ref`, and `version_to_download` returns it),
  which is what the mismatch check reads back. A branch would record `None`
  and show the commit instead, one more reason branches are refused.
- Refresh the repository before deciding there is nothing to do — the cached
  index lags a release and reports "already up to date" (0.10.1).
- A newly added repository reports `installed_version: None` even when the files
  are already on disk, which is exactly the shape of a hand-installed agent. The
  downgrade guard therefore takes the **newer** of HACS's record and the version
  read off our own manifest.

**Backups**
- `include_all_addons` and `include_folders` are **Supervisor-only**. Sending them
  to a Container install makes core backup reject the entire request (0.10.2).
- Core backups declare no size, so report the bytes actually streamed (0.10.3).
- `backup_upload` is **not** window-gated. It needs the home's own
  `offsite_backups` option (`service_policy.OPT_IN_ACTIONS`), set in the
  integration's options, so scheduled copies can run with nobody there. The
  window does not substitute for it, and neither does `unattended_support`.
  `backup_delete` stays behind the window (0.16.0).

**HACS token**
- HACS reads its GitHub token once, at setup, so a new token only takes
  effect when the entry reloads; `hacs_token_set` reloads it and then waits
  for `ConfigEntryState.LOADED`, because a reload that returns is not a HACS
  that loaded.
- Only 401/403-bad-credentials/404 from GitHub mean the token is bad. A 5xx,
  a 429, a rate-limited 403 or no answer at all is `github_unreachable`, and
  the manager retries later — calling those `token_rejected` would condemn a
  good token.
- Errors from updating the entry are reported by type only: an exception
  message can echo the data it was given, and that data holds the token.

**Branding**
- The module is fetched **once per page load**. A tab left open keeps running the
  copy that was current when it opened, so disabling branding could not reach it
  — the script now re-reads `config.json` on navigation/theme/visibility events
  and can undo a sidebar it already rewrote (0.10.4).
- Resolve theme colours through a probe element. A regex over the hex `#17181a`
  returns garbage and picked the wrong logo (0.7.1).
- Anything injected must survive HA re-rendering the sidebar, and must fail
  silently rather than break a customer's UI.

**Config entries**
- HA writes entries with a full schema. A hand-written HACS entry missing
  `disabled_by` caused a `KeyError` on boot and took a live Home Assistant down
  for ~25 minutes. Never hand-write entries; use the config-entry APIs.

---

## 8. Version history — the load-bearing changes

| Version | Change |
|---|---|
| 0.2.1 | Dashboard creation via the official websocket command |
| 0.3.1 | Blocking I/O moved off the event loop |
| 0.3.2 | ARM64 CPU model decoding |
| 0.4.0 | Entities inherit area from device |
| 0.9.0 | HA user management, self-update, tunnels |
| 0.10.0 | Backups |
| 0.10.1 | Forced updates actually update (HACS refresh first) |
| 0.10.2 | Backups work on Container installs |
| 0.10.3 | Accurate offsite copy size |
| 0.10.4 | Branding removal takes effect without a refresh; downgrade protection; `frontend`/`http` dependencies declared |
| 0.11.0 | Service allowlist moved to the `domain.service` pair (default-deny, permanently-blocked tier); homeowner maintenance window for consequential actions; house-wide targeting refused; commands logged to the home's own logbook; config flow refuses non-https |
| 0.16.0 | Commissioning lasts until the install is marked complete (`complete_commissioning` service, the switch turned off, or the manager's close-only `commissioning_complete`), capped at `COMMISSIONING_DAYS` = 30 unless a different `commissioning_days` (or a fresh period) is set in the options flow on the home, stored in the entry's options; the switch reads on while it is open and reports `commissioning_ends_at`; the snapshot carries `commissioning` so the manager can warn about installs left open; offsite backup copies need the home's `offsite_backups` opt-in instead of a maintenance window |
| 0.17.0 | Fleet-wide HACS token rotation: the snapshot reports `hacs_token.token_fingerprint` (first 16 hex of SHA-256, never the token), and `hacs_token_set` verifies a new token with GitHub, replaces only it in an existing HACS entry, reloads, and rolls back to the previous token if HACS does not load; swaps and rollbacks logbooked by fingerprint; routine pending the owner's sign-off. **Swapping broke HACS on a real install (#8): do not run 0.17.0 where the manager pushes tokens** |
| 0.17.1 | `hacs_token_set` unloads HACS through Home Assistant before writing its entry and sets it up after, so HACS's own reload listener no longer races the swap (#8); refuses `hacs_busy` and `hacs_not_loaded` without touching anything; 30 s load wait so a swap and its rollback fit the manager's timeout; routine, confirmed by the owner |
| 0.18.0 | The snapshot carries `batteries` (one row per device: level and/or the binary low flag, lowest first, capped at 200, `battery_count` the true total; phones' `mobile_app` batteries left out) and `offline_devices` (devices whose every judged entity is `unavailable`/`unknown`, with the time the last went down, capped at 100, plus `offline_count` and `devices_judged`), summarised by `device_health.py` so the manager can alert on low batteries and devices offline for hours without being sent every entity's state; a failed pass sends `device_health_error` rather than empty lists that would read as "all fine"; registry entities only. Also carries the refusal of calls on the integration's own entities and of area/device/floor/label targeting (#11, #12) |
| 0.19.0 | **Guarded Home Assistant updates** (`ha_update.py`, owner decision 2026-09-18): `ha_core_update` / `ha_os_update` to one exact stable, newer, Supervisor-offered version, **without a maintenance window**. They take a full backup (confirmed), update, resume after the restart from a `Store`, run a health check, and roll back if it fails: Core by version, then by restoring the backup; the OS by its other boot slot. Every step goes to the logbook and to `snapshot.ha_update`. The homeowner can opt out with the new `switch.dartec_approved_updates` or the options flow (`guarded_updates`, on by default). Supervised installs refuse OS updates. `maintenance_status` reports `guarded`. Snapshots go out at once on each update step (`SIGNAL_SNAPSHOT_NOW`). Released in 0.19.0, not 0.18.0: it merged after 0.18.0 was tagged |
| 0.19.0 | The snapshot says which machine this is and how its disk is holding up. `hardware` gains `ha_uuid`, `machine`, `supervisor_arch`, `mac`, `chassis` and `storage` (model, vendor, serial, size, type, bus, device), read every 6 h; `disk_health` (wear, spare, media errors, bad sectors, power-on hours, temperature and the drive's own limits, with the sources they came from, or `unavailable` saying why) every 15 min; `host.disk_temp_c` and `host.soc_temp_c` every cycle. Nothing new is asked of the home: see `disk_health.py` |
| 0.19.0 | **Guarded agent updates** (owner decision 2026-09-18): `agent_update` joins `GUARDED_ACTIONS` and `GUARDED_WITHOUT_CONSENT`. With consent it runs as before; without it, only as `{restart, job_id, rollout_id}` (no `allow_downgrade`), never when `guarded_updates` is off, and always with a logbook line. The opt-out switch is renamed "Allow Dartec to install approved updates" (same entity id), and a consent refusal carries `code: "consent"`. Homes on older agents still need consent for the update that brings this |
| 0.19.0 | **Every entity** (owner decision on dartec-ha-manager-server#16): the snapshot carries `unregistered_entities` (what Home Assistant runs outside its entity registry — no `unique_id` — shaped like registry rows and marked `registered: false`, capped at 500, `unregistered_count` the true total) and `entity_inventory_digest` (16 hex over every entity's registry facts, never state), so the manager keeps a full per-home inventory past the 2,500-row cap and refetches it only when the digest moves; `registry_query` takes `include_unregistered`, which adds those entities, allows 1,000-row pages and returns `inventory_digest`; battery sensors outside the registry are reported as their own rows keyed `entity:<entity_id>` |
| 0.20.0 | **"My Home"**, the homeowner's household panel (`household.py`, `household_ws.py`, `www/household/`): add a person (Family (can manage the home) = admin, Family = user, Guest = user who can only sign in at home), edit, pause/resume, remove, owner-only password reset, and the first dashboard each person sees (`core.default_panel` in their frontend user data; **a convenience, not a security boundary**). English and Arabic (Arabic unreviewed). Inside the home only: admin-only panel and commands, no remote command, the snapshot gains `household` with counts by role and nothing else. Guide: `docs/my-home/`. Released 2026-09-18 |
| 0.21.0 | **The brand's old spelling, and Dwains' card picker** (dartec-ha-manager#25). The snapshot gains `frontend_theme` (default themes as stored and as running, and the loaded theme names: HA silently runs its own default when the stored name is missing, as for `DarTec` after dartec-theme v1.1.0) and `branding` (`enabled`, `title`). New `lovelace_update` renames a storage dashboard (answers `previous_title`, logbook line). The agent corrects "DarTec" in its own entry title at setup. `www/dashboard-fix.js` moves Dwains' Add card picker inside `<home-assistant>` so HA 2026.7+ gives its previews their formatters (upstream dwains-dashboard-next#18; owner decision 2026-09-19). `hardware.py` renamed `machine.py` so HA 2026.5–2026.6 stop loading it as a hardware platform (#27). LICENSE: Dartec Smart Homes. Released 2026-09-19 |
| 0.21.1 | `www/dashboard-fix.js` hands Dwains' card editor its config back after every `config-changed`, so an entity chosen in the Add card editor no longer goes blank after a few seconds or gets wiped by the next change (upstream dwains-dashboard-next#19; reproduced with the agent removed). Released 2026-09-19 |
| 0.21.2 | `www/dashboard-fix.js` carries `home_custom_cards` through Dwains' dashboard and view strategy generators, so Home custom cards saved in Dwains' settings show after a reload (upstream dwains-dashboard-next#20; reproduced with only Dwains installed). Released 2026-09-19 |
| 0.23.0 | **Room panels** (Phase 3 of the planner-manager integration). `panel_setup` (sensitive), `panel_update`, `panel_remove`, `panel_status` (routine) for `panel-<slug>` accounts: non-admin, local-only, first dashboard = the room's dashboard, every other sidebar entry hidden (`panels.py`, `panel_cmds.py`, §6). Snapshot gains `panels` and `core.language`. My Home: kind `panel`, not a member, not counted, not changeable, one read-only line, `panel-` reserved; `dartec-room-*` dashboards never offered. Arabic strings added unreviewed, like the rest of `ar.json`. New CI job `live-panels`. Released 2026-09-20 |
| 0.22.0 | **Area ids and per-device presence in the snapshot** (#37). Device and entity rows carry `area_id` beside `area` (the name stays for older managers). Device rows gain `available` (the `device_offline` rule without the alerting exclusions, so a device labelled expected-offline still reports `false`; `null` when nothing on it can be judged) and `last_seen` (latest `last_updated` among its running entities). The inventory digest is unchanged. The manager compares room pairs by area id when present (dartec-ha-manager-server#36). Released 2026-09-19 |
| 0.24.0 | **The manager is not trusted** (security release, 2026-09-28; GHSA-vj2g-mxcr-wx5r, GHSA-4m4w-x5r5-hmvh, GHSA-rwhg-fxhq-m6pg, GHSA-v9pc-vw88-p798, GHSA-qj62-wg85-79cv, GHSA-mqpf-x42g-g28w, GHSA-88r5-5vxg-53px, GHSA-cvj4-75mr-7858, GHSA-qx58-m2pf-8388, GHSA-hrm5-cvxj-cc7w). Backup uploads and media downloads go only to `manager.dartec.ae` over https, with redirects refused (uploads) or re-checked hop by hop (downloads); Dartec Link joins only `https://headscale.dartec.ae`, rebuilt from the allowlist; media size capped at 20 MB and backup size at 4096 MB whatever the command asks, enforced on the bytes; automation ids, add-on slugs and backup ids checked against strict patterns before they reach a path; `entity_id` targets split as Home Assistant splits them, each entry checked, and the call made with the checked list (it now reaches HA as a list); `scene.turn_on` and `automation.turn_on`/`turn_off` sensitive; only an administrator can grant consent (the service, the support switch, approved updates back on), anyone can withdraw it; household create/update/remove serialised so the last administrator cannot be removed by two at once; Link setup re-checks consent after installing. **`tunnel_setup` retired** (owner, 2026-09-27; Dartec Link replaces it): refused by name with `code: "retired"` before consent is read (`service_policy.RETIRED_ACTIONS`); `tunnel_status` and `tunnel_stop` remain. `trust.py`, `docs/trust-boundary.md`, `tests/test_untrusted_manager.py` (one regression per advisory) |
| 0.25.0 | Not yet released. **Switches and toggle helpers need consent** (owner's decision, 2026-09-28). `switch.turn_on`/`turn_off`/`toggle` and `input_boolean.turn_on`/`turn_off` move from routine to sensitive: a switch may be a door strike, gate or valve, and a toggle helper drives automations. Lights and fans stay routine. The home's own switches stay refused with or without consent (`check_own_entities`). **Dwains workarounds gated** (2026-10-02): Dwains fixed #18, #19 and #20 in v1.8.1, so `www/dashboard-fix.js` now hands the card editor its config back only in a picker Dwains put on `document.body`, which only releases before 1.8.1 do (0.24.0 did it on every release, a second `setConfig` per change on 1.8.1+); the picker move and the `home_custom_cards` carry were already inert there. Dwains exposes no version to the page, so the gate is that behaviour, not a number. `tests/test_dashboard_fix.py`; live matrix on the bench and in CI against v1.8.0 and v1.10.0, `docs/dwains-popup/`. **Formal Arabic state words** (owner's decision, kaboomAE/dartec-ops#15, 2026-10-06; #55): `arabic_states.py` corrects Home Assistant's own Arabic translation cache at setup, for lights, switches and sockets, toggle helpers, fans, covers, ACs (modes, current action, presets, fan and swing modes) and door, window, leak, gas, smoke, CO, motion, occupancy, safety and problem sensors: «مُشغَّل» / «مُطفأ» for On/Off, masculine except the window («مفتوحة» / «مغلقة»), and «نشاط» for the preset HA's Arabic calls «السجل». Only where the value is still HA's English (or that one mistranslation): HA's own Arabic always wins, a key HA lacks is never added, and unloading puts every value back. Why the backend cache and not the browser is in the module's docstring. **"C° 22.0" fixed** (#55): `www/dashboard-fix.js` wraps each number-and-unit in Unicode isolates (U+2068/U+2069, what HA's own `bidiIsolate()` does since frontend #54205) inside `state-display` and `ha-tile-info`'s secondary line, and lays out the stepper's value box left to right, only while `document.dir` is `rtl`, undone when it is not, and never where HA already isolated the text; each element is corrected from its own Lit `updated()`. `tests/test_arabic_states.py`, `tests/test_dashboard_fix.py` (Arabic and English), `arabic_states_check.py` in `live`, the unit measurements in `live-dashboards`. **HA 2026.10's device config entries** (2026-10-06): since 2026.8 a device belongs to exactly one config entry, `DeviceEntry.config_entry_id`, and `config_entries` is a shim that 2026.10 reports on every read (removed in 2027.10); `device_health` read it, so `live` failed on 2026.10.0b1. `registry_access.device_config_entries` reads `config_entry_id` where it exists and the real `config_entries` set before 2026.8. `tests/test_registry_access.py` (both shapes, and a sweep for `.config_entries`, `.config_entries_subentries` and `.primary_config_entry`); the `live` canary now reads the shim and must be reported from 2026.10. Needs a bench check on HA 2026.10 before release |

---

## 9. Open items

- **The real home runs 0.10.3**; 0.10.4 is released and not yet deployed there.
- **0.11.0 is committed but NOT released or tagged.** The manifest says
  0.11.0; until a GitHub release exists, Fleet maintenance would install an
  older asset over it — the exact trap 0.10.4 fell into above. Cut the
  release before triggering any update.
- **Roll 0.11.0 out before relying on the window.** `agent_update` is itself
  gated by the maintenance window, but homes still on 0.10.x have no gate,
  so this update installs without one. Every update *after* it needs the
  homeowner to open a window first.
- **`hacs.json` has no `homeassistant` floor above 2024.6.0** — revisit if a
  newer core API gets used.
- **The `brands` check is ignored in CI.** Listing in the HACS default store
  needs a merged PR to `home-assistant/brands`; custom-repository installs work
  without it.
- **Test coverage is thin on this side.** The HA-free modules are unit-tested,
  and `live` covers pairing, the snapshot's device and version data, and
  `registry_query` inside real Home Assistant. Every other command is covered
  only from the server repo's end-to-end suite, which needs both a manager and
  a Home Assistant running. `tests/live/stub_manager.py` is the place to add
  more: it can send any command and records the result.
- **`live` reads the log about a second after the second snapshot.** Anything
  the agent does later — a scheduled job, a command the stub does not send —
  is not covered by its log check.
