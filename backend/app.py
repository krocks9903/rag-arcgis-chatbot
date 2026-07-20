import hashlib
import json
import os
import re
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
from langchain_groq import ChatGroq

import ingest
from schema_aliases import row_value

app = FastAPI(title="Estero Development Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

vectorstore = None
llm = None
_embeddings = None
board_df: "pd.DataFrame | None" = None

BOARD_CSV = "data/data.csv"
WEBSITE_CSV = "data/esterotoday_content.csv"
INDEX_DIR = "faiss_index"
MANIFEST_FILE = os.path.join(INDEX_DIR, "manifest.json")

# Bump this whenever the chunk schema/metadata shape changes so cached indexes
# from before the change are treated as stale and rebuilt.
CACHE_VERSION = "v3-geocoords"

SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.35"))
RETRIEVE_K = int(os.getenv("RETRIEVE_K", "12"))


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


def build_rag_chain(board_csv: str = BOARD_CSV, website_csv: str = WEBSITE_CSV):
    global vectorstore, llm, board_df

    if os.path.exists(board_csv):
        board_df = pd.read_csv(board_csv, encoding="utf-8")

    digest = _csv_digest(board_csv, website_csv)
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
        docs = ingest.build_documents(board_csv, website_csv)
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

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise ValueError("Set GROQ_API_KEY in your .env file")

    llm = ChatGroq(
        model="llama-3.1-8b-instant",
        groq_api_key=groq_key,
        temperature=0.1,
        max_tokens=1200,
    )
    print("RAG backend ready.")


# ─────────────────────────────────────────────
# Retrieval + metadata-driven cards (never LLM-authored)
# ─────────────────────────────────────────────
def _dedupe_key(doc) -> tuple:
    md = doc.metadata
    if md.get("source_type") == "board_record":
        return ("board", md.get("record_id"))
    return ("article", md.get("url"))


def retrieve(question: str) -> list[tuple]:
    """Dense search, deduped to one hit per underlying record/article, best-first."""
    hits = vectorstore.similarity_search_with_relevance_scores(question, k=RETRIEVE_K)
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

    A board card requires the *top-scoring* passing chunk to be a board_record
    with a real RecordId. Anything else (top chunk is an article, or a board
    chunk with no RecordId) falls through to an article card, and if nothing
    passing is a linkable article either, no card is emitted at all.
    """
    if not passing:
        return None
    top_doc, _ = passing[0]
    if top_doc.metadata.get("source_type") == "board_record" and top_doc.metadata.get("record_id"):
        return _board_card(top_doc.metadata)
    for doc, _ in passing:
        if doc.metadata.get("source_type") == "website_article" and doc.metadata.get("url"):
            return _article_card(doc)
    return None


PROSE_PROMPT = """You are the assistant for Engage Estero, a community organization in Estero, Florida. You help residents understand local developments using two sources: official Planning Zoning & Design Board records, and EsteroToday.com news articles.

Use ONLY the context below. Never invent URLs, dates, or facts.

Each context block starts with "DATE: YYYY-MM-DD" (when that source was decided or published) and "SOURCE_TYPE:" (board_record or website_article).

Today's date is {today}.

FORMAT AND ACCURACY RULES — follow exactly:
1. Answer in clear plain English. Use short paragraphs. Use **bold** for project names.
2. If multiple projects/topics match, use a numbered list with one line of detail each. NEVER merge facts from one project into another — every fact you state must come from the same context block as the project it describes.
3. If two context blocks disagree, treat the one with the most recent DATE as current, and explicitly note that an earlier source said something different.
4. Every time-sensitive claim must be attributed to its source date, e.g. "As of {today_month_year}, ..." or "A March 2021 article reported...".
5. Do NOT use the words "yet", "currently", "still", "so far", or "to date" unless the context block supporting that claim has a DATE on or after {six_months_ago} (six months before today). Older claims must be phrased in the past tense with their date instead, e.g. "As of March 2021, work had not yet begun" rather than "work has not started yet".
6. Only mention a project if a context block actually describes it in enough detail to summarize. Do not pad the answer with items you cannot support from the context — every bullet or sentence must be traceable to a specific context block.
7. Never write meta-sentences like "Based on the provided context", "Here is a summary", "The most relevant item is". Start directly with the substance.
8. If nothing in the context is relevant to the question, say plainly that you don't have reliable records on the topic. Do not guess.
9. Do not end your answer with a JSON block or any code fence of any kind. A separate system attaches structured card data automatically — your job is prose only.

Context:
{context}

Resident Question: {question}

Answer:"""

_STRAY_FENCE_RE = re.compile(r"```(?:json)?[\s\S]*?```", re.IGNORECASE)


def answer_question(question: str) -> "ChatResponse":
    hits = retrieve(question)
    passing = [(d, s) for d, s in hits if s >= SCORE_THRESHOLD]
    if not passing:
        keyword_hits = [(d, s) for d, s in hits if _keyword_match(question, d)]
        if keyword_hits:
            passing = keyword_hits[:6]

    if passing:
        context = "\n\n---\n\n".join(doc.page_content for doc, _ in passing)
    else:
        context = "No context passed the relevance threshold for this question. There is nothing reliable to report."

    today_dt = datetime.now()
    prompt = PROSE_PROMPT.format(
        today=today_dt.strftime("%B %d, %Y"),
        today_month_year=today_dt.strftime("%B %Y"),
        six_months_ago=(today_dt - timedelta(days=182)).strftime("%Y-%m-%d"),
        context=context,
        question=question,
    )

    response = llm.invoke(prompt)
    prose = response.content.strip()
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
        prefix = "📰 " if label == "website_article" else "🏛 "
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
    if vectorstore is None or llm is None:
        raise HTTPException(status_code=503, detail="No data loaded yet.")
    try:
        return answer_question(req.question)
    except Exception:
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@app.get("/health")
async def health():
    return {"status": "ok", "index_loaded": vectorstore is not None and llm is not None}


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
