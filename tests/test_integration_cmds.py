"""Integration setup: what gets submitted to a config flow, and what never does.

The rule under test is "no guessing". A config flow is Home Assistant asking a
question about somebody's house; the manager may answer only what a catalogue
entry declared, and must stop and say so when asked anything else.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from integration_cmds import (ALREADY_DONE, plan_step, valid_domain,  # noqa: E402
                              validate_answers)
from service_policy import is_sensitive  # noqa: E402


class TestPlanStep:

    def test_supplied_answers_are_submitted(self):
        fields = [{"name": "location", "required": True}]
        payload, missing = plan_step(fields, {"location": "Ajman"})
        assert payload == {"location": "Ajman"} and missing == []

    def test_a_required_field_nobody_answered_is_missing(self):
        """The whole point: stop and name it, rather than submit an empty or
        invented value into someone's configuration."""
        fields = [{"name": "latitude", "required": True},
                  {"name": "longitude", "required": True}]
        payload, missing = plan_step(fields, {})
        assert payload == {} and missing == ["latitude", "longitude"]

    def test_a_required_field_with_a_default_is_not_missing(self):
        """HA has an answer of its own; leave it to HA rather than invent one."""
        fields = [{"name": "name", "required": True, "default": "Alarmo"}]
        payload, missing = plan_step(fields, {})
        assert payload == {} and missing == []

    def test_defaults_are_not_copied_into_the_payload(self):
        """Submitting a default back would freeze today's default into the
        entry; omitting it lets HA apply its own."""
        fields = [{"name": "interval", "required": False, "default": 30}]
        assert plan_step(fields, {})[0] == {}

    def test_optional_fields_are_never_missing(self):
        assert plan_step([{"name": "note", "optional": True}], {})[1] == []

    def test_answers_for_fields_the_step_does_not_ask_are_not_sent(self):
        """An answer meant for a later step must not leak into this one."""
        payload, _ = plan_step([{"name": "a", "required": True}],
                               {"a": 1, "b": 2})
        assert payload == {"a": 1}

    def test_an_acknowledgement_checkbox_is_not_ticked_on_anyone_s_behalf(self):
        """HACS's own setup asks the person to confirm they understand four
        things. A required boolean with no default is a question for a human."""
        fields = [{"name": "acc_logs", "required": True, "type": "boolean"}]
        assert plan_step(fields, {})[1] == ["acc_logs"]


class TestAnswers:

    @pytest.mark.parametrize("answers", [None, {}, {"a": "x", "b": 1, "c": 2.5, "d": True},
                                         {"zones": ["home", "work"]}])
    def test_plain_values_are_accepted(self, answers):
        assert validate_answers(answers) is None

    @pytest.mark.parametrize("answers", [
        "a string",
        {"a": {"nested": 1}},
        {"a": [{"nested": 1}]},
        {"": 1},
    ])
    def test_structures_are_refused(self, answers):
        assert validate_answers(answers)


class TestDomain:

    @pytest.mark.parametrize("domain", ["alarmo", "scheduler", "sun2", "hacs"])
    def test_real_domains(self, domain):
        assert valid_domain(domain)

    @pytest.mark.parametrize("domain", ["", "../etc", "Alarmo", "a.b", "a b", "a/b"])
    def test_junk(self, domain):
        assert not valid_domain(domain)


class TestGates:

    def test_setting_an_integration_up_needs_consent(self):
        """It runs the integration's code and writes configuration into the
        house — the step that makes installed code actually do something."""
        assert is_sensitive({"action": "integration_setup", "domain": "alarmo"})

    def test_checking_status_does_not(self):
        """It is polled while waiting for a home to settle, so gating it would
        also mean logging it on every poll."""
        assert not is_sensitive({"action": "integration_status", "domain": "alarmo"})

    def test_only_benign_aborts_count_as_done(self):
        assert ALREADY_DONE == {"already_configured", "single_instance_allowed"}
