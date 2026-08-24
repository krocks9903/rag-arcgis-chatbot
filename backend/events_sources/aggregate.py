"""Merge EsteroToday + FGCU + manual community events."""
from __future__ import annotations

import logging
from typing import Any

from events_sources.esterotoday import fetch_esterotoday_events
from events_sources.fgcu import fetch_fgcu_events
from events_sources.manual import fetch_manual_events
from events_sources.normalize import dedupe_events, sanitize_category

logger = logging.getLogger(__name__)

# Soft caps keep one noisy feed (e.g. athletics) from burying Village /
# community listings on Pulse. Applied per source before merge/dedupe.
_SOURCE_CAPS = {
    "esterotoday": 40,
    "venue": 20,
    "manual": 40,
    "lee_county": 20,
}


def _cap_source(events: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    limit = _SOURCE_CAPS.get(source, 40)
    # Already sorted ascending by each fetcher when possible.
    return events[:limit]


def collect_upcoming_events() -> list[dict[str, Any]]:
    """Fetch all sources, dedupe, sort ascending by start."""
    merged: list[dict[str, Any]] = []

    try:
        merged.extend(_cap_source(fetch_esterotoday_events(), "esterotoday"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("EsteroToday source failed: %s", exc)

    try:
        merged.extend(_cap_source(fetch_fgcu_events(), "venue"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("FGCU source failed: %s", exc)

    try:
        merged.extend(_cap_source(fetch_manual_events(), "manual"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Manual events source failed: %s", exc)

    for ev in merged:
        ev["category"] = sanitize_category(ev.get("category"))

    merged = dedupe_events(merged)
    merged.sort(key=lambda ev: ev.get("start") or "")
    return merged
