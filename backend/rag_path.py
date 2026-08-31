"""Corrective RAG path: Gemini extracts facts; Haiku (or Groq) writes the summary."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any, Literal

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_core.documents import Document

from config import (
    ANTHROPIC_MODEL,
    CRAG_MAX_ITERS,
    ENABLE_LLM_COLLABORATE,
    GEMINI_MODEL,
    GROQ_MODEL,
    SCORE_THRESHOLD,
)
from models import ChatResponse, ProjectOut, RouteKind
from prompt_loader import load_prompt
from config import RECENT_QUERY_MAX_AGE_YEARS
from retrieval import (
    best_score,
    format_docs,
    hits_meta,
    hybrid_retrieve,
    query_wants_recent,
    scope_hits_to_project,
)
from stale_sources import parse_source_date
from store import DataStore

logger = logging.getLogger(__name__)

Provider = Literal["gemini", "groq", "anthropic"]
SummaryProvider = Literal["anthropic", "groq"]
_llms: dict[str, Any] = {}
_anthropic_client: Any = None


def _prompt(name: str) -> str:
    import config as cfg

    return load_prompt(name, cfg.PROMPT_VARIANT)


def _variant_name() -> str:
    import config as cfg

    return cfg.PROMPT_VARIANT


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def groq_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def anthropic_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def summary_backend_available() -> bool:
    return anthropic_available() or groq_available()


def choose_summary_provider() -> SummaryProvider:
    """Prefer Claude Haiku for the resident summary; Groq is the fallback."""
    if anthropic_available():
        return "anthropic"
    if groq_available():
        return "groq"
    raise RuntimeError("No summary LLM key set (need ANTHROPIC_API_KEY or GROQ_API_KEY)")


def summary_model_name(provider: SummaryProvider | None = None) -> str:
    provider = provider or choose_summary_provider()
    return ANTHROPIC_MODEL if provider == "anthropic" else GROQ_MODEL


def get_anthropic_client():
    """Lazy Anthropic client (Haiku) — avoids import-time hard fail when key unset."""
    global _anthropic_client
    if not anthropic_available():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        logger.info("Initialized Anthropic LLM model=%s", ANTHROPIC_MODEL)
    return _anthropic_client


def get_llm(provider: Provider):
    """Return a cached chat model for gemini or groq (LangChain)."""
    if provider == "anthropic":
        # Anthropic is used via the SDK for summary streaming; warmup just
        # constructs the client.
        return get_anthropic_client()

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
                max_output_tokens=1600,
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
            max_tokens=450,
            timeout=90,
            max_retries=1,
        )
        logger.info("Initialized Groq LLM model=%s", GROQ_MODEL)
    return _llms[cache_key]


# Back-compat for warmup / older callers that used tier names.
def choose_llm_tier(question: str, crag_meta: dict[str, Any] | None = None) -> str:
    _ = question, crag_meta
    if gemini_available() and summary_backend_available() and ENABLE_LLM_COLLABORATE:
        return "collaborate"
    if gemini_available():
        return "gemini"
    if anthropic_available():
        return "anthropic"
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


def finalize_prose(text: str) -> str:
    """Trim quotes/whitespace and drop a trailing incomplete fragment."""
    text = (text or "").strip().strip('"').strip("'").strip()
    if not text:
        return text
    if text[-1] in ".!?":
        return text
    sentence_ends = [m.end() - 1 for m in re.finditer(r"[.!?](?=\s|$)", text)]
    if sentence_ends and sentence_ends[-1] >= 20:
        return text[: sentence_ends[-1] + 1].strip()
    if len(text.split()) >= 6:
        return text.rstrip(",;:- ") + "."
    return text


def format_summary_bullets(text: str) -> str:
    """Normalize a summary into markdown '- ' bullet lines."""
    text = (text or "").strip().strip('"').strip("'").strip()
    if not text:
        return text
    empty = "I don't have records on that."
    if text.lower().rstrip(".") == empty.lower().rstrip("."):
        return empty

    bullets: list[str] = []
    # Already bullet-ish
    if re.search(r"(?m)^(?:[-*•]|\d+\.)\s+", text):
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^(?:[-*•]|\d+\.)\s+", "", line).strip()
            line = finalize_prose(line)
            if line:
                bullets.append(f"- {line}")
    else:
        # Split prose into sentence bullets
        parts = re.split(r"(?<=[.!?])\s+", text)
        for part in parts:
            line = finalize_prose(part.strip())
            if line:
                bullets.append(f"- {line}")

    if not bullets:
        return empty
    # Keep the answer scannable (concise pack asks for 2–3; never dump a wall of text)
    limit = 3 if (_variant_name() or "").lower() == "concise" else 5
    return "\n".join(bullets[:limit])


def parse_structured_answer(raw: str, route: str = RouteKind.RAG.value) -> ChatResponse:
    try:
        payload = _extract_json(raw)
        projects = [ProjectOut.model_validate(p) for p in payload.get("projects", [])]
        for p in projects:
            p.summary = finalize_prose(p.summary)
            p.title = (p.title or "").strip()
        summary = format_summary_bullets(str(payload.get("summary", "")).strip())
        if not summary and not projects:
            summary = "I don't have records on that."
        result = ChatResponse(summary=summary, projects=projects[:3], answer=summary, route=route)
        result.meta["parse_ok"] = True
        return result
    except Exception:
        result = ChatResponse(summary=raw.strip(), projects=[], answer=raw.strip(), route=route)
        result.meta["parse_ok"] = False
        return result


def parse_projects_only(raw: str) -> list[ProjectOut]:
    try:
        payload = _extract_json(raw)
        projects = [ProjectOut.model_validate(p) for p in payload.get("projects", [])]
        for p in projects:
            p.summary = finalize_prose(p.summary)
        return projects[:3]
    except Exception:
        logger.warning("Gemini extract JSON parse failed")
        return []


_QUERY_STOP = frozenset(
    {
        "are",
        "is",
        "was",
        "were",
        "there",
        "any",
        "new",
        "the",
        "and",
        "for",
        "what",
        "show",
        "about",
        "have",
        "has",
        "had",
        "with",
        "from",
        "that",
        "this",
        "these",
        "those",
        "minutes",
        "meeting",
        "estero",
        "village",
        "please",
        "tell",
        "me",
        "you",
        "how",
        "many",
        "when",
        "where",
        "which",
        "who",
        "why",
        "did",
        "does",
        "do",
        "can",
        "could",
        "would",
        "should",
        "latest",
        "recent",
        "recently",
        "find",
        "list",
        "all",
        "developments",
        "development",
        "projects",
        "project",
        "updates",
        "update",
        "happening",
        "going",
        "on",
    }
)


def _content_tokens(text: str) -> set[str]:
    """Content tokens for query↔project overlap (light plural stemming)."""
    toks = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    out: set[str] = set()
    for t in toks:
        if t in _QUERY_STOP:
            continue
        out.add(t)
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            out.add(t[:-1])
    return out


def filter_projects_for_query(question: str, projects: list[ProjectOut]) -> list[ProjectOut]:
    """Drop extracted cards that share no content tokens with the question.

    Prevents BM25/LLM false positives like a 2017 "any new evidence" discussion
    matching "are there any new wawas?".

    If the question has no content tokens, or overlap would drop every card
    (e.g. "what was approved?" vs titles that omit that word), keep the
    original list so vague status questions still work.
    """
    q = _content_tokens(question)
    if not q:
        return projects
    kept: list[ProjectOut] = []
    for p in projects:
        hay = _content_tokens(" ".join([p.title, p.id, p.location, p.summary]))
        if q & hay:
            kept.append(p)
    if not kept:
        return projects
    if kept != projects:
        logger.info(
            "filter_projects_for_query dropped %s/%s projects for %r",
            len(projects) - len(kept),
            len(projects),
            question[:80],
        )
    return kept


def filter_projects_for_recency(question: str, projects: list[ProjectOut]) -> list[ProjectOut]:
    """Prefer newest project/article cards; harden when user asks for recent.

    Always sorts dated cards newest-first so citations lead with recent sources.
    When the question asks for recent/new/latest, also drop cards older than
    RECENT_QUERY_MAX_AGE_YEARS (fresh → undated → empty).
    """
    if not projects:
        return projects

    def _newest_first(items: list[ProjectOut]) -> list[ProjectOut]:
        return sorted(
            items,
            key=lambda p: parse_source_date(p.date) or date.min,
            reverse=True,
        )

    if not query_wants_recent(question):
        return _newest_first(list(projects))

    years = RECENT_QUERY_MAX_AGE_YEARS
    if years <= 0:
        return _newest_first(list(projects))
    cutoff = date.today() - timedelta(days=int(years * 365.25))
    kept: list[ProjectOut] = []
    undated: list[ProjectOut] = []
    for p in projects:
        d = parse_source_date(p.date)
        if d is None:
            undated.append(p)
        elif d >= cutoff:
            kept.append(p)
    if kept:
        result = _newest_first(kept)
    elif undated:
        result = undated
    else:
        result = []
    if result != projects:
        logger.info(
            "filter_projects_for_recency kept %s/%s for %r",
            len(result),
            len(projects),
            question[:80],
        )
    return result


def refine_projects_for_question(question: str, projects: list[ProjectOut]) -> list[ProjectOut]:
    """Apply entity overlap then recency filters to extracted project cards."""
    return filter_projects_for_recency(question, filter_projects_for_query(question, projects))


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
    """Expand a weak query for a CRAG retry without injecting bare years.

    Prefer Claude Haiku when configured (cheap rewrite job). Otherwise use
    the deterministic rule-based expansion. Bare years are avoided so they
    cannot trip query_wants_recent if intent were derived from the rewrite.
    """
    haiku = _haiku_rewrite_query(question)
    if haiku:
        return haiku
    base = f"{question.strip()} Estero Florida planning zoning design board"
    if query_wants_recent(question):
        return f"{base} recent planning meetings last two years"
    return f"{base} newest articles recent coverage"


def _haiku_rewrite_query(question: str) -> str | None:
    """Optional Haiku job: rewrite a weak RAG query toward recent Estero sources."""
    from config import ENABLE_HAIKU_REWRITE, HAIKU_REWRITE_MODEL

    if not ENABLE_HAIKU_REWRITE:
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=HAIKU_REWRITE_MODEL,
            max_tokens=120,
            temperature=0,
            system=(
                "Rewrite the citizen question into one short English search query "
                "for Village of Estero planning/zoning records and EsteroToday articles. "
                "Prefer terms that surface the newest coverage. "
                "Do not invent years. Reply with the query only — no quotes or preamble."
            ),
            messages=[{"role": "user", "content": question.strip()}],
        )
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        cleaned = " ".join((text or "").strip().split())
        if not cleaned or len(cleaned) < 8:
            return None
        # Guard against year injection that would disable recent-mode intent.
        if re.search(r"\b20\d{2}\b", cleaned) and not re.search(r"\b20\d{2}\b", question):
            cleaned = re.sub(r"\b20\d{2}\b", "", cleaned)
            cleaned = " ".join(cleaned.split())
        logger.info("Haiku CRAG rewrite: %r -> %r", question[:80], cleaned[:120])
        return cleaned
    except Exception as exc:  # noqa: BLE001 — fall back to rules
        logger.warning("Haiku rewrite unavailable (%s); using rule-based rewrite", exc)
        return None


def retrieve_with_crag(store: DataStore, question: str) -> tuple[str, dict[str, Any]]:
    query = question
    meta: dict[str, Any] = {"crag_iters": 0, "rewrites": []}
    hits: list[tuple[Document, float]] = []
    for i in range(CRAG_MAX_ITERS):
        meta["crag_iters"] = i + 1
        # Recency intent always follows the original citizen question.
        hits = hybrid_retrieve(store, query, intent_query=question)
        verdict = grade_context(hits)
        meta["last_verdict"] = verdict
        if verdict == "correct":
            break
        if verdict in {"incorrect", "ambiguous"} and i < CRAG_MAX_ITERS - 1:
            query = rewrite_query(question)
            meta["rewrites"].append(query)
    scoped = scope_hits_to_project(store, hits)
    if len(scoped) != len(hits):
        meta["project_scoped"] = len(scoped)
    meta.update(hits_meta(scoped))
    return format_docs(scoped), meta


def _invoke_solo(question: str, context: str, provider: Provider, route: str) -> ChatResponse:
    prompt = PromptTemplate(template=_prompt("solo"), input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | get_llm(provider)
        | StrOutputParser()
    )
    raw = chain.invoke(question)
    result = parse_structured_answer(raw, route=route)
    result.projects = refine_projects_for_question(question, result.projects)
    result.meta["llm_provider"] = provider
    result.meta["llm_model"] = GEMINI_MODEL if provider == "gemini" else GROQ_MODEL
    result.meta["llm_mode"] = "solo"
    result.meta["prompt_variant"] = _variant_name()
    return result


def gemini_extract_projects(question: str, context: str) -> list[ProjectOut]:
    prompt = PromptTemplate(template=_prompt("extract"), input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | get_llm("gemini")
        | StrOutputParser()
    )
    raw = chain.invoke(question)
    return refine_projects_for_question(question, parse_projects_only(raw))


def _summary_prompt_text(question: str, projects: list[ProjectOut]) -> str:
    projects_json = json.dumps([p.model_dump() for p in projects[:3]], ensure_ascii=False)
    return _prompt("summary").format(question=question, projects_json=projects_json)


def _fallback_summary_from_projects(projects: list[ProjectOut]) -> str:
    if projects:
        titles = [p.title for p in projects[:3] if p.title]
        bullets = [f"- Related record: {t}." for t in titles] or [
            f"- Records show {len(projects)} related item{'s' if len(projects) != 1 else ''}."
        ]
        return "\n".join(bullets)
    return "I don't have records on that."


def _haiku_write_summary(question: str, projects: list[ProjectOut]) -> str:
    client = get_anthropic_client()
    user = _summary_prompt_text(question, projects)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=450,
        temperature=0,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    text = format_summary_bullets(text)
    return text or _fallback_summary_from_projects(projects)


def _stream_haiku_summary(question: str, projects: list[ProjectOut]) -> Iterator[str]:
    client = get_anthropic_client()
    user = _summary_prompt_text(question, projects)
    with client.messages.stream(
        model=ANTHROPIC_MODEL,
        max_tokens=450,
        temperature=0,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        for chunk in stream.text_stream:
            if chunk:
                yield chunk


def groq_write_summary(question: str, projects: list[ProjectOut]) -> str:
    projects_json = json.dumps([p.model_dump() for p in projects[:3]], ensure_ascii=False)
    prompt = PromptTemplate(template=_prompt("summary"), input_variables=["question", "projects_json"])
    chain = prompt | get_llm("groq") | StrOutputParser()
    text = chain.invoke({"question": question, "projects_json": projects_json})
    text = format_summary_bullets(text)
    return text or _fallback_summary_from_projects(projects)


def stream_groq_summary(question: str, projects: list[ProjectOut]) -> Iterator[str]:
    projects_json = json.dumps([p.model_dump() for p in projects[:3]], ensure_ascii=False)
    prompt = PromptTemplate(template=_prompt("summary"), input_variables=["question", "projects_json"])
    chain = prompt | get_llm("groq") | StrOutputParser()
    for chunk in chain.stream({"question": question, "projects_json": projects_json}):
        if chunk:
            yield chunk


def write_summary(question: str, projects: list[ProjectOut]) -> str:
    """Citizen summary via Haiku when available, otherwise Groq."""
    provider = choose_summary_provider()
    if provider == "anthropic":
        try:
            return _haiku_write_summary(question, projects)
        except Exception as e:
            logger.warning("Haiku summary failed (%s); falling back to Groq", e)
            if not groq_available():
                raise
    return groq_write_summary(question, projects)


def stream_summary(question: str, projects: list[ProjectOut]) -> Iterator[str]:
    """Stream citizen summary tokens (Haiku preferred, Groq fallback)."""
    provider = choose_summary_provider()
    if provider == "anthropic":
        try:
            yield from _stream_haiku_summary(question, projects)
            return
        except Exception as e:
            logger.warning("Haiku stream failed (%s); falling back to Groq", e)
            if not groq_available():
                raise
    yield from stream_groq_summary(question, projects)


def generate_collaborative(
    question: str,
    context: str,
    route: str = RouteKind.RAG.value,
) -> ChatResponse:
    """Gemini extracts projects; Haiku (or Groq) writes the closing summary."""
    t_extract = time.perf_counter()
    projects = gemini_extract_projects(question, context)
    extract_ms = round((time.perf_counter() - t_extract) * 1000)

    summary_provider = choose_summary_provider()
    t_summary = time.perf_counter()
    summary = write_summary(question, projects)
    summary_ms = round((time.perf_counter() - t_summary) * 1000)
    projects = projects[:3]

    result = ChatResponse(
        summary=summary,
        projects=projects,
        answer=summary,
        route=route,
        meta={
            "parse_ok": True,
            "llm_mode": "collaborate",
            "llm_providers": ["gemini", summary_provider],
            "llm_models": {
                "extract": GEMINI_MODEL,
                "summary": summary_model_name(summary_provider),
            },
            "prompt_variant": _variant_name(),
            "extract_ms": extract_ms,
            "summary_ms": summary_ms,
        },
    )
    logger.info(
        "collaborate extract_ms=%s summary_ms=%s summary_provider=%s projects=%s",
        extract_ms,
        summary_ms,
        summary_provider,
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
    both = gemini_available() and summary_backend_available() and ENABLE_LLM_COLLABORATE
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

    raise RuntimeError(
        "No LLM API key set (need GEMINI_API_KEY plus ANTHROPIC_API_KEY and/or GROQ_API_KEY)"
    )


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
    prompt = PromptTemplate(template=_prompt("solo"), input_variables=["context", "question"])
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
