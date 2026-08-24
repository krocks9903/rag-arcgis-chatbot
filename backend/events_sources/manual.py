"""Manual community-events.json fallback (flea markets, one-offs)."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from events_sources.normalize import make_event, map_category

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATES = [
    _REPO_ROOT / "frontend-react" / "public" / "community-events.json",
    Path(__file__).resolve().parent.parent / "data" / "ops" / "community-events.json",
]


def _load_raw() -> list[dict[str, Any]]:
    for path in _CANDIDATES:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            events = data.get("events", data if isinstance(data, list) else [])
            if isinstance(events, list):
                return [e for e in events if isinstance(e, dict)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("manual events load failed (%s): %s", path, exc)
    return []


def fetch_manual_events() -> list[dict[str, Any]]:
    today = date.today().isoformat()
    out: list[dict[str, Any]] = []
    for raw in _load_raw():
        start = str(raw.get("start") or raw.get("date") or "")
        if "T" not in start and re_full_date(start):
            start = f"{start}T00:00:00"
        if not start or start[:10] < today:
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        end = str(raw.get("end") or start)
        venue = raw.get("venue")
        category = str(raw.get("category") or "").strip().lower()
        if category not in {
            "government",
            "music",
            "market",
            "sports",
            "fair",
            "community",
            "other",
        }:
            category = map_category(title=title, default="community")
        eid = str(raw.get("id") or f"manual-{title[:40]}-{start[:10]}")
        out.append(
            make_event(
                id=f"manual-{eid}" if not eid.startswith("manual-") else eid,
                title=title,
                start=start,
                end=end if "T" in end else f"{end}T00:00:00",
                all_day=bool(raw.get("allDay", "T" not in str(raw.get("start") or ""))),
                venue=venue if isinstance(venue, str) else None,
                url=str(raw.get("url") or ""),
                category=category,
                source="manual",
                location_tag=str(raw.get("location") or "estero"),
            )
        )
    out.sort(key=lambda ev: ev["start"])
    return out


def re_full_date(value: str) -> bool:
    return bool(value) and len(value) >= 10 and value[4] == "-" and value[7] == "-"
