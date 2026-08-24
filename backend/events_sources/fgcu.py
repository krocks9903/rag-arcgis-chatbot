"""FGCU Athletics ICS calendar (sports near Estero / Fort Myers)."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo
from xml.sax.saxutils import unescape

import requests

from events_sources.normalize import is_local_enough, make_event, map_category

logger = logging.getLogger(__name__)

ICS_URL = "https://fgcuathletics.com/calendar.ashx/calendar.ics"
REQUEST_TIMEOUT = 10
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_EASTERN = ZoneInfo("America/New_York")
_VEVENT_RE = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.DOTALL | re.IGNORECASE)


def _unfold(ics: str) -> str:
    # RFC 5545 line folding: CRLF + space/tab continues previous line.
    return re.sub(r"\r?\n[ \t]", "", ics)


def _field(block: str, name: str) -> str:
    match = re.search(rf"(?m)^{name}[;:](.*)$", block, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _parse_dt(raw: str) -> tuple[str, bool]:
    """Return (naive local ISO start, all_day)."""
    raw = raw.strip()
    if ";" in raw and ":" in raw:
        # DTSTART;VALUE=DATE:20260806 or DTSTART;TZID=...:20260806T150000
        raw = raw.split(":", 1)[-1]
    elif raw.upper().startswith("VALUE=DATE:"):
        raw = raw.split(":", 1)[-1]

    if re.fullmatch(r"\d{8}", raw):
        d = datetime.strptime(raw, "%Y%m%d")
        return d.strftime("%Y-%m-%dT00:00:00"), True

    if raw.endswith("Z"):
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        local = dt.astimezone(_EASTERN).replace(tzinfo=None)
        return local.strftime("%Y-%m-%dT%H:%M:%S"), False

    if "T" in raw or len(raw) > 8:
        # Floating local or TZID already stripped to local-ish wall clock
        compact = raw.replace("-", "").replace(":", "")
        if len(compact) >= 15:
            local = datetime.strptime(compact[:15], "%Y%m%dT%H%M%S")
            return local.strftime("%Y-%m-%dT%H:%M:%S"), False
    return "", False


def _clean_summary(summary: str) -> str:
    text = unescape(summary.replace("\\,", ",").replace("\\n", " ").replace("\\;", ";"))
    text = re.sub(r"^\[.\]\s*", "", text).strip()
    return text


def fetch_fgcu_events(*, limit: int = 40) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    try:
        resp = requests.get(ICS_URL, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        body = _unfold(resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FGCU ICS fetch failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for block in _VEVENT_RE.findall(body):
        summary = _clean_summary(_field(block, "SUMMARY"))
        location = _field(block, "LOCATION").replace("\\,", ",").replace("\\n", " ")
        url = unescape(_field(block, "URL").replace("\\,", ","))
        uid = _field(block, "UID") or f"fgcu-{len(out)}"
        start_raw = _field(block, "DTSTART")
        end_raw = _field(block, "DTEND") or start_raw
        start, all_day = _parse_dt(start_raw)
        end, _ = _parse_dt(end_raw)
        if not start or start[:10] < today:
            continue
        if not is_local_enough(summary, location, location):
            continue
        category = map_category(title=summary, source_slugs=["sports"], default="sports")
        out.append(
            make_event(
                id=f"fgcu-{uid}",
                title=summary,
                start=start,
                end=end or start,
                all_day=all_day,
                venue=location or "FGCU",
                url=url,
                category=category if category != "other" else "sports",
                source="venue",
                location_tag="fort_myers",
            )
        )
        if len(out) >= limit:
            break

    out.sort(key=lambda ev: ev["start"])
    logger.info("fgcu events kept=%s", len(out))
    return out
