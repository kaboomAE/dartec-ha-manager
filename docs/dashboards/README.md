# Dashboards and wall panels for Dartec homes

**Written** 2026-09-25 for the owner (Abdulla). **Tested** on the bench Pi 5
(Home Assistant 2026.9.3, HAOS, agent 0.23.0) with a test villa built for the
purpose and removed afterwards. **Status:** a guide and a set of
recommendations. Nothing here changes what the agent or the manager does today;
each recommended change is a separate GitHub issue for the owner to approve.

## What is in this folder

| Page | What it answers |
|---|---|
| This page | What makes a dashboard friendly for a homeowner who is not technical, and what is different about a UAE villa |
| [layout.md](layout.md) | The structure we recommend (home overview, rooms, floors, climate, safety), navigation, which cards to use and which to avoid, with screenshots of today's output next to the prototypes |
| [panels.md](panels.md) | Wall panels: Android tablets and the Sonoff NSPanel Pro, kiosk set-up, screen and brightness, touch targets, what a bedroom panel should show, and how a panel maps to a room |
| [arabic-rtl.md](arabic-rtl.md) | Everything found about Arabic and right-to-left, including fonts (Lateef and Dubai) |
| [themes.md](themes.md) | All 105 themes from the cloudapp.dev list, evaluated in one table, the shortlist and the recommendation |
| [design-gallery.md](design-gallery.md) | How eight showcase theme repositories (Frosted Glass, visionOS, iOS, LCARS, Catppuccin, Material You, Graphite, Noctis) get their look: how much is theme, CSS, custom cards, photographs and blur; licences and risk |
| [dartec-variants/](dartec-variants/) | **Dartec's own theme family**, drafted: Dartec (corrected), Glass, Glass Lite, Soft, Material, Graphite. Design briefs, the theme YAML, contrast, the cost of blur on a tablet, brand fonts, screenshots |
| [maintenance.md](maintenance.md) | What each choice costs to keep working across Home Assistant updates, and which dependencies are risky for a fleet |
| [recommendations.md](recommendations.md) | Ranked changes to the dashboards and room panels the manager and agent generate, each with benefit, effort, risk and its GitHub issue |
| [prototypes/](prototypes/) | The exact YAML of every test dashboard (English and Arabic), and the log of every API call made on the bench, so each test can be rebuilt and undone |
| [screenshots/](screenshots/) | Our own screenshots from the bench, at phone, tablet, wall-panel and NSPanel sizes, light and dark, English and Arabic |
| [diagrams/dashboard-map.html](diagrams/dashboard-map.html) | How a home's dashboards, rooms, floors and panels fit together (made with `/archify`) |

## The short version

1. **Rooms first, climate first.** A homeowner thinks "the majlis is too
   warm", not "climate.majlis_ac". Organise by floor and room, and inside a room
   put the AC before anything else: in a UAE villa it is the control people use
   every day, several times a day.
2. **Names are the interface.** The single biggest problem on the bench was not
   layout or theme but names: 24 tiles all reading "East guest suit…", the
   room's name repeated on every tile, four relay channels called
   "SONOFF 4CHP…", and English names losing their beginning in Arabic. Good
   names from the plan, in the household's language, matter more than any card.
3. **Say when something is broken.** Today a device that is offline when the
   dashboard is generated is silently left out, so a family sees a room with a
   light missing and no reason. Show it as unavailable, and gather everything
   offline in one "needs attention" list.
4. **Built-in cards only.** Home Assistant's own sections view, tile, heading,
   area, thermostat and clock cards now do everything a room needs, keep working
   across monthly updates, and mirror correctly in Arabic. Custom cards and
   card-mod run third-party JavaScript in the homeowner's browser and break on
   updates (card-mod is breaking on 2026.8 and 2026.9 right now).
5. **A panel is a different screen, not a small dashboard.** A bedroom panel
   shows that room only, fits one screen without scrolling, and puts the one or
   two things people do at night (AC, "Good night") on big targets.
6. **Themes matter far less than layout.** On built-in cards, six themes
   produced almost identical screens. Keep the Dartec theme, fix its font and
   its AC colours, and add a licence.

## What makes a dashboard friendly for a homeowner who is not technical

These come from Home Assistant's own documentation (sources below), from the
cloudapp.dev guides the owner chose, and from what the bench showed. Each is
something the generated dashboards can do automatically.

- **One idea per screen.** The first screen answers "is the house comfortable
  and is anything wrong?". Everything else is one tap away. HA's docs make the
  same point: a clean main view that links to detail views
  ([views](https://www.home-assistant.io/dashboards/views/), `subview`).
- **Group before adding.** The five-steps guide's central lesson is that
  grouping the same cards under labelled headings did more for legibility than
  any card added afterwards
  ([cloudapp.dev, five steps](https://www.cloudapp.dev/home-assistant-dashboard-from-zero-five-steps)).
  In a villa the groups are floors and rooms.
- **Show state in words and colour, not only icons.** The AC's preset buttons
  (a house, a bed, a person with an arrow) meant nothing to a first-time viewer
  on the bench. Where a choice has no obvious icon, use a labelled dropdown or a
  named button.
- **Every control is a real touch target.** The sections grid gives a one-row
  tile about 56 px of height
  ([developer docs](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/)),
  which clears the usual 44–48 px guidance. The small heading badges do not
  (about 24–28 px): keep them for information, not for the action people use most.
- **Nothing hidden without saying so.** No silent truncation, no silently
  dropped devices, no blank boxes for a device that was renamed.
- **The same layout on every device.** Families use phones far more than wall
  panels. HA's sections view reflows a layout by section order on narrow
  screens, so the order of sections is the phone's order too
  ([sections](https://www.home-assistant.io/dashboards/sections/)).
- **Safe one-tap actions.** "All ACs off" asks for confirmation; "All off" in a
  room does not, because it is easy to undo.

## What is different about a UAE villa

| Villa trait | What it means for the dashboard |
|---|---|
| **Many rooms on two or three floors**, often 15–30 areas including majlis, family hall, maid's room, driver's room, outdoor majlis | The overview groups rooms by floor ([floors](https://www.home-assistant.io/docs/organizing/floors/)); each room is a subview. A flat list of 25 rooms is unusable |
| **Climate is the main daily control.** Split units or VRF per room, set many times a day, off when a room is empty | Climate is the first section of the overview and of every room. The AC tile carries mode, target temperature and presets. Room temperature sits in the heading, not as a separate tile |
| **Households with family and domestic staff** | Who sees what is a real question. A maid's-room panel, a driver's phone and the family's overview should not be the same dashboard. My Home (agent 0.20.0) already sets each person's first dashboard; that is a convenience, **not a security boundary** (HA has no per-user device permissions) |
| **Arabic-first users** | Home Assistant mirrors the whole interface right-to-left when a user picks Arabic, but the names Dartec writes (rooms, headings, tiles) are not translated by HA. They have to be written in Arabic. See [arabic-rtl.md](arabic-rtl.md) |
| **Bright rooms, glass walls, afternoon sun** | Contrast matters more than in the brochure photos. Every theme we recommend passes WCAG AA in both modes; see [themes.md](themes.md) |
| **Large majlis and guest suites with many circuits** | A room with 25 or more switches needs names people can read at a glance, and must never be truncated silently |
| **Multi-gang wall switches and multi-channel relays** | One device, several independent circuits. Each channel needs its own name ("Incense burner socket", not "Switch 3"). See [layout.md](layout.md#multi-channel-devices) |

## How this was tested

- **Where:** the bench Pi 5 only (`192.168.1.51`, location name "TEST",
  per dartec-onboarding's run logs). Not the owner's home, not a customer home.
- **How:** through Home Assistant's own REST and WebSocket APIs with a
  long-lived token the owner created for the purpose. Every call that changed
  the bench is in [prototypes/bench-log.jsonl](prototypes/bench-log.jsonl)
  (730 calls, no secrets). Screenshots were taken by a headless Chromium signed
  in with the same token; the browser pane was used only to look.
- **What was built:** a test villa made from Home Assistant's UI helpers, labelled
  "Dartec dashboard test": two floors, seven Arabic-named rooms, six ACs
  (generic thermostats with Home, Sleep and Away presets), dimmable and
  on/off lights, curtains, a leak sensor, a 4-channel relay and a dual wall
  switch with their raw names, a room that can be taken offline, and a
  guest suite with 26 circuits. Then today's generator, the prototypes and six
  themes, in English and Arabic.
- **Undone:** everything added was removed at the end, and the bench was
  compared with a snapshot taken before any change: the same 3 areas, 0 floors,
  19 devices, 141 entities, 2 dashboards, 18 config entries, no themes, the same
  HACS installs and the same language setting.
- **What it cannot show:** the bench has no real AC, Zigbee lights or relays,
  so device-specific behaviour (a VRF controller that overrides the setpoint, a
  relay that reports late) is not covered. Screenshots were rendered by
  Chromium on Windows, so Arabic is drawn in Segoe UI; an Android tablet draws it
  in Noto. The Galaxy Tab A7 run of 2026-09-20 (dartec-onboarding
  `docs/panels.md`) is the real-hardware evidence for panels.

## Sources

Summaries in our own words, with a link for each. No text or images were
copied from these pages.

**cloudapp.dev** (chosen by the owner; a personal blog by one hobbyist in the
German-speaking region, current to HA 2026.8, no affiliate links found):
- [Home Assistant dashboard from zero, five steps](https://www.cloudapp.dev/home-assistant-dashboard-from-zero-five-steps): build one dashboard in stages; the grouping step is the one that matters; cap the width at three sections; heading cards, not markdown, for titles; YAML files for reproducibility.
- [HACS themes directory](https://www.cloudapp.dev/hacs/themes): 105 themes ranked by GitHub stars; a directory, not a review. Every one is evaluated in [themes.md](themes.md).
- [Themes, six compared](https://www.cloudapp.dev/home-assistant-themes-six-compared): half the popular themes need card-mod; blur-heavy themes are costly; compare with fixed-size automated screenshots.
- [Dashboards from stock cards](https://www.cloudapp.dev/home-assistant-dashboard-examples-built-in-cards) and [templates to copy](https://www.cloudapp.dev/home-assistant-dashboard-templates-to-copy): the author's most-used dashboards, one on a wall tablet, use built-in cards only; the version with Mushroom, button-card and card-mod is four dependencies that break on their own schedules; a missing entity is a silent blank box.
- [Best HACS integrations](https://www.cloudapp.dev/best-hacs-integrations-home-assistant): keep HACS at the edges; four questions before installing anything (maintained, issues answered, does core already do it, would anyone notice if it vanished).
- [KNX thermostat presets](https://www.cloudapp.dev/home-assistant-knx-thermostat-preset-modes) and [standby after restart](https://www.cloudapp.dev/home-assistant-knx-thermostat-standby-after-restart-fix): decide which layer owns the setpoint, or the dashboard's slider "snaps back"; re-apply climate state after a restart by waiting for the entities, not a fixed delay.
- [Alarm dashboard](https://www.cloudapp.dev/home-assistant-alarm-dashboard-tecnoalarm-keypad): design against simulated entities first; a dashboard is not the security source of truth; never show a map of the house's weak points on a shared screen.
- [Update with rollback](https://www.cloudapp.dev/home-assistant-docker-update-rollback) and [21 releases behind](https://www.cloudapp.dev/home-assistant-21-releases-behind-rebuild): update custom components first, then core; a short checklist after every update; pick the language at onboarding so names start out in it.

The site has nothing on wall panels, kiosk mode, per-user dashboards,
navigation or right-to-left, so those sections rest on Home Assistant's own
documentation and the bench.

**Home Assistant documentation** (read 2026-09-25, HA 2026.9):
[dashboards](https://www.home-assistant.io/dashboards/),
[built-in dashboards and the default dashboard](https://www.home-assistant.io/dashboards/dashboards/),
[views](https://www.home-assistant.io/dashboards/views/),
[sections view](https://www.home-assistant.io/dashboards/sections/),
[tile card](https://www.home-assistant.io/dashboards/tile/),
[card features](https://www.home-assistant.io/dashboards/features/),
[area card](https://www.home-assistant.io/dashboards/area/),
[heading card](https://www.home-assistant.io/dashboards/heading/),
[badges](https://www.home-assistant.io/dashboards/badges/),
[thermostat card](https://www.home-assistant.io/dashboards/thermostat/),
[visibility and conditions](https://www.home-assistant.io/dashboards/conditional/),
[naming](https://www.home-assistant.io/dashboards/naming/),
[themes (frontend integration)](https://www.home-assistant.io/integrations/frontend/),
[frontend.set_theme](https://www.home-assistant.io/actions/frontend.set_theme/),
[areas](https://www.home-assistant.io/docs/organizing/areas/),
[floors](https://www.home-assistant.io/docs/organizing/floors/),
[users and profile settings](https://www.home-assistant.io/docs/configuration/user-configuration/),
[Android home-app launcher](https://companion.home-assistant.io/docs/integrations/android-home-app-launcher),
[Android web view settings](https://companion.home-assistant.io/docs/integrations/android-webview),
[notification commands](https://companion.home-assistant.io/docs/notifications/notification-commands/),
[companion sensors](https://companion.home-assistant.io/docs/core/sensors),
[iOS kiosk mode](https://companion.home-assistant.io/docs/integrations/ios-kiosk-mode),
[Fully Kiosk Browser](https://www.home-assistant.io/integrations/fully_kiosk/),
and the release notes that introduced each feature (listed in
[layout.md](layout.md#minimum-home-assistant-versions)).
