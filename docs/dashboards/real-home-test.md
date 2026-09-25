# The rules on a real house: what failed, and what the generator should do

**2026-09-25.** At the owner's request, the dashboard rules from this guide
were run against a real, lived-in home: the owner's own. The aim was to find
where the rules break on a house that grew over years, rather than to
hand-craft a dashboard. On the bench, everything the rules saw had been
built for them.

**Privacy.** This page carries patterns and counts only. There are no names of
people, rooms or devices, no layout, and no screenshots. The inventory, the
generated YAML, the screenshots and the log of changes stay on the owner's PC.

## What was done, and how it was kept safe

The owner authorised this in person, with limits:

- **Admin only.** Nothing any other user sees was allowed to change.
- **No renames and no re-areas.**
- **No restarts and no updates.**
- **The master wall panel untouched.**
- **Every change logged and reversible in one step.**

Within those limits:

- **Access.** A long-lived token on the home's own Dartec admin account, kept
  in a local file. Every call first checked the home's location name. The
  connection was over the home's LAN, by the owner's choice; this PC's
  Tailscale was on another tailnet.
- **The inventory** was read-only.
- **The only writes:**
  - two dashboards, English and Arabic, created with `require_admin: true` and
    `show_in_sidebar: false`, so they are in nobody's sidebar and no
    non-administrator can open them;
  - the Dartec admin account's own language, switched to Arabic for the Arabic
    check and set back straight after.

  That is 16 calls in all, and none failed.
- **Checked afterwards:** the five existing dashboards were compared, and are
  unchanged.
- **Themes:** the drafts (corrected Dartec in light, Graphite in dark) were
  applied only in the screenshot browser, by HA's own frontend theme code
  ([dartec-variants/README.md](dartec-variants/README.md#how-these-were-tested)).
  The home's themes were not touched, because installing the drafts would mean
  writing theme files, and that is not yet sanctioned.
- **Undo:** one script, on the owner's PC, deletes both dashboards and restores
  the admin account's language and theme settings to what they were.

## The house, in numbers

| | Count |
|---|---|
| Floors | 1 |
| Areas | 7 (one empty) |
| Devices (hardware, not services) | 134 |
| **Devices with no area** | **90** |
| Registry entities | 2,203 |
| **Disabled** | **1,144** |
| Config or diagnostic entities | 340 |
| User-facing entities with no area | 347 |
| Unavailable at the time | 121 |
| ACs | 3, all infrared (cool/off, one also heat), one unavailable and not in any area |
| Covers, locks, alarm, leak or smoke sensors | none |
| Cameras | 4 |
| Media players | 25, of which 15 were unavailable and 23 not in an area |

Compared with the bench, this house has more of everything that makes
dashboards hard: devices never placed in a room, entities nobody uses,
machines with dozens of controls, and names typed over the years.

## Rule by rule

Figures are per dashboard; each rule acted the same on the English and Arabic
ones. Each row gives what happened, how much it touched, what was done
instead, and what the generator should do.

| Rule (guide / issue) | What happened on this house | Scale | What was done | What the generator should do |
|---|---|---|---|---|
| **Rooms first** (layout, R7 #50) | Most of the house is in no room at all. A room-first dashboard, which is what the manager generates today, shows a fraction of the house and says nothing about the rest | 90 of 134 devices, 347 entities with no area | A **"Not in a room yet (N)"** view, linked from the overview with its count | Never drop unassigned devices: show them, count them, and ask the technician or owner to place them (R16, #61) |
| **Floors** (layout) | One floor, so the floor heading adds nothing | 1 floor | Kept as a single heading | Only emit floor structure when there are two or more floors |
| **Climate first** (R1 #44) | Worked. `hvac_modes` from each unit gave the right buttons (cool/off, plus heat on one). The ACs are infrared and report an *assumed* state. One AC is a duplicate that is unavailable and in no room | 3 ACs | Climate section first; the duplicate shows as unavailable, under Needs attention | Mark infrared or assumed-state ACs as such, and flag duplicate climate entities for clean-up |
| **Offline shown, "Needs attention"** (R2 #45) | At first **20** entries, flooded by unassigned media players (cast groups, personal laptops and phones) and a dishwasher's program switches, which are "unavailable" whenever it is off. **Worse, the entities card had a header toggle that could switch every listed switch at once** | 20 → 5 after the fixes | Media players with no room left off; machine controls left off; `show_header_toggle: false` | Always set `show_header_toggle: false` on generated entities cards. Keep "Needs attention" to devices a family can act on |
| **Readable names** (R3 #46) | 12 names had the room removed from the front, as designed. The names that remained included four lights called only by a number (two with the same number), one TV named by its IP address, one device named by model and MAC fragment, and a doubled room word | 12 names cleaned; 7 still need a person; 12 channels named by position (Left/Center/Right) | Shown as they are, and listed privately for the owner | Report names that need a person (numbers only, IPs, MACs, duplicates within a room) to the technician. Never invent a name |
| **Arabic** (R4 #47) | With the plan wrong, there was no source for Arabic room or device names | 25 names hand-written in Arabic; 33 kept as written (product and model names, which stay Latin) | Arabic written for Arabic readers, not word for word ("bd" became the bedroom, not the letters). English names left in the Arabic dashboard are cut at their start, as on the bench | Arabic names must come from the plan or the commissioning apps. Without them, the generator should fall back to the English dashboard rather than a half-Arabic one |
| **No silent truncation** (R8 #51) | No section passed the old 24 cap once segments were collapsed. **Before collapsing**, one room had 45 light entities from 2 devices, and the unassigned list 67 lights | — | Collapse (below) | Collapse first, then never cap |
| **Multi-channel devices** (R9 #52) | Four 3-gang switches and several LED strips. The switches' channels are named by position. The strips expose **15–28 "Segment NNN" lights each**, plus effect toggles ("Gradient", "Dream View") | 116 segment lights, 15 effect toggles | Segments hidden behind their strip's own light; effect toggles left off | New rule (R17, #62): a light whose name is its device's name plus "Segment N" is a segment. Show the parent only |
| **Duplicates across integrations** (new) | The same physical strip is registered twice, as a light from one integration and a "Power Switch" from another, on **two different devices** | 8 duplicate power switches | Matched by name ("X Power Switch" when a light "X" exists) and left off | Same rule (R17, #62): prefer the light, and report the duplicate to the technician |
| **Machines** (new) | 3D printers expose heater output pins as ordinary switches. A dishwasher exposes 15 program switches. A pet appliance exposes feeding and dispensing controls | 90 machine controls and details | Left off. At most one power tile per machine is shown | New rule (R18, #63): never put controls that heat, dispense or run a program on a family dashboard. Show the machine's status, and send people to its own page |
| **A room of only machines** | Once its machine controls are left off, one area has nothing to show and disappears from the overview, like the genuinely empty area | 2 areas left off | Left off, and noted | Show a machine's status tile (for example, its current job) so the room keeps a presence |
| **Room temperature in the heading** (R1, R7) | Only 2 of 7 areas have their temperature sensor set. The rest cannot show a room temperature, and HA's own Overview uses a median of whatever sensors are there | 5 areas | Badges only where set | When applying the plan's structure, set each area's temperature and humidity sensor, and report areas without one |
| **Area icons** | 1 of 7 areas has an icon | 6 areas | Default icon | Set the icon from the room type (the manager already maps room types to icons for its own dashboards) |
| **Cameras** (layout) | 4 cameras, from appliances and machines | 4 | Left off the family dashboards | As designed. Cameras belong on HA's Security dashboard, not on shared screens |
| **Theme** (#53, dartec-theme#1) | The house already runs Dartec v1.1.0, with copper as its accent. The corrected Dartec (light) and Graphite (dark) drafts looked right on the real house | — | Browser-only preview | Ship the corrected Dartec first (dartec-theme#1) |

## What held up

- **Built-in cards only.** Nothing needed a custom card. The house has 33
  custom frontend resources installed, and the preview used none of them.
- **Climate first, with modes from each unit's own `hvac_modes`.**
- **The overview's shape:** climate, rooms, and safety with "Needs attention".
  It stayed clear even with a single floor.
- **Arabic:** the layout mirrored correctly, and the brand fonts rendered in
  the preview. The same HA gaps as on the bench remain (#55): "On/Off" in
  English and "C° 22.0". HA did translate "Unavailable".
- **Admin-only preview dashboards** (`require_admin` with `show_in_sidebar:
  false`) are a safe way to show an owner a new dashboard before anyone else
  sees it. **One caveat:** other administrators in the household can still find
  them under *Settings → Dashboards*.

## The numbers the generator should print

A generator run on this house should end with a summary like this, for the
technician rather than the family:

```
shown on the dashboards        55
not in a room yet              24  (please place them)
light segments collapsed      116
duplicate power switches        8
machine controls left off      90
names that need a person        7
needs attention                 5
areas with no temperature       5
Arabic names missing           25  (falling back to English)
```

Every one of those lines was a silent loss, or an unsafe control, in the rules
as they stood.
