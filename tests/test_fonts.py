"""The brand fonts the agent serves (dartec-ha-manager#59).

This repository is public, so every font file in it is published. These
checks pin what may be here: exactly the unmodified OFL files recorded in
www/fonts/README.md, each with its licence, and never Dubai, whose licence
does not allow it. They also pin the two rules the bench proved: Lateef is
Arabic only and set at 150%. Plain file checks, so CI runs them in seconds.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WWW = ROOT / "custom_components" / "dartec_ha_manager" / "www"
FONTS = WWW / "fonts"
FIX = (WWW / "dashboard-fix.js").read_text(encoding="utf-8")
README = (FONTS / "README.md").read_text(encoding="utf-8")

FONT_SUFFIXES = {".woff2", ".woff", ".ttf", ".otf", ".eot"}
SHIPPED = {
    "Lateef-Regular.woff2": "OFL-Lateef.txt",
    "Lateef-Medium.woff2": "OFL-Lateef.txt",
    "Lateef-Bold.woff2": "OFL-Lateef.txt",
    "IBMPlexMono-Medium-Latin1.woff2": "OFL-IBMPlexMono.txt",
}
RESERVED = {
    "OFL-Lateef.txt": 'Reserved Font Names "Lateef" and "SIL"',
    "OFL-IBMPlexMono.txt": 'Reserved Font Name "Plex"',
}
ARABIC_ONLY = "U+0600-06FF, U+0750-077F, U+08A0-08FF, U+FB50-FDFF, U+FE70-FEFF"


def recorded_hashes() -> dict[str, str]:
    return {name: digest for digest, name in
            re.findall(r"^([0-9a-f]{64})\s+(\S+)$", README, flags=re.MULTILINE)}


def test_no_dubai_anywhere_in_the_repository():
    # Its licence (TEC's EULA, clauses 2.2, 3.1(d), 3.1(i)) forbids publishing it.
    found = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
             if ".git" not in p.parts and "dubai" in p.name.lower()
             and p.suffix.lower() in FONT_SUFFIXES]
    assert not found, f"Dubai font files may not be in this public repository: {found}"


def test_the_only_font_files_are_the_recorded_ones():
    fonts = {p.name for p in (ROOT / "custom_components").rglob("*")
             if p.suffix.lower() in FONT_SUFFIXES}
    assert fonts == set(SHIPPED)


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_each_file_is_the_one_whose_provenance_is_recorded(name):
    digest = hashlib.sha256((FONTS / name).read_bytes()).hexdigest()
    assert recorded_hashes().get(name) == digest, (
        f"{name} changed: check it is still unmodified upstream, give it a new "
        "file name, and record its hash in www/fonts/README.md")


@pytest.mark.parametrize("licence", sorted(RESERVED))
def test_each_licence_ships_with_its_fonts(licence):
    text = (FONTS / licence).read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text
    assert RESERVED[licence] in text


def test_the_module_declares_no_file_that_is_not_shipped():
    declared = set(re.findall(r'"([\w.-]+\.woff2)"', FIX)) | set(
        re.findall(r'\$\{BASE\}([\w.-]+\.woff2)', FIX))
    assert declared == set(SHIPPED)


def test_lateef_is_arabic_only_and_set_at_150_percent():
    rule = re.search(r"const lateef = .*?;\n", FIX, flags=re.DOTALL).group(0)
    assert "size-adjust: 150%" in rule
    assert "unicode-range: ${ARABIC}" in rule
    assert f'const ARABIC = "{ARABIC_ONLY}";' in FIX
    assert FIX.count('lateef("') == 3


def test_no_dubai_face_is_declared():
    assert "Dubai" not in re.search(r"const CSS = \[.*?\]\.join", FIX, flags=re.DOTALL).group(0)
