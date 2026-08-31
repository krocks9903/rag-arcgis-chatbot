"""API / contract smoke tests that avoid heavy RAG stack imports where possible."""
from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import ChatRequest  # noqa: E402


def test_chat_request_rejects_empty_question():
    with pytest.raises(ValidationError):
        ChatRequest(question="")


def test_chat_request_rejects_oversized_question():
    with pytest.raises(ValidationError):
        ChatRequest(question="x" * 4001)


def test_chat_request_accepts_normal_question():
    req = ChatRequest(question="What's happening this weekend?")
    assert req.question.startswith("What")


def test_api_events_get_events_uses_cache(monkeypatch):
    import events as events_mod

    sample = [
        {
            "id": "et-1",
            "title": "Village Council",
            "start": "2099-01-15T09:30:00",
            "end": "2099-01-15T11:00:00",
            "allDay": False,
            "venue": "Village Hall",
            "url": "https://example.com",
            "category": "government",
            "source": "esterotoday",
            "location": "estero",
        }
    ]
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return sample

    monkeypatch.setattr(events_mod, "collect_upcoming_events", _fetch)
    events_mod._cache["events"] = None
    events_mod._cache["fetched_at"] = 0.0

    first = events_mod.get_events()
    second = events_mod.get_events()
    assert first["error"] is False
    assert first["events"][0]["category"] == "government"
    assert "meta" in first
    assert second["events"][0]["id"] == "et-1"
    assert calls["n"] == 1  # second hit served from cache


def test_safe_csv_name_rejects_path_traversal():
    """Mirror the /load filename guard without importing the full FastAPI app."""
    import re

    safe = re.compile(r"^[\w.\- ]+\.csv$", re.IGNORECASE)
    assert safe.match("upload.csv")
    assert not safe.match("../../evil.csv")
    assert not safe.match("foo/bar.csv")
    assert not safe.match("notes.txt")
