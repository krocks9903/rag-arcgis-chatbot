"""Sync every Engage Estero content type into backend/data/engage_estero/*.csv.

Reads the site's WordPress REST API and appends rows that are not already
present, keyed on record_id. Existing rows are never rewritten, so an upstream
edit does not churn the committed corpus.

Usage:
    python backend/scripts/sync_engage_estero.py
    python backend/scripts/sync_engage_estero.py --types posts,events
    python backend/scripts/sync_engage_estero.py --dry-run --limit 5
    python backend/scripts/sync_engage_estero.py --types documents --fetch-pdf-text

Run in CI: see .github/workflows/sync-engage-estero.yml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import requests

from config import ENGAGE_ESTERO_DIR
from sources import SOURCE_SPECS, SPECS_BY_KEY, load_records
from sources.base import merge_records, write_records
from sources.wordpress import fetch


def sync_source(key: str, args: argparse.Namespace) -> int:
    spec = SPECS_BY_KEY[key]
    print(f"\n=== {spec.label} ({key}) ===")

    kwargs = {}
    if key == "documents":
        kwargs = {"fetch_text": args.fetch_pdf_text, "max_pdf_mb": args.max_pdf_mb}

    try:
        incoming = fetch(key, limit=args.limit, **kwargs)
    except requests.RequestException as exc:
        print(f"  fetch failed: {exc}")
        return 0

    usable = [r for r in incoming if r.is_usable()]
    print(f"  fetched {len(incoming)} item(s), {len(usable)} usable")

    existing = load_records(spec)
    merged, added = merge_records(existing, usable)
    print(f"  {len(existing)} existing → {added} new")

    if args.dry_run:
        for record in merged[len(existing) :][:3]:
            print(f"    + {record.publish_date} | {record.title[:70]}")
        return added

    path = spec.csv_path(ENGAGE_ESTERO_DIR)
    # Write when there is something new, or when the canonical CSV does not exist
    # yet — that second case migrates a legacy file into the new layout.
    if added or not Path(path).exists():
        total = write_records(path, merged)
        print(f"  wrote {total} row(s) to {path}")
    else:
        print("  no changes")
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--types",
        default=",".join(spec.key for spec in SOURCE_SPECS),
        help="Comma-separated source keys to sync.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap items per source (testing).")
    parser.add_argument("--dry-run", action="store_true", help="Report only; never write CSVs.")
    parser.add_argument(
        "--fetch-pdf-text",
        action="store_true",
        help="Download and extract PDF body text (slow; documents source only).",
    )
    parser.add_argument("--max-pdf-mb", type=float, default=8.0, help="Skip PDFs larger than this.")
    args = parser.parse_args()

    keys = [k.strip() for k in args.types.split(",") if k.strip()]
    unknown = [k for k in keys if k not in SPECS_BY_KEY]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")

    total_added = sum(sync_source(key, args) for key in keys)
    print(f"\nDone. {total_added} new record(s) across {len(keys)} source(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
