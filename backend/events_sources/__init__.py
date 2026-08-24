"""Multi-source community events feeds for Community Pulse /api/events."""
from __future__ import annotations

from events_sources.aggregate import collect_upcoming_events

__all__ = ["collect_upcoming_events"]
