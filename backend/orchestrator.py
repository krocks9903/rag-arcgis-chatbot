"""Orchestrate router-first answers across structured, keyword, and RAG paths."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from fastapi import HTTPException

from keyword_path import answer_keyword
from models import ChatResponse, RouteKind
from rag_path import answer_rag, parse_structured_answer, retrieve_with_crag, stream_llm_tokens
from router import route_question
from store import get_store
from structured_path import answer_structured
from tracing import trace_span

logger = logging.getLogger(__name__)


def _dedupe_projects(projects: list) -> list:
    seen: set[str] = set()
    out = []
    for p in projects:
        key = p.id or p.title
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def answer_question(question: str) -> ChatResponse:
    store = get_store()
    if store is None or not store.is_ready():
        raise HTTPException(503, "No dataset loaded. Use Load CSV in the UI first.")

    route = route_question(question)
    t0 = time.perf_counter()
    with trace_span("answer_question", {"route": route.value, "question": question[:120]}):
        if route == RouteKind.STRUCTURED:
            result = answer_structured(store.dataframe, question)
        elif route == RouteKind.KEYWORD:
            result = answer_keyword(store.dataframe, question)
        elif route == RouteKind.MIXED:
            kw = answer_keyword(store.dataframe, question)
            if kw.projects:
                kw.route = RouteKind.MIXED.value
                kw.meta["paths"] = ["keyword"]
                result = kw
            else:
                result = answer_rag(store, question)
        else:
            result = answer_rag(store, question)
        total_ms = round((time.perf_counter() - t0) * 1000)
        result.meta["latency_ms"] = total_ms
        logger.info("answer_question route=%s total_ms=%s", result.route, total_ms)
        return result


def stream_answer(question: str) -> Iterator[str]:
    """SSE events: meta → tokens (RAG only) → done."""
    store = get_store()
    if store is None or not store.is_ready():
        yield _sse({"type": "error", "detail": "No dataset loaded"})
        return

    route = route_question(question)
    t0 = time.perf_counter()
    yield _sse({"type": "meta", "route": route.value})

    if route == RouteKind.STRUCTURED:
        result = answer_structured(store.dataframe, question)
        result.meta["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        yield _sse({"type": "done", **result.model_dump()})
        return
    if route == RouteKind.KEYWORD:
        result = answer_keyword(store.dataframe, question)
        result.meta["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        yield _sse({"type": "done", **result.model_dump()})
        return
    if route == RouteKind.MIXED:
        kw = answer_keyword(store.dataframe, question)
        if kw.projects:
            kw.route = RouteKind.MIXED.value
            kw.meta["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            yield _sse({"type": "done", **kw.model_dump()})
            return

    t_retrieve = time.perf_counter()
    context, crag_meta = retrieve_with_crag(store, question)
    retrieve_ms = round((time.perf_counter() - t_retrieve) * 1000)
    crag_meta["retrieve_ms"] = retrieve_ms
    yield _sse({"type": "meta", "route": RouteKind.RAG.value, **crag_meta})

    buffer = ""
    t_gen = time.perf_counter()
    first_token_ms: int | None = None
    for token in stream_llm_tokens(question, context):
        if first_token_ms is None:
            first_token_ms = round((time.perf_counter() - t0) * 1000)
        buffer += token
        yield _sse({"type": "token", "text": token})
    generate_ms = round((time.perf_counter() - t_gen) * 1000)

    result = parse_structured_answer(buffer, route=RouteKind.RAG.value)
    result.meta.update(crag_meta)
    result.meta["generate_ms"] = generate_ms
    result.meta["ttft_ms"] = first_token_ms
    result.meta["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    logger.info(
        "stream_answer route=rag retrieve_ms=%s generate_ms=%s ttft_ms=%s total_ms=%s",
        retrieve_ms,
        generate_ms,
        first_token_ms,
        result.meta["latency_ms"],
    )
    yield _sse({"type": "done", **result.model_dump()})


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=_json_default)}\n\n"


def _json_default(obj: Any) -> Any:
    """Serialize numpy scalars (and similar) that sneaks into response meta."""
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
