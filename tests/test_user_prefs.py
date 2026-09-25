"""A person's own language and theme, as room panels and My Home set them.

What is pinned here is the shape Home Assistant's frontend reads back: the
`language` user data is the whole locale (the bench wrote exactly this to
switch an account to Arabic), `theme` is the profile page's `{"theme": name}`,
and `null` clears either. Also that nothing the person chose for themselves
is thrown away when Dartec changes the other half, and that a theme the home
does not have is refused rather than silently falling back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import user_prefs as up  # noqa: E402
from user_prefs import Invalid  # noqa: E402

BENCH_ARABIC = {"language": "ar", "number_format": "language", "time_format": "language",
                "date_format": "language", "time_zone": "local",
                "first_weekday": "language"}


def invalid(code, fn, *args):
    with pytest.raises(Invalid) as caught:
        fn(*args)
    assert caught.value.code == code, caught.value.message


class TestLanguage:
    @pytest.mark.parametrize("value,expected", [("en", "en"), ("ar", "ar"), (" AR ", "ar"),
                                                (None, None), ("", None)])
    def test_accepted(self, value, expected):
        assert up.clean_language(value) == expected

    @pytest.mark.parametrize("value", ["fr", "arabic", "ar-AE", 1, True, ["ar"], {"language": "ar"}])
    def test_refused(self, value):
        invalid("language", up.clean_language, value)

    def test_the_value_is_the_one_the_bench_wrote(self):
        """The shape Home Assistant's profile page writes, and the one that
        made the bench mirror right to left."""
        assert up.language_value(None, "ar") == BENCH_ARABIC

    def test_a_person_s_own_formats_are_kept(self):
        mine = {**BENCH_ARABIC, "language": "en", "time_format": "24", "first_weekday": "sunday"}
        value = up.language_value(mine, "ar")
        assert value["language"] == "ar"
        assert value["time_format"] == "24" and value["first_weekday"] == "sunday"

    def test_null_clears_it(self):
        assert up.language_value(BENCH_ARABIC, None) is None

    @pytest.mark.parametrize("stored,expected", [(BENCH_ARABIC, "ar"), (None, None), ({}, None),
                                                 ("ar", None), ({"language": 3}, None)])
    def test_reading_it_back(self, stored, expected):
        assert up.language_of(stored) == expected


class TestTheme:
    LOADED = ["Dartec", "Dartec Glass Lite"]

    def test_a_loaded_theme(self):
        assert up.clean_theme("Dartec Glass Lite", self.LOADED) == "Dartec Glass Lite"

    def test_home_assistant_s_own_is_always_there(self):
        assert up.clean_theme("default", []) == "default"

    @pytest.mark.parametrize("value", [None, ""])
    def test_null_means_the_system_default(self, value):
        assert up.clean_theme(value, self.LOADED) is None

    def test_a_theme_the_home_does_not_have_is_refused(self):
        """Home Assistant would silently show its own default instead, the
        failure mode that made one home read 'DarTec' for weeks."""
        invalid("no_theme", up.clean_theme, "DarTec", self.LOADED)

    @pytest.mark.parametrize("value", [3, ["Dartec"], " ", "x" * 65])
    def test_malformed(self, value):
        invalid("theme", up.clean_theme, value, self.LOADED)

    def test_the_value_is_the_profile_page_s(self):
        assert up.theme_value(None, "Dartec Glass Lite") == {"theme": "Dartec Glass Lite"}

    def test_a_person_s_dark_mode_and_colours_are_kept(self):
        mine = {"theme": "Dartec", "dark": True, "primaryColor": "#1a6b6b"}
        assert up.theme_value(mine, "Dartec Glass Lite") == {
            "theme": "Dartec Glass Lite", "dark": True, "primaryColor": "#1a6b6b"}

    def test_null_clears_it(self):
        assert up.theme_value({"theme": "Dartec"}, None) is None

    @pytest.mark.parametrize("stored,expected", [({"theme": "Dartec"}, "Dartec"), (None, None),
                                                 ({"theme": ""}, None), ({"dark": True}, None)])
    def test_reading_it_back(self, stored, expected):
        assert up.theme_of(stored) == expected
