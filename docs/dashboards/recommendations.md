# Recommendations: ranked changes to Dartec's dashboards and room panels

Ranked by benefit to the family first, then by effort. Each section says whether it has been implemented. Each has its own GitHub issue so the owner can approve them one at a time. Most of the code lives in the manager (`dartec-ha-manager-server`, private); R6 and R11 change this repository; R10 changes `dartec-theme`; R3, R4 and R9 need the planner. Effort: S = about a day, M = a few days, L = a week or more.

| # | Change | Benefit | Effort | Risk | Issue |
|---|---|---|---|---|---|
| R1 | [Room dashboards: climate first, with real AC controls on the tile](#r1) | High: the daily control becomes one tap, on every phone and panel | S (about a day with tests) | Low: built-in features since 2024–2026, filtered to what each unit supports | [#44](https://github.com/kaboomAE/dartec-ha-manager/issues/44) |
| R2 | [Show offline devices instead of dropping them, and list them under “Needs attention”](#r2) | High: failures are visible to the family and to support | S | Low: a device removed for good stays listed until it is removed from HA, which is itself worth seeing | [#45](https://github.com/kaboomAE/dartec-ha-manager/issues/45) |
| R3 | [Tile names people can read: drop the room from the name, use the plan's labels](#r3) | High: the single biggest legibility problem on the bench | M (planner field plus generator) | Low | [#46](https://github.com/kaboomAE/dartec-ha-manager/issues/46) |
| R4 | [Arabic-first dashboards: Arabic names, reviewed headings, one dashboard per language](#r4) | High for Arabic-speaking families | M | Low: both dashboards are generated from one model | [#47](https://github.com/kaboomAE/dartec-ha-manager/issues/47) |
| R5 | [A panel variant of the room dashboard, and night behaviour for bedroom panels](#r5) | High: this is what the family touches at night | M | Low–medium: the automations need the tablet's `notify.mobile_app_*` name, known only after the tablet registers | [#48](https://github.com/kaboomAE/dartec-ha-manager/issues/48) |
| R6 | [Set the language of panel accounts, and let My Home set a person's language](#r6) | Medium–high: Arabic panels without a technician touching the profile | S–M (with the live panel test extended) | Low | [#49](https://github.com/kaboomAE/dartec-ha-manager/issues/49) |
| R7 | [Rebuild the Dartec Home overview: climate, floors, safety](#r7) | High: the first screen answers “is the house comfortable, is anything wrong” | M | Low | [#50](https://github.com/kaboomAE/dartec-ha-manager/issues/50) |
| R8 | [Never truncate a room silently](#r8) | Medium | S | Low | [#51](https://github.com/kaboomAE/dartec-ha-manager/issues/51) |
| R9 | [Multi-channel devices: a channel count in the catalogue and a name per channel](#r9) | Medium | M–L (schema change across planner, apps and manager) | Low | [#52](https://github.com/kaboomAE/dartec-ha-manager/issues/52) |
| R10 | [Dartec theme: licence, a working font, and brand colours on the AC controls](#r10) | Medium: the brand shows on the control people use most; legal clarity | S | Low | [#53](https://github.com/kaboomAE/dartec-ha-manager/issues/53) |
| R11 | [Pin every frontend install, and stop offering Dwains Dashboard Next to new homes](#r11) | Medium–high: no untested frontend code reaches the fleet | M | Medium: the real home uses Dwains and must be migrated with care | [#54](https://github.com/kaboomAE/dartec-ha-manager/issues/54) |
| R12 | [Upstream: translate HA's states into Arabic and report the “C° 22.0” bug](#r12) | High for every Arabic-speaking HA user, at almost no cost | S–M | None | [#55](https://github.com/kaboomAE/dartec-ha-manager/issues/55) |
| R13 | [Render the generated dashboards in CI on every HA version, at panel sizes, in English and Arabic](#r13) | Medium: catches breakage before the fleet takes a release | M | None (CI only) | [#56](https://github.com/kaboomAE/dartec-ha-manager/issues/56) |
| R14 | [Dashboards for domestic staff, chosen per person in My Home](#r14) | Medium | M | Medium: families may read it as access control; the wording must be clear | [#57](https://github.com/kaboomAE/dartec-ha-manager/issues/57) |
| R15 | [Serve Dartec's brand fonts from the agent: Lateef for Arabic, Dubai for Latin, Plex Mono](#r15) | Medium: the brand's typography in every home, and correctly sized Arabic | S–M | Low–medium: an unsupported HA variable (`--ha-font-family-body`) to re-check each release; Dubai's licence | [#59](https://github.com/kaboomAE/dartec-ha-manager/issues/59) |
| R16 | [Never drop devices that are in no room: show them, count them, ask for them to be placed](#r16) | High on any house that grew over time | S–M | Low | [#61](https://github.com/kaboomAE/dartec-ha-manager/issues/61) |
| R17 | [Collapse light-strip segments, effect toggles and duplicate power switches](#r17) | High: removes most of the noise on houses with LED strips | S | Low: name patterns, so keep a report of what was collapsed | [#62](https://github.com/kaboomAE/dartec-ha-manager/issues/62) |
| R18 | [Keep machine controls (heaters, programs, dispensers) off family dashboards](#r18) | High: a safety rule | S | Low | [#63](https://github.com/kaboomAE/dartec-ha-manager/issues/63) |

## R1

**Room dashboards: climate first, with real AC controls on the tile**

- **Where:** Manager: `server/app/room_dashboards.py` (`GROUPS` order) and `blueprint._tile` (climate features)
- **Problem:** Today a room's sections start with lights, and the AC tile has only a temperature stepper. The room's temperature and humidity appear as two extra tiles beside it. In a UAE villa the AC is the control used most, and on a phone the first section is what people see first.
- **Change:** Put the climate section first. Give the AC tile `climate-hvac-modes` (cool, dry, fan_only, off; HA shows only the modes the unit supports), `target-temperature`, and `climate-preset-modes` when the unit has presets, with `state_content: [state, current_temperature]`. Show the room's temperature and humidity as entity badges on the climate heading instead of separate tiles. Consider `style: dropdown` for presets, because icon-only preset buttons did not explain themselves.
- **Evidence:** [layout.md#what-the-manager-generates-today](layout.md#what-the-manager-generates-today)
- **Benefit:** High: the daily control becomes one tap, on every phone and panel. **Effort:** S (about a day with tests). **Risk:** Low: built-in features since 2024–2026, filtered to what each unit supports.
- **Issue:** [#44](https://github.com/kaboomAE/dartec-ha-manager/issues/44)

## R2

**Show offline devices instead of dropping them, and list them under “Needs attention”**

- **Where:** Manager: `blueprint._usable` drops `state == unavailable`; overview blueprint
- **Problem:** A device that is unavailable when the dashboard is published is left out of it. On the bench the kids' room lost its light and air-quality sensor without a trace, until the next publish after it came back. A family sees a room with a light missing and no reason; support cannot tell broken from never-installed.
- **Change:** Keep unavailable entities on room dashboards (the tile shows “Unavailable” with HA's warning mark). Add an `entity-filter` card titled “Needs attention” (state `unavailable`, `show_empty: false`) to the home overview. Keep leaving out devices labelled `Dartec expected offline`.
- **Evidence:** [layout.md#crowded-rooms-offline-devices-and-odd-names](layout.md#crowded-rooms-offline-devices-and-odd-names)
- **Benefit:** High: failures are visible to the family and to support. **Effort:** S. **Risk:** Low: a device removed for good stays listed until it is removed from HA, which is itself worth seeing.
- **Issue:** [#45](https://github.com/kaboomAE/dartec-ha-manager/issues/45)

## R3

**Tile names people can read: drop the room from the name, use the plan's labels**

- **Where:** Manager (tile `name`), planner (a short per-device label), onboarding apps (label at commissioning)
- **Problem:** Tiles show HA's full names, so every tile repeats the room (“Majlis chandeli…”), relay channels read “SONOFF 4CHP…” four times, and in the crowded suite 24 tiles all read “East guest suit…”.
- **Change:** Write a `name` on every generated tile: the plan's short label when there is one; otherwise the entity name with the room's name removed from the front, or `name: {type: entity}` for entities that belong to a device. Never rename the entity in HA itself.
- **Evidence:** [layout.md#crowded-rooms-offline-devices-and-odd-names](layout.md#crowded-rooms-offline-devices-and-odd-names)
- **Benefit:** High: the single biggest legibility problem on the bench. **Effort:** M (planner field plus generator). **Risk:** Low.
- **Issue:** [#46](https://github.com/kaboomAE/dartec-ha-manager/issues/46)

## R4

**Arabic-first dashboards: Arabic names, reviewed headings, one dashboard per language**

- **Where:** Planner (Arabic and English room and device names), manager (generate per language), native review of `HEADINGS["ar"]`
- **Problem:** HA mirrors its interface for Arabic users but never translates names. Today's generated dashboards use English device names, which in an Arabic layout are cut at their start (“…jlis wall washers”). The Arabic section headings are marked unreviewed.
- **Change:** Generate each dashboard in the household's language, or both where the household is mixed, taking room and tile names from the plan in that language. Have a native reader review the Arabic headings. When creating HA areas from Arabic room names, create them with an ASCII name first and then rename them, so ids stay readable (HA transliterates Arabic into ids like `ltbq_lrdy`).
- **Evidence:** [arabic-rtl.md](arabic-rtl.md)
- **Benefit:** High for Arabic-speaking families. **Effort:** M. **Risk:** Low: both dashboards are generated from one model.
- **Issue:** [#47](https://github.com/kaboomAE/dartec-ha-manager/issues/47)

## R5

**A panel variant of the room dashboard, and night behaviour for bedroom panels**

- **Where:** Manager (compile a panel view for a room with a panel); agent `automation_create` for the night automations
- **Problem:** A room panel shows the same dashboard as the phone. On the bench the phone layout on a 1280 × 800 panel scrolls, puts no single “good night” action within reach, and on the NSPanel's 480 × 480 the useful part is below the fold. Nothing dims a bedroom panel at night.
- **Change:** When a room has a panel, publish a panel view: heading with temperature and humidity; clock and one big `button` card for “Good night” (`scene.apply`: lights off, blackout closed, AC to Sleep) side by side; the AC as a `thermostat` dial; lights and curtains. Use `visibility` (`screen`, min-width 600px) to hide the clock and dial on small screens and show a compact AC tile instead. Generate night automations per bedroom panel with the Companion app's notification commands (brightness, screen timeout, return to the room), driven by the tablet's light and proximity sensors.
- **Evidence:** [panels.md](panels.md)
- **Benefit:** High: this is what the family touches at night. **Effort:** M. **Risk:** Low–medium: the automations need the tablet's `notify.mobile_app_*` name, known only after the tablet registers.
- **Issue:** [#48](https://github.com/kaboomAE/dartec-ha-manager/issues/48)

## R6

**Set the language of panel accounts, and let My Home set a person's language**

- **Where:** Agent (this repo): `panel_setup` / `panel_update` and `household_ws.py`
- **Problem:** A panel account is created with no language, so it follows the browser; My Home shows its own screens in Arabic but cannot set a person's HA language. Arabic is stored per account on the server (`frontend` user data, key `language`), which the bench confirmed.
- **Change:** Accept an optional `language` (`en` / `ar`) in `panel_setup` and `panel_update` and write it to the account's own user data, as the agent already does for the first dashboard. Add the same to My Home's add and edit person. Leave the panel's theme unset so it follows the system default.
- **Evidence:** [panels.md#arabic-on-a-panel](panels.md#arabic-on-a-panel)
- **Benefit:** Medium–high: Arabic panels without a technician touching the profile. **Effort:** S–M (with the live panel test extended). **Risk:** Low.
- **Issue:** [#49](https://github.com/kaboomAE/dartec-ha-manager/issues/49)
- **Status (2026-09-26):** implemented in the agent, not yet released. `panel_setup` and `panel_update` take `language` and `theme` (the owner's update of 2026-09-25: a panel may have its own theme, such as Dartec Glass Lite), and My Home can set anyone's language, their own included. The manager has to send them: it is a new optional field on its panel set-up.

## R7

**Rebuild the Dartec Home overview: climate, floors, safety**

- **Where:** Manager: `server/app/blueprint.py` (Home and Rooms views); “apply plan structure” (area temperature and humidity sensors)
- **Problem:** The whole-home blueprint lists rooms as sections of tiles and has no single place for what matters every day (the ACs) or for what is wrong.
- **Change:** Overview in this order: Climate (one tile per AC named after its room, target temperature, “All ACs off” with a confirmation); one section per floor with a compact `area` card per room that opens the room's subview; Safety (leak, smoke, gas) plus “Needs attention”. When applying the plan's structure, also set each area's temperature and humidity sensor so area cards and HA's own Overview show the right figure.
- **Evidence:** [layout.md#1-home-overview-one-per-household-language](layout.md#1-home-overview-one-per-household-language)
- **Benefit:** High: the first screen answers “is the house comfortable, is anything wrong”. **Effort:** M. **Risk:** Low.
- **Issue:** [#50](https://github.com/kaboomAE/dartec-ha-manager/issues/50)

## R8

**Never truncate a room silently**

- **Where:** Manager: `room_dashboards.py` (`max_per_section`, default 24, `items[:limit]`)
- **Problem:** A section is cut at 24 tiles with no sign. The 26-circuit guest suite lost two circuits on the bench.
- **Change:** Show every entity, or when a cap is needed, end the section with a tile saying how many more there are that opens a subview holding the rest.
- **Evidence:** [layout.md#crowded-rooms-offline-devices-and-odd-names](layout.md#crowded-rooms-offline-devices-and-odd-names)
- **Benefit:** Medium. **Effort:** S. **Risk:** Low.
- **Issue:** [#51](https://github.com/kaboomAE/dartec-ha-manager/issues/51)

## R9

**Multi-channel devices: a channel count in the catalogue and a name per channel**

- **Where:** Planner catalogue (a channel or gang field), onboarding apps (name each channel), manager (use the names)
- **Problem:** A 4-channel relay or a 2-gang switch is one catalogue line and one device, but several independent circuits in HA (`Switch 1…4`, `Left/Right`). Nothing records which circuit each channel drives, so the dashboard cannot name them, and counts of controllable points undercount.
- **Change:** Add a channel count to catalogue devices, record a label per channel in the plan or at commissioning, and have the generator use it as each channel tile's name.
- **Evidence:** [layout.md#multi-channel-devices](layout.md#multi-channel-devices)
- **Benefit:** Medium. **Effort:** M–L (schema change across planner, apps and manager). **Risk:** Low.
- **Issue:** [#52](https://github.com/kaboomAE/dartec-ha-manager/issues/52)

## R10

**Dartec theme: licence, a working font, and brand colours on the AC controls**

- **Where:** `kaboomAE/dartec-theme` (a v1.2.0 release)
- **Problem:** The theme passes WCAG AA in both modes, but: it has no LICENSE file; its font is set through `primary-font-family` / `paper-font-*`, which current HA no longer reads, and IBM Plex Sans has no Arabic; it leaves the AC's mode colours at HA blue (3.07:1 on its cream card); `hacs.json` states no minimum HA version.
- **Change:** Add a LICENSE. Set `ha-font-family-body` (and `-heading`) to `"IBM Plex Sans", "IBM Plex Sans Arabic", Roboto, sans-serif`, knowing the fonts only render where they are loaded (see R11/arabic-rtl.md). Set the documented `state-climate-cool-color`, `state-climate-dry-color`, `state-climate-fan_only-color` and `state-light-on-color` from the brand palette, checked at ≥ 3:1 in both modes. Add `homeassistant` to `hacs.json`.
- **Evidence:** [themes.md](themes.md)
- **Benefit:** Medium: the brand shows on the control people use most; legal clarity. **Effort:** S. **Risk:** Low.
- **Issue:** [#53](https://github.com/kaboomAE/dartec-ha-manager/issues/53)

## R11

**Pin every frontend install, and stop offering Dwains Dashboard Next to new homes**

- **Where:** Agent (this repo): `hacs_install` gains an exact `version`; manager: catalogue versions and approval
- **Problem:** `hacs_install` always downloads HACS's latest release, and the dashboard catalogue installs Dwains with `only_if_missing: false`, so every rollout takes whatever was released last. Dwains has already cost three agent releases of workarounds for upstream bugs (dwains-dashboard-next issues 18, 19 and 20).
- **Change:** Let `hacs_install` take an exact `version` (HACS's `hacs/repository/download` accepts one; the bench used it for every theme). Give each catalogue entry an approved version. Require the owner's approval, recorded in the catalogue, for anything that runs JavaScript. Stop offering Dwains to new homes in favour of the built-in dashboards above; keep it working on the home that has it until the family is moved.
- **Evidence:** [maintenance.md](maintenance.md)
- **Benefit:** Medium–high: no untested frontend code reaches the fleet. **Effort:** M. **Risk:** Medium: the real home uses Dwains and must be migrated with care.
- **Issue:** [#54](https://github.com/kaboomAE/dartec-ha-manager/issues/54)
- **Status (2026-09-26):** agent side implemented, not yet released: `hacs_install` requires an exact `version` and refuses one without (`version_required`), with `agent_update`'s own latest release as the one owner-approved exception. The manager side (catalogue versions, owner approval, Dwains withdrawn for new homes) is [dartec-ha-manager-server#57](https://github.com/kaboomAE/dartec-ha-manager-server/issues/57). No live home is changed by either until the owner approves.

## R12

**Upstream: translate HA's states into Arabic and report the “C° 22.0” bug**

- **Where:** Home Assistant (Lokalise translations; home-assistant/frontend issue)
- **Problem:** HA's Arabic translation leaves the core states in English: `frontend/get_translations` for Arabic returns “On”, “Off”, “Cool”, “Open” for light, switch, climate and cover. Temperatures shown on their own render as “C° 22.0” in right-to-left layouts, including on HA's own Overview.
- **Change:** Contribute Arabic translations for the entity-component states through HA's translation platform, and open a frontend issue for the unit placement with our screenshots. Track both here.
- **Evidence:** [arabic-rtl.md#what-breaks](arabic-rtl.md#what-breaks)
- **Benefit:** High for every Arabic-speaking HA user, at almost no cost. **Effort:** S–M. **Risk:** None.
- **Issue:** [#55](https://github.com/kaboomAE/dartec-ha-manager/issues/55)

## R13

**Render the generated dashboards in CI on every HA version, at panel sizes, in English and Arabic**

- **Where:** This repo: `tests/live/` (the Dwains runner already uses Playwright)
- **Problem:** Nothing checks that a generated dashboard still renders after an HA release, or that every entity it names exists; a missing entity is a silent blank card.
- **Change:** A live runner that builds a small test villa from helpers (as `docs/dashboards/prototypes/bench-log.jsonl` did), saves the prototypes, fails on any card error or missing entity, and keeps phone, tablet, wall and NSPanel screenshots in both languages as artifacts for review.
- **Evidence:** [maintenance.md#a-per-release-routine-15-minutes-on-the-bench](maintenance.md#a-per-release-routine-15-minutes-on-the-bench)
- **Benefit:** Medium: catches breakage before the fleet takes a release. **Effort:** M. **Risk:** None (CI only).
- **Issue:** [#56](https://github.com/kaboomAE/dartec-ha-manager/issues/56)

## R14

**Dashboards for domestic staff, chosen per person in My Home**

- **Where:** Manager (a staff dashboard from the plan: common areas plus staff rooms); agent My Home (offer it as a first dashboard)
- **Problem:** Villas have staff who use some rooms. Today everyone lands on the same dashboards unless a family member changes it, and the family overview includes every room.
- **Change:** Generate a staff dashboard (kitchen, laundry, maid's and driver's rooms, outdoor areas; no cameras, locks or bedrooms) and offer it in My Home's first-dashboard choice. Say plainly, as My Home does, that this is a convenience and not a security boundary.
- **Evidence:** [README.md#what-is-different-about-a-uae-villa](README.md#what-is-different-about-a-uae-villa)
- **Benefit:** Medium. **Effort:** M. **Risk:** Medium: families may read it as access control; the wording must be clear.
- **Issue:** [#57](https://github.com/kaboomAE/dartec-ha-manager/issues/57)

## R15

**Serve Dartec's brand fonts from the agent: Lateef for Arabic, Dubai for Latin, Plex Mono**

- **Where:** Agent (this repo): the static path it already serves (brand SVGs) and the module it already loads into every page (`www/dashboard-fix.js` via `add_extra_js_url`)
- **Problem:** A theme can name a font but cannot load one, so the Dartec theme's font has never rendered in any home. The brand's faces are Dubai (Latin) and Lateef (Arabic); on the bench, with the storefront's own `@font-face` rules (Lateef restricted to Arabic by `unicode-range` and set at `size-adjust: 150%`, Dubai at 113%), both rendered correctly in every Home Assistant component, right to left included.
- **Change:** Ship the brand's web font files (278 KB for all seven) with the agent, serve them from its static path, and add the `@font-face` rules to the page from the module it already loads, so the Dartec themes' `ha-font-family-*` variables resolve on every page. Lateef and IBM Plex Mono are OFL (include the licence text). **Dubai is under its own licence and the agent's repository is public: confirm redistribution is allowed first**, or serve Dubai from the manager, or keep a system Latin font in HA.
- **Evidence:** [dartec-variants/README.md#fonts](dartec-variants/README.md#fonts)
- **Benefit:** Medium: the brand's typography in every home, and correctly sized Arabic. **Effort:** S–M. **Risk:** Low–medium: an unsupported HA variable (`--ha-font-family-body`) to re-check each release; Dubai's licence.
- **Issue:** [#59](https://github.com/kaboomAE/dartec-ha-manager/issues/59)
- **Status (2026-09-26):** approved by the owner on condition that Dubai's licence allows it. It does not (TEC's EULA forbids redistribution), so the agent ships **Lateef and IBM Plex Mono only**, both unmodified with their OFL text; Latin text uses the theme's next face. See [dartec-variants/README.md#fonts](dartec-variants/README.md#fonts). Not yet released.

## R16

**Never drop devices that are in no room: show them, count them, ask for them to be placed**

- **Where:** Manager: `room_dashboards.build_rooms` (counts `unassigned` and drops them) and the Dartec Home overview
- **Problem:** On a real house 90 of 134 devices and 347 user-facing entities had no area. A room-first dashboard shows a fraction of the house and says nothing about the rest; the manager's generator counts them as `unassigned` and leaves them out.
- **Change:** Add a 'Not in a room yet (N)' view linked from the overview with its count, holding every unassigned device's main entity (media players with no room excepted: cast groups and personal laptops and phones). Report the count to the technician in the manager, with the manager's existing room suggestions (names often contain a room word) as a nudge to place them.
- **Evidence:** [real-home-test.md](real-home-test.md)
- **Benefit:** High on any house that grew over time. **Effort:** S–M. **Risk:** Low.
- **Issue:** [#61](https://github.com/kaboomAE/dartec-ha-manager/issues/61)

## R17

**Collapse light-strip segments, effect toggles and duplicate power switches**

- **Where:** Manager: entity selection for generated dashboards (`blueprint._usable` / `room_dashboards`)
- **Problem:** LED strips expose 15–28 'Segment NNN' light entities each plus effect toggles, and the same strip can be registered twice by two integrations (a light, and a 'Power Switch' on a different device). On a real house that was 116 segment lights, 15 effect toggles and 8 duplicate switches; one room had 45 light entities from 2 devices.
- **Change:** Treat a light named '<device> Segment N' as a segment and show only the strip's own light; leave effect toggles off; when a light 'X' exists, leave off a switch named 'X Power Switch' and report the duplicate to the technician.
- **Evidence:** [real-home-test.md](real-home-test.md)
- **Benefit:** High: removes most of the noise on houses with LED strips. **Effort:** S. **Risk:** Low: name patterns, so keep a report of what was collapsed.
- **Issue:** [#62](https://github.com/kaboomAE/dartec-ha-manager/issues/62)

## R18

**Keep machine controls (heaters, programs, dispensers) off family dashboards**

- **Where:** Manager: entity selection for generated dashboards
- **Problem:** 3D printers expose heater output pins as ordinary switches, a dishwasher exposes 15 program switches, and a pet appliance exposes dispensing controls. A generator that includes every switch puts a heater one tap away on a family dashboard.
- **Change:** For devices from machine integrations (3D printers, appliances with programs, feeders and the like), show at most one status or power tile and link to the device's own page; never generate tiles for heaters, programs or dispensers. Give a room whose devices are all machines a status tile so it does not vanish from the overview.
- **Evidence:** [real-home-test.md](real-home-test.md)
- **Benefit:** High: a safety rule. **Effort:** S. **Risk:** Low.
- **Issue:** [#63](https://github.com/kaboomAE/dartec-ha-manager/issues/63)

## What is deliberately not recommended

- **Custom card frameworks** (Mushroom, Bubble Card, button-card) and **card-mod**: built-in cards now cover every room control, and these run third-party JavaScript in the homeowner's browser. See [maintenance.md](maintenance.md).
- **HACS kiosk-mode** for panels: the panels work without it, and it is more code to keep working.
- **A different third-party theme as the fleet default**: the Dartec theme already passes; fix it (R10).
- **Lateef without the storefront's sizing**: unadjusted it is too small at interface sizes; with `size-adjust: 150%` it is right (see [arabic-rtl.md](arabic-rtl.md#fonts) and R15).
- **Third-party showcase looks as-is** (Frosted Glass, visionOS, iOS, LCARS): see [design-gallery.md](design-gallery.md); Dartec's own variants are in [dartec-variants/](dartec-variants/).
