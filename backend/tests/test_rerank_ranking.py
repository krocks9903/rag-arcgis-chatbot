"""Unit tests for backend/reranker.py (cross-encoder reorder helper).

The original integration cases targeted a legacy monolithic FAISS pipeline in
app.py. Main now retrieves via store/retrieval; these tests cover the
standalone rerank() helper with a mocked CrossEncoder so CI stays fast and
architecture-agnostic.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Avoid importing the real torch/sentence-transformers stack in lightweight CI.
sys.modules.setdefault("sentence_transformers", MagicMock())

from langchain_core.documents import Document  # noqa: E402

from reranker import rerank  # noqa: E402


def _doc(body: str, **meta) -> Document:
    return Document(page_content=f"SEARCH: aid\n\n{body}", metadata=meta)


def test_rerank_empty_docs():
    assert rerank("anything", [], top_n=5) == []


def test_rerank_orders_by_cross_encoder_score():
    docs = [
        _doc("unrelated corridor news", project_name="East Corkscrew"),
        _doc("Wawa convenience store rezoning", project_name="Wawa"),
        _doc("generic village update", project_name="Other"),
    ]
    model = MagicMock()
    # Higher score for the Wawa chunk (index 1).
    model.predict.return_value = [0.2, 0.95, 0.4]

    with patch("reranker.get_reranker", return_value=model):
        out = rerank("Wawa", docs, top_n=2)

    assert [d.metadata["project_name"] for d in out] == ["Wawa", "Other"]
    assert out[0].metadata["rerank_score"] == 0.95
    model.predict.assert_called_once()
    pairs = model.predict.call_args[0][0]
    # Header lines must be stripped before scoring.
    assert all(not text.startswith("SEARCH:") for _, text in pairs)


def test_rerank_respects_top_n():
    docs = [_doc(f"chunk {i}", i=i) for i in range(5)]
    model = MagicMock()
    model.predict.return_value = [0.1, 0.2, 0.9, 0.3, 0.4]

    with patch("reranker.get_reranker", return_value=model):
        out = rerank("q", docs, top_n=1)

    assert len(out) == 1
    assert out[0].metadata["i"] == 2
