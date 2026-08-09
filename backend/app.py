import hashlib
import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

import ingest
from events import router as events_router
from llm_provider import generate
from reranker import get_reranker, rerank
from schema_aliases import row_value

app = FastAPI(title="Estero Development Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events_router)

vectorstore = None
_embeddings = None
board_df: "pd.DataFrame | None" = None

BOARD_CSV = "data/data.csv"
WEBSITE_CSV = "data/esterotoday_content.csv"
VILLAGE_COUNCIL_CSV = "data/meetings_ai_public.csv"
INDEX_DIR = "faiss_index"
MANIFEST_FILE = os.path.join(INDEX_DIR, "manifest.json")

# Bump this whenever the chunk schema/metadata shape changes so cached indexes
# from before the change are treated as stale and rebuilt.
CACHE_VERSION = "v4-village-council"

SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.35"))
RETRIEVE_K = int(os.getenv("RETRIEVE_K", "12"))

# Cross-encoder reranking (see reranker.py). Set RERANK_ENABLED=false for an
# instant rollback to pre-rerank behavior — with it false, retrieve() and
# answer_question() are byte-identical to the original top-RETRIEVE_K flow.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() not in {"0", "false", "no"}
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "20"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "5"))


class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = "default"


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = []


class LoadRequest(BaseModel):
    csv_path: str


# ─────────────────────────────────────────────
# Index build / cache
# ─────────────────────────────────────────────
def get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            encode_kwargs={"batch_size": 64, "normalize_embeddings": True},
        )
    return _embeddings


def _csv_digest(*paths: str) -> str:
    h = hashlib.md5()
    h.update(CACHE_VERSION.encode("utf-8"))
    for p in paths:
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def build_rag_chain(board_csv: str = BOARD_CSV, website_csv: str = WEBSITE_CSV, vc_csv: str = VILLAGE_COUNCIL_CSV):
    global vectorstore, board_df

    if os.path.exists(board_csv):
        board_df = pd.read_csv(board_csv, encoding="utf-8")

    digest = _csv_digest(board_csv, website_csv, vc_csv)
    manifest = {}
    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE, encoding="utf-8") as f:
            manifest = json.load(f)

    embeddings = get_embeddings()

    if manifest.get("digest") == digest and os.path.isdir(INDEX_DIR):
        print(f"Cache hit — loading FAISS index ({manifest.get('chunk_count')} chunks)")
        vectorstore = FAISS.load_local(INDEX_DIR, embeddings, allow_dangerous_deserialization=True)
    else:
        print("Building chunks from CSV sources…")
        docs = ingest.build_documents(board_csv, website_csv, vc_csv)
        if not docs:
            raise ValueError("No data files found in data/ folder")
        print(f"Indexing {len(docs)} chunks…")
        vectorstore = FAISS.from_documents(docs, embeddings)
        os.makedirs(INDEX_DIR, exist_ok=True)
        vectorstore.save_local(INDEX_DIR)
        with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"digest": digest, "chunk_count": len(docs), "cache_version": CACHE_VERSION},
                f,
                indent=2,
            )
        print("FAISS index built and saved.")

    # LLM generation no longer goes through a client built here — llm_provider
    # validates LLM_PROVIDER and constructs the selected provider's client
    # once at import time (see llm_provider.py). If that failed, this module
    # itself would never have finished importing.

    if RERANK_ENABLED:
        get_reranker()  # load once at startup, not on the first request

    print("RAG backend ready.")


# ─────────────────────────────────────────────
# Retrieval + metadata-driven cards (never LLM-authored)
# ─────────────────────────────────────────────
def _dedupe_key(doc) -> tuple:
    md = doc.metadata
    source_type = md.get("source_type")
    if source_type == "board_record":
        return ("board", md.get("record_id"))
    if source_type == "village_council":
        return ("village_council", md.get("record_id"))
    return ("article", md.get("url"))


def retrieve(question: str) -> list[tuple]:
    """Dense search, deduped to one hit per underlying record/article, best-first.

    Pulls a wider candidate pool (RERANK_CANDIDATES) when reranking is
    enabled, since the correct chunk on generic queries is usually in the
    top-20 dense hits but not top-12 — see answer_question() for the rerank
    step that reorders this pool afterward.
    """
    k = RERANK_CANDIDATES if RERANK_ENABLED else RETRIEVE_K
    hits = vectorstore.similarity_search_with_relevance_scores(question, k=k)
    hits.sort(key=lambda x: x[1], reverse=True)
    seen: set[tuple] = set()
    deduped = []
    for doc, score in hits:
        key = _dedupe_key(doc)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((doc, score))
    return deduped


# Generic words that appear in most/all project names and would make the
# tier-2 word-level keyword match below fire indiscriminately if not excluded.
_KEYWORD_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "tell", "me", "about", "what", "whats", "when", "where", "who", "how", "why",
    "please", "can", "you", "do", "does", "did", "this", "that", "these", "those",
    "for", "to", "of", "in", "on", "at", "with", "and", "or", "it", "its",
    "development", "developments", "project", "projects", "record", "records",
    "board", "meeting", "meetings", "order", "orders", "application", "applications",
    "estero", "village", "planning", "zoning", "design", "happening", "going", "news",
    "recent", "latest", "update", "updates", "status", "info", "information",
}


def _keyword_match(question: str, doc) -> bool:
    """Exact-name fallback for queries whose dense-embedding score is diluted
    by everything else in the chunk — both bare short queries ("wawa",
    "sandy lane") AND natural-language phrasing that buries the entity name
    in filler ("Tell me about the Wawa development"). Only used when nothing
    clears SCORE_THRESHOLD on similarity alone."""
    q = question.strip().lower()
    if len(q) < 3:
        return False
    md = doc.metadata
    haystacks = [(md.get(f) or "").lower() for f in ("project_name", "title", "location")]
    haystacks = [h for h in haystacks if h]
    if not haystacks:
        return False
    # Tier 1: the whole question is a substring of a name field — bare "wawa",
    # "sandy lane", exact project-name lookups.
    if any(q in h for h in haystacks):
        return True
    # Tier 2: a distinctive (non-generic, 4+ char) word from the question
    # appears in a name field — catches full-sentence phrasing where filler
    # words would otherwise dilute a whole-phrase substring match.
    words = [w for w in re.findall(r"[a-z0-9]+", q) if len(w) >= 4 and w not in _KEYWORD_STOPWORDS]
    return any(w in h for w in words for h in haystacks)


def _board_card(md: dict) -> dict:
    return {
        "source_type": "board_record",
        "title": md.get("project_name") or None,
        "location": md.get("location") or None,
        "document_url": md.get("primary_source_url") or None,
        "pdf_url": md.get("primary_source_url") or None,
        "pdf_name": md.get("source_filename") or None,
        "application_id": md.get("application_id") or None,
        "meeting_date": md.get("meeting_date") or None,
        "lat": md.get("lat"),
        "lng": md.get("lng"),
        "status": (md.get("outcome") or "")[:80] or None,
    }


def _village_council_card(md: dict) -> dict:
    return {
        "source_type": "village_council",
        "title": md.get("project_name") or None,
        "location": md.get("location") or None,
        "document_url": md.get("primary_source_url") or None,
        "pdf_url": md.get("primary_source_url") or None,
        "pdf_name": md.get("source_filename") or None,
        "application_id": md.get("application_id") or None,
        "meeting_date": md.get("meeting_date") or None,
        "lat": md.get("lat"),
        "lng": md.get("lng"),
        "status": (md.get("outcome") or "")[:80] or None,
    }


def _article_card(doc) -> dict:
    md = doc.metadata
    excerpt = ingest.strip_header_lines(doc.page_content)
    summary = excerpt[:220].rsplit(" ", 1)[0] + "…" if len(excerpt) > 220 else excerpt
    return {
        "source_type": "website_article",
        "title": md.get("title") or None,
        "article_url": md.get("url") or None,
        "publish_date": md.get("publish_date") or None,
        "category": md.get("category") or None,
        "summary": summary or None,
    }


def build_card(passing: list[tuple]) -> Optional[dict]:
    """Only ever build a card from verified chunk metadata — never from LLM text.

    A board/village-council card requires the *top-scoring* passing chunk to
    be that source type with a real RecordId. Anything else (top chunk is an
    article, or a record chunk with no RecordId) falls through to an article
    card, and if nothing passing is a linkable article either, no card is
    emitted at all.
    """
    if not passing:
        return None
    top_doc, _ = passing[0]
    top_type = top_doc.metadata.get("source_type")
    if top_type == "board_record" and top_doc.metadata.get("record_id"):
        return _board_card(top_doc.metadata)
    if top_type == "village_council" and top_doc.metadata.get("record_id"):
        return _village_council_card(top_doc.metadata)
    for doc, _ in passing:
        if doc.metadata.get("source_type") == "website_article" and doc.metadata.get("url"):
            return _article_card(doc)
    return None


# System turn: static identity + hard rules, rewritten for a stronger
# instruction-following model (Claude Haiku 4.5) — see llm_provider.py.
# {today}/{six_months_ago} are filled in per-request since the rules
# reference "today", not because the text itself changes call to call.
SYSTEM_PROMPT = """You are the assistant for Engage Estero, a community organization in Estero, Florida. You help residents understand local developments using Planning Zoning & Design Board records, Village Council records, and EsteroToday.com news articles.

Today's date is {today}.

Each context block starts with header lines (DATE:, SOURCE_TYPE:, sometimes TRUE_URL:, SEARCH:) — retrieval aids for you only. Never quote or echo the literal text "DATE:", "SOURCE_TYPE:", "TRUE_URL:", or "SEARCH:" in your answer.

Hard rules — follow exactly:
1. Answer ONLY from the context blocks below. If the context lacks the answer, say so plainly. Never speculate or fill gaps with outside knowledge.
2. Never invent a URL, date, motion outcome, vote count, or board name. If a block has a TRUE_URL line, use that value verbatim for any link. Never construct or guess a URL.
3. Cite the ApplicationId shown in a context block (its record identifier, e.g. "ApplicationId: 12345") for every project fact, whenever provided.
4. If one block covers multiple projects, use only the section matching the resident's question. Never blend facts from a different project in.
5. Every fact must trace to a specific context block. Do not pad the answer with anything you cannot support from the context.
6. If two blocks disagree, treat the one with the more recent DATE as current, and note the earlier source said something different.
7. Attribute time-sensitive claims to their source date ("As of March 2021..."). Don't say "currently" or "still" for a claim dated before {six_months_ago} — use past tense with the date instead.
8. Write plain English in short paragraphs. **Bold** project names; use a numbered list for multiple matches. No meta-commentary. Start with the substance.
9. Never output a JSON block or code fence — a separate system attaches card data automatically."""

# User turn: the actual question first (so a truncated preview of this
# string — used for usage-log query previews, see llm_provider.py — shows
# the real question), then the retrieved context.
USER_PROMPT = """Resident question: {question}

Context blocks:
{context}"""

_STRAY_FENCE_RE = re.compile(r"```(?:json)?[\s\S]*?```", re.IGNORECASE)


def answer_question(question: str) -> "ChatResponse":
    hits = retrieve(question)
    passing = [(d, s) for d, s in hits if s >= SCORE_THRESHOLD]
    if not passing:
        keyword_hits = [(d, s) for d, s in hits if _keyword_match(question, d)]
        if keyword_hits:
            passing = keyword_hits[:6]

    if RERANK_ENABLED and passing:
        # Reorder the dense-qualified candidates with the cross-encoder so the
        # correct board/village-council chunk can beat a larger, phrase-rich
        # article corpus on generic phrasing. Original dense scores are kept
        # (not replaced by rerank scores) so SCORE_THRESHOLD semantics upstream
        # are unaffected — only the order (and count, truncated to
        # RERANK_TOP_N) of `passing` changes.
        docs_only = [d for d, _ in passing]
        dense_score_by_id = {id(d): s for d, s in passing}
        t0 = time.perf_counter()
        reranked_docs = rerank(question, docs_only, top_n=RERANK_TOP_N)
        print(f"Rerank step: {time.perf_counter() - t0:.3f}s total ({len(docs_only)} -> {len(reranked_docs)})")
        passing = [(d, dense_score_by_id[id(d)]) for d in reranked_docs]

    if passing:
        context = "\n\n---\n\n".join(doc.page_content for doc, _ in passing)
    else:
        context = "No context passed the relevance threshold for this question. There is nothing reliable to report."

    today_dt = datetime.now()
    system_prompt = SYSTEM_PROMPT.format(
        today=today_dt.strftime("%B %d, %Y"),
        six_months_ago=(today_dt - timedelta(days=182)).strftime("%Y-%m-%d"),
    )
    user_prompt = USER_PROMPT.format(context=context, question=question)

    result = generate(system=system_prompt, user=user_prompt, max_tokens=1200)
    prose = result.text.strip()
    if "Answer:" in prose:
        prose = prose.split("Answer:")[-1].strip()
    # Safety net: cards are built from metadata below, never from the LLM —
    # strip any fence the model writes anyway despite rule 9.
    prose = _STRAY_FENCE_RE.sub("", prose).strip()

    card = build_card(passing)
    answer = prose
    if card:
        answer = f"{prose}\n\n```json\n{json.dumps(card, ensure_ascii=False)}\n```"

    sources = []
    for doc, _ in passing[:4]:
        label = doc.metadata.get("source_type", "record")
        if label == "website_article":
            prefix = "📰 "
        elif label == "village_council":
            prefix = "🏘️ "
        else:
            prefix = "🏛 "
        snippet = ingest.strip_header_lines(doc.page_content)[:280]
        src = prefix + snippet
        if src not in sources:
            sources.append(src)

    return ChatResponse(answer=answer, sources=sources)


@app.on_event("startup")
async def startup():
    try:
        build_rag_chain()
    except Exception as e:
        print(f"Warning: Could not build index on startup: {e}")


@app.post("/load")
async def load_csv(req: LoadRequest):
    """Swap the board-records CSV. Website content always stays in the index."""
    path = f"data/{req.csv_path}"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        build_rag_chain(board_csv=path)
        return {"status": "ok", "message": f"Rebuilt unified index with {path} + website content"}
    except Exception:
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if vectorstore is None:
        raise HTTPException(status_code=503, detail="No data loaded yet.")
    try:
        return answer_question(req.question)
    except Exception:
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@app.get("/health")
async def health():
    # LLM readiness is no longer a runtime toggle here: llm_provider validates
    # LLM_PROVIDER and constructs its client at import time, so if this
    # process is running at all, the LLM provider is already ready.
    return {"status": "ok", "index_loaded": vectorstore is not None}


@app.get("/recent-decisions")
async def recent_decisions():
    """5 most recent board decisions with a ProjectName, newest MeetingDate first.
    Powers the Community Pulse dashboard's Recent Decisions widget — reads from
    the board CSV already loaded into memory, no re-indexing involved."""
    if board_df is None:
        raise HTTPException(status_code=503, detail="No board data loaded yet.")

    df = board_df.copy()
    name_col = "ProjectName" if "ProjectName" in df.columns else None
    if name_col:
        df = df[df[name_col].notna() & (df[name_col].astype(str).str.strip() != "")]
    date_col = "MeetingDate" if "MeetingDate" in df.columns else None
    if date_col:
        df["_sort_date"] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.sort_values("_sort_date", ascending=False)

    rows = df.head(5).to_dict(orient="records")
    decisions = []
    for row in rows:
        outcome = row_value(row, "outcome", "action_taken", "status")
        decisions.append({
            "title": ingest.clean_project_title(row_value(row, "project_name")),
            "date": row_value(row, "meeting_date") or None,
            "board": row_value(row, "board") or None,
            "status": outcome[:80] or None,
            "application_id": row_value(row, "application_id") or None,
        })
    return {"decisions": decisions}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
