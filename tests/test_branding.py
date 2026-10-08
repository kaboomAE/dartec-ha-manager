"""The sidebar branding after the rename: Baytec by default, the lockup in the
viewer's language, and every home branded before it left exactly as it was.

ADDED refused "Dar" in the trade name, so the brand is Baytec / «بيتك»
(2026-10-08). The owner's decision for the Home Assistant sidebar is the mark
and the name together, in the household's language: the Latin lockup 26 px
tall, the Arabic one 30 px (below that the dots under ب and ي fuse). These pin
what the agent stores and what it hands the browser module. Homes already
store `{"title": "Dartec", "logo": "mark", ...}` in their options, and must
draw exactly what they drew until someone re-applies branding from the
manager. tests/test_branding_js.py runs the module itself.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


def _stub(name, **attrs):
    module = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        if not hasattr(module, key):
            setattr(module, key, value)
    sys.modules[name] = module
    return module


class _View:
    pass


_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, ServiceCall=object, callback=lambda f: f)
_stub("homeassistant.components")
_stub("homeassistant.components.frontend", add_extra_js_url=lambda hass, url: None)
_stub("homeassistant.components.http", HomeAssistantView=_View,
      StaticPathConfig=lambda *a: a)

ROOT = Path(__file__).resolve().parents[1]
_AGENT = ROOT / "custom_components" / "dartec_ha_manager"
WWW = _AGENT / "www"
if "dartec_ha_manager" not in sys.modules:
    _pkg = types.ModuleType("dartec_ha_manager")
    _pkg.__path__ = [str(_AGENT)]
    sys.modules["dartec_ha_manager"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"dartec_ha_manager._test_{name}",
                                                  _AGENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


branding = _load("branding")

# What a home branded before the rename has in its options, as branding_set
# stored it.
STORED_DARTEC = {"enabled": True, "title": "Dartec", "logo": "mark", "tab_suffix": True}
# What the manager sends for the new look (the contract in the PR).
NEW_LOOK = {"enabled": True, "title": "Baytec", "title_ar": "بيتك",
            "logo": "baytec-lockup", "tab_suffix": True}


class Entry:
    def __init__(self, options=None):
        self.options = dict(options or {})


class Entries:
    def __init__(self, entries):
        self._entries = entries
        self.updates = []

    def async_entries(self, domain):
        return self._entries

    def async_update_entry(self, entry, options=None):
        self.updates.append(options)
        entry.options = dict(options)


class Http:
    async def async_register_static_paths(self, paths):
        pass

    def register_view(self, view):
        pass


class Hass:
    def __init__(self, options=None, language="en"):
        self.data = {}
        self.config = types.SimpleNamespace(language=language)
        self.http = Http()
        self.config_entries = Entries([Entry(options)])

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def payload(config, lang=""):
    return branding._payload({**branding.DEFAULTS, **config}, lang)


def apply(hass, **cmd):
    return asyncio.run(branding.branding_set(hass, cmd))


class TestDefaults:
    def test_the_default_title_is_baytec(self):
        assert branding.DEFAULTS["title"] == "Baytec"

    def test_the_default_logo_is_the_baytec_lockup(self):
        assert branding.DEFAULTS["logo"] == "baytec-lockup"

    def test_branding_stays_off_until_the_manager_turns_it_on(self):
        assert branding.DEFAULTS["enabled"] is False

    def test_turning_it_on_with_nothing_else_gives_baytec_in_both_languages(self):
        hass = Hass()
        result = apply(hass, enabled=True)
        assert result["ok"], result
        stored = hass.config_entries._entries[0].options["branding"]
        assert stored["title"] == "Baytec" and stored["logo"] == "baytec-lockup"
        out = payload(stored)
        assert out["title"] == "Baytec" and out["titleAr"] == "بيتك"


class TestNewLook:
    def test_the_manager_payload_is_accepted_and_stored_as_sent(self):
        hass = Hass()
        result = apply(hass, **NEW_LOOK)
        assert result["ok"], result
        assert hass.config_entries._entries[0].options["branding"] == NEW_LOOK
        assert "'Baytec' / 'بيتك'" in result["detail"]
        assert "baytec-lockup" in result["detail"]

    def test_the_lockup_has_a_latin_and_an_arabic_file_at_their_own_heights(self):
        out = payload(NEW_LOOK)
        assert out["logoBase"] == "/dartec_branding/baytec-lockup-en"
        assert out["logoBaseAr"] == "/dartec_branding/baytec-lockup-ar"
        # The owner's sizes: 26 px Latin, 30 px Arabic, each its own floor.
        assert out["logoHeight"] == 26 and out["logoHeightAr"] == 30
        assert out["title"] == "Baytec" and out["titleAr"] == "بيتك"
        assert out["tabSuffix"] is True

    def test_baytec_alone_still_gets_its_arabic_name(self):
        """A manager that sends only the Latin title still puts «بيتك» in an
        Arabic viewer's tab."""
        out = payload({**NEW_LOOK, "title_ar": ""})
        assert out["titleAr"] == "بيتك"

    def test_another_installer_name_is_not_given_an_arabic_name(self):
        out = payload({**NEW_LOOK, "title": "Villa Systems", "title_ar": ""})
        assert out["titleAr"] == ""

    def test_an_explicit_arabic_title_wins(self):
        out = payload({**NEW_LOOK, "title_ar": "بيتك الذكي"})
        assert out["titleAr"] == "بيتك الذكي"

    def test_the_baytec_mark_is_the_mark_alone_at_the_old_height(self):
        out = payload({**NEW_LOOK, "logo": "baytec-mark"})
        assert out["logoBase"] == "/dartec_branding/baytec-mark"
        assert out["logoBaseAr"] == "" and out["logoHeight"] == 22

    def test_the_home_language_is_carried_as_a_fallback_and_changes_the_stamp(self):
        en, ar = payload(NEW_LOOK, "en"), payload(NEW_LOOK, "ar")
        assert en["lang"] == "en" and ar["lang"] == "ar"
        assert en["stamp"] != ar["stamp"]

    @pytest.mark.parametrize("logo", ["mark", "lockup", "baytec-mark", "baytec-lockup"])
    def test_every_file_a_logo_names_is_shipped(self, logo):
        spec = branding.LOGOS[logo]
        for key in ("file", "file_ar"):
            if spec.get(key):
                for theme in ("light", "dark"):
                    assert (WWW / f"{spec[key]}-{theme}.svg").is_file(), (logo, key, theme)


class TestBackwardCompatibility:
    def test_a_stored_dartec_mark_renders_exactly_as_before(self):
        """The payload an existing home's browser gets is what it got before
        the rename, plus fields an old module ignores."""
        out = payload(STORED_DARTEC)
        assert out["title"] == "Dartec"
        assert out["titleAr"] == ""            # no «بيتك» on a Dartec home
        assert out["logoBase"] == "/dartec_branding/dartec-mark"
        assert out["logoBaseAr"] == ""         # one logo for every viewer
        assert out["logoHeight"] == 22         # the height it always had
        assert out["enabled"] is True and out["tabSuffix"] is True

    def test_a_stored_dartec_lockup_still_draws_the_dartec_lockup(self):
        out = payload({**STORED_DARTEC, "logo": "lockup"})
        assert out["logoBase"] == "/dartec_branding/dartec-lockup"
        assert out["logoHeight"] == 22 and out["logoBaseAr"] == ""

    def test_the_stored_options_are_not_rewritten_at_startup(self):
        hass = Hass(options={"branding": STORED_DARTEC})
        asyncio.run(branding.async_setup_branding(hass, STORED_DARTEC))
        assert hass.config_entries.updates == []
        assert branding._config(hass)["title"] == "Dartec"
        assert branding._config(hass)["logo"] == "mark"

    def test_old_files_are_still_shipped(self):
        for name in ("dartec-mark", "dartec-lockup"):
            for theme in ("light", "dark"):
                assert (WWW / f"{name}-{theme}.svg").is_file()

    def test_an_unknown_stored_logo_falls_back_to_text(self):
        """A value this agent does not know, stored by some later agent and
        then downgraded, draws the name rather than a broken image."""
        out = payload({**STORED_DARTEC, "logo": "something-new"})
        assert out["logoBase"] == "" and out["logoBaseAr"] == ""
        assert out["title"] == "Dartec"

    def test_an_unknown_logo_is_refused_with_a_code_and_nothing_changes(self):
        hass = Hass(options={"branding": STORED_DARTEC})
        result = apply(hass, **{**NEW_LOOK, "logo": "something-new"})
        assert result["ok"] is False and result["code"] == "invalid_logo"
        assert result["logos"] == ["mark", "lockup", "baytec-mark", "baytec-lockup", "none"]
        assert hass.config_entries.updates == []

    def test_re_applying_the_old_look_still_works(self):
        hass = Hass()
        result = apply(hass, **STORED_DARTEC)
        assert result["ok"], result
        assert hass.config_entries._entries[0].options["branding"]["logo"] == "mark"

    def test_a_long_arabic_title_is_cut_like_the_latin_one(self):
        hass = Hass()
        apply(hass, **{**NEW_LOOK, "title_ar": "ب" * 100})
        assert len(hass.config_entries._entries[0].options["branding"]["title_ar"]) == 60


class TestModule:
    def test_the_rendered_module_carries_the_payload(self):
        js = branding._render_js({**branding.DEFAULTS, **NEW_LOOK}, "ar")
        assert "__DARTEC_CONFIG" not in js
        start = js.index("let CFG = ") + len("let CFG = ")
        cfg = json.loads(js[start:js.index(";\n", start)])
        assert cfg == payload(NEW_LOOK, "ar")
        assert '"/dartec_branding/config.json"' in js

    def test_text_in_the_sidebar_is_given_room_for_arabic_dots(self):
        js = branding._render_js({**branding.DEFAULTS, **NEW_LOOK})
        assert 'lineHeight = "1.35"' in js

    def test_the_snapshot_reports_the_logos_this_agent_can_draw(self):
        assert branding.logo_choices() == list(branding.LOGOS)
