"""Corrective RAG path: Gemini extracts facts, Groq writes the citizen summary."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterator
from typing import Any, Literal

from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain.schema import Document

from config import (
    CRAG_MAX_ITERS,
    ENABLE_LLM_COLLABORATE,
    GEMINI_MODEL,
    GROQ_MODEL,
    SCORE_THRESHOLD,
)
from models import ChatResponse, ProjectOut, RouteKind
from retrieval import best_score, format_docs, hybrid_retrieve
from store import DataStore

logger = logging.getLogger(__name__)

# Solo full-answer prompt (when only one provider is available).
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

# Gemini role: structured extraction from retrieved records.
EXTRACT_TEMPLATE = """You extract Planning, Zoning & Design Board project facts for the Village of Estero.

RULES:
1. Only use facts from the Context. Never invent details.
2. If nothing relevant, return {{"projects": []}}.
3. document_url must be copied exactly from Document_Link.
4. status must be one of: Approved, Denied, Continued, or No decision recorded.
5. Keep each project summary to 1-2 plain sentences (no markdown).

Return ONLY valid JSON (no markdown fences):
{{
  "projects": [
    {{
      "title": "short project name",
      "id": "ApplicationID",
      "location": "Location",
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

# Groq role: citizen-facing closing summary from Gemini's extracted projects.
SUMMARY_TEMPLATE = """You write short answers for Estero residents about Planning & Zoning decisions.

Given the resident question and the verified project list, write ONE plain closing sentence.
Rules:
- Use only these projects. Do not invent records.
- If projects is empty, reply exactly: I don't have records on that.
- No markdown, no JSON, no bullet lists — just the sentence.

Question: {question}

Projects JSON:
{projects_json}

Answer:"""

Provider = Literal["gemini", "groq"]
_llms: dict[str, Any] = {}


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def groq_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def get_llm(provider: Provider):
    """Return a cached chat model for gemini or groq."""
    if provider == "gemini":
        if not gemini_available():
            raise RuntimeError("GEMINI_API_KEY is not set")
        cache_key = f"gemini:{GEMINI_MODEL}"
        if cache_key not in _llms:
            from langchain_google_genai import ChatGoogleGenerativeAI

            _llms[cache_key] = ChatGoogleGenerativeAI(
                model=GEMINI_MODEL,
                google_api_key=os.environ["GEMINI_API_KEY"],
                temperature=0,
                max_output_tokens=600,
            )
            logger.info("Initialized Gemini LLM model=%s", GEMINI_MODEL)
        return _llms[cache_key]

    if not groq_available():
        raise RuntimeError("GROQ_API_KEY is not set")
    cache_key = f"groq:{GROQ_MODEL}"
    if cache_key not in _llms:
        from langchain_groq import ChatGroq

        _llms[cache_key] = ChatGroq(
            model=GROQ_MODEL,
            groq_api_key=os.environ["GROQ_API_KEY"],
            temperature=0.0,
            max_tokens=400,
            timeout=60,
            max_retries=1,
        )
        logger.info("Initialized Groq LLM model=%s", GROQ_MODEL)
    return _llms[cache_key]


# Back-compat for warmup / older callers that used tier names.
def choose_llm_tier(question: str, crag_meta: dict[str, Any] | None = None) -> str:
    _ = question, crag_meta
    if gemini_available() and groq_available() and ENABLE_LLM_COLLABORATE:
        return "collaborate"
    if gemini_available():
        return "gemini"
    return "groq"


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


def parse_projects_only(raw: str) -> list[ProjectOut]:
    try:
        payload = _extract_json(raw)
        return [ProjectOut.model_validate(p) for p in payload.get("projects", [])]
    except Exception:
        logger.warning("Gemini extract JSON parse failed")
        return []


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


def _invoke_solo(question: str, context: str, provider: Provider, route: str) -> ChatResponse:
    prompt = PromptTemplate(template=PROMPT_TEMPLATE, input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | get_llm(provider)
        | StrOutputParser()
    )
    raw = chain.invoke(question)
    result = parse_structured_answer(raw, route=route)
    result.meta["llm_provider"] = provider
    result.meta["llm_model"] = GEMINI_MODEL if provider == "gemini" else GROQ_MODEL
    result.meta["llm_mode"] = "solo"
    return result


def gemini_extract_projects(question: str, context: str) -> list[ProjectOut]:
    prompt = PromptTemplate(template=EXTRACT_TEMPLATE, input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | get_llm("gemini")
        | StrOutputParser()
    )
    raw = chain.invoke(question)
    return parse_projects_only(raw)


def groq_write_summary(question: str, projects: list[ProjectOut]) -> str:
    projects_json = json.dumps([p.model_dump() for p in projects], ensure_ascii=False)
    prompt = PromptTemplate(template=SUMMARY_TEMPLATE, input_variables=["question", "projects_json"])
    chain = prompt | get_llm("groq") | StrOutputParser()
    text = chain.invoke({"question": question, "projects_json": projects_json}).strip()
    text = text.strip().strip('"').strip()
    if not text:
        if projects:
            return f"Found {len(projects)} matching record{'s' if len(projects) != 1 else ''}."
        return "I don't have records on that."
    return text


def stream_groq_summary(question: str, projects: list[ProjectOut]) -> Iterator[str]:
    projects_json = json.dumps([p.model_dump() for p in projects], ensure_ascii=False)
    prompt = PromptTemplate(template=SUMMARY_TEMPLATE, input_variables=["question", "projects_json"])
    chain = prompt | get_llm("groq") | StrOutputParser()
    for chunk in chain.stream({"question": question, "projects_json": projects_json}):
        if chunk:
            yield chunk


def generate_collaborative(
    question: str,
    context: str,
    route: str = RouteKind.RAG.value,
) -> ChatResponse:
    """Gemini extracts projects; Groq writes the closing summary."""
    t_extract = time.perf_counter()
    projects = gemini_extract_projects(question, context)
    extract_ms = round((time.perf_counter() - t_extract) * 1000)

    t_summary = time.perf_counter()
    summary = groq_write_summary(question, projects)
    summary_ms = round((time.perf_counter() - t_summary) * 1000)

    result = ChatResponse(
        summary=summary,
        projects=projects,
        answer=summary,
        route=route,
        meta={
            "parse_ok": True,
            "llm_mode": "collaborate",
            "llm_providers": ["gemini", "groq"],
            "llm_models": {"extract": GEMINI_MODEL, "summary": GROQ_MODEL},
            "extract_ms": extract_ms,
            "summary_ms": summary_ms,
        },
    )
    logger.info(
        "collaborate extract_ms=%s summary_ms=%s projects=%s",
        extract_ms,
        summary_ms,
        len(projects),
    )
    return result


def generate_answer(
    question: str,
    context: str,
    route: str = RouteKind.RAG.value,
    crag_meta: dict[str, Any] | None = None,
) -> ChatResponse:
    _ = crag_meta
    both = gemini_available() and groq_available() and ENABLE_LLM_COLLABORATE
    if both:
        try:
            return generate_collaborative(question, context, route=route)
        except Exception as e:
            logger.warning("Collaborate failed (%s); falling back to solo", e)

    if gemini_available():
        try:
            return _invoke_solo(question, context, "gemini", route)
        except Exception as e:
            logger.warning("Gemini solo failed (%s)", e)
            if groq_available():
                result = _invoke_solo(question, context, "groq", route)
                result.meta["escalated_from"] = "gemini_error"
                return result
            raise

    if groq_available():
        return _invoke_solo(question, context, "groq", route)

    raise RuntimeError("No LLM API key set (need GEMINI_API_KEY and/or GROQ_API_KEY)")


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


# Legacy aliases used by orchestrator / warmup
def invoke_llm(question: str, context: str, tier: str = "fast", route: str = RouteKind.RAG.value) -> ChatResponse:
    provider: Provider = "gemini" if tier in {"fast", "gemini"} and gemini_available() else "groq"
    return _invoke_solo(question, context, provider, route)


def stream_llm_tokens(question: str, context: str, tier: str = "fast") -> Iterator[str]:
    """Solo-stream full JSON (fallback when collaborate streaming is not used)."""
    provider: Provider = "gemini" if tier in {"fast", "gemini"} and gemini_available() else "groq"
    prompt = PromptTemplate(template=PROMPT_TEMPLATE, input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | get_llm(provider)
        | StrOutputParser()
    )
    for chunk in chain.stream(question):
        if chunk:
            yield chunk


def should_escalate(result: ChatResponse, crag_meta: dict[str, Any] | None = None) -> bool:
    """Kept for import compatibility; collaborate replaces escalate."""
    _ = result, crag_meta
    return False
