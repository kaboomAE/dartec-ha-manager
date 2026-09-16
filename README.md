# Dartec HA Manager — Agent

The Home Assistant integration that links an HA instance to [Dartec HA Manager](https://manager.dartec.ae), Dartec's centralized fleet-management dashboard for smart homes.

> **Maintaining this integration?** See [HANDOVER.md](HANDOVER.md) — module
> map, the release process (HACS installs *Releases*, not tags), CI, and the
> gotchas that cost real debugging time.

## Code quality

[![Validate](https://github.com/kaboomAE/dartec-ha-manager/actions/workflows/validate.yml/badge.svg)](https://github.com/kaboomAE/dartec-ha-manager/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/kaboomAE/dartec-ha-manager?color=1a6b6b)](LICENSE)
![Commit activity](https://img.shields.io/github/commit-activity/y/kaboomAE/dartec-ha-manager?color=8f8368)
![Last commit](https://img.shields.io/github/last-commit/kaboomAE/dartec-ha-manager?color=8f8368)
![Version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FkaboomAE%2Fdartec-ha-manager%2Fmain%2Fcustom_components%2Fdartec_ha_manager%2Fmanifest.json&query=%24.version&label=version&color=1a6b6b)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5)](https://hacs.xyz)

The version badge reads `manifest.json` on `main`, so it shows what HACS would
install rather than a number somebody remembered to update in two places.

| | |
|---|---|
| Tests | 5 files, `python -m pytest tests` |
| CI | hassfest and HACS validation on every push |

## What it does

- Opens a single **outbound** encrypted WebSocket to the Dartec cloud — no port forwarding, no VPN, no exposed services.
- Sends a health snapshot every 60 seconds: HA version, integrations and their states, add-ons (HA OS), HACS repositories, automations, dashboards, critical logs, and host metrics.
- Executes a small **allowlisted** set of remote commands. The allowlist is
  enforced inside the agent, on the `domain.service` pair rather than on the
  command name, and the default is to refuse. Three tiers:
  - **Never** — `shell_command`, `python_script`, `hassio.addon_stdin`,
    `hassio.host_shutdown` and `homeassistant.stop` are permanently blocked and
    no permission you grant can unlock them.
  - **Sensitive** — locks, covers, alarm, climate, reboots, deleting backups
    and account changes need a *maintenance window* that only you can open, from inside
    Home Assistant, by calling `dartec_ha_manager.allow_maintenance`. It expires
    on its own and closes on every restart. Support can ask for one; it cannot
    grant itself one.
  - **Routine** — reversible commissioning and diagnostic calls, which need no
    window.
- **Offsite backup copies** send your configuration and history to Dartec's
  storage, so they only run if you turn on *Copy backups offsite to Dartec* in
  this integration's options. It is off by default, and you can turn it off
  there at any time. It needs no maintenance window, so copies can run on a
  schedule while nobody is home. The maintenance window does not stand in for
  this setting.
- Calls that would target the whole house at once (`entity_id: all`, or no
  target at all) are refused.
- Every command executed or refused is written to **your own logbook**, so the
  record of what Dartec did in your home lives in your system, not only ours.
- Admin credentials never leave the home, and no long-lived Home Assistant
  token is ever created: privileged work mints a 5-minute owner token and
  revokes it immediately afterwards. The cloud stores only the *hash* of a
  per-home pairing token, so a breach of our database does not yield a key
  to your house.

## Installation

### HACS (recommended)
1. HACS → Integrations → ⋮ → Custom repositories → add this repository (category: Integration).
2. Install **Dartec HA Manager**, restart Home Assistant.

### Manual
Copy `custom_components/dartec_ha_manager/` into your HA `config/custom_components/` directory and restart.

## Setup

*Settings → Devices & Services → Add Integration → "Dartec HA Manager"*, then enter the server URL and the pairing token provided by your Dartec installer.

## Supported installations

All install types (HA OS, Supervised, Container, Core) on HA 2024.6 or newer. Add-on management and full host metrics require HA OS/Supervised; other install types degrade gracefully.
