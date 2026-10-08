"""The branding module in the browser: which lockup each viewer gets, and the tab title.

Runs the module branding.py renders in Node against a small stand-in for Home
Assistant's page (tests/js/branding_harness.js). The sidebar is drawn per
person, so the lockup follows the viewer's own Home Assistant language: the
Latin lockup 26 px tall, the Arabic «بيتك» lockup 30 px. A home branded before
the rename keeps its one logo for everyone, at the height it always had.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_branding import NEW_LOOK, STORED_DARTEC, branding

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "branding_harness.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node.js")


def run(tmp_path, config, home_lang="en", **opts) -> list[dict]:
    script = tmp_path / "branding.js"
    script.write_text(branding._render_js({**branding.DEFAULTS, **config}, home_lang),
                      encoding="utf-8")
    done = subprocess.run([NODE, str(HARNESS), str(script), json.dumps(opts)],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_an_english_viewer_gets_the_latin_lockup_at_26px(tmp_path):
    [got] = run(tmp_path, NEW_LOOK, lang="en")
    assert got["img"] == {"src": "/dartec_branding/baytec-lockup-en-light.svg",
                          "alt": "Baytec", "height": "26px"}
    assert got["title"] == "Overview — Baytec"


def test_an_arabic_viewer_gets_the_arabic_lockup_at_30px(tmp_path):
    [got] = run(tmp_path, NEW_LOOK, lang="ar", title="نظرة عامة")
    assert got["img"] == {"src": "/dartec_branding/baytec-lockup-ar-light.svg",
                          "alt": "بيتك", "height": "30px"}
    assert got["title"] == "نظرة عامة — بيتك"


def test_dark_theme_gets_the_dark_files(tmp_path):
    [en] = run(tmp_path, NEW_LOOK, lang="en", dark=True)
    [ar] = run(tmp_path, NEW_LOOK, lang="ar", dark=True)
    assert en["img"]["src"].endswith("baytec-lockup-en-dark.svg")
    assert ar["img"]["src"].endswith("baytec-lockup-ar-dark.svg")


def test_the_viewer_language_wins_over_the_home_language(tmp_path):
    """An English-speaking member of an Arabic household sees the Latin name."""
    [got] = run(tmp_path, NEW_LOOK, home_lang="ar", lang="en")
    assert got["img"]["src"].endswith("baytec-lockup-en-light.svg")


def test_without_hass_the_page_language_then_the_home_language_decide(tmp_path):
    [page] = run(tmp_path, NEW_LOOK, home_lang="en", lang="", htmlLang="ar")
    [home] = run(tmp_path, NEW_LOOK, home_lang="ar", lang="", htmlLang="")
    assert page["img"]["src"].endswith("baytec-lockup-ar-light.svg")
    assert home["img"]["src"].endswith("baytec-lockup-ar-light.svg")


def test_switching_language_swaps_the_lockup_and_never_stacks_the_tab_name(tmp_path):
    first, arabic, back = run(tmp_path, NEW_LOOK, lang="en", steps=["ar", "en"])
    assert first["title"] == "Overview — Baytec"
    assert arabic["img"]["src"].endswith("baytec-lockup-ar-light.svg")
    assert arabic["title"] == "Overview — بيتك"
    assert back["img"]["src"].endswith("baytec-lockup-en-light.svg")
    assert back["title"] == "Overview — Baytec"


def test_a_stored_dartec_mark_is_unchanged_for_every_viewer(tmp_path):
    for lang in ("en", "ar"):
        [got] = run(tmp_path, STORED_DARTEC, lang=lang)
        assert got["img"] == {"src": "/dartec_branding/dartec-mark-light.svg",
                              "alt": "Dartec", "height": "22px"}
        assert got["title"] == "Overview — Dartec"


def test_text_only_arabic_is_given_line_height_and_is_not_clipped(tmp_path):
    [got] = run(tmp_path, {**NEW_LOOK, "logo": "none"}, lang="ar")
    assert got["img"] is None and got["text"] == "بيتك"
    assert got["lineHeight"] == "1.35" and got["overflow"] == "visible"


def test_the_name_takes_the_place_of_home_assistant_in_the_tab(tmp_path):
    """Home Assistant 2026.9 titles a page "History – Home Assistant" (en
    dash); the name replaces that rather than being added after it."""
    [en] = run(tmp_path, NEW_LOOK, lang="en", title="History – Home Assistant")
    [ar] = run(tmp_path, NEW_LOOK, lang="ar", title="السجل التاريخي – Home Assistant")
    [old] = run(tmp_path, NEW_LOOK, lang="en", title="History — Home Assistant")
    assert en["title"] == "History — Baytec"
    assert ar["title"] == "السجل التاريخي — بيتك"
    assert old["title"] == "History — Baytec"
