"""Enrolment codes in the config flow: which input is a code, and what a
refusal says.

The field accepts an enrolment code or a pairing token, and getting that
wrong in one direction sends a pairing token to be "redeemed" (refused), and
in the other stores a one-hour code as the home's permanent credential (the
home silently never reconnects). Kept importable without Home Assistant, like
test_version.py.
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "dartec_ha_manager"
sys.path.insert(0, str(PACKAGE))

from enrolment import ALPHABET, KNOWN_REASONS, error_key, normalize  # noqa: E402


class TestNormalize:
    def test_code_as_the_admin_panel_shows_it(self):
        assert normalize("W8DZ-HF3Q-PDTV-4RAQ") == "W8DZHF3QPDTV4RAQ"

    @pytest.mark.parametrize("typed", ["w8dz-hf3q-pdtv-4raq", " W8DZ HF3Q PDTV 4RAQ ",
                                       "W8DZHF3QPDTV4RAQ", "w8dz\thf3q-pdtv 4raq"])
    def test_forgives_how_a_person_types_it(self, typed):
        assert normalize(typed) == "W8DZHF3QPDTV4RAQ"

    def test_a_pairing_token_is_never_a_code(self):
        for _ in range(2000):
            assert normalize(secrets.token_urlsafe(32)) is None

    @pytest.mark.parametrize("typed", ["", None, "W8DZ-HF3Q-PDTV", "W8DZ-HF3Q-PDTV-4RAQ-A",
                                       "O8DZ-HF3Q-PDTV-4RAQ", "18DZ-HF3Q-PDTV-4RAQ",
                                       "W8DZ_HF3Q_PDTV_4RAQ"])
    def test_rejects_what_the_manager_never_issues(self, typed):
        assert normalize(typed) is None

    def test_alphabet_has_no_lookalikes(self):
        assert not set(ALPHABET) & set("01ILOU")
        assert len(set(ALPHABET)) == len(ALPHABET) == 30


class TestErrorKey:
    @pytest.mark.parametrize("reason", sorted(KNOWN_REASONS))
    def test_manager_reasons_pass_through(self, reason):
        payload = {"detail": {"reason": reason, "message": "..."}}
        assert error_key(410, payload) == reason

    @pytest.mark.parametrize("status,expected", [(404, "enrolment_unsupported"),
                                                 (429, "rate_limited"),
                                                 (401, "invalid_code"),
                                                 (502, "cannot_connect"),
                                                 (500, "cannot_connect")])
    def test_falls_back_on_status(self, status, expected):
        assert error_key(status, None) == expected
        assert error_key(status, {"detail": "Not Found"}) == expected
        assert error_key(status, "<html>proxy error</html>") == expected

    def test_unknown_reason_is_not_trusted_as_a_key(self):
        assert error_key(410, {"detail": {"reason": "something_new"}}) == "cannot_connect"

    @pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
    def test_every_key_has_a_message(self, name):
        errors = json.loads((PACKAGE / name).read_text(encoding="utf-8"))["config"]["error"]
        for key in (*KNOWN_REASONS, "enrolment_unsupported", "cannot_connect", "invalid_token"):
            assert errors.get(key), f"{name} has no message for {key}"
