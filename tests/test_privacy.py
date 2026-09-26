"""Household names never reach the manager (dartec-ha-manager#20).

Written as claims about a synthetic home with an English- and an
Arabic-speaking family: nothing the manager is sent names anyone, in any
spelling; what the manager sends back reaches the right person and the
right phone; and nothing that is not a name is touched. Importable without
Home Assistant, like test_household.py.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import household  # noqa: E402
import privacy as p  # noqa: E402

SECRET = bytes(range(32))


def user(uid, name, username=None, *, owner=False, system=False, groups=("system-users",)):
    return {"id": uid, "name": name, "username": username, "is_owner": owner,
            "is_active": True, "system_generated": system, "group_ids": list(groups)}


USERS = [
    user("u-1", "Maryam Al Hashimi", "maryam.h", owner=True, groups=("system-admin",)),
    user("u-2", "Omar", "omar", groups=("system-admin",)),
    user("u-3", "Layla", "layla"),
    user("u-4", "ليلى", "laila"),
    user("u-5", "عمر الهاشمي", "omar.hashimi"),
    user("u-dartec", "Dartec", "dartec", groups=("system-admin",)),
    user("u-panel", "Kitchen panel", "panel-kitchen"),
    user("u-super", "Supervisor", system=True, groups=("system-admin",)),
    user("u-cloud", "Home Assistant Cloud", system=True, groups=()),
]
PERSONS = [
    {"entity_id": "person.maryam_al_hashimi", "name": "Maryam Al Hashimi", "user_id": "u-1"},
    {"entity_id": "person.omar", "name": "Omar", "user_id": "u-2"},
    {"entity_id": "person.layla", "name": "Layla", "user_id": "u-3"},
    {"entity_id": "person.lyl", "name": "ليلى", "user_id": "u-4"},
    # A child with a tracker and no account; renamed since, so the entity id
    # still carries the old name.
    {"entity_id": "person.yousef", "name": "Yusuf", "user_id": None},
    # A person someone linked to the panel's account: not a member.
    {"entity_id": "person.kitchen_panel", "name": "Kitchen panel", "user_id": "u-panel"},
]

# Every spelling of a household name that must never reach the manager.
FORBIDDEN = re.compile(
    r"maryam|hashimi|omar|layla|laila|yousef|yusuf|ليلى|الهاشمي"
    # Omar in Arabic, but not Amr (عمرو), a different name the home also has.
    r"|عمر(?!و)", re.IGNORECASE)


def entity(entity_id, name, **extra):
    return {"entity_id": entity_id, "name": name, "domain": entity_id.split(".")[0],
            "platform": extra.pop("platform", None), "area": extra.pop("area", None),
            "state": extra.pop("state", "on"), **extra}


SNAPSHOT = {
    "core": {"version": "2026.9.2", "location_name": "Hashimi Villa", "language": "ar"},
    "entities": [
        entity("person.maryam_al_hashimi", "Maryam Al Hashimi", state="home"),
        entity("person.omar", "Omar", state="not_home"),
        entity("person.layla", "Layla", state="home"),
        entity("person.lyl", "ليلى", state="home"),
        entity("person.yousef", "Yusuf", state="home"),
        entity("device_tracker.laylas_iphone", "Layla's iPhone", platform="mobile_app",
               state="home"),
        entity("sensor.laylas_iphone_battery_level", "Layla's iPhone Battery level",
               platform="mobile_app", state="81"),
        entity("device_tracker.omar_pixel", "Omar Pixel", platform="mobile_app"),
        entity("light.omar_bedroom", "Omar bedroom", area="غرفة عمر"),
        entity("light.kitchen", "Kitchen", area="Kitchen"),
        entity("sensor.sun_next_dawn", "Next dawn"),
        entity("climate.majlis", "المجلس", area="المجلس"),
        entity("light.amr_study", "مكتب عمرو", area="مكتب عمرو"),
        entity("switch.panel_kitchen_screen", "Kitchen panel screen", platform="mobile_app"),
    ],
    "unregistered_entities": [entity("person.guest_yaml", "Yusuf")],
    "devices": [
        {"id": "d1", "name": "Layla's iPhone", "manufacturer": "Apple", "model": "iPhone 15"},
        {"id": "d2", "name": "هاتف ليلى", "manufacturer": "Samsung", "model": "S24"},
        {"id": "d3", "name": "Kitchen panel", "manufacturer": "Lenovo", "model": "Tab M10"},
    ],
    "areas": [{"id": "omars_room", "name": "غرفة عمر"}, {"id": "kitchen", "name": "Kitchen"}],
    "notify_targets": ["mobile_app_laylas_iphone", "mobile_app_omar_pixel",
                       "mobile_app_kitchen_panel", "persistent_notification"],
    "automations": {"recent": [{"entity_id": "automation.wake_omar", "name": "Wake Omar",
                                "last_triggered": "2026-09-27T05:00:00"}]},
    "logs": [
        {"level": "WARNING", "name": "homeassistant.components.http.ban",
         "message": "Login attempt failed for user Layla from 192.168.1.20", "count": 2},
        {"level": "ERROR", "name": "homeassistant.components.mobile_app",
         "message": "Failed to send to مصباح ليلى (notify.mobile_app_laylas_iphone)", "count": 1},
        {"level": "ERROR", "name": "custom_components.x",
         "message": "User maryam.h has no permission", "count": 1},
        {"level": "ERROR", "name": "homeassistant.components.zha",
         "message": "Device 00:11 on Home Assistant did not answer; admin notified", "count": 1},
    ],
    "household": {"owner": 1, "admin": 1, "family": 3, "total": 5},
    "hacs_token": {"token_fingerprint": "8b5eada14c0ffee1"},
    "entity_inventory_digest": "0123456789abcdef",
}


def make(secret=SECRET):
    names, entity_ids = p.household_names(USERS, PERSONS, household.kind)
    return p.Pseudonymiser(secret, names, entity_ids)


def dump(node) -> str:
    return json.dumps(node, ensure_ascii=False)


def rows(snap, key="entities"):
    return {row["entity_id"]: row for row in snap[key]}


# ---- nothing the manager is sent names anyone --------------------------------


def test_a_scrubbed_snapshot_names_nobody_in_any_spelling():
    out = make().scrub_snapshot(SNAPSHOT)
    found = FORBIDDEN.findall(dump(out))
    assert found == [], found


def test_the_original_snapshot_is_left_as_it_was():
    before = copy.deepcopy(SNAPSHOT)
    make().scrub_snapshot(SNAPSHOT)
    assert SNAPSHOT == before


def test_people_are_still_there_as_stable_ids_with_their_state():
    out = rows(make().scrub_snapshot(SNAPSHOT))
    people = {eid: row for eid, row in out.items() if eid.startswith("person.")}
    assert len(people) == 5
    for entity_id, row in people.items():
        assert p.ALIAS_RE.fullmatch(entity_id.split(".", 1)[1]), entity_id
        assert p.ALIAS_RE.search(row["name"]), row
    # "that a person's device is home" survives, without saying whose.
    assert sorted(row["state"] for row in people.values()) == [
        "home", "home", "home", "home", "not_home"]


def test_a_renamed_person_is_hidden_by_their_old_entity_id_too():
    out = rows(make().scrub_snapshot(SNAPSHOT))
    assert "person.yousef" not in out
    assert not any("yousef" in eid for eid in out)


def test_the_same_name_gets_the_same_id_every_time_and_everywhere():
    one, two = make().scrub_snapshot(SNAPSHOT), make().scrub_snapshot(SNAPSHOT)
    assert one == two
    ids = rows(one)
    phone = next(eid for eid in ids if eid.startswith("device_tracker.") and "iphone" in eid)
    battery = next(eid for eid in ids if eid.startswith("sensor.") and "iphone" in eid)
    alias = p.ALIAS_RE.search(phone).group(0)
    assert alias in battery
    assert f"mobile_app_{alias}_iphone" in one["notify_targets"]
    # "Layla" in a sentence and `layla` in an id are the same person.
    layla = p.ALIAS_RE.search(ids["person." + make().alias("layla")]["name"]).group(0)
    assert layla == make().alias("layla")


def test_ids_depend_on_the_homes_own_secret():
    """Nobody holding a list of common names can hash their way back."""
    import hashlib

    ours, theirs = make(), make(secret=b"another home entirely, 32 bytes")
    assert ours.alias("layla") != theirs.alias("layla")
    assert ours.alias("layla")[3:] not in hashlib.sha256(b"layla").hexdigest()


def test_arabic_names_are_hidden_as_arabic_words_and_nothing_longer():
    out = make().scrub_snapshot(SNAPSHOT)
    by_id = rows(out)
    assert "ليلى" not in dump(out)
    assert "عمر" not in by_id["light.omar_bedroom".replace("omar", make().alias("omar"))]["area"]
    # Amr (عمرو) is a different name that happens to start with Omar's.
    assert by_id["light.amr_study"]["name"] == "مكتب عمرو"
    assert by_id["climate.majlis"]["name"] == "المجلس"


def test_everything_that_is_not_a_name_is_untouched():
    out = make().scrub_snapshot(SNAPSHOT)
    ids = rows(out)
    assert ids["light.kitchen"] == SNAPSHOT["entities"][9]
    assert ids["sensor.sun_next_dawn"]["name"] == "Next dawn"
    # Dartec's account and a room panel are not people, and the manager
    # needs to read them.
    assert "switch.panel_kitchen_screen" in ids
    assert "mobile_app_kitchen_panel" in out["notify_targets"]
    assert out["devices"][2]["name"] == "Kitchen panel"
    assert out["logs"][3]["message"] == SNAPSHOT["logs"][3]["message"]
    assert out["household"] == SNAPSHOT["household"]
    assert out["hacs_token"] == SNAPSHOT["hacs_token"]
    assert out["core"]["version"] == "2026.9.2"
    assert out["privacy"] == {"version": p.PRIVACY_VERSION}


def test_log_lines_keep_their_meaning_without_the_name():
    logs = make().scrub_snapshot(SNAPSHOT)["logs"]
    assert logs[0]["message"] == f"Login attempt failed for user {make().alias('layla')} from 192.168.1.20"
    assert logs[0]["count"] == 2 and logs[0]["name"] == SNAPSHOT["logs"][0]["name"]


def test_the_inventory_digest_moves_when_the_names_do():
    out = make().scrub_snapshot(SNAPSHOT)
    assert out["entity_inventory_digest"] != SNAPSHOT["entity_inventory_digest"]
    assert len(out["entity_inventory_digest"]) == 16
    # The same in a snapshot and in a registry_query reply, or the manager
    # would refetch its copy every minute.
    assert make().scrub({"inventory_digest": "0123456789abcdef"})["inventory_digest"] \
        == out["entity_inventory_digest"]
    alone = p.Pseudonymiser(SECRET, ["Zainab"])
    assert alone.scrub(SNAPSHOT)["entity_inventory_digest"] != out["entity_inventory_digest"]


def test_the_users_list_reply_names_nobody_but_keeps_staff_accounts_readable():
    reply = {"ok": True, "users": [
        {"id": u["id"], "name": u["name"], "username": u["username"]}
        for u in USERS if not u["system_generated"]]}
    out = make().scrub(reply)
    assert FORBIDDEN.findall(dump(out)) == []
    by_id = {u["id"]: u for u in out["users"]}
    assert by_id["u-dartec"]["username"] == "dartec"
    assert by_id["u-panel"]["username"] == "panel-kitchen"
    assert by_id["u-3"]["id"] == "u-3"


# ---- what the manager sends back reaches the right person --------------------


def test_a_dashboard_built_on_ids_is_saved_with_the_real_entities():
    filt = make()
    omar, layla = filt.alias("omar"), filt.alias("layla")
    command = {"id": "c1", "action": "lovelace_save", "config": {"views": [{
        "title": f"{omar}'s room",
        "cards": [{"type": "tile", "entity": f"person.{omar}"},
                  {"type": "tile", "entity": f"device_tracker.{filt.alias('laylas')}_iphone",
                   "name": f"{layla}'s phone"}]}]}}
    back = filt.reveal(command)
    view = back["config"]["views"][0]
    assert view["title"] == "Omar's room"
    assert view["cards"][0]["entity"] == "person.omar"
    assert view["cards"][1]["entity"] == "device_tracker.laylas_iphone"
    assert view["cards"][1]["name"] == "Layla's phone"


def test_every_id_in_a_snapshot_turns_back_into_what_the_home_has():
    filt = make()
    out = filt.scrub_snapshot(SNAPSHOT)
    assert [row["entity_id"] for row in filt.reveal(out["entities"])] == \
        [row["entity_id"] for row in SNAPSHOT["entities"]]
    assert filt.reveal(out["notify_targets"]) == SNAPSHOT["notify_targets"]


def test_a_live_activity_reaches_the_phone():
    filt = make()
    target = next(t for t in filt.scrub(SNAPSHOT["notify_targets"]) if "iphone" in t)
    command = {"action": "call_service", "domain": "notify", "service": target,
               "data": {"message": "Washing done"}}
    assert filt.reveal(command)["service"] == "mobile_app_laylas_iphone"


def test_ids_it_did_not_issue_and_secrets_are_left_alone():
    filt = make()
    alias = filt.alias("layla")
    command = {"action": "hacs_token_set", "token": f"github_pat_x_{alias}_y",
               "note": "hm_0000000000 is nobody"}
    back = filt.reveal(command)
    assert back["token"] == command["token"]
    assert back["note"] == command["note"]


# ---- whose names ---------------------------------------------------------------


def test_only_the_household_is_hidden():
    names, entity_ids = p.household_names(USERS, PERSONS, household.kind)
    assert "Dartec" not in names and "dartec" not in names
    assert "Kitchen panel" not in names and "panel-kitchen" not in names
    assert "Supervisor" not in names and "Home Assistant Cloud" not in names
    assert "person.kitchen_panel" not in entity_ids
    assert {"Maryam Al Hashimi", "maryam.h", "Omar", "Layla", "ليلى", "عمر الهاشمي",
            "Yusuf"} <= set(names)


def test_a_person_called_home_or_admin_does_not_garble_the_house():
    filt = p.Pseudonymiser(SECRET, ["Home", "Admin", "Owner", "Bin"])
    text = {"state": "home", "message": "Home Assistant admin owner bin", "id": "not_home"}
    assert filt.scrub(text) == text


def test_a_short_name_is_hidden_whole_but_not_inside_other_words():
    filt = p.Pseudonymiser(SECRET, ["Mo"], ["person.mo"])
    out = filt.scrub({"a": "person.mo", "b": "Mo's phone", "c": "sensor.motion_mode"})
    assert out["a"] == f"person.{filt.alias('mo')}"
    assert out["b"] == f"{filt.alias('mo')}'s phone"
    assert out["c"] == "sensor.motion_mode"


def test_no_household_means_nothing_changes():
    filt = p.Pseudonymiser(SECRET)
    assert filt.scrub(SNAPSHOT) == SNAPSHOT
    assert filt.reveal({"x": "hm_0123456789"}) == {"x": "hm_0123456789"}


def test_a_reply_is_scrubbed_once_with_the_names_before_and_after_a_command():
    """A registry_query reply's digest has to match the snapshot's, or the
    manager refetches its inventory every minute; and a person renamed by
    the command is hidden under both names."""
    before = make()
    digest = {"inventory_digest": "0123456789abcdef"}
    assert before.merged(make()).scrub(digest) == before.scrub(digest)
    renamed = before.merged(p.Pseudonymiser(SECRET, ["Layla Karim"]))
    out = renamed.scrub({"old": "Layla's iPhone", "new": "Karim's iPad"})
    assert "Layla" not in out["old"] and "Karim" not in out["new"]
