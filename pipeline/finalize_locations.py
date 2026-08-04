"""Apply the location gap-fillers to committed silver without a geocode run, in
the same order as build.py's write(): curated gazetteer, then project/
application inheritance. Rewrites silver/v2/locations_v2.csv.

Start from the resolver's committed base for a reproducible result:

    git checkout HEAD -- backend/data/silver/v2/locations_v2.csv
    python pipeline/finalize_locations.py --data-dir backend/data
    python pipeline/export_gold.py --data-dir backend/data
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from eaglegis.gazetteer import apply_gazetteer
from eaglegis.location_propagation import _has_coords, propagate_locations
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


def _located_items(locations: list[dict]) -> set[str]:
    return {str(r["item_id"]) for r in locations if _has_coords(r)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="backend/data")
    args = parser.parse_args()

    silver = Path(args.data_dir) / "silver"
    items = _read(silver / "core" / "agenda_items.csv")
    links = _read(silver / "core" / "agenda_item_projects.csv")
    locations = _read(silver / "v2" / "locations_v2.csv")

    total = len(items)
    before = len(_located_items(locations))

    gazetteered = apply_gazetteer(items, locations)
    after_gz = len(_located_items(locations))
    inherited = propagate_locations(items, locations, links)
    after = len(_located_items(locations))

    write_csv(silver / "v2" / "locations_v2.csv", locations, LOCATIONS_V2_FIELDS)

    print(f"gazetteer:   +{gazetteered} named-site locations")
    print(f"inheritance: +{inherited} propagated locations")
    print(f"located items: {before} -> {after_gz} (gazetteer) -> {after} (inherit)"
          f"  [{100 * before / total:.0f}% -> {100 * after / total:.0f}% of {total}]")


if __name__ == "__main__":
    main()
