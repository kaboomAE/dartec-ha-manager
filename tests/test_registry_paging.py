"""Counting is the part that goes wrong.

The bug this replaces was not a crash — it was a dashboard confidently
reporting "2,500 of 2,500 shown" for a home with 4,100 entities, because the
denominator came from the array that had been handed to it rather than from
the registry. Every test here is about a count being taken from the whole set
rather than from the page.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

from registry_paging import DEFAULT_PAGE, MAX_PAGE, paginate_rows  # noqa: E402


def entities(count: int, domain: str = "sensor", **overrides) -> list[dict]:
    return [{"entity_id": f"{domain}.thing_{i:04d}", "name": f"Thing {i}",
             "domain": domain, "area": "Kitchen", "state": "on",
             "disabled": False, "hidden": False, **overrides}
            for i in range(count)]


def devices(count: int, integration: str = "zha", **overrides) -> list[dict]:
    return [{"id": f"dev{i:04d}", "name": f"Device {i}", "manufacturer": "Aqara",
             "model": "T1", "area": "Kitchen", "integrations": [integration],
             "disabled": False, **overrides}
            for i in range(count)]


class TestCountsComeFromTheWholeSet:
    def test_matched_is_the_filtered_total_not_the_page_size(self):
        """The original bug, stated directly."""
        page = paginate_rows(entities(4100), "entities", limit=100)
        assert len(page["items"]) == 100
        assert page["matched"] == 4100
        assert page["total"] == 4100

    def test_a_filter_narrows_matched_but_never_total(self):
        rows = entities(300, "light") + entities(700, "sensor")
        page = paginate_rows(rows, "entities", domain="light", limit=50)
        assert page["total"] == 1000, "total is the registry, not the filter"
        assert page["matched"] == 300
        assert len(page["items"]) == 50

    def test_facets_count_the_whole_registry_not_the_page(self):
        """A page of 50 must not report that the home has 50 lights."""
        rows = entities(300, "light") + entities(700, "sensor")
        page = paginate_rows(rows, "entities", limit=50)
        assert page["facets"] == {"sensor": 700, "light": 300}

    def test_facets_survive_selecting_one_of_them(self):
        """Counted before the facet filter, so the chips still show what
        switching to another domain would get you rather than collapsing to
        the single selected one."""
        rows = entities(300, "light") + entities(700, "sensor")
        page = paginate_rows(rows, "entities", domain="light")
        assert page["facets"] == {"sensor": 700, "light": 300}
        assert page["matched"] == 300

    def test_a_search_is_applied_across_everything_not_just_the_first_page(self):
        rows = entities(500, "sensor")
        rows[499]["name"] = "Needle in the last row"
        page = paginate_rows(rows, "entities", query="needle", limit=10)
        assert page["matched"] == 1
        assert page["items"][0]["name"] == "Needle in the last row"


class TestPaging:
    def test_pages_tile_the_matched_set_exactly_once(self):
        rows = entities(250)
        seen = []
        for offset in range(0, 250, 100):
            seen += [r["entity_id"] for r in
                     paginate_rows(rows, "entities", offset=offset, limit=100)["items"]]
        assert len(seen) == 250
        assert len(set(seen)) == 250, "no row appears on two pages"

    def test_ordering_is_stable_so_paging_cannot_skip_or_repeat(self):
        """Paging over an unordered set silently drops rows. The sort has to
        happen before the slice, and has to be total."""
        rows = entities(120, "light") + entities(120, "sensor")
        first = [r["entity_id"] for r in
                 paginate_rows(rows, "entities", limit=200)["items"]]
        again = [r["entity_id"] for r in
                 paginate_rows(list(reversed(rows)), "entities", limit=200)["items"]]
        assert first == again

    def test_an_offset_past_the_end_is_an_empty_page_not_an_error(self):
        page = paginate_rows(entities(30), "entities", offset=500)
        assert page["items"] == []
        assert page["matched"] == 30

    def test_limit_is_clamped_so_one_request_cannot_pull_the_whole_registry(self):
        page = paginate_rows(entities(5000), "entities", limit=99999)
        assert page["limit"] == MAX_PAGE
        assert len(page["items"]) == MAX_PAGE

    @pytest.mark.parametrize("bad", [0, -5, None])
    def test_a_nonsense_limit_falls_back_rather_than_returning_nothing(self, bad):
        page = paginate_rows(entities(300), "entities", limit=bad)
        assert page["limit"] == DEFAULT_PAGE
        assert len(page["items"]) == DEFAULT_PAGE


class TestFilters:
    def test_status_problem_selects_unavailable_and_unknown(self):
        rows = entities(10) + entities(3, state="unavailable") + entities(2, state="unknown")
        assert paginate_rows(rows, "entities", status="problem")["matched"] == 5

    def test_status_active_excludes_disabled_and_hidden(self):
        rows = (entities(10) + entities(4, disabled=True) + entities(3, hidden=True))
        assert paginate_rows(rows, "entities", status="active")["matched"] == 10

    def test_area_filter_is_exact_not_substring(self):
        rows = entities(5) + entities(7, area="Kitchen Extension")
        assert paginate_rows(rows, "entities", area="Kitchen")["matched"] == 5

    def test_areas_are_listed_from_the_registry_not_the_page(self):
        rows = entities(200, area="Kitchen") + entities(200, area="Hallway")
        assert paginate_rows(rows, "entities", limit=10)["areas"] == ["Hallway", "Kitchen"]


class TestDevices:
    def test_devices_are_faceted_by_integration(self):
        rows = devices(40, "zha") + devices(15, "mqtt")
        page = paginate_rows(rows, "devices", limit=10)
        assert page["facets"] == {"zha": 40, "mqtt": 15}
        assert page["total"] == 55

    def test_a_device_on_two_integrations_matches_either(self):
        rows = devices(3, "zha")
        rows[0]["integrations"] = ["zha", "mqtt"]
        assert paginate_rows(rows, "devices", domain="mqtt")["matched"] == 1
        assert paginate_rows(rows, "devices", domain="zha")["matched"] == 3

    def test_unassigned_is_a_device_status(self):
        rows = devices(6) + devices(4, area=None)
        assert paginate_rows(rows, "devices", status="unassigned")["matched"] == 4

    def test_devices_are_searched_on_manufacturer_and_model(self):
        rows = devices(5) + devices(2, manufacturer="Sonoff", model="ZBMINI")
        assert paginate_rows(rows, "devices", query="sonoff")["matched"] == 2
        assert paginate_rows(rows, "devices", query="zbmini")["matched"] == 2


class TestEmptyRegistry:
    def test_an_empty_registry_reports_zeroes_rather_than_failing(self):
        page = paginate_rows([], "entities")
        assert page["total"] == 0 and page["matched"] == 0
        assert page["items"] == [] and page["facets"] == {}
