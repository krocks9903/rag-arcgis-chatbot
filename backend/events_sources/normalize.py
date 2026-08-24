"""Shared event normalization, category mapping, geo filter, and dedupe."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OPS_DIR = Path(__file__).resolve().parent.parent / "data" / "ops"
_CATEGORIES_PATH = _OPS_DIR / "event_categories.json"

PULSE_CATEGORIES = (
    "government",
    "music",
    "market",
    "sports",
    "fair",
    "community",
    "other",
)

_config_cache: dict[str, Any] | None = None


def load_event_config() -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        _config_cache = json.loads(_CATEGORIES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_categories.json load failed (%s); using defaults", exc)
        _config_cache = {
            "esterotoday_fetch_mode": "all",
            "esterotoday_per_page": 50,
            "esterotoday_slug_map": {
                "village-of-estero-event": "government",
                "engage-estero-event": "community",
            },
            "title_keywords": {},
            "geo_keywords": ["estero", "bonita", "fgcu", "fort myers", "lee county"],
            "chip_order": list(PULSE_CATEGORIES),
            "chip_labels": {c: c.title() for c in PULSE_CATEGORIES},
        }
    return _config_cache


def reload_event_config() -> dict[str, Any]:
    global _config_cache
    _config_cache = None
    return load_event_config()


def _keyword_category(title: str, config: dict[str, Any]) -> str | None:
    low = (title or "").lower()
    keywords = config.get("title_keywords") or {}
    # Prefer more specific buckets first.
    for cat in ("market", "fair", "music", "sports"):
        for kw in keywords.get(cat) or []:
            if kw.lower() in low:
                return cat
    return None


def map_category(*, title: str, source_slugs: list[str] | None = None, default: str = "community") -> str:
    """Map Tribe slugs + title keywords to a Pulse chip category.

    Priority: government slugs → title keywords → other slug map → default.
    Village/government tags always win so a council agenda mention of
    \"music\" or \"sports\" cannot reclassify the meeting.
    """
    config = load_event_config()
    slug_map = config.get("esterotoday_slug_map") or {}
    slugs = list(source_slugs or [])

    for slug in slugs:
        mapped = slug_map.get(slug)
        if mapped == "government":
            return "government"

    by_kw = _keyword_category(title, config)
    if by_kw:
        return by_kw

    for slug in slugs:
        mapped = slug_map.get(slug)
        if mapped in PULSE_CATEGORIES:
            return mapped

    return default if default in PULSE_CATEGORIES else "other"


def sanitize_category(category: str | None) -> str:
    """Clamp any free-form category string to the Pulse chip set."""
    if category in PULSE_CATEGORIES:
        return category  # type: ignore[return-value]
    if category == "village":
        return "government"
    if category == "engage-estero":
        return "community"
    return "other"


def is_local_enough(title: str, venue: str | None, location: str | None = None) -> bool:
    """Keep Estero-area / nearby SWFL events; drop far-away noise.

    When a venue/location string is present, match geo keywords against that
    place only — otherwise \"FGCU … at Atlanta\" would pass on the team name.
    """
    config = load_event_config()
    keywords = [kw.lower() for kw in (config.get("geo_keywords") or [])]
    title_low = (title or "").lower()
    place = " ".join(x for x in (venue or "", location or "") if x).lower().strip()
    if place:
        return any(kw in place for kw in keywords)
    if not title_low:
        return True
    return any(kw in title_low for kw in keywords)


def fingerprint(title: str, start: str, venue: str | None) -> str:
    date_key = (start or "")[:10]
    norm_title = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    norm_venue = re.sub(r"[^a-z0-9]+", " ", (venue or "").lower()).strip()
    return f"{date_key}|{norm_title}|{norm_venue}"


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer earlier sources; keep first fingerprint win."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ev in events:
        key = fingerprint(str(ev.get("title") or ""), str(ev.get("start") or ""), ev.get("venue"))
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def make_event(
    *,
    id: str,
    title: str,
    start: str,
    end: str,
    all_day: bool,
    venue: str | None,
    url: str,
    category: str,
    source: str,
    location_tag: str = "regional",
) -> dict[str, Any]:
    return {
        "id": str(id),
        "title": (title or "").strip() or "Untitled event",
        "start": start or "",
        "end": end or start or "",
        "allDay": bool(all_day),
        "venue": venue,
        "url": url or "",
        "category": sanitize_category(category),
        "source": source or "other",
        "location": location_tag or "regional",
    }
