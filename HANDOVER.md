# Dartec HA Manager Agent — Handover

**Written**: 2026-08-27 · **Repo**: `kaboomAE/dartec-ha-manager` (**public**)
**Current version**: 0.11.0

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
| `hardware.py` | Host metrics; includes the ARM64 CPU-model decoding |
| `lovelace_cmds.py` | Dashboard read/create/save |
| `registry_cmds.py` | Areas, floors, device and entity assignment |
| `user_cmds.py` | Home Assistant user accounts |
| `hacs_cmds.py` | HACS install/list, **including the downgrade guard** |
| `hacs_token.py` | The GitHub token in HACS's config entry: its fingerprint for the snapshot, and `hacs_token_set`. No HA imports |
| `home_cmds.py` | Themes, branding, agent self-update, HA restart |
| `backup_cmds.py` | Backup list/create/delete/schedule, and upload to Dartec storage |
| `tunnel_cmds.py` | Cloudflare tunnel setup on the home |
| `branding.py` | Installer branding in the sidebar and tab title, plus its config endpoint |
| `version.py` | Version comparison, and the agent's own running version (from HA's loader). No module-level HA imports, so CI can test it directly |
| `registry_access.py` | Enumerating the device registry in a way that works on both its pre- and post-2026.9 shapes. No HA imports |
| `www/` | Brand SVGs served as static assets |

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
  no `device_registry.devices` mapping deprecation, no blocking call on
  `manifest.json`, no other `Detected ...` report, no `ERROR` line naming it.
- **A HACS token swap** (added 2026-09-17, #8): the stub manager sends
  `hacs_token_set` to a stand-in HACS (`tests/live/hacs_stub/`) that registers
  HACS's reload-from-update-listener and writes to disk before it unloads. The
  run fails unless the swap answers `ok`, the entry is still `loaded`, the
  running setup has the new token, a snapshot reports it, the listener never
  fired, and the log shows no `failed_unload`, `OperationNotAllowed` or
  unretrieved task exception. The stub also replaces the agent's GitHub check,
  since the container must not depend on GitHub accepting a made-up token.
- **Batteries and offline devices** (added 2026-09-18, 0.18.0): `device_health_setup.py` takes demo devices down, labels one `dartec_expected_offline` and drains a battery to 4%, all through Home Assistant's own APIs. The run fails unless `offline_devices` is exactly the one fully-down device — not the labelled one, not the device with one dead entity out of two, not Push's never-pressed button, not the Backup service device — and `batteries` equals every registered battery-percentage sensor Home Assistant shows, lowest first, with the demo's `battery_charging` sensor left out.
- **A canary integration** (`tests/live/canary/`) deliberately does both
  wrong things, and the run fails unless Home Assistant reports the canary for
  them (the mapping one only from 2026.9). Without it a clean log could mean
  the warning's wording changed. It is never installed outside the container.

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
lovelace_get, lovelace_save, lovelace_create,
hacs_install, hacs_list, hacs_token_set, theme_set, automation_create, branding_set,
floor_upsert, floor_delete, area_upsert, area_delete,
devices_assign, entities_assign,
users_list, user_create, user_update, user_set_password, user_delete,
agent_update, ha_restart,
tunnel_status, tunnel_setup, tunnel_stop,
backup_list, backup_create, backup_delete, backup_schedule, backup_upload
```

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

**Dashboards**
- The live `DashboardsCollection` is a setup-local variable and cannot be reached
  from a command. Use the official `lovelace/dashboards/create` over the loopback
  websocket (0.2.1).

**HACS**
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
| 0.18.0 | The snapshot carries `batteries` (one row per device: level and/or the binary low flag, lowest first, capped at 200, `battery_count` the true total; phones' `mobile_app` batteries left out) and `offline_devices` (devices whose every judged entity is `unavailable`/`unknown`, with the time the last went down, capped at 100, plus `offline_count` and `devices_judged`), summarised by `device_health.py` so the manager can alert on low batteries and devices offline for hours without being sent every entity's state; a failed pass sends `device_health_error` rather than empty lists that would read as "all fine"; registry entities only. Unreleased |

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
