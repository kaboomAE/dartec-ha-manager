# Dartec HA Manager — Agent

The Home Assistant integration that links an HA instance to [Dartec HA Manager](https://manager.dartec.ae), Dartec's centralized fleet-management dashboard for smart homes.

## What it does

- Opens a single **outbound** encrypted WebSocket to the Dartec cloud — no port forwarding, no VPN, no exposed services.
- Sends a health snapshot every 60 seconds: HA version, integrations and their states, add-ons (HA OS), HACS repositories, automations, dashboards, critical logs, and host metrics.
- Executes a small **allowlisted** set of remote commands (add-on start/stop/restart, service calls). The allowlist is enforced inside the agent, so the cloud can never run arbitrary code in your home.
- Admin credentials never leave the home. The cloud stores only a per-home pairing token.

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
