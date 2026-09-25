"""`lovelace_create`: what it asks Home Assistant to create.

`require_admin` used to be hard-coded to false, so the manager could not make
an admin-only preview of a dashboard before the family saw it
(dartec-ha-manager#65). Both flags are now optional and pass through; left
out, a dashboard is created exactly as before: in the sidebar, open to
everyone. The message is built without Home Assistant, so CI checks it here;
`tests/live/panels_driver.py` checks Home Assistant agrees.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from lovelace_cmds import create_message  # noqa: E402


def test_left_out_it_is_what_it_always_was():
    message = create_message({"url_path": "dartec-home", "title": "Home"})
    assert message == {"type": "lovelace/dashboards/create", "url_path": "dartec-home",
                       "title": "Home", "icon": "mdi:view-dashboard",
                       "show_in_sidebar": True, "require_admin": False}


def test_null_means_left_out():
    message = create_message({"url_path": "dartec-home", "show_in_sidebar": None,
                              "require_admin": None})
    assert message["show_in_sidebar"] is True and message["require_admin"] is False


@pytest.mark.parametrize("require_admin", [True, False])
@pytest.mark.parametrize("show_in_sidebar", [True, False])
def test_both_pass_through(require_admin, show_in_sidebar):
    message = create_message({"url_path": "dartec-preview", "require_admin": require_admin,
                              "show_in_sidebar": show_in_sidebar})
    assert message["require_admin"] is require_admin
    assert message["show_in_sidebar"] is show_in_sidebar


@pytest.mark.parametrize("key", ["require_admin", "show_in_sidebar"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, "yes", [], {}])
def test_anything_but_a_boolean_is_refused(key, value):
    """The string "false" is truthy. Guessing which way a malformed flag was
    meant would publish a preview to the family, or hide a dashboard they
    use, so it is refused and nothing is created."""
    refusal = create_message({"url_path": "dartec-home", key: value})
    assert isinstance(refusal, str) and key in refusal


@pytest.mark.parametrize("url_path", ["", "nohyphen", None, "   "])
def test_home_assistant_s_url_rule_is_checked_first(url_path):
    assert isinstance(create_message({"url_path": url_path, "require_admin": True}), str)


def test_title_and_icon_default_as_before():
    message = create_message({"url_path": " dartec-room-majlis "})
    assert message["url_path"] == "dartec-room-majlis"
    assert message["title"] == "dartec-room-majlis" and message["icon"] == "mdi:view-dashboard"
