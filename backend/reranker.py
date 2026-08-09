"""Cross-encoder reranker for card/context selection.

Dense FAISS similarity alone favors whichever corpus is larger and more
phrase-rich (EsteroToday's ~3,500 article chunks) over the correct board or
village-council chunk when a query is worded generically — the right chunk
is usually in the top-20 dense hits, just not #1. A cross-encoder scores
query+chunk pairs directly and reorders the candidates before context
assembly and card selection.

Scored against header-stripped chunk text: the DATE:/SOURCE_TYPE:/TRUE_URL:/
SEARCH: lines ingest.py injects to help dense retrieval are keyword-dense by
design and would over-boost a cross-encoder's relevance score if left in.
The original Document objects (headers intact) are returned unmodified —
only metadata gains "rerank_score" for logging/debug.

Standalone from retrieval.py/store.py (the unused orchestrator pipeline) —
same CrossEncoder approach, but no import from that module chain, so this
stays usable from app.py (the live pipeline) without activating it.
"""
from __future__ import annotations

import os
import time

from langchain.schema import Document
from sentence_transformers import CrossEncoder

from ingest import strip_header_lines

RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")

_model: CrossEncoder | None = None


def get_reranker() -> CrossEncoder:
    """Lazy-loaded, process-wide singleton — never re-instantiated per request."""
    global _model
    if _model is None:
        print(f"Loading reranker {RERANKER_MODEL}…")
        _model = CrossEncoder(RERANKER_MODEL)
        print("Reranker ready.")
    return _model


def rerank(query: str, docs: list[Document], top_n: int = 5) -> list[Document]:
    """Rerank docs against query with the cross-encoder, best-first.

    Scores header-stripped body text (see module docstring) but returns the
    original Document objects unmodified — headers intact — so downstream
    prompt assembly and card building see exactly what they saw before.
    """
    if not docs:
        return []

    model = get_reranker()
    pairs = [(query, strip_header_lines(d.page_content)) for d in docs]

    t0 = time.perf_counter()
    scores = model.predict(pairs)
    elapsed = time.perf_counter() - t0
    print(f"Rerank: {len(docs)} candidates -> top {min(top_n, len(docs))} in {elapsed:.3f}s")

    for doc, score in zip(docs, scores):
        doc.metadata["rerank_score"] = float(score)

    ranked = sorted(docs, key=lambda d: d.metadata["rerank_score"], reverse=True)
    return ranked[:top_n]
