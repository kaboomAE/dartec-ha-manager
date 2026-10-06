"""Dartec's formal Arabic for the Home Assistant state words HA leaves in English.

Home Assistant's Arabic translation stops short of the words a family sees
most: a light reads "On", an AC "Cool", a curtain "Open". Read from the HA
images themselves (2026.9.3 and 2026.10.0b1): `light`, `switch`, `fan`,
`cover` and `climate` ship no Arabic for their states at all, `switch` has no
`ar.json`, and the one climate preset that is translated, "Activity", is
translated as السجل ("the log"). "Unavailable" is translated (غير متوفر),
because that one is a frontend string. dartec-ha-manager#55; the owner chose
the words in kaboomAE/dartec-ops#15 (2026-10-06): formal Arabic, On/Off as
مُشغَّل / مُطفأ, masculine unless a key's noun needs the feminine (a window).

The words are contributed upstream too (#55). This is what a Dartec home sees
until Home Assistant ships them, and it steps aside by itself when it does.

Mechanism: the backend translation cache, through Home Assistant's own helper.
`translation.async_get_translations(hass, "ar", "entity_component", [domain])`
loads Arabic for one integration, English underneath as the fallback, and for
a single integration returns the cache's own dict for it. Entries in that
dict are corrected in place, and from then on every reader gets them: the
frontend's `frontend/get_translations` (every browser and the companion
app, on every page load) and the backend's own `async_translate_state`.

Why here and not in the browser: the frontend asks for `entity_component`
early in its start-up, often before an extra module such as
www/dashboard-fix.js has even loaded, and keeps the answer for the session,
so a browser-side patch would have to reach into the frontend's private
state to make it ask again. The cache is consulted on every ask, and HA
never rebuilds an integration's entry once it is loaded, so one correction
lasts until Home Assistant restarts.

Narrow, like the dashboard fixes:

- only the keys listed below, only in Arabic, and only where the value is
  still the English HA falls back to, or one listed known-wrong Arabic value.
  Anything HA translates itself is left alone, so the day a word reaches
  HA's Arabic, HA's word is shown, not this one;
- a key HA does not have is never added: if HA renames one, it is skipped;
- if HA ever returns a copy instead of its cache, the corrections land in
  the copy and are thrown away, which is a no-op, not a breakage. That is
  checked after applying and logged;
- unloading the integration puts back every value it changed, if the value
  is still the one it set.

No Home Assistant import at module level, so the table and `apply` are tested
on their own (tests/test_arabic_states.py).
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

LANGUAGE = "ar"
CATEGORY = "entity_component"
_DATA = f"{__name__}.changed"

# The shared state words (core's common::state keys), each used by several
# integrations below. State, not command: «مُشغَّل», not «تشغيل», which is
# what the "Turn on" button already says.
ON = ("On", "مُشغَّل")
OFF = ("Off", "مُطفأ")
OPEN = ("Open", "مفتوح")
CLOSED = ("Closed", "مغلق")
IDLE = ("Idle", "خامل")
AUTO = ("Auto", "تلقائي")
LOW = ("Low", "منخفض")
MEDIUM = ("Medium", "متوسط")
HIGH = ("High", "مرتفع")
DETECTED = ("Detected", "مُكتشَف")
CLEAR = ("Clear", "خالٍ")

# Per integration: key under component.<domain>.entity_component. ->
# (HA's English, Dartec's Arabic[, known-wrong Arabic values to replace...]).
# Scope is what Dartec dashboards show: lights, switches and sockets, fans,
# ACs, curtains and blinds, door and window sensors, and safety alarms
# (docs/dashboards/arabic-rtl.md).
STATES: dict[str, dict[str, tuple[str, ...]]] = {
    "light": {
        "_.state.on": ON,
        "_.state.off": OFF,
    },
    "switch": {
        "_.state.on": ON,
        "_.state.off": OFF,
        "outlet.state.on": ON,
        "outlet.state.off": OFF,
        "switch.state.on": ON,
        "switch.state.off": OFF,
    },
    "input_boolean": {
        "_.state.on": ON,
        "_.state.off": OFF,
    },
    "fan": {
        "_.state.on": ON,
        "_.state.off": OFF,
    },
    "cover": {
        "_.state.open": OPEN,
        "_.state.closed": CLOSED,
        "_.state.opening": ("Opening", "جارٍ الفتح"),
        "_.state.closing": ("Closing", "جارٍ الإغلاق"),
        "_.state.stopped": ("Stopped", "متوقف"),
    },
    "climate": {
        "_.state.off": OFF,
        "_.state.auto": AUTO,
        "_.state.cool": ("Cool", "تبريد"),
        "_.state.dry": ("Dry", "تجفيف"),
        "_.state.fan_only": ("Fan only", "مروحة فقط"),
        "_.state.heat": ("Heat", "تدفئة"),
        "_.state.heat_cool": ("Heat/Cool", "تدفئة/تبريد"),
        "_.state_attributes.hvac_action.name": ("Current action", "الإجراء الحالي"),
        "_.state_attributes.hvac_action.state.cooling": ("Cooling", "جارٍ التبريد"),
        "_.state_attributes.hvac_action.state.heating": ("Heating", "جارٍ التدفئة"),
        "_.state_attributes.hvac_action.state.drying": ("Drying", "جارٍ التجفيف"),
        "_.state_attributes.hvac_action.state.fan": ("Fan", "المروحة تعمل"),
        "_.state_attributes.hvac_action.state.defrosting": ("Defrosting", "جارٍ إذابة الجليد"),
        "_.state_attributes.hvac_action.state.preheating": ("Preheating", "جارٍ التسخين المسبق"),
        "_.state_attributes.hvac_action.state.idle": IDLE,
        "_.state_attributes.hvac_action.state.off": OFF,
        "_.state_attributes.preset_mode.name": ("Preset", "الإعداد المسبق"),
        "_.state_attributes.preset_mode.state.none": ("None", "بلا"),
        "_.state_attributes.preset_mode.state.eco": ("Eco", "اقتصادي"),
        "_.state_attributes.preset_mode.state.comfort": ("Comfort", "راحة"),
        "_.state_attributes.preset_mode.state.boost": ("Boost", "تعزيز"),
        "_.state_attributes.preset_mode.state.sleep": ("Sleep", "نوم"),
        # HA's Arabic has السجل, "the log": a mistranslation, not a choice.
        "_.state_attributes.preset_mode.state.activity": ("Activity", "نشاط", "السجل"),
        "_.state_attributes.fan_mode.name": ("Fan mode", "وضع المروحة"),
        "_.state_attributes.fan_mode.state.auto": AUTO,
        "_.state_attributes.fan_mode.state.low": LOW,
        "_.state_attributes.fan_mode.state.medium": MEDIUM,
        "_.state_attributes.fan_mode.state.high": HIGH,
        "_.state_attributes.fan_mode.state.on": ON,
        "_.state_attributes.fan_mode.state.off": OFF,
        "_.state_attributes.swing_mode.name": ("Swing mode", "وضع التأرجح"),
        "_.state_attributes.swing_mode.state.on": ON,
        "_.state_attributes.swing_mode.state.off": OFF,
        "_.state_attributes.swing_mode.state.vertical": ("Vertical", "عمودي"),
        "_.state_attributes.swing_mode.state.horizontal": ("Horizontal", "أفقي"),
        "_.state_attributes.swing_mode.state.both": ("Both", "كلاهما"),
    },
    "binary_sensor": {
        "_.state.on": ON,
        "_.state.off": OFF,
        "door.state.on": OPEN,
        "door.state.off": CLOSED,
        "garage_door.state.on": OPEN,
        "garage_door.state.off": CLOSED,
        "opening.state.on": OPEN,
        "opening.state.off": CLOSED,
        # The one feminine pair: the label is the window's, النافذة.
        "window.state.on": ("Open", "مفتوحة"),
        "window.state.off": ("Closed", "مغلقة"),
        # «مبلل», not «رطب»: "damp" is too weak for a leak.
        "moisture.state.on": ("Wet", "مبلل"),
        "moisture.state.off": ("Dry", "جاف"),
        "gas.state.on": DETECTED,
        "gas.state.off": CLEAR,
        "smoke.state.on": DETECTED,
        "smoke.state.off": CLEAR,
        "carbon_monoxide.state.on": DETECTED,
        "carbon_monoxide.state.off": CLEAR,
        "motion.state.on": DETECTED,
        "motion.state.off": CLEAR,
        "occupancy.state.on": DETECTED,
        "occupancy.state.off": CLEAR,
        "safety.state.on": ("Unsafe", "غير آمن"),
        "safety.state.off": ("Safe", "آمن"),
        "problem.state.on": ("Problem", "مشكلة"),
        "problem.state.off": ("OK", "سليم"),
    },
}


def _prefix(domain: str) -> str:
    return f"component.{domain}.{CATEGORY}."


def apply(resources: Any, domain: str) -> dict[str, str]:
    """Correct `resources`, one integration's Arabic `entity_component` strings,
    in place. Returns {key: value it replaced} for every key it changed."""
    changed: dict[str, str] = {}
    if not isinstance(resources, dict):
        return changed
    prefix = _prefix(domain)
    for suffix, (english, arabic, *wrong) in STATES.get(domain, {}).items():
        key = prefix + suffix
        current = resources.get(key)
        if not isinstance(current, str) or current == arabic:
            continue  # not a key HA has (never add one), or already right
        if current.strip().casefold() == english.casefold() or current in wrong:
            changed[key] = current
            resources[key] = arabic
    return changed


def restore(resources: Any, domain: str, changed: dict[str, str]) -> int:
    """Undo `apply` for the keys still holding the value it set."""
    if not isinstance(resources, dict):
        return 0
    arabic_by_key = {_prefix(domain) + s: v[1] for s, v in STATES.get(domain, {}).items()}
    undone = 0
    for key, original in changed.items():
        if key in arabic_by_key and resources.get(key) == arabic_by_key[key]:
            resources[key] = original
            undone += 1
    return undone


async def async_setup(hass) -> None:
    """Apply the corrections to Home Assistant's Arabic translation cache.

    Never raises: a home whose Home Assistant does not work the way this
    expects keeps HA's own Arabic, exactly as before.
    """
    try:
        from homeassistant.helpers.translation import async_get_translations
    except Exception as err:  # noqa: BLE001 - an HA without the helper: do nothing
        _LOGGER.debug("Arabic states: no translation helper (%s)", err)
        return

    done: dict[str, dict[str, str]] = hass.data.setdefault(_DATA, {})
    for domain in STATES:
        try:
            resources = await async_get_translations(hass, LANGUAGE, CATEGORY, [domain])
            changed = apply(resources, domain)
            if not changed:
                continue
            again = await async_get_translations(hass, LANGUAGE, CATEGORY, [domain])
            key = next(iter(changed))
            if again.get(key) != resources.get(key):
                # HA handed out a copy: nothing reached its cache. Harmless,
                # and worth knowing when a home still shows English.
                _LOGGER.debug("Arabic states: %s not applied; HA returned a copy", domain)
                continue
            done.setdefault(domain, {}).update(changed)
        except Exception as err:  # noqa: BLE001 - one integration failing is not worth the rest
            _LOGGER.debug("Arabic states: %s skipped (%s)", domain, err)
    if done:
        _LOGGER.debug("Arabic states: %s", {d: len(c) for d, c in done.items()})


async def async_unload(hass) -> None:
    """Put back what `async_setup` changed, where it is still what it set."""
    done: dict[str, dict[str, str]] = hass.data.pop(_DATA, None) or {}
    if not done:
        return
    try:
        from homeassistant.helpers.translation import async_get_translations
    except Exception:  # noqa: BLE001
        return
    for domain, changed in done.items():
        try:
            resources = await async_get_translations(hass, LANGUAGE, CATEGORY, [domain])
            restore(resources, domain, changed)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Arabic states: %s not restored (%s)", domain, err)
