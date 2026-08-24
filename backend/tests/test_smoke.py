"""Smoke tests for the RAG ArcGIS chatbot backend.

Importing the app module does not build the index (lifespan runs at serve time),
so CI can validate wiring cheaply without model downloads.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as backend_app  # noqa: E402
from rag_path import parse_structured_answer  # noqa: E402
from store import csv_hash  # noqa: E402


def test_app_metadata():
    assert backend_app.app.title == "Engage Estero RAG API"


def test_expected_routes_registered():
    paths = {route.path for route in backend_app.app.routes}
    assert {
        "/health",
        "/ready",
        "/chat",
        "/chat/stream",
        "/load",
        "/feedback",
        "/reports",
        "/admin/status",
        "/recent-decisions",
    }.issubset(paths)


def test_csv_hash_is_stable(tmp_path):
    sample = tmp_path / "sample.csv"
    sample.write_text("a,b\n1,2\n", encoding="utf-8")
    first = csv_hash(str(sample))
    second = csv_hash(str(sample))
    assert first == second and len(first) == 32


def test_parse_structured_answer_json():
    raw = (
        '{"summary":"Found one project.",'
        '"projects":[{"title":"Wawa","id":"DOS2022-E016","location":"Estero",'
        '"summary":"Approved with conditions.","status":"Approved",'
        '"date":"8/22/2023","document_url":"https://example.com/doc.pdf"}]}'
    )
    result = parse_structured_answer(raw)
    assert result.summary == "- Found one project."
    assert len(result.projects) == 1
    assert result.projects[0].id == "DOS2022-E016"


def test_parse_structured_answer_strips_markdown_fence():
    raw = '```json\n{"summary":"No match.","projects":[]}\n```'
    result = parse_structured_answer(raw)
    assert result.summary == "- No match."
    assert result.projects == []
    assert result.meta.get("parse_ok") is True


def test_choose_llm_tier_collaborate_when_both_keys(monkeypatch):
    from rag_path import choose_llm_tier, choose_summary_provider

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert choose_llm_tier("Explain RiverCreek") == "collaborate"
    assert choose_summary_provider() == "groq"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert choose_llm_tier("Explain RiverCreek") == "collaborate"
    assert choose_summary_provider() == "anthropic"

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert choose_llm_tier("Corkscrew Road") == "gemini"


def test_format_summary_bullets():
    from rag_path import format_summary_bullets

    out = format_summary_bullets("Project A was approved. Project B was continued.")
    assert out.startswith("- ")
    assert "\n- " in out
    assert format_summary_bullets("- One thing.\n- Two thing.") == "- One thing.\n- Two thing."


def test_stale_source_notice_when_older_than_five_years():
    from datetime import date

    from models import ChatResponse, ProjectOut
    from stale_sources import attach_stale_source_notice, stale_notice_meta

    meta = stale_notice_meta(
        [date(2018, 5, 1), date(2024, 1, 1)],
        today=date(2026, 7, 15),
        threshold_years=5,
    )
    assert meta["stale_sources"] is True
    assert "2018-05-01" in meta["stale_notice"]
    assert "2018-05-01" in meta["stale_source_dates"]

    fresh = stale_notice_meta([date(2024, 1, 1)], today=date(2026, 7, 15), threshold_years=5)
    assert fresh["stale_sources"] is False

    result = ChatResponse(
        summary="- something",
        projects=[ProjectOut(title="Old", date="01/15/2019")],
        answer="- something",
    )
    attach_stale_source_notice(result)
    assert result.meta.get("stale_sources") is True
    assert "stale_notice" in result.meta


def test_recency_boost_prefers_newer_when_no_year():
    from langchain.schema import Document
    from retrieval import apply_recency_boost

    older = Document(
        page_content="meeting_date: 2018-01-01\nSummary: old road work",
        metadata={"chunk_id": "old", "meeting_date": "2018-01-01"},
    )
    newer = Document(
        page_content="meeting_date: 2025-06-01\nSummary: new road work",
        metadata={"chunk_id": "new", "meeting_date": "2025-06-01"},
    )
    # Same relevance score — recency should put 2025 first.
    ranked = apply_recency_boost([(older, 1.0), (newer, 1.0)], "Corkscrew Road", boost=0.5)
    assert ranked[0][0].metadata["chunk_id"] == "new"


def test_recency_boost_honors_year_in_query():
    from langchain.schema import Document
    from retrieval import apply_recency_boost

    d2018 = Document(
        page_content="meeting_date: 2018-05-01\nSummary: approved in 2018",
        metadata={"chunk_id": "y2018", "meeting_date": "2018-05-01"},
    )
    d2025 = Document(
        page_content="meeting_date: 2025-05-01\nSummary: approved in 2025",
        metadata={"chunk_id": "y2025", "meeting_date": "2025-05-01"},
    )
    ranked = apply_recency_boost([(d2025, 1.0), (d2018, 1.0)], "What was approved in 2018?", boost=0.5)
    assert ranked[0][0].metadata["chunk_id"] == "y2018"


def test_keyword_shortcut_for_app_id():
    from keyword_path import is_strong_keyword_hit
    from models import ChatResponse, ProjectOut

    hit = ChatResponse(
        summary="Found 1 record.",
        projects=[ProjectOut(title="Wawa", id="DOS2022-E016")],
        answer="Found 1 record.",
        meta={"matched_rows": 1},
    )
    assert is_strong_keyword_hit(hit, "DOS2022-E016")
    miss = ChatResponse(summary="none", projects=[], answer="none", meta={"matched_rows": 0})
    assert not is_strong_keyword_hit(miss, "Corkscrew Road")


def test_prompt_loader_default_and_concise():
    from prompt_loader import clear_prompt_cache, load_prompt

    clear_prompt_cache()
    solo = load_prompt("solo", "default")
    assert "{context}" in solo and "{question}" in solo
    concise = load_prompt("summary", "concise")
    assert "{projects_json}" in concise
    assert "2–3" in concise or "2-3" in concise


def test_feedback_endpoint_writes_jsonl(tmp_path, monkeypatch):
    import feedback_store
    import models

    feedback_file = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(feedback_store, "FEEDBACK_DIR", str(tmp_path))
    monkeypatch.setattr(feedback_store, "FEEDBACK_FILE", str(feedback_file))

    req = models.FeedbackRequest(
        session_id="test",
        question="What about Wawa?",
        rating="up",
        route="rag",
        summary="- Wawa was discussed.",
        project_ids=["DCI2021-E004"],
    )
    out = feedback_store.append_feedback(req)
    assert out["ok"] is True
    line = feedback_file.read_text(encoding="utf-8").strip()
    payload = __import__("json").loads(line)
    assert payload["rating"] == "up"
    assert payload["question"] == "What about Wawa?"
    assert "DCI2021-E004" in payload["project_ids"]


def test_query_wants_recent_detects_conversational_cues():
    from retrieval import query_wants_recent

    assert query_wants_recent("What are the recent developments?")
    assert query_wants_recent("anything new happening?")
    assert query_wants_recent("latest zoning decisions")
    assert not query_wants_recent("What about Corkscrew Road?")
    assert not query_wants_recent("What was approved in 2017?")


def test_recent_query_hard_filters_old_hits():
    from langchain.schema import Document
    from retrieval import apply_recency_boost

    older = Document(
        page_content="meeting_date: 2017-05-15\nSummary: old evidence rules",
        metadata={"chunk_id": "y2017", "meeting_date": "2017-05-15"},
    )
    newer = Document(
        page_content="meeting_date: 2025-03-01\nSummary: new subdivision",
        metadata={"chunk_id": "y2025", "meeting_date": "2025-03-01"},
    )
    # High lexical score on the 2017 hit must not keep it for "recent" queries.
    ranked = apply_recency_boost(
        [(older, 5.0), (newer, 1.0)],
        "What are the recent developments?",
    )
    assert [d.metadata["chunk_id"] for d, _ in ranked] == ["y2025"]


def test_recent_query_empty_when_only_old_hits():
    from langchain.schema import Document
    from retrieval import apply_recency_boost, prefer_recent_hits

    older = Document(
        page_content="meeting_date: 2017-05-15\nSummary: old evidence rules",
        metadata={"chunk_id": "y2017", "meeting_date": "2017-05-15"},
    )
    ranked = apply_recency_boost([(older, 5.0)], "What are the recent developments?")
    assert ranked == []
    assert prefer_recent_hits([(older, 5.0)], "recent developments") == []


def test_recency_intent_follows_original_not_rewrite_years():
    from langchain.schema import Document
    from retrieval import apply_recency_boost

    older = Document(
        page_content="meeting_date: 2017-05-15\nSummary: old",
        metadata={"chunk_id": "y2017", "meeting_date": "2017-05-15"},
    )
    newer = Document(
        page_content="meeting_date: 2025-03-01\nSummary: new",
        metadata={"chunk_id": "y2025", "meeting_date": "2025-03-01"},
    )
    # Rewrite-shaped retrieval string includes years; intent stays on the original.
    ranked = apply_recency_boost(
        [(older, 5.0), (newer, 1.0)],
        "recent developments Estero 2026 2025",
        intent_query="What are the recent developments?",
    )
    assert [d.metadata["chunk_id"] for d, _ in ranked] == ["y2025"]


def test_rewrite_query_avoids_bare_years():
    from rag_path import rewrite_query

    rewritten = rewrite_query("What are the recent developments?")
    assert "recent planning meetings" in rewritten
    assert "2026" not in rewritten
    assert "2025" not in rewritten


def test_filter_projects_for_query_drops_offtopic_new_evidence():
    from models import ProjectOut
    from rag_path import filter_projects_for_query

    wawa = ProjectOut(
        title="Wawa Convenience Food & Beverage Store with Gas",
        id="DOS2022-E016",
        location="10081 Estero Town Commons Place",
    )
    ordinance = ProjectOut(
        title="Ordinance No. 2022-10 Estero Town Center (Wawa) Zoning Amendment",
        id="Ordinance No. 2022-10",
        location="Estero Town Center Commercial",
    )
    junk = ProjectOut(
        title="Discussion regarding the requirement related to the petitioner providing any new evidence",
        id="Section 13",
        location="Village Council meeting",
        summary="Petitioners must provide new evidence seven days prior.",
    )
    kept = filter_projects_for_query("are there any new wawas?", [wawa, ordinance, junk])
    assert [p.id for p in kept] == ["DOS2022-E016", "Ordinance No. 2022-10"]


def test_filter_projects_for_query_keeps_all_when_vague():
    from models import ProjectOut
    from rag_path import filter_projects_for_query

    projects = [
        ProjectOut(title="Some Road Work", id="A1"),
        ProjectOut(title="Other Item", id="B2"),
    ]
    assert filter_projects_for_query("what was approved?", projects) == projects


def test_filter_projects_for_recency_drops_2017():
    from models import ProjectOut
    from rag_path import filter_projects_for_recency

    old = ProjectOut(title="Old Item", id="O1", date="2017-05-15")
    new = ProjectOut(title="New Item", id="N1", date="2025-06-01")
    kept = filter_projects_for_recency("recent developments", [old, new])
    assert [p.id for p in kept] == ["N1"]


def test_filter_projects_for_recency_empty_when_only_old():
    from models import ProjectOut
    from rag_path import filter_projects_for_recency

    old = ProjectOut(title="Old Item", id="O1", date="2017-05-15")
    older = ProjectOut(title="Older Item", id="O2", date="2015-01-01")
    assert filter_projects_for_recency("recent developments", [old, older]) == []


def test_filter_projects_for_recency_sorts_newest_first():
    from models import ProjectOut
    from rag_path import filter_projects_for_recency

    a = ProjectOut(title="A", id="A", date="2024-01-01")
    b = ProjectOut(title="B", id="B", date="2025-06-01")
    kept = filter_projects_for_recency("latest decisions", [a, b])
    assert [p.id for p in kept] == ["B", "A"]


def test_filter_projects_always_sorts_newest_without_recent_cue():
    from models import ProjectOut
    from rag_path import filter_projects_for_recency

    a = ProjectOut(title="A", id="A", date="2022-01-01")
    b = ProjectOut(title="B", id="B", date="2025-06-01")
    kept = filter_projects_for_recency("What happened on Corkscrew Road?", [a, b])
    assert [p.id for p in kept] == ["B", "A"]


def test_document_date_reads_article_publish_date():
    from langchain.schema import Document
    from retrieval import document_meeting_date, format_docs

    older = Document(
        page_content="DATE: 2022-01-15\nSOURCE_TYPE: website_article\nold story",
        metadata={
            "chunk_id": "old-art",
            "source_type": "website_article",
            "publish_date": "2022-01-15",
            "date": "2022-01-15",
        },
    )
    newer = Document(
        page_content="DATE: 2025-11-01\nSOURCE_TYPE: website_article\nnew story",
        metadata={
            "chunk_id": "new-art",
            "source_type": "website_article",
            "publish_date": "2025-11-01",
            "date": "2025-11-01",
        },
    )
    assert document_meeting_date(newer).isoformat() == "2025-11-01"
    ctx = format_docs([(older, 0.9), (newer, 0.5)])
    assert ctx.index("2025-11-01") < ctx.index("2022-01-15")


def test_recency_boost_prefers_newer_article_publish_date():
    from langchain.schema import Document
    from retrieval import apply_recency_boost

    older = Document(
        page_content="DATE: 2019-03-01\narticle",
        metadata={"chunk_id": "old", "publish_date": "2019-03-01", "date": "2019-03-01"},
    )
    newer = Document(
        page_content="DATE: 2025-08-01\narticle",
        metadata={"chunk_id": "new", "publish_date": "2025-08-01", "date": "2025-08-01"},
    )
    ranked = apply_recency_boost([(older, 1.0), (newer, 1.0)], "Estero news", boost=0.5)
    assert ranked[0][0].metadata["chunk_id"] == "new"


def test_recent_intent_blocks_keyword_shortcut():
    from keyword_path import is_strong_keyword_hit
    from models import ChatResponse, ProjectOut

    kw = ChatResponse(
        summary="Found 2 records.",
        projects=[
            ProjectOut(title="Old Dev", id="X1", date="2017-05-15"),
            ProjectOut(title="Other", id="X2", date="2018-01-01"),
        ],
        answer="Found 2 records.",
        meta={"matched_rows": 2},
    )
    assert not is_strong_keyword_hit(kw, "What are the recent developments?")
    # App IDs still shortcut even if the question also says "new".
    assert is_strong_keyword_hit(
        ChatResponse(
            summary="Found 1 record.",
            projects=[ProjectOut(title="Wawa", id="DOS2022-E016")],
            answer="Found 1 record.",
            meta={"matched_rows": 1},
        ),
        "DOS2022-E016",
    )


def test_hits_meta_includes_meeting_dates():
    from langchain.schema import Document
    from retrieval import hits_meta

    doc = Document(
        page_content="meeting_date: 2025-03-01",
        metadata={"chunk_id": "c1", "meeting_date": "2025-03-01"},
    )
    meta = hits_meta([(doc, 1.0)])
    assert meta["retrieved"] == 1
    assert meta["meeting_dates"] == ["2025-03-01"]
    assert meta["chunk_ids"] == ["c1"]


def test_bm25_tokenize_stems_wawas_and_drops_stopwords():
    from store import _tokenize

    toks = _tokenize("are there any new wawas?")
    assert "wawas" in toks
    assert "wawa" in toks
    assert "any" not in toks
    assert "are" not in toks
    assert "there" not in toks
