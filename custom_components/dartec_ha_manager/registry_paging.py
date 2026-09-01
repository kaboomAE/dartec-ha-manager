"""Filtering, counting and slicing a registry listing.

Kept free of Home Assistant imports so it can be tested on its own, like
service_policy and version — and it needs testing, because this is where the
counting bugs live rather than in the registry access that feeds it.

The bug that motivated the module: the manager reported "2,500 of 2,500 shown"
for a home with 4,100 entities. Nothing crashed. The denominator was simply the
length of the array that had been handed over, and the snapshot caps that array
at 2,500 — so the UI was reporting the size of its own truncated copy as though
it were the size of the home.
"""
from __future__ import annotations

MAX_PAGE = 200
DEFAULT_PAGE = 100


def _matches_query(haystack: tuple, needle: str) -> bool:
    if not needle:
        return True
    return needle in " ".join(str(part).lower() for part in haystack if part)


def _entity_status(row: dict) -> str:
    if row.get("disabled"):
        return "disabled"
    if row.get("state") in ("unavailable", "unknown"):
        return "problem"
    if row.get("hidden"):
        return "hidden"
    return "active"


def _device_status(row: dict) -> str:
    if row.get("disabled"):
        return "disabled"
    if not row.get("area"):
        return "unassigned"
    return "active"


def paginate_rows(rows: list[dict], kind: str, *, query: str = "", domain: str = "",
                  status: str = "all", area: str = "", offset: int = 0,
                  limit: int = DEFAULT_PAGE) -> dict:
    """Filter, count and slice — deliberately free of Home Assistant imports.

    Kept pure because this is where the counting bugs live, not in the registry
    access above it. `matched` must be the size of the whole filtered set and
    `facets` must be counted across it too: computing either from the returned
    page is how a UI ends up reporting "12 lights" to a home that has 300.
    """
    needle = str(query or "").strip().lower()
    status = str(status or "all").strip() or "all"
    area = str(area or "").strip()
    domain = str(domain or "").strip()
    # A malformed limit falls back to the default rather than being clamped
    # into range: max(1, -5) is 1, which would page a 4,000-entity registry one
    # row at a time and look like the server had simply stopped returning data.
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE
    if limit <= 0:
        limit = DEFAULT_PAGE
    limit = min(limit, MAX_PAGE)
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0

    if kind == "entities":
        def searchable(row):
            return (row.get("name"), row.get("entity_id"), row.get("area"))

        def facet_of(row):
            return row.get("domain")

        def in_facet(row):
            return row.get("domain") == domain

        status_of = _entity_status
        rows = sorted(rows, key=lambda r: (r.get("domain") or "", r.get("entity_id") or ""))
    else:
        def searchable(row):
            return (row.get("name"), row.get("manufacturer"), row.get("model"), row.get("area"))

        # A device has no domain; its integrations are the equivalent facet.
        # It can legitimately belong to more than one, so the chip counts the
        # first and the filter matches any — a Zigbee device that also reports
        # over MQTT should appear under both.
        def facet_of(row):
            return (row.get("integrations") or [None])[0]

        def in_facet(row):
            return domain in (row.get("integrations") or [])

        status_of = _device_status
        rows = sorted(rows, key=lambda r: ((r.get("name") or "").lower(), r.get("id") or ""))

    total = len(rows)

    def passes_base(row: dict) -> bool:
        if not _matches_query(searchable(row), needle):
            return False
        if status != "all" and status_of(row) != status:
            return False
        if area and (row.get("area") or "") != area:
            return False
        return True

    # Facets are counted before the facet filter is applied, so the chips still
    # show what switching to another domain would get you rather than collapsing
    # to just the one already selected.
    base = [row for row in rows if passes_base(row)]
    facets: dict[str, int] = {}
    for row in base:
        key = facet_of(row)
        if key:
            facets[key] = facets.get(key, 0) + 1

    matched = base if not domain else [row for row in base if in_facet(row)]
    page = matched[offset:offset + limit]

    return {
        "ok": True,
        "kind": kind,
        "items": page,
        "total": total,
        "matched": len(matched),
        "offset": offset,
        "limit": limit,
        "facets": dict(sorted(facets.items(), key=lambda kv: (-kv[1], kv[0]))),
        "areas": sorted({row["area"] for row in rows if row.get("area")}),
        "detail": f"{len(page)} of {len(matched)} matching {kind} ({total} total)",
    }
