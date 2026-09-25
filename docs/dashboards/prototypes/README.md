# Prototype dashboards and the bench log

Everything here was applied to the **bench Pi 5** (HA 2026.9.3, location
"TEST") on 2026-09-25 and removed again the same evening.

## The dashboards

| File | Pushed to (`url_path`) | What it is |
|---|---|---|
| `today-generator.en.yaml`, `.ar.yaml` | `dartec-proto-current-en`, `-ar` | The manager's own room compiler (`server/app/room_dashboards.py`, `compile_room`, manager `main` at `5935326`) run over the seven test rooms. For comparison, it has one view per room, where a real home has one `dartec-room-*` dashboard per room |
| `home-overview.en.yaml`, `.ar.yaml` | `dartec-proto-home-en`, `-ar` | The recommended home overview (climate, floors with area cards, safety and "needs attention") with one subview per room |
| `room-majlis.en.yaml`, `.ar.yaml` | `dartec-proto-room-en`, `-ar` | The recommended stand-alone room dashboard (majlis) |
| `panel-master-bedroom.en.yaml`, `.ar.yaml` | `dartec-proto-panel-en`, `-ar` | The recommended bedroom wall panel |

These are Lovelace storage-mode configurations written as YAML. To apply one,
create the dashboard (`lovelace/dashboards/create`, `mode: storage`,
`show_in_sidebar: false`), then save the file's content, converted to JSON, with
`lovelace/config/save`. Both are Home Assistant WebSocket commands. Paste the
same YAML into the dashboard's raw configuration editor for the same result.

The entity ids are the test villa's (below). On a real home the generator
would fill in that home's own.

## The test villa

It was built only from Home Assistant's own UI helpers. No integration was
installed and no YAML file was edited, so it can be recreated on any HA from
2025.x onwards. Everything was labelled **"Dartec dashboard test"**.

| Room (area) | Floor | Contents |
|---|---|---|
| المجلس (majlis) | الطابق الأرضي | AC (generic thermostat, cool/off, Home/Sleep/Away presets), temperature and humidity sensors, chandelier, downlights, a long-named Arabic cove light, wall washers (on/off lights: template switches shown as lights), a dimmable reading lamp (template light), curtains (template cover with position), `SONOFF 4CHPROR3 Switch 1–4`, `Aqara Wall Switch H1 (Dual) Left/Right` |
| الصالة العائلية (family hall) | ground | AC, sensors, two lights, blinds |
| المطبخ (kitchen) | ground | two lights, a water-leak sensor |
| غرفة الخادمة (maid's room) | ground | AC, a light |
| غرفة النوم الرئيسية (master bedroom) | first | AC, sensors, dimmable ceiling light, two bedside lights, blackout curtain |
| غرفة الأطفال (kids' room) | first | AC; a light and an air-quality sensor that are **unavailable** while the helper "Test kids room online" is off |
| جناح الضيوف الشرقي مع الحمام وغرفة الملابس (east guest suite) | first | AC, sensors, 26 switches with long English names |

## `bench-log.jsonl`

One JSON object per line, one line for every API call that changed the bench:

- `at`: the time, Gulf Standard Time
- `why`: what the call was for
- `call`: the exact WebSocket message, or the REST method, path and body
- `ok`: whether it succeeded
- `result`: the result, shortened

The log has 730 lines, in five groups:

1. **Build.** The final build created:
   - a label, 2 floors and 7 areas
   - 67 template helpers, 11 switch-as-light wrappers and 6 generic thermostats
   - 6 number helpers and 1 boolean helper

   It then set the area, label and hidden settings on each entity.
2. **States.** Realistic states (ACs cooling, some lights on, curtains half
   open), set through `call_service`.
3. **Dashboards.** 8 dashboards created and saved (`lovelace/dashboards/create`,
   `lovelace/config/save`).
4. **Themes and language.**
   - Four themes installed through HACS, each pinned to a version
     (`hacs/repository/refresh`, `hacs/repository/download` with `version`).
   - The Dartec theme added as a custom repository (`hacs/repositories/add`)
     and installed.
   - `frontend.set_theme` for each theme.
   - The Dartec admin account's language set to Arabic and back
     (`frontend/set_user_data`, key `language`).
5. **Teardown.** Everything above deleted in reverse. The HACS installs were
   removed, the default theme was put back to `default`, and the language was
   put back to unset.

The first run also includes one aborted build: a follow-up form was missing,
and a unit used the micro sign µ where HA wants the Greek μ. It was torn down
and rebuilt, so some create and delete pairs appear twice.

**Check afterwards:** a snapshot of the bench taken before the first call was
compared with one taken after the teardown. They had the same areas (3),
floors (0), devices (19), entities (141), dashboards (2), config entries (18),
labels (0), themes (none), HACS installs (HACS and the agent), Lovelace
resources (none), and the same user language (unset).

The log contains no token or password. The token lived in a file outside the
repository and was only ever sent in the WebSocket `auth` message, which is
not logged.

## What was not undone

- Before switching to the API, the owner first signed in to the bench in the
  browser pane as the panel account `panel-phone-run` (from a previous run),
  and that session was then signed out. That account now shows as signed in
  once.
- The long-lived access token the owner created for this work is still valid.
  **Delete it** (the bench's Dartec profile → Security → Long-lived access
  tokens), then delete the local file that held it.
