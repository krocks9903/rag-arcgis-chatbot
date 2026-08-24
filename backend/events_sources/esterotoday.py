"""EsteroToday The Events Calendar REST feed."""
from __future__ import annotations

import html
import logging
from datetime import date
from typing import Any

import requests

from events_sources.normalize import load_event_config, make_event, map_category

logger = logging.getLogger(__name__)

EVENTS_API = "https://esterotoday.com/wp-json/tribe/events/v1/events"
REQUEST_TIMEOUT = 8
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _to_iso(dt_str: str) -> str:
    return dt_str.replace(" ", "T") if dt_str else ""


def _simplify(raw: dict[str, Any]) -> dict[str, Any]:
    venue = (raw.get("venue") or {}).get("venue") or None
    start = _to_iso(raw.get("start_date", ""))
    end = _to_iso(raw.get("end_date") or raw.get("start_date", ""))
    title = html.unescape(raw.get("title") or "")
    slugs = [c.get("slug") for c in raw.get("categories", []) if isinstance(c, dict) and c.get("slug")]
    category = map_category(title=title, source_slugs=slugs, default="community")
    # Prefer government when Village-tagged even if keywords match something else.
    if "village-of-estero-event" in slugs or "governance" in slugs:
        category = "government"
    return make_event(
        id=f"et-{raw['id']}",
        title=title,
        start=start,
        end=end,
        all_day=bool(raw.get("all_day")),
        venue=venue,
        url=raw.get("url") or "",
        category=category,
        source="esterotoday",
        location_tag="estero",
    )


def fetch_esterotoday_events() -> list[dict[str, Any]]:
    """Fetch upcoming EsteroToday events (all categories, future-only)."""
    config = load_event_config()
    today = date.today().isoformat()
    per_page = int(config.get("esterotoday_per_page") or 50)
    mode = (config.get("esterotoday_fetch_mode") or "all").lower()

    raw_by_id: dict[Any, dict] = {}
    if mode == "all":
        resp = requests.get(
            EVENTS_API,
            params={"start_date": today, "per_page": per_page},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        for ev in resp.json().get("events", []):
            raw_by_id.setdefault(ev["id"], ev)
    else:
        slugs = list((config.get("esterotoday_slug_map") or {}).keys())
        for slug in slugs:
            resp = requests.get(
                EVENTS_API,
                params={"categories": slug, "start_date": today, "per_page": min(per_page, 20)},
                headers=_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            for ev in resp.json().get("events", []):
                raw_by_id.setdefault(ev["id"], ev)

    simplified = [_simplify(ev) for ev in raw_by_id.values()]
    simplified = [ev for ev in simplified if (ev.get("start") or "")[:10] >= today]
    simplified.sort(key=lambda ev: ev["start"])
    logger.info("esterotoday events fetched=%s", len(simplified))
    return simplified
