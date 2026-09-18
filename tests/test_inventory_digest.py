"""The entity inventory digest, and the larger pages the inventory fetch uses.

The manager keeps a full copy of every home's entities and refetches it only
when this digest moves. Two ways that goes wrong: a digest that moves with
state (a full refetch every minute, from every home), and one that stays put
when the inventory really changed (a stale copy, trusted indefinitely).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from registry_paging import (MAX_INVENTORY_PAGE, MAX_PAGE, inventory_digest,  # noqa: E402
                             paginate_rows)


def row(entity_id, **extra):
    return {"entity_id": entity_id, "name": entity_id, "domain": entity_id.split(".")[0],
            "platform": "demo", "device_class": None, "area": None, "device_id": "d1",
            "entity_category": None, "disabled": False, "hidden": False,
            "state": "on", **extra}


class TestTheDigest:
    def test_state_does_not_move_it(self):
        assert inventory_digest([row("light.a", state="on")]) == \
            inventory_digest([row("light.a", state="off")])

    def test_order_does_not_move_it(self):
        assert inventory_digest([row("light.a"), row("light.b")]) == \
            inventory_digest([row("light.b"), row("light.a")])

    def test_an_added_entity_does(self):
        assert inventory_digest([row("light.a")]) != \
            inventory_digest([row("light.a"), row("light.b")])

    def test_a_rename_a_move_or_a_disable_does(self):
        base = inventory_digest([row("light.a")])
        assert inventory_digest([row("light.a", name="Porch")]) != base
        assert inventory_digest([row("light.a", area="Majlis")]) != base
        assert inventory_digest([row("light.a", disabled=True)]) != base

    def test_an_entity_leaving_the_registry_does(self):
        assert inventory_digest([row("light.a")]) != \
            inventory_digest([row("light.a", registered=False)])

    def test_it_is_short(self):
        assert len(inventory_digest([])) == 16


class TestInventoryPages:
    ROWS = [row(f"sensor.s{i:04d}") for i in range(2500)]

    def test_an_ordinary_page_is_still_capped(self):
        assert len(paginate_rows(self.ROWS, "entities", limit=5000)["items"]) == MAX_PAGE

    def test_an_inventory_page_is_larger(self):
        page = paginate_rows(self.ROWS, "entities", limit=5000, max_page=MAX_INVENTORY_PAGE)
        assert len(page["items"]) == MAX_INVENTORY_PAGE

    def test_paging_by_what_came_back_reaches_every_row(self):
        seen, offset = [], 0
        while True:
            page = paginate_rows(self.ROWS, "entities", offset=offset, limit=1000,
                                 max_page=MAX_INVENTORY_PAGE)
            if not page["items"]:
                break
            seen += [r["entity_id"] for r in page["items"]]
            offset += len(page["items"])
        assert sorted(seen) == sorted(r["entity_id"] for r in self.ROWS)
