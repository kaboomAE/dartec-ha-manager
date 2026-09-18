"""The rules behind "My Home", the homeowner's household panel.

Written as claims about who can be locked out of a house, and by whom, rather
than as branch coverage: the owner, Dartec's support account and Home
Assistant's own accounts are untouchable, the home never ends up with nobody
who can manage it, nobody changes their own role, and the manager learns
numbers and never names. Importable without Home Assistant, like
test_service_policy.py, so CI runs it in seconds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import household as h  # noqa: E402
from household import Refused  # noqa: E402


def user(uid, name, *, username=None, owner=False, groups=("system-users",),
         active=True, local_only=False, system=False):
    return {"id": uid, "name": name, "username": username, "is_owner": owner,
            "is_active": active, "local_only": local_only,
            "system_generated": system, "group_ids": list(groups)}


OWNER = user("u-owner", "Maryam", username="maryam.h", owner=True, groups=("system-admin",))
DARTEC = user("u-dartec", "Dartec", username="dartec", groups=("system-admin",))
SUPERVISOR = user("u-super", "Supervisor", system=True, groups=("system-admin",))
CLOUD = user("u-cloud", "Home Assistant Cloud", system=True, groups=())
ADMIN = user("u-admin", "Omar", username="omar", groups=("system-admin",))
FAMILY = user("u-family", "Layla", username="layla")
GUEST = user("u-guest", "Sam", username="sam", local_only=True)
VIEWER = user("u-view", "Nana", username="nana", groups=("system-read-only",))

HOME = [OWNER, DARTEC, SUPERVISOR, CLOUD, ADMIN, FAMILY, GUEST, VIEWER]
GUESTS = {"u-guest"}
GOOD = "k7mp-wq4t-zr9h"


def refused(code, fn, *args, **kwargs):
    with pytest.raises(Refused) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code, caught.value.message
    return caught.value


class TestWhoIsWho:
    def test_kinds(self):
        assert h.kind(OWNER) == "owner"
        assert h.kind(DARTEC) == "maintenance"
        assert h.kind(SUPERVISOR) == "system"
        assert h.kind(CLOUD) == "system"
        assert h.kind(FAMILY) == "person"

    def test_dartec_before_its_login_exists_is_still_dartec(self):
        # The onboarding app creates the user, then attaches the login.
        assert h.kind(user("u-x", "Dartec", username=None, groups=("system-admin",))) \
            == "maintenance"

    def test_a_person_merely_named_dartec_with_another_login_is_a_person(self):
        assert h.kind(user("u-x", "Dartec", username="dartec.fan")) == "person"

    def test_dartec_username_is_recognised_whatever_its_name(self):
        assert h.kind(user("u-x", "Support", username="Dartec")) == "maintenance"

    def test_system_wins_over_everything(self):
        assert h.kind(user("u-x", "Dartec", username="dartec", system=True)) == "system"

    def test_roles(self):
        roles = {u["id"]: u["role"] for u in h.household(HOME, GUESTS)}
        assert roles == {"u-owner": "owner", "u-admin": "admin", "u-family": "family",
                         "u-guest": "guest", "u-view": "view_only"}

    def test_the_list_hides_dartec_and_system_accounts_and_puts_the_owner_first(self):
        shown = h.household(HOME, GUESTS)
        assert [u["id"] for u in shown][0] == "u-owner"
        assert {"u-dartec", "u-super", "u-cloud"}.isdisjoint(u["id"] for u in shown)

    def test_a_guest_promoted_to_admin_reads_as_admin(self):
        assert h.role(user("u-guest", "Sam", groups=("system-admin",)), GUESTS) == "admin"


class TestTheManagerSeesNumbersOnly:
    def test_counts(self):
        assert h.counts(HOME, GUESTS) == {"owner": 1, "admin": 1, "family": 1, "guest": 1,
                                          "view_only": 1, "paused": 0, "total": 5}

    def test_paused_people_are_counted(self):
        home = [OWNER, {**FAMILY, "is_active": False}]
        assert h.counts(home, ())["paused"] == 1

    def test_no_name_username_or_id_leaks_into_the_counts(self):
        text = repr(h.counts(HOME, GUESTS))
        for u in HOME:
            for value in (u["id"], u["name"], u["username"]):
                if value:
                    assert value not in text


class TestUntouchable:
    """The owner, Dartec's account and Home Assistant's own accounts."""

    @pytest.mark.parametrize("target,code", [(OWNER, "owner"), (DARTEC, "maintenance"),
                                             (SUPERVISOR, "system"), (CLOUD, "system")])
    def test_cannot_be_edited(self, target, code):
        refused(code, h.check_update, OWNER, HOME, target["id"], {"name": "X"}, GUESTS)

    @pytest.mark.parametrize("target,code", [(OWNER, "owner"), (DARTEC, "maintenance"),
                                             (SUPERVISOR, "system")])
    def test_cannot_be_paused(self, target, code):
        refused(code, h.check_update, ADMIN, HOME, target["id"], {"is_active": False}, GUESTS)

    @pytest.mark.parametrize("target,code", [(OWNER, "owner"), (DARTEC, "maintenance"),
                                             (SUPERVISOR, "system"), (CLOUD, "system")])
    def test_cannot_be_removed(self, target, code):
        refused(code, h.check_remove, ADMIN, HOME, target["id"])

    @pytest.mark.parametrize("target,code", [(DARTEC, "maintenance"), (SUPERVISOR, "system")])
    def test_passwords_cannot_be_set(self, target, code):
        refused(code, h.check_reset_password, OWNER, HOME, target["id"], GOOD)

    def test_the_owners_password_cannot_be_set_by_an_admin(self):
        refused("owner", h.check_reset_password, ADMIN, HOME, OWNER["id"], GOOD)

    def test_nobody_can_take_the_dartec_username(self):
        refused("username_reserved", h.check_create, OWNER, HOME,
                {"name": "Me", "username": " DARTEC ", "password": GOOD, "role": "family"})

    def test_dartecs_first_dashboard_is_not_ours_to_set(self):
        refused("maintenance", h.check_dashboard, OWNER, HOME, DARTEC["id"], None, [])


class TestSomeoneCanAlwaysManageTheHome:
    # A home with no owner (never true after onboarding, but Home Assistant
    # allows it), so the last-admin rule is what stands between the family
    # and a house nobody can manage.
    NO_OWNER = [DARTEC, SUPERVISOR, ADMIN, user("u-a2", "Huda", username="huda",
                                                 groups=("system-admin",)), FAMILY]

    def test_one_of_two_admins_can_be_demoted_paused_or_removed(self):
        h.check_update(ADMIN, self.NO_OWNER, "u-a2", {"role": "family"}, ())
        h.check_update(ADMIN, self.NO_OWNER, "u-a2", {"is_active": False}, ())
        h.check_remove(ADMIN, self.NO_OWNER, "u-a2")

    def test_the_last_admin_cannot_be_demoted(self):
        home = [DARTEC, ADMIN, user("u-a2", "Huda", username="huda",
                                    groups=("system-admin",), active=False), FAMILY]
        actor = {**ADMIN, "id": "someone-else"}  # an admin session on its way out
        refused("last_admin", h.check_update, actor, home, ADMIN["id"], {"role": "family"}, ())

    def test_the_last_admin_cannot_be_paused_or_removed(self):
        home = [DARTEC, ADMIN, FAMILY]
        actor = {**ADMIN, "id": "someone-else"}
        refused("last_admin", h.check_update, actor, home, ADMIN["id"],
                {"is_active": False}, ())
        refused("last_admin", h.check_remove, actor, home, ADMIN["id"])

    def test_dartecs_account_does_not_count_as_someone_who_manages_the_home(self):
        # Dartec is an active admin, and still the family's last admin is
        # protected: a home only its installer can manage is not the owner's.
        home = [DARTEC, ADMIN, FAMILY]
        refused("last_admin", h.check_remove, {**ADMIN, "id": "x"}, home, ADMIN["id"])

    def test_with_an_owner_admins_can_come_and_go(self):
        h.check_remove(OWNER, HOME, ADMIN["id"])
        h.check_update(OWNER, HOME, ADMIN["id"], {"role": "guest"}, GUESTS)

    def test_a_paused_owner_does_not_count(self):
        home = [{**OWNER, "is_active": False}, ADMIN, FAMILY]
        refused("last_admin", h.check_update, {**ADMIN, "id": "x"}, home, ADMIN["id"],
                {"role": "family"}, ())

    def test_resuming_the_last_admin_is_fine(self):
        home = [DARTEC, {**ADMIN, "is_active": False}, user("u-a2", "Huda",
                username="huda", groups=("system-admin",))]
        h.check_update(home[2], home, ADMIN["id"], {"is_active": True}, ())


class TestNobodyChangesTheirOwnStanding:
    def test_own_role(self):
        refused("own_role", h.check_update, ADMIN, HOME, ADMIN["id"], {"role": "family"}, GUESTS)

    def test_own_pause(self):
        refused("self_pause", h.check_update, ADMIN, HOME, ADMIN["id"], {"is_active": False},
                GUESTS)

    def test_own_removal(self):
        refused("self_remove", h.check_remove, ADMIN, HOME, ADMIN["id"])

    def test_own_sign_in_location(self):
        refused("self_local", h.check_update, ADMIN, HOME, ADMIN["id"], {"local_only": True},
                GUESTS)

    def test_own_password_goes_through_the_profile(self):
        refused("own_password", h.check_reset_password, OWNER, HOME, OWNER["id"], GOOD)

    def test_own_name_is_fine(self):
        assert h.check_update(ADMIN, HOME, ADMIN["id"], {"name": "Omar K"}, GUESTS) \
            == {"name": "Omar K"}

    def test_own_role_unchanged_is_not_a_change_of_role(self):
        # The edit form sends every field; sending your own role back as it is
        # must not trip the rule.
        assert h.check_update(ADMIN, HOME, ADMIN["id"],
                              {"name": "Omar K", "role": "admin", "is_active": True,
                               "local_only": False}, GUESTS) == {"name": "Omar K"}

    def test_own_first_dashboard_is_fine(self):
        target, board = h.check_dashboard(ADMIN, HOME, ADMIN["id"], "dashboard-kids",
                                          ["dashboard-kids"])
        assert target["id"] == ADMIN["id"] and board == "dashboard-kids"


class TestWhoMayAct:
    def test_dartecs_account_cannot_use_the_panel(self):
        refused("actor_maintenance", h.check_create, DARTEC, HOME,
                {"name": "X", "username": "xx", "password": GOOD, "role": "family"})
        refused("actor_maintenance", h.check_update, DARTEC, HOME, FAMILY["id"],
                {"is_active": False}, GUESTS)
        refused("actor_maintenance", h.check_remove, DARTEC, HOME, FAMILY["id"])

    def test_a_regular_user_cannot_act_even_if_they_reach_it(self):
        refused("actor_not_admin", h.check_remove, FAMILY, HOME, GUEST["id"])

    def test_a_system_account_cannot_act(self):
        refused("actor_not_admin", h.check_remove, SUPERVISOR, HOME, GUEST["id"])

    def test_only_the_owner_sets_someone_elses_password(self):
        # Home Assistant's own rule (admin_change_password), not ours to widen.
        refused("owner_only", h.check_reset_password, ADMIN, HOME, FAMILY["id"], GOOD)
        assert h.check_reset_password(OWNER, HOME, FAMILY["id"], GOOD)["id"] == FAMILY["id"]

    def test_a_person_without_a_login_has_no_password_to_set(self):
        home = [*HOME, user("u-sso", "Tariq")]
        refused("no_login", h.check_reset_password, OWNER, home, "u-sso", GOOD)

    def test_someone_already_gone(self):
        refused("not_found", h.check_remove, OWNER, HOME, "u-nobody")


class TestAddingAPerson:
    def base(self, **over):
        return {"name": "Ahmed", "username": "ahmed", "password": GOOD,
                "role": "family", **over}

    def test_clean_values(self):
        assert h.check_create(OWNER, HOME, self.base(name="  Ahmed   Ali ",
                                                    username=" Ahmed.Ali ")) == {
            "name": "Ahmed Ali", "username": "ahmed.ali", "password": GOOD,
            "role": "family", "local_only": False}

    def test_a_guest_is_always_local_only(self):
        assert h.check_create(OWNER, HOME, self.base(role="guest", local_only=False))[
            "local_only"] is True

    def test_family_may_be_local_only(self):
        assert h.check_create(OWNER, HOME, self.base(local_only=True))["local_only"] is True

    @pytest.mark.parametrize("role", ["owner", "view_only", "system-admin", "", None])
    def test_only_the_three_roles_can_be_given(self, role):
        refused("role_invalid", h.check_create, OWNER, HOME, self.base(role=role))

    def test_usernames_are_unique_ignoring_case(self):
        refused("username_taken", h.check_create, OWNER, HOME, self.base(username="LAYLA"))

    @pytest.mark.parametrize("username", ["a", "has space", "أحمد", "-dash", "x" * 33, ""])
    def test_usernames_are_typeable(self, username):
        refused("username_invalid", h.check_create, OWNER, HOME, self.base(username=username))

    @pytest.mark.parametrize("password,code", [
        ("short1", "password_short"),
        ("aaaaaaaaaaaa", "password_simple"),
        ("ahmed-2024!", "password_personal"),
        ("x" * 129, "password_long"),
        (12345678, "password_short"),
    ])
    def test_plainly_unsafe_passwords(self, password, code):
        refused(code, h.check_create, OWNER, HOME, self.base(password=password))

    def test_names_are_needed(self):
        refused("name_required", h.check_create, OWNER, HOME, self.base(name="   "))

    def test_admins_may_add_people(self):
        h.check_create(ADMIN, HOME, self.base())


class TestEditing:
    def test_making_someone_a_guest_makes_them_local_only(self):
        assert h.check_update(OWNER, HOME, FAMILY["id"], {"role": "guest"}, GUESTS) \
            == {"role": "guest", "local_only": True}

    def test_a_guest_cannot_be_allowed_to_sign_in_from_anywhere(self):
        refused("no_change", h.check_update, OWNER, HOME, GUEST["id"],
                {"local_only": False}, GUESTS)

    def test_nothing_changed(self):
        refused("no_change", h.check_update, OWNER, HOME, FAMILY["id"],
                {"name": "Layla", "role": "family", "is_active": True}, GUESTS)

    def test_view_only_can_be_moved_to_family(self):
        assert h.check_update(OWNER, HOME, VIEWER["id"], {"role": "family"}, GUESTS) \
            == {"role": "family"}

    def test_pause_and_resume(self):
        assert h.check_update(OWNER, HOME, FAMILY["id"], {"is_active": False}, GUESTS) \
            == {"is_active": False}
        home = [OWNER, {**FAMILY, "is_active": False}]
        assert h.check_update(OWNER, home, FAMILY["id"], {"is_active": True}, ()) \
            == {"is_active": True}


class TestDashboards:
    def test_unset_means_the_homes_default(self):
        assert h.check_dashboard(OWNER, HOME, FAMILY["id"], None, [])[1] is None
        assert h.check_dashboard(OWNER, HOME, FAMILY["id"], "", [])[1] is None

    def test_only_dashboards_that_exist(self):
        refused("dashboard_unknown", h.check_dashboard, OWNER, HOME, FAMILY["id"],
                "config", ["dartec-home"])
        refused("dashboard_unknown", h.check_dashboard, OWNER, HOME, FAMILY["id"],
                "../x", ["../x"])
        assert h.check_dashboard(OWNER, HOME, FAMILY["id"], "dartec-home",
                                 ["dartec-home"])[1] == "dartec-home"


class TestTheLogbookLine:
    def test_says_who_did_what(self):
        assert h.summary("create", "Maryam", "Sam", {"role": "guest", "local_only": True}) \
            == "Maryam added Sam as Guest, signing in only at home"
        assert h.summary("update", "Maryam", "Sam", {"is_active": False}) \
            == "Maryam paused Sam"
        assert h.summary("update", "Maryam", "Sam", {"role": "admin", "local_only": False}) \
            == "Maryam made Sam Family (can manage the home), and let Sam sign in from anywhere"
        assert h.summary("remove", "Maryam", "Sam") == "Maryam removed Sam from the household"
        assert h.summary("dashboard", "Maryam", "Sam", {"title": "Kids"}) \
            == "Maryam set the first dashboard Sam sees to 'Kids'"

    def test_never_carries_a_password(self):
        line = h.summary("password", "Maryam", "Sam", {"password": GOOD, "signed_out": True})
        assert GOOD not in line
        assert line == "Maryam set a new password for Sam and signed them out everywhere"
