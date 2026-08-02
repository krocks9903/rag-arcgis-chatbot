"""Rebuild the project tables from committed silver, without a PDF/geocode run.

Projects are derived from silver agenda_items + resolved locations using the
shared grouping in eaglegis.projects, then written back to silver:

    core/projects.csv
    core/agenda_item_projects.csv
    core/agenda_items.csv   (project_matches column only)

Run `python pipeline/export_gold.py` afterwards to refresh the gold CSV.

    python pipeline/rebuild_projects.py --data-dir backend/data
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from eaglegis.projects import ItemView, build_projects
from eaglegis.writer import write_csv


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _primary_location_names(locations: list[dict]) -> dict[str, str]:
    by_item: dict[str, list[dict]] = {}
    for loc in locations:
        by_item.setdefault(loc.get("item_id"), []).append(loc)
    names: dict[str, str] = {}
    for item_id, locs in by_item.items():
        primary = next(
            (rec for rec in locs if str(rec.get("is_primary")).lower() == "true"),
            min(locs, key=lambda rec: int(rec.get("location_seq") or 0)),
        )
        names[item_id] = (primary.get("location_name")
                          or primary.get("address_normalized") or "")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="backend/data")
    args = parser.parse_args()

    silver = Path(args.data_dir) / "silver"
    items = _read(silver / "core" / "agenda_items.csv")
    locations = _read(silver / "v2" / "locations_v2.csv")
    categories = {c["category_id"]: c["category_name"]
                  for c in _read(silver / "core" / "agenda_categories.csv")}

    loc_names = _primary_location_names(locations)
    views = [
        ItemView(
            item_id=item["item_id"],
            title=item.get("item_title") or item.get("project_title") or "",
            application_id=item.get("application_id") or "",
            category_name=categories.get(item.get("category_id"), ""),
            action_type=item.get("action_type") or "",
            location_name=loc_names.get(item["item_id"], ""),
        )
        for item in items
    ]

    projects, links, names = build_projects(views)

    # project_matches on the item reflects the single derived project name.
    for item in items:
        item["project_matches"] = names.get(item["item_id"], "")

    write_csv(silver / "core" / "projects.csv", projects,
              ["project_id", "project_name", "description", "start_year", "status"])
    write_csv(silver / "core" / "agenda_item_projects.csv", links,
              ["item_id", "project_id"])
    write_csv(silver / "core" / "agenda_items.csv", items, list(items[0].keys()))

    grouped = sum(1 for v in names.values() if v)
    print(f"projects: {len(projects)} | links: {len(links)} | "
          f"items with a project: {grouped}/{len(items)}")


if __name__ == "__main__":
    main()
