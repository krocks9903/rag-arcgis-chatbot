"""Lee County Parks & Recreation calendar (HTML scrape)."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote

import requests

from events_sources.normalize import is_local_enough, make_event, map_category

logger = logging.getLogger(__name__)

# SharePoint calendar list view — month/year query params work for navigation.
CALENDAR_URL = "https://www.leegov.com/parks/s_events/Pages/default.aspx"
REQUEST_TIMEOUT = 12
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# onclick="gotoEvent('/parks/events/event','%7Buuid%7D%3B876.0.2026-08-01T12%3A00%3A00Z',true)"
_EVENT_LINK_RE = re.compile(
    r'<a\s+class="eventLink"\s+title="([^"]+)"\s+onclick="gotoEvent\('
    r"'([^']*)',\s*'([^']+)'",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)
_ISO_IN_TOKEN_RE = re.compile(r"(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?)")

# Prefer parks / areas residents of Estero actually attend.
_LOCAL_PARK_HINTS = (
    "estero",
    "lakes park",
    "three oaks",
    "bonita",
    "san carlos",
    "fgcu",
    "miromar",
    "coconut",
    "corkscrew",
    "lee county",
    "fort myers",
    "wildlife",
    "bird patrol",
    "farmers",
    "market",
    "concert",
    "festival",
    "fair",
)


def _months_to_fetch() -> list[tuple[int, int]]:
    """Current month + next month (1-indexed month)."""
    today = date.today()
    months = [(today.year, today.month)]
    if today.month == 12:
        months.append((today.year + 1, 1))
    else:
        months.append((today.year, today.month + 1))
    return months


def _parse_onclick_token(token: str) -> str | None:
    """Extract ISO timestamp from SharePoint gotoEvent second argument."""
    from zoneinfo import ZoneInfo

    decoded = unquote(token)
    match = _ISO_IN_TOKEN_RE.search(decoded)
    if not match:
        return None
    raw = match.group(1)
    try:
        if raw.endswith("Z"):
            local = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                ZoneInfo("America/New_York")
            ).replace(tzinfo=None)
            return local.strftime("%Y-%m-%dT%H:%M:%S")
        return datetime.fromisoformat(raw).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return raw.replace("Z", "")


def _looks_local(title: str) -> bool:
    low = title.lower()
    return any(h in low for h in _LOCAL_PARK_HINTS)


def _parse_calendar_html(html: str) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in _EVENT_LINK_RE.finditer(html):
        title = match.group(1).strip()
        token = match.group(3)
        start = _parse_onclick_token(token)
        if not title or not start or start[:10] < today:
            continue
        if not _looks_local(title):
            continue

        # Time display is often just before the link in the same cell.
        window = html[max(0, match.start() - 120) : match.start()]
        time_m = _TIME_RE.search(window)
        # If HTML shows a wall-clock time, prefer stitching it onto the date
        # (SharePoint tokens are often noon UTC placeholders).
        if time_m:
            try:
                t = datetime.strptime(time_m.group(1).upper().replace(" ", ""), "%I:%M%p")
                start = f"{start[:10]}T{t.strftime('%H:%M:%S')}"
            except ValueError:
                pass

        key = f"{title.lower()}|{start[:10]}"
        if key in seen:
            continue
        seen.add(key)

        venue = "Lee County Parks"
        # Enrich venue from title when it embeds a park name.
        for park in ("Lakes Park", "Three Oaks", "Estero", "Bonita"):
            if park.lower() in title.lower():
                venue = f"{park}, Lee County"
                break

        if not is_local_enough(title, venue, "Lee County, Fort Myers"):
            continue

        category = map_category(title=title, default="community")
        out.append(
            make_event(
                id=f"lee-{abs(hash(key)) % 10_000_000}",
                title=title,
                start=start,
                end=start,
                all_day=False,
                venue=venue,
                url=CALENDAR_URL,
                category=category,
                source="lee_county",
                location_tag="regional",
            )
        )
    return out


def fetch_lee_county_events(*, limit: int = 25) -> list[dict[str, Any]]:
    """Scrape Lee County Parks calendar for Estero-area-relevant events."""
    merged: list[dict[str, Any]] = []
    for year, month in _months_to_fetch():
        try:
            resp = requests.get(
                CALENDAR_URL,
                params={"m": month, "y": year, "v": 0},
                headers=_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            merged.extend(_parse_calendar_html(resp.text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lee County calendar fetch failed (%s-%s): %s", year, month, exc)

    # Dedupe across months by title+date
    by_key: dict[str, dict[str, Any]] = {}
    for ev in merged:
        by_key[f"{ev['title'].lower()}|{ev['start'][:10]}"] = ev
    out = sorted(by_key.values(), key=lambda e: e["start"])[:limit]
    logger.info("lee_county events kept=%s", len(out))
    return out
