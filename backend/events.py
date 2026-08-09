"""Upcoming Events widget — server-side proxy for EsteroToday's Events
Calendar (The Events Calendar plugin) REST API.

Fetched server-side (not from the browser) so the frontend never needs CORS
handling for esterotoday.com, and so the site's bot-blocking WAF quirk (see
below) lives in one place.

Verified directly against the live API before writing this:
- Base: https://esterotoday.com/wp-json/tribe/events/v1/events
- `categories` accepts either the slug ("village-of-estero-event") or the
  numeric term ID (157) directly — no need to resolve slugs through
  /wp-json/tribe/events/v1/categories first; slugs work as-is.
- The site returns 403 for the default python-requests User-Agent (its WAF
  blocks generic bot UAs) — a browser-like User-Agent header is required or
  every request fails.
- `start_date`/`end_date` come back as naive local datetime strings
  ("YYYY-MM-DD HH:MM:SS", in the event's own `timezone` field — observed
  America/New_York for this site), not full ISO-8601 with a UTC offset.
- `title` is HTML-entity-encoded (e.g. "Meeting &#038; Discussion").
"""
from __future__ import annotations

import html
import time
from datetime import date

import requests
from fastapi import APIRouter

router = APIRouter()

EVENTS_API = "https://esterotoday.com/wp-json/tribe/events/v1/events"
CATEGORY_SLUGS = ["engage-estero-event", "village-of-estero-event"]
REQUEST_TIMEOUT = 6
CACHE_TTL_SECONDS = 30 * 60

# esterotoday.com's WAF 403s the default python-requests UA.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_cache: dict = {"events": None, "fetched_at": 0.0}


def _to_iso(dt_str: str) -> str:
    """'YYYY-MM-DD HH:MM:SS' (naive local time) -> 'YYYY-MM-DDTHH:MM:SS'.
    No offset is appended — the frontend treats this as local wall-clock
    time, the same convention the rest of the app uses for meeting dates."""
    return dt_str.replace(" ", "T") if dt_str else ""


def _category_label(ev: dict) -> str:
    slugs = {c.get("slug") for c in ev.get("categories", []) if isinstance(c, dict)}
    if "village-of-estero-event" in slugs:
        return "village"
    return "engage-estero"


def _simplify(ev: dict) -> dict:
    venue = (ev.get("venue") or {}).get("venue") or None
    start = _to_iso(ev.get("start_date", ""))
    end = _to_iso(ev.get("end_date") or ev.get("start_date", ""))
    return {
        "id": ev["id"],
        "title": html.unescape(ev.get("title") or ""),
        "start": start,
        "end": end,
        "allDay": bool(ev.get("all_day")),
        "venue": venue,
        "url": ev.get("url") or "",
        "category": _category_label(ev),
    }


def _fetch_category(slug: str, today: str) -> list[dict]:
    resp = requests.get(
        EVENTS_API,
        params={"categories": slug, "start_date": today, "per_page": 20},
        headers=_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("events", [])


def _fetch_all_events() -> list[dict]:
    """Fetch both category feeds, merge, dedupe by event id, drop past
    events, sort by start date ascending. Raises on any HTTP/network/JSON
    failure — the caller decides whether to fall back to a stale cache."""
    today = date.today().isoformat()

    raw_by_id: dict[int, dict] = {}
    for slug in CATEGORY_SLUGS:
        for ev in _fetch_category(slug, today):
            raw_by_id.setdefault(ev["id"], ev)

    simplified = [_simplify(ev) for ev in raw_by_id.values()]
    simplified = [ev for ev in simplified if ev["start"][:10] >= today]
    simplified.sort(key=lambda ev: ev["start"])
    return simplified


@router.get("/api/events")
def get_events() -> dict:
    """Merged, deduped, future-only events from both EsteroToday event
    categories. Cached in-process for CACHE_TTL_SECONDS; on a refresh
    failure, serves the last good cache if one exists so the Pulse panel
    never goes blank because of an esterotoday.com outage."""
    now = time.time()
    cache_fresh = _cache["events"] is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS
    if cache_fresh:
        return {"events": _cache["events"], "error": False}

    try:
        events = _fetch_all_events()
        _cache["events"] = events
        _cache["fetched_at"] = now
        return {"events": events, "error": False}
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never break the widget
        print(f"Warning: /api/events fetch failed: {exc}")
        if _cache["events"] is not None:
            return {"events": _cache["events"], "error": False}
        return {"events": [], "error": True}
