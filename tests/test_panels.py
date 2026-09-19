"""The rules behind room panel accounts.

Written as claims about whose account the manager can reach without the
home's consent: `panel_update` and `panel_remove` are routine, so the test for
"is this a panel account" is the only thing keeping them off a person's login,
and it is pinned here from every side. The rest is what a panel's screen is
set to (its first dashboard, its hidden sidebar), what the manager is told
about it, and that the password never comes back out. Importable without Home
Assistant, like test_household.py, so CI runs it in seconds.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import panels as p  # noqa: E402
from panels import Refused  # noqa: E402


def user(uid, name, *, username=None, owner=False, groups=("system-users",),
         active=True, local_only=False, system=False):
    return {"id": uid, "name": name, "username": username, "is_owner": owner,
            "is_active": active, "local_only": local_only,
            "system_generated": system, "group_ids": list(groups)}


KITCHEN = user("u-kitchen", "Kitchen panel", username="panel-kitchen", local_only=True)
OWNER = user("u-owner", "Maryam", username="maryam", owner=True, groups=("system-admin",))
# Someone who is not a panel whatever their username says.
ADMIN_PANEL_NAMED = user("u-adm", "Test admin", username="panel-admin", groups=("system-admin",))
OWNER_PANEL_NAMED = user("u-own2", "Owner", username="panel-owner", owner=True)
SYSTEM_PANEL_NAMED = user("u-sys", "Supervisor", username="panel-sys", system=True)
PERSON = user("u-layla", "Layla", username="layla")

HOME = [KITCHEN, OWNER, ADMIN_PANEL_NAMED, OWNER_PANEL_NAMED, SYSTEM_PANEL_NAMED, PERSON]
PASSWORD = "k7mp-wq4t-zr9h-x2df"


def refused(code, fn, *args, **kwargs):
    with pytest.raises(Refused) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code, caught.value.message
    return caught.value


class TestWhoIsAPanel:
    def test_a_panel_account(self):
        assert p.is_panel_account(KITCHEN)

    def test_a_read_only_panel_account_is_still_one(self):
        assert p.is_panel_account(user("u", "x", username="panel-x",
                                       groups=("system-read-only",)))

    @pytest.mark.parametrize("account", [ADMIN_PANEL_NAMED, OWNER_PANEL_NAMED,
                                         SYSTEM_PANEL_NAMED, PERSON, OWNER])
    def test_the_prefix_alone_is_never_enough(self, account):
        """The routine commands may delete a panel account without consent, so
        an administrator, the owner or Home Assistant's own account called
        panel-something must not read as one."""
        assert not p.is_panel_account(account)

    def test_no_login_is_no_panel(self):
        assert not p.is_panel_account(user("u", "panel-kitchen", username=None))

    def test_admin_given_as_a_dict_group_is_recognised(self):
        """`config/auth/list` has given groups as objects in some versions."""
        assert not p.is_panel_account(user("u", "x", username="panel-x",
                                           groups=({"id": "system-admin"},)))


class TestTheUsername:
    @pytest.mark.parametrize("username", ["panel-kitchen", "panel-1", "panel-a-b-2",
                                          "panel-" + "a" * 26, " Panel-Kitchen "])
    def test_accepted(self, username):
        assert p.clean_username(username) == username.strip().lower()

    @pytest.mark.parametrize("username", ["kitchen", "panel-", "panel--x", "panel-_x",
                                          "panel-kit.chen", "panel-kitchen!", "panel-" + "a" * 27,
                                          "panelkitchen", "", None, "panel-ki tchen"])
    def test_refused(self, username):
        refused("invalid", p.clean_username, username)

    def test_at_most_thirty_two_characters(self):
        assert len("panel-" + "a" * 26) == 32


class TestThePassword:
    def test_twelve_characters_is_the_floor(self):
        p.check_password("a" * 12)
        refused("invalid", p.check_password, "a" * 11)

    @pytest.mark.parametrize("value", [None, 123456789012345, ["x" * 20]])
    def test_not_a_string(self, value):
        refused("invalid", p.check_password, value)

    def test_the_refusal_never_contains_it(self):
        short = "s3cr3t-pw"
        err = refused("invalid", p.check_password, short)
        assert short not in err.message and short not in str(err)

    def test_scrub_cuts_it_out_of_an_error(self):
        assert PASSWORD not in p.scrub(f"bad password {PASSWORD}", PASSWORD)
        assert p.scrub("nothing here", PASSWORD) == "nothing here"
        assert p.scrub("x", None) == "x"


class TestSetupIsIdempotentButNeverTakesOver:
    def test_a_new_username_is_created(self):
        assert p.plan_setup(HOME, "panel-lounge") is None

    def test_the_same_panel_again_is_updated(self):
        assert p.plan_setup(HOME, "panel-kitchen") is KITCHEN

    @pytest.mark.parametrize("username", ["panel-admin", "panel-owner", "panel-sys"])
    def test_a_username_held_by_someone_else_is_taken(self, username):
        refused("username_taken", p.plan_setup, HOME, username)


class TestOnlyPanelsAreTouched:
    def test_a_panel_is_found(self):
        assert p.find_panel(HOME, "u-kitchen") is KITCHEN

    def test_gone_is_none(self):
        assert p.find_panel(HOME, "u-nobody") is None

    @pytest.mark.parametrize("uid", ["u-owner", "u-adm", "u-own2", "u-sys", "u-layla"])
    def test_anyone_else_is_refused(self, uid):
        refused("not_panel", p.find_panel, HOME, uid)


PANELS = {
    "lovelace": {"require_admin": False},
    "map": {"require_admin": False},
    "logbook": {"require_admin": False},
    "history": {"require_admin": False},
    "energy": {"require_admin": False},
    "config": {"require_admin": True},
    "dartec-household": {"require_admin": True},
    "dartec-room-kitchen": {"require_admin": False},
    "dartec-room-lounge": {"require_admin": False},
    "staff-only": {"require_admin": True},
}
DASHBOARDS = {k: v for k, v in PANELS.items()
              if k in ("dartec-room-kitchen", "dartec-room-lounge", "staff-only")}


class TestTheScreen:
    def test_everything_a_non_admin_sees_is_hidden_except_the_room(self):
        hidden = p.hidden_panels(PANELS, "dartec-room-kitchen")
        assert hidden == ["dartec-room-lounge", "energy", "history", "logbook",
                          "lovelace", "map"]

    def test_admin_only_panels_are_not_listed(self):
        """A non-admin is never sent them, so there is nothing to hide."""
        hidden = p.hidden_panels(PANELS, "dartec-room-kitchen")
        assert not {"config", "dartec-household", "staff-only"} & set(hidden)

    def test_the_sidebar_setting(self):
        value = p.sidebar_value(PANELS, "dartec-room-kitchen")
        assert value["panelOrder"] == ["dartec-room-kitchen"]
        assert "dartec-room-kitchen" not in value["hiddenPanels"]

    def test_the_first_dashboard_keeps_other_core_settings(self):
        assert p.core_value({"showAdvanced": True, "default_panel": "lovelace"},
                            "dartec-room-kitchen") == {"showAdvanced": True,
                                                       "default_panel": "dartec-room-kitchen"}
        assert p.core_value(None, "x-y") == {"default_panel": "x-y"}

    def test_the_dashboard_must_exist(self):
        refused("no_dashboard", p.check_dashboard, "dartec-room-attic", DASHBOARDS)

    def test_an_admin_only_dashboard_is_refused(self):
        refused("no_dashboard", p.check_dashboard, "staff-only", DASHBOARDS)

    def test_a_room_dashboard_is_fine(self):
        p.check_dashboard("dartec-room-kitchen", DASHBOARDS)

    @pytest.mark.parametrize("value", [None, "", "../etc", "Dartec Room", 5])
    def test_a_malformed_url_path(self, value):
        refused("no_dashboard", p.clean_url_path, value)


class TestWhatTheManagerIsTold:
    def test_a_panel_that_never_signed_in(self):
        row = p.status_row(KITCHEN, [], "dartec-room-kitchen")
        assert row == {"user_id": "u-kitchen", "username": "panel-kitchen",
                       "name": "Kitchen panel", "is_active": True, "local_only": True,
                       "default_panel": "dartec-room-kitchen", "signed_in": False,
                       "last_used_at": None}

    def test_the_latest_sign_in_is_reported(self):
        now = datetime(2026, 9, 19, 10, tzinfo=timezone.utc)
        tokens = [{"token_type": "normal", "last_used_at": now - timedelta(days=2)},
                  {"token_type": "normal", "last_used_at": now},
                  {"token_type": "normal", "last_used_at": None}]
        row = p.status_row(KITCHEN, tokens, None)
        assert row["signed_in"] is True
        assert row["last_used_at"] == now.isoformat()

    def test_system_and_long_lived_tokens_are_not_sign_ins(self):
        later = datetime(2026, 9, 20, tzinfo=timezone.utc)
        tokens = [{"token_type": "system", "last_used_at": later},
                  {"token_type": "long_lived_access_token", "last_used_at": later}]
        row = p.status_row(KITCHEN, tokens, None)
        assert row["signed_in"] is False and row["last_used_at"] is None

    def test_no_address_is_ever_reported(self):
        tokens = [{"token_type": "normal", "last_used_at": None,
                   "last_used_ip": "192.168.1.20"}]
        assert "192.168.1.20" not in str(p.status_row(KITCHEN, tokens, None))
