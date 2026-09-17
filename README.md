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
  - **Sensitive** — locks, covers, alarm, climate, reboots, deleting backups and
    account changes need your consent, given on your own Home Assistant. It can come
    from three places, and support can ask for it but never grant itself any:
    - **Commissioning.** Pairing opens it, because whoever paired the home was
      standing in it with its pairing token. It lasts until the install is
      marked complete — by calling `dartec_ha_manager.complete_commissioning`,
      by switching **Allow Dartec support** off, or from Dartec's manager — and
      never longer than **30 days** by default. It survives restarts. The
      manager can end it early; nothing remote can open, extend or restart it.
      On the home, the integration's options (*Configure*) can set a different
      length — up to 3650 days, e.g. for a test server — or start a new
      commissioning period now, which also reopens a home that is closed.
    - **The Allow Dartec support switch** (or
      `dartec_ha_manager.allow_maintenance`) opens a window that ends on its
      own and on every restart. The switch reads *on* while commissioning is
      open too, and its `commissioning_ends_at` attribute says until when.
    - **Unattended support**, an opt-in in this integration's options, off
      unless you turn it on.

    Once commissioning ends, access is closed until you switch it on again.
  - **Routine** — reversible commissioning and diagnostic calls, which need no
    window. Replacing the read-only GitHub token HACS uses (so Dartec can
    rotate it across every home) is routine too: it only ever changes the
    token in a HACS setup you already have, checks the new token with GitHub
    first, puts the old one back if HACS does not start with the new one, and
    is written to your logbook. It does not touch this integration's own
    connection to Dartec. The token itself is never reported back or logged.
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

*Settings → Devices & Services → Add Integration → "Dartec HA Manager"*, then enter the server URL and the enrolment code provided by your Dartec installer. A code works once and expires within the hour; the integration trades it for this home's long-lived pairing token, which is what it keeps. A pairing token pasted directly still works.

## Supported installations

All install types (HA OS, Supervised, Container, Core) on HA 2024.6 or newer. Add-on management and full host metrics require HA OS/Supervised; other install types degrade gracefully.
