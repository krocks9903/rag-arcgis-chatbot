"""Phase 1 geocoding coverage: inherit a location across items that share a
project or application ID.

Many agenda items are about a site whose address was only spelled out in one
meeting — a later reading, a contract, or a budget line references the same
project/application by name or ID but carries no parseable address, so the
resolver leaves it unmapped. This step copies the resolved point from a located
sibling onto those items. It is purely deterministic (no network, no geocoding):
an item only inherits from another item that was *independently* resolved, so
inherited points never chain off other inherited points.

The inherited row is marked ``location_type="INHERITED"`` with a lowered
confidence and a note naming its source, so it is always distinguishable from a
directly-resolved point.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

INHERITED_TYPE = "INHERITED"
INHERITED_CONFIDENCE = 0.5


def _has_coords(row: dict) -> bool:
    lat = str(row.get("latitude") or "").strip().lower()
    lon = str(row.get("longitude") or "").strip().lower()
    if lat in ("", "none", "0", "0.0") or lon in ("", "none", "0", "0.0"):
        return False
    return True


def _conf(row: dict) -> float:
    try:
        return float(row.get("geocode_confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _located_row_for_item(rows: list[dict]) -> dict | None:
    """The best directly-resolved point for one item: a primary located row if
    present, else the highest-confidence located row."""
    located = [r for r in rows if _has_coords(r) and r.get("location_type") != INHERITED_TYPE]
    if not located:
        return None
    primary = [r for r in located if str(r.get("is_primary")).lower() == "true"]
    pool = primary or located
    return max(pool, key=_conf)


def propagate_locations(
    items: list[dict],
    locations: list[dict],
    links: list[dict],
) -> int:
    """Append inherited location rows to *locations* in place; return the count
    added. An unlocated item inherits (application ID first, then project) from a
    sibling item that has a directly-resolved point.
    """
    rows_by_item: dict[Any, list[dict]] = defaultdict(list)
    for row in locations:
        rows_by_item[str(row.get("item_id"))].append(row)

    # Items with ANY point (including a previously-inherited one) are already
    # covered — skip them, so re-runs are idempotent.
    already_located_ids = {
        iid for iid, rows in rows_by_item.items() if any(_has_coords(r) for r in rows)
    }
    # Only directly-resolved points may serve as an inheritance source, so
    # inherited points never chain off other inherited points.
    source_item_ids = {
        iid for iid, rows in rows_by_item.items()
        if any(_has_coords(r) and r.get("location_type") != INHERITED_TYPE for r in rows)
    }

    item_app: dict[str, str] = {}
    for item in items:
        app = (item.get("application_id") or "").strip()
        if app:
            item_app[str(item["item_id"])] = app
    item_proj: dict[str, str] = {
        str(link["item_id"]): link["project_id"] for link in links
    }

    # Best directly-resolved source point per application ID and per project.
    # Deterministic: break ties on confidence, then lowest item_id.
    def _register(index: dict[str, tuple], key: str, item_id: str, src: dict) -> None:
        cand = (-_conf(src), _int(item_id))
        if key not in index or cand < index[key][0]:
            index[key] = (cand, src)

    app_source: dict[str, tuple] = {}
    proj_source: dict[str, tuple] = {}
    for iid in sorted(source_item_ids, key=_int):
        src = _located_row_for_item(rows_by_item[iid])
        if src is None:
            continue
        app = item_app.get(iid)
        if app:
            _register(app_source, app, iid, src)
        proj = item_proj.get(iid)
        if proj:
            _register(proj_source, proj, iid, src)

    next_id = 1 + max((_int(r.get("location_id")) for r in locations), default=0)
    added = 0
    for item in sorted(items, key=lambda it: _int(it["item_id"])):
        iid = str(item["item_id"])
        if iid in already_located_ids:
            continue
        app = item_app.get(iid)
        proj = item_proj.get(iid)
        chosen = None
        origin = ""
        if app and app in app_source:
            chosen = app_source[app][1]
            origin = f"application {app}"
        elif proj and proj in proj_source:
            chosen = proj_source[proj][1]
            origin = f"project {proj}"
        if chosen is None:
            continue

        existing = rows_by_item.get(iid, [])
        for r in existing:
            r["is_primary"] = "false"
        seq = 1 + max((_int(r.get("location_seq")) for r in existing), default=0)
        new_row = {
            "location_id": next_id,
            "item_id": item["item_id"],
            "address_raw": chosen.get("address_raw") or chosen.get("address_normalized") or "",
            "address_normalized": chosen.get("address_normalized") or chosen.get("address_raw") or "",
            "latitude": chosen.get("latitude"),
            "longitude": chosen.get("longitude"),
            "parcel_id": chosen.get("parcel_id"),
            "geocode_confidence": INHERITED_CONFIDENCE,
            "created_at": None,
            "location_name": chosen.get("location_name") or chosen.get("address_normalized") or "",
            "project_name": chosen.get("project_name"),
            "location_type": INHERITED_TYPE,
            "resolution_notes": f"inherited from item {chosen.get('item_id')} via {origin}",
            "location_seq": seq,
            "is_primary": "true",
        }
        locations.append(new_row)
        rows_by_item[iid].append(new_row)
        next_id += 1
        added += 1
    return added


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
