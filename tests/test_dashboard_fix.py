"""The Dwains corrections in www/dashboard-fix.js act only on a Dwains that needs them.

Dwains Dashboard Next fixed dwains-dashboard-next#18, #19 and #20 in v1.8.1
(2026-09-30). Homes still on 1.8.0 keep the agent's workarounds; homes on
1.8.1 or later must not have them act at all, because a second move of the
Add card picker, or a second `setConfig` into its editor, is the agent working
against a Dwains that already does the right thing. Dwains exposes no version
number to the page, so the module tells the two apart by what Dwains does:
only a release before 1.8.1 puts the picker on `document.body`, and only one
before 1.8.1 builds its views without `home_custom_cards`.

These run the real module in Node against a small stand-in for the browser
(tests/js/dashboard_fix_harness.js), once per Dwains behaviour. The same is
measured in real browsers against real Dwains releases by
tests/live/run_live_dwains.py.

The same module isolates a value and its unit when the page is right to left
(dartec-ha-manager#55), so 22.0 °C stops reading "C° 22.0" in Arabic, and
leaves left-to-right pages exactly as Home Assistant drew them. The harness
gives it Home Assistant's own elements as Lit renders them, in Arabic and in
English; tests/live/run_live_dashboards.py measures where the unit is drawn
in real Home Assistant.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "custom_components" / "dartec_ha_manager" / "www" / "dashboard-fix.js"
HARNESS = ROOT / "tests" / "js" / "dashboard_fix_harness.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node.js")


def run(scenario: str) -> dict:
    done = subprocess.run([NODE, str(HARNESS), str(FIX), scenario], capture_output=True,
                          text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_dwains_1_8_0_picker_is_moved_inside_home_assistant():
    """#18 on 1.8.0: the picker Dwains put on document.body ends up where Home
    Assistant gives its cards their formatters."""
    got = run("old-picker")
    assert got["where"] == "home-assistant"
    assert got["mounts"] == 2  # Dwains' own, then the move


def test_dwains_1_8_0_editor_is_handed_its_config_back():
    """#19 on 1.8.0: Dwains never hands it back, so the module does, once per
    change, with what Dwains merged."""
    got = run("old-picker")
    assert got["set_config_calls"] == 2
    assert got["last_config"] == {"type": "thermostat", "entity": "climate.hall", "name": "Hall"}


def test_dwains_1_8_1_picker_is_left_where_dwains_mounts_it():
    """No second move: from 1.8.1 Dwains mounts it inside <home-assistant>."""
    got = run("new-picker")
    assert got["where"] == "home-assistant"
    assert got["mounts"] == 1


def test_dwains_1_8_1_editor_gets_one_set_config_per_change_dwains_own():
    """No second hand-back: from 1.8.1 Dwains calls setConfig itself, and the
    module must not repeat it."""
    got = run("new-picker")
    assert got["set_config_calls"] == 2  # two changes, Dwains' call for each


def test_a_change_without_a_card_type_is_never_handed_back_on_1_8_1():
    """Dwains 1.8.1 deliberately ignores a change that has no card type. The
    ungated module handed the old card back there, overriding that."""
    assert run("new-picker")["untyped_change_calls"] == 0


def test_dwains_1_8_0_home_custom_cards_are_carried_to_the_layout_card():
    """#20 on 1.8.0: both generators dropped the key."""
    got = run("old-strategy")
    assert len(got["view_strategy"]["home_custom_cards"]) == 1
    assert len(got["layout_card"]["home_custom_cards"]) == 1
    assert got["writes"] == 2


def test_dwains_1_8_1_strategies_are_returned_as_dwains_built_them():
    """From 1.8.1 Dwains passes the key itself; the module writes nothing."""
    got = run("new-strategy")
    assert got["writes"] == 0
    assert got["layout_card"] == {"type": "custom:dwains-dashboard-next-layout-card",
                                  "home_custom_cards": [{"id": "c1",
                                                         "card": {"type": "markdown"}}]}


FSI, PDI = "\u2068", "\u2069"


def iso(text: str) -> str:
    return FSI + text + PDI


def units(scenario: str) -> list[dict]:
    return run(scenario)["steps"]


def test_rtl_isolates_a_value_and_its_unit():
    """The bug itself: a temperature on its own, in a tile or a badge."""
    got = units("units-rtl")[0]["displays"]
    assert got["sensor"] == iso("22.0 °C")


def test_rtl_isolates_each_value_not_the_whole_line():
    """A climate tile: "تبريد · 23.5 °C". Isolating the whole line left to
    right would put the mode on the wrong side, so only the value is."""
    got = units("units-rtl")[0]["displays"]
    assert got["climate"] == "تبريد · " + iso("23.5 °C")
    assert got["climate_en"] == "Cool · " + iso("23.5 °C")


def test_rtl_handles_arabic_digits_signs_and_grouping():
    got = units("units-rtl")[0]["displays"]
    assert got["arabic_digits"] == iso("٢٢٫٥ °C")
    assert got["negative"] == iso("-3.5 °C")
    assert got["energy"] == iso("1,234.5 kWh")


def test_rtl_leaves_words_names_and_what_ha_isolated_alone():
    got = units("units-rtl")[0]["displays"]
    assert got["words"] == "On"
    assert got["name"] == "Bedroom 2"
    # Home Assistant isolated it itself (bidiIsolate, frontend #54205): once.
    assert got["upstream"] == iso("22.0 °C")


def test_rtl_area_card_line_isolates_every_value_and_not_the_primary():
    step = units("units-rtl")[0]
    assert step["tile_secondary"] == iso("22.0 °C") + " · " + iso("45%")
    assert step["tile_primary"] == "Kitchen 230 W"
    assert step["tile_info_own_updated"]  # its own updated() still runs


def test_rtl_stepper_value_box_is_laid_out_left_to_right():
    """Number and unit are two flex items; only their box turns, and Home
    Assistant's own stylesheet stays first."""
    sheets = units("units-rtl")[0]["stepper_sheets"]
    assert sheets[0] == "home-assistant"
    assert sheets[1:] == [".value { direction: ltr; unicode-bidi: isolate; }"]


def test_rtl_a_second_render_does_not_isolate_twice():
    step = units("units-rtl")[0]
    again = step["repainted"]
    assert again["displays"] == step["displays"]
    assert again["tile_secondary"] == step["tile_secondary"]
    assert again["stepper_sheets"] == step["stepper_sheets"]
    assert again["updates"] == step["updates"] + 1


def test_ltr_is_left_exactly_as_home_assistant_drew_it():
    step = units("units-ltr")[0]
    assert step["displays"] == {
        "sensor": "22.0 °C", "climate": "تبريد · 23.5 °C", "climate_en": "Cool · 23.5 °C",
        "upstream": iso("22.0 °C"), "arabic_digits": "٢٢٫٥ °C", "negative": "-3.5 °C",
        "energy": "1,234.5 kWh", "words": "On", "name": "Bedroom 2"}
    assert step["tile_secondary"] == "22.0 °C · 45%"
    assert step["stepper_sheets"] == ["home-assistant"]
    assert step["repainted"]["displays"] == step["displays"]


def test_switching_language_undoes_and_redoes_the_correction():
    arabic, english, new_value, back = units("units-switch")
    assert arabic["displays"]["sensor"] == iso("22.0 °C")
    # To English with the same value: Home Assistant writes nothing, so the
    # text Home Assistant wrote is put back exactly, and the sheet comes off.
    assert english["displays"]["sensor"] == "22.0 °C"
    assert english["displays"]["climate"] == "تبريد · 23.5 °C"
    assert english["tile_secondary"] == "22.0 °C · 45%"
    assert english["stepper_sheets"] == ["home-assistant"]
    assert new_value["displays"]["sensor"] == "22.5 °C"
    # Back to Arabic, with a value Home Assistant has just written.
    assert back["displays"]["sensor"] == iso("23.0 °C")
    assert back["tile_secondary"] == iso("22.0 °C") + " · " + iso("45%")
    assert back["stepper_sheets"][1:] == [".value { direction: ltr; unicode-bidi: isolate; }"]
