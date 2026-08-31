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

Model default: ms-marco-MiniLM-L-6-v2 (~22M params) rather than
bge-reranker-base (~278M params) — the same lighter model ci.yml already
pins for tests. On a contended CPU the heavier model's per-call latency can
blow up dramatically (observed: 28s for a 2-candidate rerank on a machine
that should take well under a second); the smaller model gives less room
for that to happen and is still enough to fix the dense-retrieval ordering
problem described above. Override via RERANKER_MODEL if you want the
heavier model's slightly better ranking quality and can afford the latency.
"""
from __future__ import annotations

import os
import time

from ingest import strip_header_lines
from langchain_core.documents import Document
from sentence_transformers import CrossEncoder

RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

# Cross-encoder self-attention cost grows with sequence length — an
# unusually long chunk (a big article section) can dominate a rerank call's
# latency on its own. Cap what we feed the model; the model's own tokenizer
# max_length (512 tokens) would truncate anyway, this just avoids paying for
# tokenizing/attending over text that gets cut regardless.
_MAX_CHARS_PER_DOC = 1200

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
    pairs = [(query, strip_header_lines(d.page_content)[:_MAX_CHARS_PER_DOC]) for d in docs]

    t0 = time.perf_counter()
    scores = model.predict(pairs)
    elapsed = time.perf_counter() - t0
    print(f"Rerank: {len(docs)} candidates -> top {min(top_n, len(docs))} in {elapsed:.3f}s")

    for doc, score in zip(docs, scores):
        doc.metadata["rerank_score"] = float(score)

    ranked = sorted(docs, key=lambda d: d.metadata["rerank_score"], reverse=True)
    return ranked[:top_n]
