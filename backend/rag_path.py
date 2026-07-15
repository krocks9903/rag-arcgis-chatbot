"""Corrective RAG path: hybrid retrieval + Gemini (primary) / Groq (fallback) generation."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Literal

from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain.schema import Document

from config import (
    CRAG_MAX_ITERS,
    ENABLE_LLM_ESCALATE,
    GEMINI_MODEL,
    GROQ_MODEL,
    SCORE_THRESHOLD,
)
from models import ChatResponse, ProjectOut, RouteKind
from retrieval import best_score, format_docs, hybrid_retrieve
from store import DataStore

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a helpful assistant for the Village of Estero's Engage Estero platform.
You help residents understand Planning, Zoning & Design Board decisions using official meeting records.

RULES — follow exactly:
1. Only use facts from the Context. Never invent any detail.
2. If no relevant info exists, set summary to exactly: "I don't have records on that." and projects to [].
3. Write plain English in summary and project summaries. No markdown, no asterisks.
4. For each matching project, fill every field from the Context only.
5. document_url must be copied exactly from Document_Link in the context — never invent URLs.
6. status must be one of: Approved, Denied, Continued, or No decision recorded.

Return ONLY valid JSON (no markdown fences) with this exact shape:
{{
  "summary": "one closing sentence",
  "projects": [
    {{
      "title": "short project name from ProjectName",
      "id": "ApplicationID",
      "location": "Location or LocationName",
      "summary": "1-2 sentences",
      "status": "Approved | Denied | Continued | No decision recorded",
      "date": "MeetingDate",
      "document_url": "Document_Link"
    }}
  ]
}}

Context:
{context}

Question: {question}

JSON:"""

Tier = Literal["fast", "strong"]
_llms: dict[str, Any] = {}


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def groq_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def model_for_tier(tier: Tier) -> str:
    if tier == "fast" and gemini_available():
        return GEMINI_MODEL
    return GROQ_MODEL


def get_llm(tier: Tier = "fast"):
    """Return cached chat model. Fast = Gemini; strong/fallback = Groq."""
    use_gemini = tier == "fast" and gemini_available()
    # If Gemini missing, fast tier falls through to Groq.
    if use_gemini:
        cache_key = f"gemini:{GEMINI_MODEL}"
        if cache_key not in _llms:
            from langchain_google_genai import ChatGoogleGenerativeAI

            _llms[cache_key] = ChatGoogleGenerativeAI(
                model=GEMINI_MODEL,
                google_api_key=os.environ["GEMINI_API_KEY"],
                temperature=0,
                max_output_tokens=500,
            )
            logger.info("Initialized Gemini LLM model=%s", GEMINI_MODEL)
        return _llms[cache_key]

    if not groq_available():
        raise RuntimeError("No LLM API key set (need GEMINI_API_KEY and/or GROQ_API_KEY)")

    cache_key = f"groq:{GROQ_MODEL}"
    if cache_key not in _llms:
        from langchain_groq import ChatGroq

        _llms[cache_key] = ChatGroq(
            model=GROQ_MODEL,
            groq_api_key=os.environ["GROQ_API_KEY"],
            temperature=0.0,
            max_tokens=700,
            timeout=60,
            max_retries=1,
        )
        logger.info("Initialized Groq LLM model=%s", GROQ_MODEL)
    return _llms[cache_key]


def choose_llm_tier(question: str, crag_meta: dict[str, Any] | None = None) -> Tier:
    """Always prefer Gemini (fast). Escalate to Groq only after a failed answer."""
    _ = question, crag_meta
    if gemini_available():
        return "fast"
    return "strong" if groq_available() else "fast"


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            return json.loads(match.group(0))
        raise


def parse_structured_answer(raw: str, route: str = RouteKind.RAG.value) -> ChatResponse:
    try:
        payload = _extract_json(raw)
        projects = [ProjectOut.model_validate(p) for p in payload.get("projects", [])]
        summary = str(payload.get("summary", "")).strip()
        if not summary and not projects:
            summary = "I don't have records on that."
        result = ChatResponse(summary=summary, projects=projects, answer=summary, route=route)
        result.meta["parse_ok"] = True
        return result
    except Exception:
        result = ChatResponse(summary=raw.strip(), projects=[], answer=raw.strip(), route=route)
        result.meta["parse_ok"] = False
        return result


def should_escalate(result: ChatResponse, crag_meta: dict[str, Any] | None = None) -> bool:
    """Escalate Gemini → Groq when JSON is bad despite useful retrieval."""
    if not ENABLE_LLM_ESCALATE or not groq_available():
        return False
    meta = crag_meta or {}
    retrieved = int(meta.get("retrieved") or 0)
    best = float(meta.get("best_score") or 0.0)
    has_signal = retrieved > 0 and best >= SCORE_THRESHOLD * 0.5
    if not result.meta.get("parse_ok", True):
        return has_signal or retrieved > 0
    empty = not result.projects and "don't have records" in (result.summary or "").lower()
    if empty and has_signal and meta.get("last_verdict") == "correct":
        return True
    return False


def grade_context(hits: list[tuple[Document, float]]) -> str:
    if not hits:
        return "incorrect"
    score = best_score(hits)
    if score < SCORE_THRESHOLD * 0.5:
        return "incorrect"
    if score < SCORE_THRESHOLD:
        return "ambiguous"
    return "correct"


def rewrite_query(question: str) -> str:
    return f"{question.strip()} Estero Florida planning zoning design board"


def retrieve_with_crag(store: DataStore, question: str) -> tuple[str, dict[str, Any]]:
    query = question
    meta: dict[str, Any] = {"crag_iters": 0, "rewrites": []}
    hits: list[tuple[Document, float]] = []
    for i in range(CRAG_MAX_ITERS):
        meta["crag_iters"] = i + 1
        hits = hybrid_retrieve(store, query)
        verdict = grade_context(hits)
        meta["last_verdict"] = verdict
        if verdict == "correct":
            break
        if verdict in {"incorrect", "ambiguous"} and i < CRAG_MAX_ITERS - 1:
            query = rewrite_query(query)
            meta["rewrites"].append(query)
    meta["best_score"] = float(round(best_score(hits), 4))
    meta["retrieved"] = len(hits)
    return format_docs(hits), meta


def _invoke_llm(question: str, context: str, tier: Tier, route: str) -> ChatResponse:
    prompt = PromptTemplate(template=PROMPT_TEMPLATE, input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | get_llm(tier)
        | StrOutputParser()
    )
    raw = chain.invoke(question)
    result = parse_structured_answer(raw, route=route)
    result.meta["llm_tier"] = tier
    result.meta["llm_model"] = model_for_tier(tier)
    result.meta["llm_provider"] = "gemini" if (tier == "fast" and gemini_available()) else "groq"
    return result


def invoke_llm(question: str, context: str, tier: Tier, route: str = RouteKind.RAG.value) -> ChatResponse:
    return _invoke_llm(question, context, tier, route)


def generate_answer(
    question: str,
    context: str,
    route: str = RouteKind.RAG.value,
    crag_meta: dict[str, Any] | None = None,
) -> ChatResponse:
    tier = choose_llm_tier(question, crag_meta)
    try:
        result = _invoke_llm(question, context, tier, route)
    except Exception as e:
        logger.warning("Primary LLM failed (%s); trying Groq fallback", e)
        if tier == "fast" and groq_available():
            result = _invoke_llm(question, context, "strong", route)
            result.meta["escalated_from"] = "gemini_error"
            return result
        raise
    if tier == "fast" and should_escalate(result, crag_meta):
        logger.info("Escalating RAG answer Gemini → Groq")
        strong = _invoke_llm(question, context, "strong", route)
        strong.meta["escalated_from"] = "gemini"
        return strong
    return result


def answer_rag(store: DataStore, question: str) -> ChatResponse:
    t0 = time.perf_counter()
    context, crag_meta = retrieve_with_crag(store, question)
    crag_meta["retrieve_ms"] = round((time.perf_counter() - t0) * 1000)
    t1 = time.perf_counter()
    result = generate_answer(question, context, crag_meta=crag_meta)
    crag_meta["generate_ms"] = round((time.perf_counter() - t1) * 1000)
    result.route = RouteKind.RAG.value
    result.meta.update(crag_meta)
    return result


def stream_llm_tokens(question: str, context: str, tier: Tier = "fast"):
    """Yield text chunks from the selected LLM for SSE streaming."""
    prompt = PromptTemplate(template=PROMPT_TEMPLATE, input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | get_llm(tier)
        | StrOutputParser()
    )
    for chunk in chain.stream(question):
        if chunk:
            yield chunk
