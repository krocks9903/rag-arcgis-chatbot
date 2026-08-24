"""Unit tests for community events normalize / chat intent (no live network)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from events_path import is_events_question  # noqa: E402
from events_sources.normalize import (  # noqa: E402
    dedupe_events,
    is_local_enough,
    make_event,
    map_category,
    sanitize_category,
)


def test_map_category_village_and_keywords():
    assert map_category(title="Council Meeting", source_slugs=["village-of-estero-event"]) == "government"
    # Government slug wins over title keywords that would otherwise say "music"
    assert (
        map_category(
            title="Council workshop on live music venues",
            source_slugs=["village-of-estero-event"],
        )
        == "government"
    )
    assert map_category(title="Jazz Concert at the Park", source_slugs=["community"]) == "music"
    assert map_category(title="Weekend Flea Market", source_slugs=["other-event"]) == "market"
    assert map_category(title="Lee County Fairgrounds Carnival", source_slugs=[]) == "fair"
    assert map_category(title="FGCU Soccer vs USF", source_slugs=["sports"]) == "sports"
    assert map_category(title="Mystery meetup", source_slugs=["totally-unknown-slug"]) == "community"


def test_geo_filter_keeps_local_drops_far():
    assert is_local_enough("Home game", "Fort Myers, Fla., FGCU Soccer Complex")
    assert is_local_enough("Market", "Coconut Point, Estero")
    assert not is_local_enough("Away game", "Orlando, Fla.")
    assert not is_local_enough(
        "FGCU Women's Soccer at LSU",
        "Baton Rouge, La.",
    )
    assert not is_local_enough(
        "FGCU Volleyball vs Someone",
        "Atlanta, Ga.",
    )
    assert is_local_enough(
        "FGCU Women's Soccer vs FIU",
        "Fort Myers, Fla., FGCU Soccer Complex",
    )


def test_sanitize_category_clamps_unknown():
    assert sanitize_category("sports") == "sports"
    assert sanitize_category("village") == "government"
    assert sanitize_category("engage-estero") == "community"
    assert sanitize_category("nope") == "other"
    assert sanitize_category(None) == "other"
    ev = make_event(
        id="x",
        title="T",
        start="2026-09-01T10:00:00",
        end="2026-09-01T11:00:00",
        all_day=False,
        venue=None,
        url="",
        category="weird",
        source="manual",
    )
    assert ev["category"] == "other"


def test_dedupe_by_title_date_venue():
    a = make_event(
        id="1",
        title="Farmers Market",
        start="2026-09-06T08:00:00",
        end="2026-09-06T13:00:00",
        all_day=False,
        venue="Estero",
        url="",
        category="market",
        source="manual",
    )
    b = make_event(
        id="2",
        title="Farmers Market",
        start="2026-09-06T08:00:00",
        end="2026-09-06T13:00:00",
        all_day=False,
        venue="Estero",
        url="https://example.com",
        category="market",
        source="esterotoday",
    )
    c = make_event(
        id="3",
        title="Farmers Market",
        start="2026-09-13T08:00:00",
        end="2026-09-13T13:00:00",
        all_day=False,
        venue="Estero",
        url="",
        category="market",
        source="manual",
    )
    out = dedupe_events([a, b, c])
    assert len(out) == 2
    assert out[0]["id"] == "1"


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What's happening this weekend?", True),
        ("Any concerts near Estero?", True),
        ("upcoming flea markets", True),
        ("FGCU sports events this week", True),
        ("What was approved on Corkscrew?", False),
        ("DOS2024-E001", False),
    ],
)
def test_is_events_question(question, expected):
    assert is_events_question(question) is expected
