"""Version comparison.

Small module, disproportionate blast radius: the result decides whether a
customer's Home Assistant is overwritten with older code and restarted. Kept
importable without Home Assistant so CI can run it in seconds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from version import is_older, parse  # noqa: E402


class TestParse:
    @pytest.mark.parametrize("text,expected", [
        ("0.10.4", (0, 10, 4)),
        ("v0.10.4", (0, 10, 4)),
        (" 1.2.3 ", (1, 2, 3)),
        ("2026.8", (2026, 8)),
        ("1.2.3-beta1", (1, 2, 3)),
        ("main", None),
        ("", None),
        (None, None),
    ])
    def test_it_reads_leading_numbers(self, text, expected):
        assert parse(text) == expected


class TestIsOlder:
    def test_double_digit_minors_are_not_compared_as_text(self):
        """The bug this exists for: as strings, "0.10.4" < "0.9.0", so every
        release after .9 would look like a downgrade."""
        assert is_older("0.9.0", "0.10.4") is True
        assert is_older("0.10.4", "0.9.0") is False

    @pytest.mark.parametrize("older,newer", [
        ("0.10.3", "0.10.4"),
        ("0.9.9", "0.10.0"),
        ("1.0.0", "1.0.1"),
        ("1.9", "1.10"),
        ("0.1.0", "2026.8.1"),
    ])
    def test_ordering(self, older, newer):
        assert is_older(older, newer) is True
        assert is_older(newer, older) is False

    def test_equal_versions_are_not_older(self):
        assert is_older("0.10.4", "0.10.4") is False
        assert is_older("v0.10.4", "0.10.4") is False

    def test_different_lengths_compare_by_value(self):
        assert is_older("1.0", "1.0.0") is False
        assert is_older("1.0", "1.0.1") is True
        assert is_older("1.0.1", "1.0") is False

    @pytest.mark.parametrize("candidate,other", [
        ("main", "0.10.4"),
        ("0.10.4", "main"),
        (None, "0.10.4"),
        ("0.10.4", None),
        ("", ""),
    ])
    def test_unreadable_versions_raise_no_objection(self, candidate, other):
        """False means 'no objection', so an unparseable version never blocks a
        legitimate update — it just loses the downgrade protection."""
        assert is_older(candidate, other) is False
