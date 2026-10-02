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
