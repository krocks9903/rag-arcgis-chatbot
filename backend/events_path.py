"""Structured answers for upcoming community-event questions."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from events import list_upcoming_events
from models import ChatResponse, RouteKind

EVENTS_INTENT_RE = re.compile(
    r"\b("
    r"what'?s\s+happening|what\s+is\s+happening|whats\s+happening|"
    r"upcoming\s+events?|events?\s+this\s+(week|weekend|month)|"
    r"this\s+weekend|things\s+to\s+do|"
    r"concerts?|flea\s+markets?|farmers?\s+markets?|state\s+fairs?|"
    r"county\s+fairs?|sports?\s+events?|games?\s+(this|on)|"
    r"musical\s+performances?|live\s+music|"
    r"community\s+calendar|what'?s\s+on"
    r")\b",
    re.IGNORECASE,
)

_WEEKEND_RE = re.compile(r"\bweekend\b", re.IGNORECASE)
_WEEK_RE = re.compile(r"\b(this\s+)?week\b", re.IGNORECASE)
_SPORTS_RE = re.compile(r"\b(sports?|game|fgcu|soccer|basketball|football)\b", re.IGNORECASE)
_MUSIC_RE = re.compile(r"\b(concert|music|musical|performance)\b", re.IGNORECASE)
_MARKET_RE = re.compile(r"\b(flea\s+market|farmers?\s+market|market)\b", re.IGNORECASE)
_FAIR_RE = re.compile(r"\b(fair|carnival|expo)\b", re.IGNORECASE)


def is_events_question(question: str) -> bool:
    return bool(EVENTS_INTENT_RE.search(question or ""))


def _parse_day(start: str) -> date | None:
    try:
        return date.fromisoformat(start[:10])
    except ValueError:
        return None


def _format_when(ev: dict) -> str:
    start = ev.get("start") or ""
    d = _parse_day(start)
    if not d:
        return start[:16]
    label = d.strftime("%a %b ") + str(d.day)
    if ev.get("allDay"):
        return f"{label} (all day)"
    try:
        dt = datetime.fromisoformat(start)
        hour = dt.strftime("%I").lstrip("0") or "12"
        return f"{label} {hour}:{dt.strftime('%M %p')}"
    except ValueError:
        return label


def _window_for_question(question: str) -> tuple[date, date]:
    today = date.today()
    if _WEEKEND_RE.search(question):
        weekday = today.weekday()  # Mon=0 … Sun=6
        if weekday == 5:
            return today, today + timedelta(days=1)
        if weekday == 6:
            return today, today
        days_until_sat = 5 - weekday
        start = today + timedelta(days=days_until_sat)
        return start, start + timedelta(days=1)
    if _WEEK_RE.search(question):
        return today, today + timedelta(days=7)
    return today, today + timedelta(days=21)


def _category_filter(question: str) -> set[str] | None:
    cats: set[str] = set()
    if _SPORTS_RE.search(question):
        cats.add("sports")
    if _MUSIC_RE.search(question):
        cats.add("music")
    if _MARKET_RE.search(question):
        cats.add("market")
    if _FAIR_RE.search(question):
        cats.add("fair")
    return cats or None


def answer_upcoming_events(question: str) -> ChatResponse:
    events = list_upcoming_events()
    start_day, end_day = _window_for_question(question)
    cats = _category_filter(question)

    filtered: list[dict] = []
    for ev in events:
        d = _parse_day(str(ev.get("start") or ""))
        if d is None or d < start_day or d > end_day:
            continue
        if cats and ev.get("category") not in cats:
            continue
        filtered.append(ev)

    filtered = filtered[:5]
    if not filtered:
        summary = (
            "- I don't have upcoming community events in that window.\n"
            "- Check esterotoday.com/events for the full local calendar."
        )
        return ChatResponse(
            summary=summary,
            answer=summary,
            projects=[],
            route=RouteKind.EVENTS.value,
            meta={"llm_skipped": True, "paths": ["events"], "events_count": 0},
        )

    bullets: list[str] = []
    for ev in filtered:
        when = _format_when(ev)
        venue = ev.get("venue") or "Estero area"
        title = ev.get("title") or "Event"
        url = ev.get("url") or ""
        if url:
            bullets.append(f"- {when}: {title} at {venue} — {url}")
        else:
            bullets.append(f"- {when}: {title} at {venue}.")

    summary = "\n".join(bullets)
    return ChatResponse(
        summary=summary,
        answer=summary,
        projects=[],
        route=RouteKind.EVENTS.value,
        meta={
            "llm_skipped": True,
            "paths": ["events"],
            "events_count": len(filtered),
            "window": {"start": start_day.isoformat(), "end": end_day.isoformat()},
        },
    )
