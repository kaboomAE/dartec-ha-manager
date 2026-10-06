"""Dartec's formal Arabic for HA's state words, applied only where HA has none.

arabic_states.py corrects Home Assistant's own Arabic translation cache in
place (dartec-ha-manager#55; the owner's words, kaboomAE/dartec-ops#15). These
pin what it may touch: only listed keys, only in Arabic, only where the value
is still HA's English fallback or a listed mistranslation; never a key HA does
not have, never a word HA translates itself; and nothing at all when Home
Assistant hands out a copy instead of its cache. The same is checked against
real Home Assistant by tests/live/arabic_states_check.py.

The cache below behaves as homeassistant/helpers/translation.py does in
2026.8.3 to 2026.10.0b1: Arabic is English with Arabic laid over it, and a
request for a single integration returns that integration's own dict.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
sys.path.insert(0, str(PACKAGE))

import arabic_states  # noqa: E402

P = "component.{}.entity_component."

ENGLISH = {
    "light": {"_.state.on": "On", "_.state.off": "Off",
              "_.state_attributes.color_mode.state.hs": "HS"},
    "climate": {"_.state.cool": "Cool", "_.state.off": "Off",
                "_.state_attributes.preset_mode.state.activity": "Activity",
                "_.state_attributes.preset_mode.state.home": "Home",
                "_.state_attributes.swing_horizontal_mode.state.on": "On"},
    "binary_sensor": {"window.state.on": "Open", "door.state.on": "Open",
                      "presence.state.on": "Home"},
}
# What HA ships in Arabic for these (read from the 2026.10.0b1 image).
HA_ARABIC = {
    "climate": {"_.state_attributes.preset_mode.state.activity": "السجل",
                "_.state_attributes.preset_mode.state.home": "في المنزل",
                "_.state_attributes.swing_horizontal_mode.state.on": "تشغيل"},
    "binary_sensor": {"presence.state.on": "في المنزل"},
}


def flat(domain: str, words: dict) -> dict:
    return {P.format(domain) + k: v for k, v in words.items()}


class Cache:
    def __init__(self, copies: bool = False):
        self.copies = copies
        self.data: dict[str, dict[str, dict]] = {}
        self.asked: list[tuple] = []

    async def async_get_translations(self, hass, language, category, integrations=None,
                                     config_flow=None):
        self.asked.append((language, category, tuple(integrations or ())))
        assert category == "entity_component"
        (domain,) = integrations
        per = self.data.setdefault(language, {})
        if domain not in per:
            words = dict(flat(domain, ENGLISH.get(domain, {})))
            if language == "ar":
                words.update(flat(domain, HA_ARABIC.get(domain, {})))
            per[domain] = words
        return dict(per[domain]) if self.copies else per[domain]


@pytest.fixture
def cache(monkeypatch):
    def install(copies=False):
        fake = Cache(copies)
        helpers = types.ModuleType("homeassistant.helpers")
        translation = types.ModuleType("homeassistant.helpers.translation")
        translation.async_get_translations = fake.async_get_translations
        helpers.translation = translation
        monkeypatch.setitem(sys.modules, "homeassistant", types.ModuleType("homeassistant"))
        monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
        monkeypatch.setitem(sys.modules, "homeassistant.helpers.translation", translation)
        return fake
    return install


def hass():
    return types.SimpleNamespace(data={})


def arabic(fake, domain, suffix):
    return fake.data["ar"][domain][P.format(domain) + suffix]


def test_owner_words_for_on_and_off():
    """The owner's choice: the state, not the command («تشغيل» is "Turn on")."""
    assert arabic_states.ON == ("On", "مُشغَّل")
    assert arabic_states.OFF == ("Off", "مُطفأ")


def test_window_is_feminine_and_door_masculine():
    sensors = arabic_states.STATES["binary_sensor"]
    assert sensors["window.state.on"][1] == "مفتوحة"
    assert sensors["window.state.off"][1] == "مغلقة"
    assert sensors["door.state.on"][1] == "مفتوح"
    assert sensors["door.state.off"][1] == "مغلق"


def test_every_entry_is_english_then_arabic():
    for domain, entries in arabic_states.STATES.items():
        for suffix, entry in entries.items():
            english, word = entry[0], entry[1]
            assert english.isascii(), (domain, suffix)
            assert any("؀" <= ch <= "ۿ" for ch in word), (domain, suffix)


def test_english_fallback_is_replaced():
    resources = flat("light", ENGLISH["light"])
    changed = arabic_states.apply(resources, "light")
    assert resources[P.format("light") + "_.state.on"] == "مُشغَّل"
    assert resources[P.format("light") + "_.state.off"] == "مُطفأ"
    assert changed == {P.format("light") + "_.state.on": "On",
                       P.format("light") + "_.state.off": "Off"}


def test_keys_not_in_the_table_are_left_alone():
    resources = flat("light", ENGLISH["light"])
    arabic_states.apply(resources, "light")
    assert resources[P.format("light") + "_.state_attributes.color_mode.state.hs"] == "HS"


def test_what_home_assistant_translates_is_never_overridden():
    resources = flat("climate", {**ENGLISH["climate"], **HA_ARABIC["climate"]})
    arabic_states.apply(resources, "climate")
    attrs = P.format("climate") + "_.state_attributes."
    assert resources[attrs + "preset_mode.state.home"] == "في المنزل"
    assert resources[attrs + "swing_horizontal_mode.state.on"] == "تشغيل"


def test_a_listed_mistranslation_is_replaced():
    """HA's Arabic has «السجل» ("the log") for the preset "Activity"."""
    resources = flat("climate", {**ENGLISH["climate"], **HA_ARABIC["climate"]})
    arabic_states.apply(resources, "climate")
    assert resources[P.format("climate") + "_.state_attributes.preset_mode.state.activity"] == "نشاط"


def test_a_key_home_assistant_does_not_have_is_never_added():
    resources = {P.format("light") + "_.state.on": "On"}
    arabic_states.apply(resources, "light")
    assert set(resources) == {P.format("light") + "_.state.on"}


def test_a_reworded_english_is_skipped():
    """If HA changes "Cool" to something else, the table no longer knows what
    it is translating, so it does not."""
    resources = {P.format("climate") + "_.state.cool": "Cooling mode"}
    assert arabic_states.apply(resources, "climate") == {}
    assert resources[P.format("climate") + "_.state.cool"] == "Cooling mode"


def test_applying_twice_changes_nothing_the_second_time():
    resources = flat("light", ENGLISH["light"])
    arabic_states.apply(resources, "light")
    assert arabic_states.apply(resources, "light") == {}


def test_restore_puts_back_only_what_is_still_ours():
    resources = flat("light", ENGLISH["light"])
    changed = arabic_states.apply(resources, "light")
    resources[P.format("light") + "_.state.off"] = "something else"
    assert arabic_states.restore(resources, "light", changed) == 1
    assert resources[P.format("light") + "_.state.on"] == "On"
    assert resources[P.format("light") + "_.state.off"] == "something else"


def test_not_a_dict_is_ignored():
    assert arabic_states.apply(None, "light") == {}
    assert arabic_states.restore(None, "light", {"k": "v"}) == 0


def test_setup_corrects_home_assistants_arabic_cache(cache):
    fake = cache()
    h = hass()
    asyncio.run(arabic_states.async_setup(h))
    assert arabic(fake, "light", "_.state.on") == "مُشغَّل"
    assert arabic(fake, "climate", "_.state.cool") == "تبريد"
    assert arabic(fake, "binary_sensor", "window.state.on") == "مفتوحة"
    assert arabic(fake, "binary_sensor", "presence.state.on") == "في المنزل"
    # Only ever Arabic: English is not loaded, let alone changed.
    assert {language for language, _, _ in fake.asked} == {"ar"}
    assert "en" not in fake.data


def test_a_copy_from_home_assistant_is_a_no_op(cache):
    """If HA ever hands out a copy, nothing reaches it, and nothing is
    remembered as changed, so unloading has nothing to put back."""
    fake = cache(copies=True)
    h = hass()
    asyncio.run(arabic_states.async_setup(h))
    assert arabic(fake, "light", "_.state.on") == "On"
    assert not h.data.get(arabic_states._DATA)


def test_unload_puts_home_assistants_words_back(cache):
    fake = cache()
    h = hass()
    asyncio.run(arabic_states.async_setup(h))
    asyncio.run(arabic_states.async_unload(h))
    assert arabic(fake, "light", "_.state.on") == "On"
    assert arabic(fake, "climate", "_.state_attributes.preset_mode.state.activity") == "السجل"
    assert arabic(fake, "climate", "_.state_attributes.preset_mode.state.home") == "في المنزل"


def test_setup_never_raises_when_home_assistant_does(cache, monkeypatch):
    fake = cache()

    async def broken(*args, **kwargs):
        raise RuntimeError("translations moved")

    sys.modules["homeassistant.helpers.translation"].async_get_translations = broken
    asyncio.run(arabic_states.async_setup(hass()))
    assert fake.data == {}


def test_setup_without_the_helper_does_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.translation", None)
    asyncio.run(arabic_states.async_setup(hass()))
