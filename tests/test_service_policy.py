"""The service allowlist.

This is the module that decides what a compromised cloud could do inside a
customer's house, so the tests below are written as claims about that blast
radius rather than as coverage of branches. Kept importable without Home
Assistant, like test_version.py, so CI runs it in seconds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from service_policy import (  # noqa: E402
    OPT_OFFSITE_BACKUPS, SENSITIVE_ACTIONS, check_call_service, check_opt_in,
    classify, is_sensitive, validate_target)


def call(domain, service, **service_data):
    return {"action": "call_service", "domain": domain, "service": service,
            "service_data": service_data}


class TestPermanentlyBlocked:
    """The tier no consent flow can unlock."""

    @pytest.mark.parametrize("domain,service", [
        ("shell_command", "anything_at_all"),
        ("python_script", "run"),
        ("hassio", "addon_stdin"),
        ("hassio", "host_shutdown"),
        ("homeassistant", "stop"),
    ])
    def test_blocked_even_with_an_open_window(self, domain, service):
        assert classify(domain, service) == "never"
        refusal = check_call_service(call(domain, service), maintenance_open=True)
        assert refusal and "permanently blocked" in refusal

    def test_an_open_window_is_not_a_bypass(self):
        """The whole point of the tier: consent must not reach arbitrary code."""
        closed = check_call_service(call("shell_command", "x"), maintenance_open=False)
        opened = check_call_service(call("shell_command", "x"), maintenance_open=True)
        assert closed and opened


class TestDefaultDeny:
    @pytest.mark.parametrize("domain,service", [
        ("media_player", "play_media"),
        ("hassio", "restore_full"),
        ("some_custom_integration", "do_thing"),
    ])
    def test_unlisted_pairs_are_refused(self, domain, service):
        assert classify(domain, service) == "unlisted"
        refusal = check_call_service(call(domain, service, entity_id="x.y"),
                                     maintenance_open=True)
        assert refusal and "not on the agent's allowlist" in refusal

    @pytest.mark.parametrize("domain,service", [("", "turn_on"), ("light", ""), ("", "")])
    def test_missing_names_fail_closed(self, domain, service):
        assert classify(domain, service) == "never"

    def test_non_dict_service_data_is_refused(self):
        cmd = {"action": "call_service", "domain": "light", "service": "turn_on",
               "service_data": ["not", "an", "object"]}
        assert check_call_service(cmd, maintenance_open=True)


class TestRoutine:
    def test_commissioning_calls_need_no_window(self):
        assert check_call_service(call("light", "turn_on", entity_id="light.hall"),
                                  maintenance_open=False) is None

    def test_reloads_need_no_entity(self):
        assert check_call_service(call("automation", "reload"),
                                  maintenance_open=False) is None


class TestSensitive:
    @pytest.mark.parametrize("domain,service", [
        ("lock", "unlock"),
        ("alarm_control_panel", "alarm_disarm"),
        ("cover", "open_cover"),
        ("climate", "set_temperature"),
    ])
    def test_refused_without_a_window(self, domain, service):
        assert classify(domain, service) == "sensitive"
        refusal = check_call_service(call(domain, service, entity_id=f"{domain}.front"),
                                     maintenance_open=False)
        assert refusal and "maintenance window" in refusal

    @pytest.mark.parametrize("domain,service", [
        ("lock", "unlock"),
        ("alarm_control_panel", "alarm_disarm"),
        ("cover", "open_cover"),
    ])
    def test_allowed_with_a_window(self, domain, service):
        assert check_call_service(call(domain, service, entity_id=f"{domain}.front"),
                                  maintenance_open=True) is None

    def test_reboot_is_sensitive_but_shutdown_is_never(self):
        """Rebooting has a route back; powering the house off does not."""
        assert classify("hassio", "host_reboot") == "sensitive"
        assert classify("hassio", "host_shutdown") == "never"


class TestTargeting:
    def test_entity_id_all_is_refused(self):
        refusal = check_call_service(call("light", "turn_off", entity_id="all"),
                                     maintenance_open=False)
        assert refusal and "whole house" in refusal

    def test_entity_id_all_inside_a_target_block_is_refused(self):
        cmd = call("light", "turn_off")
        cmd["service_data"] = {"target": {"entity_id": ["all"]}}
        refusal = check_call_service(cmd, maintenance_open=False)
        assert refusal and "whole house" in refusal

    def test_an_untargeted_call_is_refused(self):
        refusal = check_call_service(call("light", "turn_off"), maintenance_open=False)
        assert refusal and "must name an entity_id" in refusal

    def test_a_list_of_entities_is_fine(self):
        assert validate_target("light", "turn_on",
                               {"entity_id": ["light.a", "light.b"]}) is None

    def test_malformed_entity_ids_are_refused(self):
        assert validate_target("light", "turn_on", {"entity_id": "hall"})

    def test_services_that_take_no_target_are_exempt(self):
        assert validate_target("backup", "create", {}) is None


class TestSensitiveActions:
    @pytest.mark.parametrize("action", [
        "user_create", "user_set_password", "user_delete", "tunnel_setup",
    ])
    def test_account_and_infrastructure_actions_are_gated(self, action):
        """These hand over standing access, so they belong behind the window
        even though they are not service calls."""
        assert action in SENSITIVE_ACTIONS

    def test_reading_the_user_list_is_not_gated(self):
        assert "users_list" not in SENSITIVE_ACTIONS

    def test_writing_an_automation_is_gated(self):
        """An automation is a stored service call. Leaving this open would let
        the cloud write `action: shell_command.x` and have a trigger run it,
        walking around the service allowlist entirely."""
        assert "automation_create" in SENSITIVE_ACTIONS

    @pytest.mark.parametrize("action", ["agent_update", "hacs_install"])
    def test_code_entering_the_home_is_gated(self, action):
        assert action in SENSITIVE_ACTIONS

    def test_offsite_copies_do_not_wait_for_a_window(self):
        """They are meant to run on a schedule with nobody at the house, and a
        window only exists while someone is. Consent for them is the offsite
        opt-in instead; see TestOffsiteBackupOptIn."""
        upload = {"action": "backup_upload", "backup_id": "abc"}
        assert "backup_upload" not in SENSITIVE_ACTIONS
        assert not is_sensitive(upload)

    def test_data_destroyed_in_the_home_is_still_gated(self):
        assert "backup_delete" in SENSITIVE_ACTIONS
        assert is_sensitive({"action": "backup_delete", "backup_id": "abc"})

    def test_taking_a_backup_is_not_gated(self):
        """Creating one harms nothing; the control that matters is on sending
        it offsite, so the action and the service agree."""
        assert "backup_create" not in SENSITIVE_ACTIONS
        assert classify("backup", "create") == "routine"


class TestOffsiteBackupOptIn:
    """Data leaving the house needs the home's agreement, given on the home."""

    UPLOAD = {"action": "backup_upload", "backup_id": "abc",
              "upload_url": "https://manager.example/agent/backup-upload",
              "upload_token": "t"}

    def test_refused_when_the_home_has_not_opted_in(self):
        refusal = check_opt_in(self.UPLOAD, {})
        assert refusal and "offsite" in refusal

    def test_refused_when_the_opt_in_is_off(self):
        assert check_opt_in(self.UPLOAD, {OPT_OFFSITE_BACKUPS: False})

    def test_allowed_once_the_home_opts_in(self):
        assert check_opt_in(self.UPLOAD, {OPT_OFFSITE_BACKUPS: True}) is None

    @pytest.mark.parametrize("value", ["true", "false", 1, "yes"])
    def test_only_a_real_true_counts(self, value):
        """An option that reads as consent by accident is the one mistake this
        must not make."""
        assert check_opt_in(self.UPLOAD, {OPT_OFFSITE_BACKUPS: value})

    def test_unattended_support_is_not_offsite_consent(self):
        """Letting Dartec act in the house is a different agreement from
        letting the house's data leave it."""
        assert check_opt_in(self.UPLOAD, {"unattended_support": True})

    def test_the_command_cannot_opt_itself_in(self):
        """check_opt_in reads options, never the command, for the opt-in."""
        cmd = {**self.UPLOAD, OPT_OFFSITE_BACKUPS: True, "force": True}
        assert check_opt_in(cmd, {})

    @pytest.mark.parametrize("action", ["backup_list", "backup_create",
                                        "backup_delete", "backup_schedule"])
    def test_other_backup_actions_do_not_need_it(self, action):
        assert check_opt_in({"action": action}, {}) is None


class TestTheHomesOwnControls:
    """dartec-ha-manager#11: the cloud must not reach the switches that are
    the home's controls over the cloud."""

    def test_switching_on_the_consent_switch_is_refused(self):
        from service_policy import check_own_entities

        data = {"entity_id": "switch.allow_dartec_support"}
        assert check_call_service(call("switch", "turn_on", **data),
                                  maintenance_open=False) is None  # routine...
        refusal = check_own_entities(data, {"switch.allow_dartec_support"})
        assert refusal and "controlled only from this home" in refusal  # ...but ours

    def test_inside_a_target_block_and_in_a_list(self):
        from service_policy import check_own_entities

        own = {"switch.dartec_renamed"}
        assert check_own_entities({"target": {"entity_id": ["light.a", "switch.DARTEC_renamed"]}}, own)
        assert check_own_entities({"entity_id": "light.a"}, own) is None

    @pytest.mark.parametrize("key", ["area_id", "device_id", "floor_id", "label_id"])
    def test_indirect_targets_are_refused(self, key):
        data = {"entity_id": "light.kitchen", key: "living_room"}
        refusal = check_call_service(call("switch", "turn_on", **data), maintenance_open=True)
        assert refusal and key in refusal
        nested = {"entity_id": "light.kitchen", "target": {key: "x"}}
        assert check_call_service(call("light", "turn_on", **nested), maintenance_open=True)


class TestRoomPanels:
    """Room panel accounts (panels.py, panel_cmds.py)."""

    def test_setting_one_up_needs_consent(self):
        """It creates a login that works on the home network for as long as it
        exists: standing access, like `user_create`."""
        assert "panel_setup" in SENSITIVE_ACTIONS
        assert is_sensitive({"action": "panel_setup", "username": "panel-kitchen"})

    @pytest.mark.parametrize("action", ["panel_update", "panel_remove", "panel_status"])
    def test_the_rest_are_routine(self, action):
        """They touch only panel accounts and grant nothing: an update changes
        a panel's name and dashboard, a removal only takes access away, and a
        status is read only."""
        assert action not in SENSITIVE_ACTIONS
        assert not is_sensitive({"action": action, "user_id": "abc"})

    def test_no_field_moves_setup_out_from_behind_the_gate(self):
        assert is_sensitive({"action": "panel_setup", "force": True,
                             "allow_override": False, "routine": True})
