"""Blueprint staging: the path guard and the override gate.

Both are claims about blast radius rather than branch coverage, so they are
written the way `test_service_policy.py` is — what can a compromised cloud
reach, and what needs the homeowner's consent. Kept importable without Home
Assistant, which is why `blueprint_cmds` defers its HA imports into the
handler bodies.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from blueprint_cmds import NAMESPACE, normalise_path  # noqa: E402
from helper_cmds import slugify, validate as validate_helper  # noqa: E402
from home_cmds import _validate_automation  # noqa: E402
from service_policy import SENSITIVE_ACTIONS, is_sensitive  # noqa: E402


class TestPathGuard:
    """The cloud can write one shape of path and nothing else."""

    def test_our_namespace_is_accepted(self):
        assert normalise_path("dartec/athan.yaml") == "dartec/athan.yaml"
        assert normalise_path("dartec/prayer_times_v2.yaml") == "dartec/prayer_times_v2.yaml"

    @pytest.mark.parametrize("path", [
        "homeassistant/motion_light.yaml",   # HA's own, shipped with the system
        "someone_else/their_blueprint.yaml",  # the customer's
        "athan.yaml",                        # no namespace at all
        "dartec/nested/athan.yaml",          # deeper than we allow
    ])
    def test_everything_outside_our_namespace_is_refused(self, path):
        """A customer's blueprints and HA's own are not ours to overwrite, and
        the refusal lives here rather than on the server — the threat model is
        our own cloud being compromised."""
        assert normalise_path(path) is None

    @pytest.mark.parametrize("path", [
        "dartec/../../../etc/passwd.yaml",
        "dartec/..%2Fescape.yaml",
        "/dartec/athan.yaml/../../x.yaml",
        "dartec/athan.yaml.bak",             # not a blueprint at all
        "dartec/.yaml",
        "dartec/Athan.yaml",                 # case is part of the shape
        "",
    ])
    def test_traversal_and_junk_are_refused(self, path):
        assert normalise_path(path) is None

    def test_the_guard_is_a_whitelist_not_a_blacklist(self):
        """`..` is the obvious attack; a path that merely looks plausible is
        the dangerous one. Neither gets through, and that is the point of
        matching one shape rather than excluding known-bad ones."""
        assert normalise_path("dartec/athan.yaml") is not None
        assert all(normalise_path(p) is None for p in
                   ("dartec/a b.yaml", "dartec/a-b.yaml", "DARTEC/athan.yaml",
                    f"{NAMESPACE}x/athan.yaml"))


class TestOverrideGate:
    """Staging is inert; overriding runs code in someone's house immediately."""

    def test_staging_a_new_blueprint_needs_no_window(self):
        """A file nothing references defines no automation and runs nothing.
        Gating it would mean the library never gets pre-staged, and the
        expensive work would land in the one short window we get."""
        cmd = {"action": "blueprint_install", "path": "dartec/athan.yaml",
               "yaml": "blueprint: {}"}
        assert is_sensitive(cmd) is False

    def test_overriding_needs_a_window(self):
        """HA reloads every automation using the path the moment the file
        lands — no restart, no further consent. That is remote code
        deployment and it belongs behind the homeowner's switch."""
        cmd = {"action": "blueprint_install", "path": "dartec/athan.yaml",
               "yaml": "blueprint: {}", "allow_override": True}
        assert is_sensitive(cmd) is True

    def test_lying_about_override_only_moves_the_cloud_behind_the_window(self):
        """The flag is safe to read from the command because it is
        self-limiting: claiming an override you do not need costs you the
        window; declining one you do need means HA refuses the write."""
        assert is_sensitive({"action": "blueprint_install", "allow_override": True})
        assert not is_sensitive({"action": "blueprint_install", "allow_override": False})

    def test_reading_and_rendering_are_never_sensitive(self):
        """Neither changes anything on the home, and the preflight is only
        useful if it can run before asking anyone for anything."""
        assert is_sensitive({"action": "blueprint_list"}) is False
        assert is_sensitive({"action": "blueprint_substitute",
                             "path": "dartec/athan.yaml", "input": {}}) is False


class TestSensitivityUnchangedElsewhere:
    """is_sensitive replaced a set membership test at the only call site, so
    every other action must still classify exactly as it did."""

    @pytest.mark.parametrize("action", sorted(SENSITIVE_ACTIONS))
    def test_every_sensitive_action_still_is(self, action):
        assert is_sensitive({"action": action}) is True

    @pytest.mark.parametrize("action", [
        "lovelace_get", "hacs_list", "users_list", "backup_list", "maintenance_status",
    ])
    def test_read_only_actions_still_are_not(self, action):
        assert is_sensitive({"action": action}) is False

    def test_creating_an_automation_is_still_sensitive_from_a_blueprint(self):
        """A blueprint instance is safer to *review* — the logic is ours, not
        model output — but it is still a stored trigger that acts on the
        house, so it keeps the same gate."""
        assert "automation_create" in SENSITIVE_ACTIONS
        assert is_sensitive({"action": "automation_create",
                             "config": {"alias": "x",
                                        "use_blueprint": {"path": "dartec/athan.yaml"}}})


class TestBlueprintAutomationShape:
    """`automation_create` accepts two shapes and must not blur them."""

    def test_a_blueprint_instance_is_accepted_without_triggers_or_actions(self):
        """The whole point: the logic lives in the blueprint, so the config
        that reaches the home carries entity ids and nothing executable."""
        assert _validate_automation({
            "alias": "Athan", "description": "Plays the athan",
            "use_blueprint": {"path": "dartec/athan.yaml",
                              "input": {"media_player": "media_player.kitchen"}}}) is None

    def test_a_written_automation_still_needs_triggers_and_actions(self):
        assert _validate_automation({"alias": "x"}) == \
            "automation needs alias, triggers and actions"

    def test_either_shape_still_needs_an_alias(self):
        """Without one the automation shows up nameless in the customer's own
        editor, which is where they are supposed to be able to audit us."""
        assert _validate_automation(
            {"use_blueprint": {"path": "dartec/athan.yaml"}}) == "automation needs an alias"

    def test_a_config_cannot_be_both_shapes_at_once(self):
        """Smuggling actions alongside a blueprint would put model output back
        into the executable path while looking like the reviewed case."""
        refusal = _validate_automation({
            "alias": "x", "use_blueprint": {"path": "dartec/athan.yaml"},
            "actions": [{"action": "shell_command.rm_rf"}]})
        assert refusal and "cannot also set" in refusal and "actions" in refusal

    @pytest.mark.parametrize("used", [None, {}, {"input": {}}, {"path": "  "}, "dartec/x.yaml"])
    def test_use_blueprint_must_name_a_path(self, used):
        assert _validate_automation({"alias": "x", "use_blueprint": used}) \
            == "use_blueprint needs a path"

    def test_inputs_must_be_an_object(self):
        assert _validate_automation({
            "alias": "x",
            "use_blueprint": {"path": "dartec/athan.yaml", "input": ["kitchen"]}}) \
            == "use_blueprint input must be an object"

    def test_an_instance_may_point_outside_our_namespace(self):
        """Deliberate: HA's own motion_light blueprint is a perfectly good
        thing for an installer to use. The namespace guard exists to stop us
        *overwriting* other people's blueprints, not to stop us using them —
        and a blueprint we cannot write is one whose logic we never chose."""
        assert _validate_automation({
            "alias": "Hall light",
            "use_blueprint": {"path": "homeassistant/motion_light.yaml",
                              "input": {}}}) is None


class TestHelperCreation:
    """The one command that makes a new object in a customer's home."""

    def test_our_namespace_is_accepted(self):
        assert validate_helper("input_boolean", "dartec_athan_playing",
                               "Dartec athan playing") is None

    @pytest.mark.parametrize("slug", [
        "athan_playing",            # no namespace — could collide with theirs
        "dartec-athan",             # not an entity-id shape
        "Dartec_Athan",
        "dartec_",
        "",
    ])
    def test_helpers_outside_our_namespace_are_refused(self, slug):
        assert validate_helper("input_boolean", slug, "Dartec athan playing")

    def test_only_input_boolean(self):
        """A toggle executes nothing. Widen this deliberately, per domain, not
        by passing whatever the server asked for."""
        assert validate_helper("input_text", "dartec_x", "Dartec x")
        assert validate_helper("script", "dartec_x", "Dartec x")

    def test_a_name_that_would_not_produce_the_slug_is_refused(self):
        """The caller predicts the entity_id from the slug and feeds it to a
        blueprint as an input. If the name would slug differently, that
        prediction is wrong and the automation points at nothing."""
        refusal = validate_helper("input_boolean", "dartec_athan_playing",
                                  "Athan playing")
        assert refusal and "not 'dartec_athan_playing'" in refusal

    @pytest.mark.parametrize("name,expected", [
        ("Dartec athan playing", "dartec_athan_playing"),
        ("Dartec  Athan   Playing", "dartec_athan_playing"),
        ("Dartec athan (playing)", "dartec_athan_playing"),
        ("  Dartec athan playing  ", "dartec_athan_playing"),
    ])
    def test_slugify_matches_what_ha_will_name_the_entity(self, name, expected):
        assert slugify(name) == expected

    def test_creating_a_helper_needs_no_window(self):
        """Same treatment as the room and floor commands: it adds an entity,
        it does not act on the house."""
        assert is_sensitive({"action": "helper_create", "domain": "input_boolean",
                             "slug": "dartec_athan_playing"}) is False

    def test_nothing_here_can_delete(self):
        """Stated as a test because it is a promise about the module, not an
        implementation detail: there is no delete path to reach."""
        import helper_cmds

        assert set(helper_cmds.HANDLERS) == {"helper_create"}
        assert not any("delete" in name for name in dir(helper_cmds))


class TestBlueprintInputsReported:
    """What `blueprint_list` reports so the manager can refuse a breaking
    upgrade. Mirrored on the server, which parses the new version's YAML — the
    two must agree on what counts as an input."""

    def test_plain_inputs_and_their_defaults(self):
        from blueprint_cmds import flatten_inputs

        assert flatten_inputs({"speaker": {"name": "Speaker", "selector": {}},
                               "volume": {"name": "Volume", "default": 0.5}}) == {
            "speaker": {"has_default": False}, "volume": {"has_default": True}}

    def test_sections_are_flattened_not_counted(self):
        """A collapsible section is not an input called 'advanced'."""
        from blueprint_cmds import flatten_inputs

        assert flatten_inputs({"speaker": {"name": "S"},
                               "advanced": {"name": "Advanced",
                                            "input": {"fade": {"default": 3}}}}) == {
            "speaker": {"has_default": False}, "fade": {"has_default": True}}

    def test_a_default_of_none_is_still_a_default(self):
        """`default:` with no value is how blueprints make an input optional."""
        from blueprint_cmds import flatten_inputs

        assert flatten_inputs({"x": {"default": None}}) == {"x": {"has_default": True}}

    def test_nothing_or_junk_is_empty_not_an_error(self):
        from blueprint_cmds import flatten_inputs

        assert flatten_inputs(None) == {}
        assert flatten_inputs({"x": "not a dict"}) == {"x": {"has_default": False}}

    def test_reading_consumers_is_not_gated(self):
        """It runs straight after an upgrade to check nothing broke, and must
        not need a second round of consent to find out."""
        assert is_sensitive({"action": "blueprint_consumers",
                             "path": "dartec/athan.yaml"}) is False


class TestConsumerIds:
    """The ids an upgrade names so a broken automation is still looked at."""

    def test_automation_ids_are_accepted(self):
        from blueprint_cmds import validate_automation_ids

        assert validate_automation_ids([]) is None
        assert validate_automation_ids(["automation.athan", "automation.hall_light_2"]) is None

    @pytest.mark.parametrize("ids", [
        "automation.athan",                 # not a list
        ["light.kitchen"],                  # a read of states, but only automations
        ["automation.../x"],
        [{"entity_id": "automation.x"}],
        ["automation.x"] * 501,
    ])
    def test_anything_else_is_refused(self, ids):
        from blueprint_cmds import validate_automation_ids

        assert validate_automation_ids(ids)
