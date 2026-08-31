"""Visit Fort Myers / Fort Myers–Sanibel tourism events (RSS + detail pages)."""
from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests

from events_sources.normalize import is_local_enough, make_event, map_category

logger = logging.getLogger(__name__)

RSS_URL = "https://www.visitfortmyers.com/event/rss"
REQUEST_TIMEOUT = 10
DETAIL_LIMIT = 12
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_ATC_START_RE = re.compile(
    r'<var class="atc_date_start">\s*([^<]+?)\s*</var>',
    re.IGNORECASE,
)
_ATC_END_RE = re.compile(
    r'<var class="atc_date_end">\s*([^<]+?)\s*</var>',
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(
    r'<p class="address"[^>]*>\s*(.*?)\s*</p>',
    re.IGNORECASE | re.DOTALL,
)
_LOCALITY_RE = re.compile(
    r'<span class="locality">\s*([^<]+?)\s*</span>',
    re.IGNORECASE,
)
_LINE1_RE = re.compile(
    r'<span class="address-line1">\s*([^<]+?)\s*</span>',
    re.IGNORECASE,
)


def _parse_rss_items(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    items: list[dict[str, str]] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append({"title": html_lib.unescape(title), "url": link})
    return items


def _to_iso(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    # "2026-06-25 19:30:00" → ISO-ish naive local
    return raw.replace(" ", "T", 1) if " " in raw and "T" not in raw else raw


def _parse_detail(html: str) -> dict[str, Any]:
    start_m = _ATC_START_RE.search(html)
    end_m = _ATC_END_RE.search(html)
    start = _to_iso(start_m.group(1) if start_m else "")
    end = _to_iso(end_m.group(1) if end_m else start)

    locality = ""
    line1 = ""
    addr_m = _ADDRESS_RE.search(html)
    if addr_m:
        block = addr_m.group(1)
        loc_m = _LOCALITY_RE.search(block)
        line_m = _LINE1_RE.search(block)
        locality = (loc_m.group(1) if loc_m else "").strip()
        line1 = (line_m.group(1) if line_m else "").strip()

    venue_parts = [p for p in (line1, locality) if p]
    venue = ", ".join(venue_parts) if venue_parts else (locality or None)
    return {"start": start, "end": end, "venue": venue, "locality": locality}


def fetch_visitfortmyers_events(*, limit: int = DETAIL_LIMIT) -> list[dict[str, Any]]:
    """RSS list + capped detail fetches for dates/venues. Geo-filtered."""
    today = date.today().isoformat()
    try:
        resp = requests.get(RSS_URL, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        items = _parse_rss_items(resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Visit Fort Myers RSS failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for item in items[: max(limit, 1)]:
        url = urljoin(RSS_URL, item["url"])
        try:
            detail = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
            detail.raise_for_status()
            parsed = _parse_detail(detail.text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("VFM detail failed (%s): %s", url, exc)
            continue

        start = parsed.get("start") or ""
        end = parsed.get("end") or start
        # Multi-day tourism listings often start before "today" but are still running.
        if not start:
            continue
        if (end or start)[:10] < today:
            continue
        title = item["title"]
        venue = parsed.get("venue")
        locality = parsed.get("locality") or ""
        if not is_local_enough(title, venue, locality or "Fort Myers"):
            continue

        category = map_category(title=title, default="community")
        eid = re.search(r"/(\d+)/?$", url)
        out.append(
            make_event(
                id=f"vfm-{eid.group(1) if eid else len(out)}",
                title=title,
                start=start,
                end=end or start,
                all_day=len(start) == 10 or start.endswith("T00:00:00"),
                venue=venue,
                url=url,
                category=category,
                source="lee_county",
                location_tag="fort_myers" if "fort myers" in locality.lower() else "regional",
            )
        )

    out.sort(key=lambda ev: ev["start"])
    logger.info("visitfortmyers events kept=%s", len(out))
    return out
