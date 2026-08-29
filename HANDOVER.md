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
| `config_flow.py` | The setup dialog — server URL + pairing token |
| `const.py` | `DOMAIN`, config keys, `SNAPSHOT_INTERVAL_S = 60`, reconnect backoff bounds |
| `cloud_link.py` | The outbound WebSocket: connect, auth, push a snapshot every 60 s, handle inbound commands, reconnect with backoff |
| `collector.py` | Builds the snapshot. Core version, integrations, add-ons, HACS, automations, dashboards, logs, host metrics, backups, areas, devices, entities |
| `commands.py` | Command dispatch — routes an inbound action to its handler |
| `ws_bridge.py` | Talks to HA's own websocket/REST over loopback with a short-lived self-minted token |
| `hardware.py` | Host metrics; includes the ARM64 CPU-model decoding |
| `lovelace_cmds.py` | Dashboard read/create/save |
| `registry_cmds.py` | Areas, floors, device and entity assignment |
| `user_cmds.py` | Home Assistant user accounts |
| `hacs_cmds.py` | HACS install/list, **including the downgrade guard** |
| `home_cmds.py` | Themes, branding, agent self-update, HA restart |
| `backup_cmds.py` | Backup list/create/delete/schedule, and upload to Dartec storage |
| `tunnel_cmds.py` | Cloudflare tunnel setup on the home |
| `branding.py` | Installer branding in the sidebar and tab title, plus its config endpoint |
| `version.py` | Version comparison. No HA imports, so CI can test it directly |
| `www/` | Brand SVGs served as static assets |

`tests/` holds the unit tests that need no Home Assistant — currently
`test_version.py`. Deeper behaviour is tested from the server repo's end-to-end
suite against a real Home Assistant.

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
5. Confirm CI is green (three jobs, below).
6. Roll out from the manager: Admin → Fleet maintenance.

**Version numbers are compared by value, not text.** `version.py` exists because
`"0.10.4" < "0.9.0"` as strings — every release past `.9` reads as a downgrade
under naive comparison. `tests/test_version.py` covers exactly that trap.

---

## 4. CI

`.github/workflows/validate.yml`, on every push and monthly on the 5th.

| Job | Checks |
|---|---|
| `hassfest` | Home Assistant's own manifest/structure validation against current dev |
| `hacs` | That the repo is HACS-installable (`brands` ignored — needs a merged PR to home-assistant/brands, only required for the default store) |
| `unit` | `pytest tests` — the HA-free modules |

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
hacs_install, hacs_list, theme_set, automation_create, branding_set,
floor_upsert, floor_delete, area_upsert, area_delete,
devices_assign, entities_assign,
users_list, user_create, user_update, user_set_password, user_delete,
agent_update, ha_restart,
tunnel_status, tunnel_setup, tunnel_stop,
backup_list, backup_create, backup_delete, backup_schedule, backup_upload
```

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
  and are cached (0.3.1).

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
- **Test coverage is thin on this side.** Only `version.py` is unit-tested
  directly; everything else is covered from the server repo's end-to-end suite,
  which needs both a manager and a Home Assistant running.
