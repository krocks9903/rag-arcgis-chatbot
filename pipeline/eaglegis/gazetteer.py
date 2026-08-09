"""Phase 2 geocoding coverage: a curated gazetteer of Estero named developments
and roads.

Each entry maps text aliases to a hand-verified point. It runs as a gap-filler —
only items that have no resolved location are matched — so it can never move a
point the resolver already produced. Deterministic, no network.

Coordinates were confirmed against the meeting record and (for several) the
user's local knowledge. Entry order encodes precedence: a more specific site is
listed before a broader one that shares words with it (e.g. "Island at West Bay"
before "West Bay Club"), and named developments come before roads so a project
name wins over a street it merely mentions.
"""
from __future__ import annotations

import re
from typing import Any

# kind: "development" | "road". suppress_if: if any phrase is present in the
# item text, this entry does not apply (belt-and-suspenders alongside ordering).
GAZETTEER: list[dict[str, Any]] = [
    # --- named developments -------------------------------------------------
    # override: force every island item onto this one verified point, replacing
    # any resolver parcel point, so the site is represented consistently.
    {"name": "Island at West Bay", "kind": "development", "override": True,
     "lat": 26.4194877270308, "lng": -81.81238500604107,
     "aliases": ["island at west bay", "island club tower", "island high rise",
                 "island high-rise", "west bay island"]},
    {"name": "West Bay Club", "kind": "development",
     "lat": 26.423473, "lng": -81.834817,
     "aliases": ["west bay club"], "suppress_if": ["island"]},
    {"name": "Estero Town Commons Place", "kind": "development",
     "lat": 26.430139, "lng": -81.786333,
     "aliases": ["estero town center", "estero town commons", "estero town common"]},
    {"name": "Estero Crossing", "kind": "development",
     "lat": 26.430908, "lng": -81.783054,
     "aliases": ["estero crossing"]},
    {"name": "Rivercreek", "kind": "development",
     "lat": 26.444700, "lng": -81.744019,
     "aliases": ["rivercreek", "corkscrew crossing"]},
    {"name": "Brooks Town Center", "kind": "development",
     "lat": 26.396990, "lng": -81.785900,
     "aliases": ["brooks town center", "coconut pointe residences",
                 "residences at brooks"]},
    {"name": "South Estero Commercial Center", "kind": "development",
     "lat": 26.428410, "lng": -81.808570,
     "aliases": ["south estero commercial center"]},
    {"name": "Miromar Square", "kind": "development",
     "lat": 26.436763, "lng": -81.776686,
     "aliases": ["miromar square", "miromar outlets", "miromar mall",
                 "miromar international design center", "miromar design center"]},
    {"name": "Grand Oaks Shoppes", "kind": "development",
     "lat": 26.441117, "lng": -81.760466,
     "aliases": ["grand oaks shoppes"]},
    {"name": "Sunny Groves", "kind": "development",
     "lat": 26.438000, "lng": -81.811300,
     "aliases": ["sunny groves"]},
    # --- roads (mapped to a representative centerline point) -----------------
    {"name": "River Ranch Road", "kind": "road",
     "lat": 26.425800, "lng": -81.794638,
     "aliases": ["river ranch road", "river ranch rd"]},
    {"name": "Walden Center Drive", "kind": "road",
     "lat": 26.394271, "lng": -81.811897,
     "aliases": ["walden center drive", "walden center dr"]},
    {"name": "North Commons Drive", "kind": "road",
     "lat": 26.396045, "lng": -81.814720,
     "aliases": ["north commons drive", "north commons dr"]},
]

_COMPILED = [
    (e, [re.compile(rf"\b{re.escape(a)}\b") for a in e["aliases"]],
     [re.compile(rf"\b{re.escape(s)}\b") for s in e.get("suppress_if", [])])
    for e in GAZETTEER
]

_ITEM_TEXT_FIELDS = ("item_title", "project_title", "summary", "outcome",
                     "action_taken", "item_text")


def match_gazetteer(text: str) -> dict[str, Any] | None:
    """Return the first (highest-precedence) gazetteer entry whose alias appears
    in *text* and whose suppressors do not, or None."""
    lo = (text or "").lower()
    if not lo:
        return None
    for entry, alias_res, suppress_res in _COMPILED:
        if any(s.search(lo) for s in suppress_res):
            continue
        if any(a.search(lo) for a in alias_res):
            return entry
    return None


def _has_coords(row: dict) -> bool:
    lat = str(row.get("latitude") or "").strip().lower()
    lon = str(row.get("longitude") or "").strip().lower()
    return lat not in ("", "none", "0", "0.0") and lon not in ("", "none", "0", "0.0")


def _already_forced(existing: list[dict], name: str) -> bool:
    """True if the item already has *name* as its primary gazetteer point."""
    return any(
        _has_coords(r)
        and str(r.get("location_type")) == "GAZETTEER"
        and str(r.get("location_name")) == name
        and str(r.get("is_primary")).lower() == "true"
        for r in existing
    )


def apply_gazetteer(items: list[dict], locations: list[dict]) -> int:
    """Add curated points from the gazetteer. Mutates *locations* in place;
    returns count added.

    Normal entries are gap-fillers — only items with no point are matched.
    ``override`` entries (e.g. Island at West Bay) are forced onto every matching
    item, demoting any resolver point so the site is represented by one point.
    Idempotent in both cases.
    """
    rows_by_item: dict[str, list[dict]] = {}
    for row in locations:
        rows_by_item.setdefault(str(row.get("item_id")), []).append(row)
    already = {iid for iid, rows in rows_by_item.items() if any(_has_coords(r) for r in rows)}

    next_id = 1 + max((_int(r.get("location_id")) for r in locations), default=0)
    added = 0
    for item in sorted(items, key=lambda it: _int(it["item_id"])):
        iid = str(item["item_id"])
        text = " ".join(str(item.get(k) or "") for k in _ITEM_TEXT_FIELDS)
        entry = match_gazetteer(text)
        if entry is None:
            continue
        existing = rows_by_item.get(iid, [])
        if entry.get("override"):
            if _already_forced(existing, entry["name"]):
                continue  # idempotent: already on this point
        elif iid in already:
            continue  # gap-filler: leave items the resolver already placed
        for r in existing:
            r["is_primary"] = "false"
        seq = 1 + max((_int(r.get("location_seq")) for r in existing), default=0)
        locations.append({
            "location_id": next_id,
            "item_id": item["item_id"],
            "address_raw": entry["name"],
            "address_normalized": f"{entry['name']}, Estero, FL",
            "latitude": entry["lat"],
            "longitude": entry["lng"],
            "parcel_id": None,
            "geocode_confidence": 0.8,
            "created_at": None,
            "location_name": entry["name"],
            "project_name": None,
            "location_type": "ROAD_SEGMENT" if entry["kind"] == "road" else "GAZETTEER",
            "resolution_notes": f"gazetteer: {entry['kind']} '{entry['name']}'",
            "location_seq": seq,
            "is_primary": "true",
        })
        rows_by_item.setdefault(iid, []).append(locations[-1])
        next_id += 1
        added += 1
    return added


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
