from __future__ import annotations

import csv
from pathlib import Path


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        # LF line endings on every platform so CI rebuild-diff guards compare
        # bytes, not line-ending conventions (csv defaults to CRLF).
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

