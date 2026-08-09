"""Flag answers that rely on meeting records older than STALE_SOURCE_YEARS."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from config import STALE_SOURCE_YEARS
from models import ChatResponse


def parse_source_date(raw: Any) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    # Bare year → mid-year so age checks still work.
    if len(text) == 4 and text.isdigit():
        try:
            return date(int(text), 6, 30)
        except ValueError:
            return None
    return None


def source_dates_from_response(result: ChatResponse) -> list[date]:
    found: list[date] = []
    for raw in result.meta.get("meeting_dates") or []:
        d = parse_source_date(raw)
        if d:
            found.append(d)
    for project in result.projects or []:
        d = parse_source_date(getattr(project, "date", None))
        if d:
            found.append(d)
    return found


def stale_notice_meta(
    dates: list[date],
    *,
    today: date | None = None,
    threshold_years: float | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    years = STALE_SOURCE_YEARS if threshold_years is None else threshold_years
    if years <= 0 or not dates:
        return {"stale_sources": False}

    cutoff = today - timedelta(days=int(years * 365.25))
    stale = sorted({d for d in dates if d <= cutoff})
    if not stale:
        return {"stale_sources": False}

    oldest = stale[0]
    age_years = max(1, int((today - oldest).days / 365.25))
    yr_label = int(years) if years == int(years) else years
    if len(stale) == 1:
        notice = (
            f"Note: This answer draws on a meeting record from {oldest.isoformat()} "
            f"(about {age_years} years old). Details may no longer be current."
        )
    else:
        notice = (
            f"Note: This answer draws on meeting records more than {yr_label} years old "
            f"(oldest: {oldest.isoformat()}). Details may no longer be current."
        )
    return {
        "stale_sources": True,
        "stale_source_dates": [d.isoformat() for d in stale],
        "stale_oldest": oldest.isoformat(),
        "stale_notice": notice,
    }


def attach_stale_source_notice(result: ChatResponse) -> ChatResponse:
    """Attach meta.stale_notice when any cited source is older than the threshold."""
    result.meta.update(stale_notice_meta(source_dates_from_response(result)))
    return result
