"""Phase 1 geocoding coverage, applied to committed silver without a full
geocode run.

Two steps:
  1. Deterministic propagation — inherit a resolved point onto items that share
     a project/application ID with a located sibling (no network).
  2. address_raw retry (optional, --no-retry to skip) — for items still without
     a point, re-run the typed resolver on the item's extracted address_raw.
     Uses the resolver's own (cert-relaxed) HTTP path, so it is unaffected by
     the corporate-proxy TLS interception.

Writes silver/v2/locations_v2.csv in place. Run export_gold.py afterwards to
refresh the gold CSV:

    python pipeline/propagate_locations.py --data-dir backend/data
    python pipeline/export_gold.py --data-dir backend/data
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from eaglegis.config import LOCATION_SEEDS
from eaglegis.location_propagation import (
    INHERITED_TYPE,
    _has_coords,
    propagate_locations,
)
from eaglegis.writer import write_csv

LOCATIONS_V2_FIELDS = [
    "location_id", "item_id", "address_raw", "address_normalized",
    "latitude", "longitude", "parcel_id", "geocode_confidence", "created_at",
    "location_name", "project_name", "location_type", "resolution_notes",
    "location_seq", "is_primary",
]


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _retry_address_raw(items: list[dict], locations: list[dict]) -> int:
    """Re-resolve items that still have no point but carry an address_raw."""
    from eaglegis.location_resolver import LocationResolver

    located = {
        str(r.get("item_id")) for r in locations if _has_coords(r)
    }
    targets = [
        it for it in items
        if str(it["item_id"]) not in located and (it.get("address_raw") or "").strip()
    ]
    if not targets:
        return 0

    resolver = LocationResolver(venue_lookup=LOCATION_SEEDS)
    rows_by_item: dict[str, list[dict]] = {}
    for r in locations:
        rows_by_item.setdefault(str(r.get("item_id")), []).append(r)
    next_id = 1 + max((int(r.get("location_id") or 0) for r in locations), default=0)
    added = 0
    for item in targets:
        addr = (item.get("address_raw") or "").strip()
        try:
            ref = resolver.resolve(addr, item_title=item.get("item_title"))
        except RuntimeError:
            ref = None
        if ref is None:
            continue
        existing = rows_by_item.get(str(item["item_id"]), [])
        for r in existing:
            r["is_primary"] = "false"
        seq = 1 + max((int(r.get("location_seq") or 0) for r in existing), default=0)
        locations.append({
            "location_id": next_id,
            "item_id": item["item_id"],
            "address_raw": ref.raw_text or addr,
            "address_normalized": ref.address_label or addr,
            "latitude": ref.latitude,
            "longitude": ref.longitude,
            "parcel_id": ref.parcel_strap or None,
            "geocode_confidence": ref.confidence,
            "created_at": None,
            "location_name": ref.address_label or ref.raw_text or addr,
            "project_name": None,
            "location_type": ref.location_type,
            "resolution_notes": f"address_raw retry: {ref.resolution_notes}",
            "location_seq": seq,
            "is_primary": "true",
        })
        next_id += 1
        added += 1
    resolver.flush()
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="backend/data")
    parser.add_argument("--no-retry", action="store_true",
                        help="skip the address_raw geocode retry (step 2)")
    args = parser.parse_args()

    silver = Path(args.data_dir) / "silver"
    items = _read(silver / "core" / "agenda_items.csv")
    links = _read(silver / "core" / "agenda_item_projects.csv")
    locations = _read(silver / "v2" / "locations_v2.csv")

    before = sum(1 for r in locations if _has_coords(r))
    before_items = len({str(r["item_id"]) for r in locations if _has_coords(r)})

    inherited = propagate_locations(items, locations, links)
    retried = 0 if args.no_retry else _retry_address_raw(items, locations)

    after_items = len({str(r["item_id"]) for r in locations if _has_coords(r)})
    write_csv(silver / "v2" / "locations_v2.csv", locations, LOCATIONS_V2_FIELDS)

    total = len(items)
    print(f"inherited (propagation): +{inherited}")
    print(f"address_raw retry: +{retried}")
    print(f"located items: {before_items} -> {after_items} "
          f"({100 * before_items / total:.0f}% -> {100 * after_items / total:.0f}% of {total})")
    print(f"located location rows: {before} -> {sum(1 for r in locations if _has_coords(r))}")
    print(f"inherited rows now present: {sum(1 for r in locations if r.get('location_type') == INHERITED_TYPE)}")


if __name__ == "__main__":
    main()
