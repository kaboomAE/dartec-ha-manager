# Recommendations: ranked changes to Dartec's dashboards and room panels

Ranked by benefit to the family first, then by effort. **None of these is implemented.** Each has its own GitHub issue so the owner can approve them one at a time. Most of the code lives in the manager (`dartec-ha-manager-server`, private); R6 and R11 change this repository; R10 changes `dartec-theme`; R3, R4 and R9 need the planner. Effort: S = about a day, M = a few days, L = a week or more.

| # | Change | Benefit | Effort | Risk | Issue |
|---|---|---|---|---|---|
| R1 | [Room dashboards: climate first, with real AC controls on the tile](#r1) | High: the daily control becomes one tap, on every phone and panel | S (about a day with tests) | Low: built-in features since 2024–2026, filtered to what each unit supports | to be opened |
| R2 | [Show offline devices instead of dropping them, and list them under “Needs attention”](#r2) | High: failures are visible to the family and to support | S | Low: a device removed for good stays listed until it is removed from HA, which is itself worth seeing | to be opened |
| R3 | [Tile names people can read: drop the room from the name, use the plan's labels](#r3) | High: the single biggest legibility problem on the bench | M (planner field plus generator) | Low | to be opened |
| R4 | [Arabic-first dashboards: Arabic names, reviewed headings, one dashboard per language](#r4) | High for Arabic-speaking families | M | Low: both dashboards are generated from one model | to be opened |
| R5 | [A panel variant of the room dashboard, and night behaviour for bedroom panels](#r5) | High: this is what the family touches at night | M | Low–medium: the automations need the tablet's `notify.mobile_app_*` name, known only after the tablet registers | to be opened |
| R6 | [Set the language of panel accounts, and let My Home set a person's language](#r6) | Medium–high: Arabic panels without a technician touching the profile | S–M (with the live panel test extended) | Low | to be opened |
| R7 | [Rebuild the Dartec Home overview: climate, floors, safety](#r7) | High: the first screen answers “is the house comfortable, is anything wrong” | M | Low | to be opened |
| R8 | [Never truncate a room silently](#r8) | Medium | S | Low | to be opened |
| R9 | [Multi-channel devices: a channel count in the catalogue and a name per channel](#r9) | Medium | M–L (schema change across planner, apps and manager) | Low | to be opened |
| R10 | [Dartec theme: licence, a working font, and brand colours on the AC controls](#r10) | Medium: the brand shows on the control people use most; legal clarity | S | Low | to be opened |
| R11 | [Pin every frontend install, and stop offering Dwains Dashboard Next to new homes](#r11) | Medium–high: no untested frontend code reaches the fleet | M | Medium: the real home uses Dwains and must be migrated with care | to be opened |
| R12 | [Upstream: translate HA's states into Arabic and report the “C° 22.0” bug](#r12) | High for every Arabic-speaking HA user, at almost no cost | S–M | None | to be opened |
| R13 | [Render the generated dashboards in CI on every HA version, at panel sizes, in English and Arabic](#r13) | Medium: catches breakage before the fleet takes a release | M | None (CI only) | to be opened |
| R14 | [Dashboards for domestic staff, chosen per person in My Home](#r14) | Medium | M | Medium: families may read it as access control; the wording must be clear | to be opened |

## R1

**Room dashboards: climate first, with real AC controls on the tile**

- **Where:** Manager: `server/app/room_dashboards.py` (`GROUPS` order) and `blueprint._tile` (climate features)
- **Problem:** Today a room's sections start with lights, and the AC tile has only a temperature stepper. The room's temperature and humidity appear as two extra tiles beside it. In a UAE villa the AC is the control used most, and on a phone the first section is what people see first.
- **Change:** Put the climate section first. Give the AC tile `climate-hvac-modes` (cool, dry, fan_only, off; HA shows only the modes the unit supports), `target-temperature`, and `climate-preset-modes` when the unit has presets, with `state_content: [state, current_temperature]`. Show the room's temperature and humidity as entity badges on the climate heading instead of separate tiles. Consider `style: dropdown` for presets, because icon-only preset buttons did not explain themselves.
- **Evidence:** [layout.md#what-the-manager-generates-today](layout.md#what-the-manager-generates-today)
- **Benefit:** High: the daily control becomes one tap, on every phone and panel. **Effort:** S (about a day with tests). **Risk:** Low: built-in features since 2024–2026, filtered to what each unit supports.
- **Issue:** to be opened

## R2

**Show offline devices instead of dropping them, and list them under “Needs attention”**

- **Where:** Manager: `blueprint._usable` drops `state == unavailable`; overview blueprint
- **Problem:** A device that is unavailable when the dashboard is published is left out of it. On the bench the kids' room lost its light and air-quality sensor without a trace, until the next publish after it came back. A family sees a room with a light missing and no reason; support cannot tell broken from never-installed.
- **Change:** Keep unavailable entities on room dashboards (the tile shows “Unavailable” with HA's warning mark). Add an `entity-filter` card titled “Needs attention” (state `unavailable`, `show_empty: false`) to the home overview. Keep leaving out devices labelled `Dartec expected offline`.
- **Evidence:** [layout.md#crowded-rooms-offline-devices-and-odd-names](layout.md#crowded-rooms-offline-devices-and-odd-names)
- **Benefit:** High: failures are visible to the family and to support. **Effort:** S. **Risk:** Low: a device removed for good stays listed until it is removed from HA, which is itself worth seeing.
- **Issue:** to be opened

## R3

**Tile names people can read: drop the room from the name, use the plan's labels**

- **Where:** Manager (tile `name`), planner (a short per-device label), onboarding apps (label at commissioning)
- **Problem:** Tiles show HA's full names, so every tile repeats the room (“Majlis chandeli…”), relay channels read “SONOFF 4CHP…” four times, and in the crowded suite 24 tiles all read “East guest suit…”.
- **Change:** Write a `name` on every generated tile: the plan's short label when there is one; otherwise the entity name with the room's name removed from the front, or `name: {type: entity}` for entities that belong to a device. Never rename the entity in HA itself.
- **Evidence:** [layout.md#crowded-rooms-offline-devices-and-odd-names](layout.md#crowded-rooms-offline-devices-and-odd-names)
- **Benefit:** High: the single biggest legibility problem on the bench. **Effort:** M (planner field plus generator). **Risk:** Low.
- **Issue:** to be opened

## R4

**Arabic-first dashboards: Arabic names, reviewed headings, one dashboard per language**

- **Where:** Planner (Arabic and English room and device names), manager (generate per language), native review of `HEADINGS["ar"]`
- **Problem:** HA mirrors its interface for Arabic users but never translates names. Today's generated dashboards use English device names, which in an Arabic layout are cut at their start (“…jlis wall washers”). The Arabic section headings are marked unreviewed.
- **Change:** Generate each dashboard in the household's language, or both where the household is mixed, taking room and tile names from the plan in that language. Have a native reader review the Arabic headings. When creating HA areas from Arabic room names, create them with an ASCII name first and then rename them, so ids stay readable (HA transliterates Arabic into ids like `ltbq_lrdy`).
- **Evidence:** [arabic-rtl.md](arabic-rtl.md)
- **Benefit:** High for Arabic-speaking families. **Effort:** M. **Risk:** Low: both dashboards are generated from one model.
- **Issue:** to be opened

## R5

**A panel variant of the room dashboard, and night behaviour for bedroom panels**

- **Where:** Manager (compile a panel view for a room with a panel); agent `automation_create` for the night automations
- **Problem:** A room panel shows the same dashboard as the phone. On the bench the phone layout on a 1280 × 800 panel scrolls, puts no single “good night” action within reach, and on the NSPanel's 480 × 480 the useful part is below the fold. Nothing dims a bedroom panel at night.
- **Change:** When a room has a panel, publish a panel view: heading with temperature and humidity; clock and one big `button` card for “Good night” (`scene.apply`: lights off, blackout closed, AC to Sleep) side by side; the AC as a `thermostat` dial; lights and curtains. Use `visibility` (`screen`, min-width 600px) to hide the clock and dial on small screens and show a compact AC tile instead. Generate night automations per bedroom panel with the Companion app's notification commands (brightness, screen timeout, return to the room), driven by the tablet's light and proximity sensors.
- **Evidence:** [panels.md](panels.md)
- **Benefit:** High: this is what the family touches at night. **Effort:** M. **Risk:** Low–medium: the automations need the tablet's `notify.mobile_app_*` name, known only after the tablet registers.
- **Issue:** to be opened

## R6

**Set the language of panel accounts, and let My Home set a person's language**

- **Where:** Agent (this repo): `panel_setup` / `panel_update` and `household_ws.py`
- **Problem:** A panel account is created with no language, so it follows the browser; My Home shows its own screens in Arabic but cannot set a person's HA language. Arabic is stored per account on the server (`frontend` user data, key `language`), which the bench confirmed.
- **Change:** Accept an optional `language` (`en` / `ar`) in `panel_setup` and `panel_update` and write it to the account's own user data, as the agent already does for the first dashboard. Add the same to My Home's add and edit person. Leave the panel's theme unset so it follows the system default.
- **Evidence:** [panels.md#arabic-on-a-panel](panels.md#arabic-on-a-panel)
- **Benefit:** Medium–high: Arabic panels without a technician touching the profile. **Effort:** S–M (with the live panel test extended). **Risk:** Low.
- **Issue:** to be opened

## R7

**Rebuild the Dartec Home overview: climate, floors, safety**

- **Where:** Manager: `server/app/blueprint.py` (Home and Rooms views); “apply plan structure” (area temperature and humidity sensors)
- **Problem:** The whole-home blueprint lists rooms as sections of tiles and has no single place for what matters every day (the ACs) or for what is wrong.
- **Change:** Overview in this order: Climate (one tile per AC named after its room, target temperature, “All ACs off” with a confirmation); one section per floor with a compact `area` card per room that opens the room's subview; Safety (leak, smoke, gas) plus “Needs attention”. When applying the plan's structure, also set each area's temperature and humidity sensor so area cards and HA's own Overview show the right figure.
- **Evidence:** [layout.md#1-home-overview-one-per-household-language](layout.md#1-home-overview-one-per-household-language)
- **Benefit:** High: the first screen answers “is the house comfortable, is anything wrong”. **Effort:** M. **Risk:** Low.
- **Issue:** to be opened

## R8

**Never truncate a room silently**

- **Where:** Manager: `room_dashboards.py` (`max_per_section`, default 24, `items[:limit]`)
- **Problem:** A section is cut at 24 tiles with no sign. The 26-circuit guest suite lost two circuits on the bench.
- **Change:** Show every entity, or when a cap is needed, end the section with a tile saying how many more there are that opens a subview holding the rest.
- **Evidence:** [layout.md#crowded-rooms-offline-devices-and-odd-names](layout.md#crowded-rooms-offline-devices-and-odd-names)
- **Benefit:** Medium. **Effort:** S. **Risk:** Low.
- **Issue:** to be opened

## R9

**Multi-channel devices: a channel count in the catalogue and a name per channel**

- **Where:** Planner catalogue (a channel or gang field), onboarding apps (name each channel), manager (use the names)
- **Problem:** A 4-channel relay or a 2-gang switch is one catalogue line and one device, but several independent circuits in HA (`Switch 1…4`, `Left/Right`). Nothing records which circuit each channel drives, so the dashboard cannot name them, and counts of controllable points undercount.
- **Change:** Add a channel count to catalogue devices, record a label per channel in the plan or at commissioning, and have the generator use it as each channel tile's name.
- **Evidence:** [layout.md#multi-channel-devices](layout.md#multi-channel-devices)
- **Benefit:** Medium. **Effort:** M–L (schema change across planner, apps and manager). **Risk:** Low.
- **Issue:** to be opened

## R10

**Dartec theme: licence, a working font, and brand colours on the AC controls**

- **Where:** `kaboomAE/dartec-theme` (a v1.2.0 release)
- **Problem:** The theme passes WCAG AA in both modes, but: it has no LICENSE file; its font is set through `primary-font-family` / `paper-font-*`, which current HA no longer reads, and IBM Plex Sans has no Arabic; it leaves the AC's mode colours at HA blue (3.07:1 on its cream card); `hacs.json` states no minimum HA version.
- **Change:** Add a LICENSE. Set `ha-font-family-body` (and `-heading`) to `"IBM Plex Sans", "IBM Plex Sans Arabic", Roboto, sans-serif`, knowing the fonts only render where they are loaded (see R11/arabic-rtl.md). Set the documented `state-climate-cool-color`, `state-climate-dry-color`, `state-climate-fan_only-color` and `state-light-on-color` from the brand palette, checked at ≥ 3:1 in both modes. Add `homeassistant` to `hacs.json`.
- **Evidence:** [themes.md](themes.md)
- **Benefit:** Medium: the brand shows on the control people use most; legal clarity. **Effort:** S. **Risk:** Low.
- **Issue:** to be opened

## R11

**Pin every frontend install, and stop offering Dwains Dashboard Next to new homes**

- **Where:** Agent (this repo): `hacs_install` gains an exact `version`; manager: catalogue versions and approval
- **Problem:** `hacs_install` always downloads HACS's latest release, and the dashboard catalogue installs Dwains with `only_if_missing: false`, so every rollout takes whatever was released last. Dwains has already cost three agent releases of workarounds for upstream bugs (dwains-dashboard-next issues 18, 19 and 20).
- **Change:** Let `hacs_install` take an exact `version` (HACS's `hacs/repository/download` accepts one; the bench used it for every theme). Give each catalogue entry an approved version. Require the owner's approval, recorded in the catalogue, for anything that runs JavaScript. Stop offering Dwains to new homes in favour of the built-in dashboards above; keep it working on the home that has it until the family is moved.
- **Evidence:** [maintenance.md](maintenance.md)
- **Benefit:** Medium–high: no untested frontend code reaches the fleet. **Effort:** M. **Risk:** Medium: the real home uses Dwains and must be migrated with care.
- **Issue:** to be opened

## R12

**Upstream: translate HA's states into Arabic and report the “C° 22.0” bug**

- **Where:** Home Assistant (Lokalise translations; home-assistant/frontend issue)
- **Problem:** HA's Arabic translation leaves the core states in English: `frontend/get_translations` for Arabic returns “On”, “Off”, “Cool”, “Open” for light, switch, climate and cover. Temperatures shown on their own render as “C° 22.0” in right-to-left layouts, including on HA's own Overview.
- **Change:** Contribute Arabic translations for the entity-component states through HA's translation platform, and open a frontend issue for the unit placement with our screenshots. Track both here.
- **Evidence:** [arabic-rtl.md#what-breaks](arabic-rtl.md#what-breaks)
- **Benefit:** High for every Arabic-speaking HA user, at almost no cost. **Effort:** S–M. **Risk:** None.
- **Issue:** to be opened

## R13

**Render the generated dashboards in CI on every HA version, at panel sizes, in English and Arabic**

- **Where:** This repo: `tests/live/` (the Dwains runner already uses Playwright)
- **Problem:** Nothing checks that a generated dashboard still renders after an HA release, or that every entity it names exists; a missing entity is a silent blank card.
- **Change:** A live runner that builds a small test villa from helpers (as `docs/dashboards/prototypes/bench-log.jsonl` did), saves the prototypes, fails on any card error or missing entity, and keeps phone, tablet, wall and NSPanel screenshots in both languages as artifacts for review.
- **Evidence:** [maintenance.md#a-per-release-routine-15-minutes-on-the-bench](maintenance.md#a-per-release-routine-15-minutes-on-the-bench)
- **Benefit:** Medium: catches breakage before the fleet takes a release. **Effort:** M. **Risk:** None (CI only).
- **Issue:** to be opened

## R14

**Dashboards for domestic staff, chosen per person in My Home**

- **Where:** Manager (a staff dashboard from the plan: common areas plus staff rooms); agent My Home (offer it as a first dashboard)
- **Problem:** Villas have staff who use some rooms. Today everyone lands on the same dashboards unless a family member changes it, and the family overview includes every room.
- **Change:** Generate a staff dashboard (kitchen, laundry, maid's and driver's rooms, outdoor areas; no cameras, locks or bedrooms) and offer it in My Home's first-dashboard choice. Say plainly, as My Home does, that this is a convenience and not a security boundary.
- **Evidence:** [README.md#what-is-different-about-a-uae-villa](README.md#what-is-different-about-a-uae-villa)
- **Benefit:** Medium. **Effort:** M. **Risk:** Medium: families may read it as access control; the wording must be clear.
- **Issue:** to be opened

## What is deliberately not recommended

- **Custom card frameworks** (Mushroom, Bubble Card, button-card) and **card-mod**: built-in cards now cover every room control, and these run third-party JavaScript in the homeowner's browser. See [maintenance.md](maintenance.md).
- **HACS kiosk-mode** for panels: the panels work without it, and it is more code to keep working.
- **A different third-party theme as the fleet default**: the Dartec theme already passes; fix it (R10).
- **Lateef as the dashboard's Arabic font**: too small at interface sizes (see [arabic-rtl.md](arabic-rtl.md#fonts)).
