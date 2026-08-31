"""Shared ICS calendar parsing helpers."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator
from xml.sax.saxutils import unescape
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")
_VEVENT_RE = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.DOTALL | re.IGNORECASE)


def unfold_ics(ics: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", ics)


def ics_field(block: str, name: str) -> str:
    match = re.search(rf"(?m)^{name}[;:](.*)$", block, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def parse_ics_dt(raw: str) -> tuple[str, bool]:
    """Return (naive local ISO start, all_day)."""
    raw = raw.strip()
    if ";" in raw and ":" in raw:
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
        compact = raw.replace("-", "").replace(":", "")
        if len(compact) >= 15:
            local = datetime.strptime(compact[:15], "%Y%m%dT%H%M%S")
            return local.strftime("%Y-%m-%dT%H:%M:%S"), False
    return "", False


def clean_ics_text(value: str) -> str:
    return unescape(value.replace("\\,", ",").replace("\\n", " ").replace("\\;", ";")).strip()


def iter_vevents(ics_body: str) -> Iterator[str]:
    yield from _VEVENT_RE.findall(unfold_ics(ics_body))
