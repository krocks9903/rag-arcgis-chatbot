"""FastAPI entrypoint: router-first Q&A API + optional static frontend."""
from __future__ import annotations

import logging
import os
import shutil
import traceback
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from config import DATA_DIR, DEFAULT_CSV_PATH, EMBEDDING_MODEL, FRONTEND_DIR, SERVE_FRONTEND
from models import ChatRequest, ChatResponse
from orchestrator import answer_question, stream_answer
from store import build_store, get_store

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BACKEND_DIR, ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _warm_models() -> dict[str, bool]:
    """Load reranker + LLMs and run one retrieve so the first user question is warm."""
    from rag_path import get_llm
    from retrieval import get_reranker, hybrid_retrieve

    store = get_store()
    ready = store is not None and store.is_ready()
    reranker_ok = False
    llm_fast_ok = False
    llm_strong_ok = False
    retrieve_ok = False
    if ready:
        get_reranker()
        reranker_ok = True
        hybrid_retrieve(store, "Estero planning zoning")
        retrieve_ok = True
        try:
            get_llm("fast")
            llm_fast_ok = True
        except Exception as e:
            logger.warning("Fast LLM warmup skipped: %s", e)
        try:
            get_llm("strong")
            llm_strong_ok = True
        except Exception as e:
            logger.warning("Strong LLM warmup skipped: %s", e)
    return {
        "chain_ready": ready,
        "reranker": reranker_ok,
        "llm": llm_fast_ok or llm_strong_ok,
        "llm_fast": llm_fast_ok,
        "llm_strong": llm_strong_ok,
        "retrieve": retrieve_ok,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.path.exists(DEFAULT_CSV_PATH):
        build_store(DEFAULT_CSV_PATH)
        # Warm models in the background so /ready is not blocked for the startup probe.
        import threading

        def _bg_warm():
            try:
                status = _warm_models()
                logger.info("Startup warmup: %s", status)
            except Exception:
                traceback.print_exc()
                logger.warning("Startup warmup failed; first RAG request may be slow")

        threading.Thread(target=_bg_warm, daemon=True, name="model-warmup").start()
    else:
        print(f"No CSV at {DEFAULT_CSV_PATH} — run pipeline/build.py or upload via /load")
    yield


app = FastAPI(title="Engage Estero RAG API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Long-cache hashed/static frontend assets."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/") or path.endswith((".css", ".js", ".png", ".webp", ".jpg", ".svg")):
            response.headers.setdefault("Cache-Control", "public, max-age=86400, stale-while-revalidate=604800")
        return response


app.add_middleware(StaticCacheMiddleware)


def _record_count() -> int:
    store = get_store()
    return store.record_count if store else 0


def _chain_ready() -> bool:
    store = get_store()
    return store is not None and store.is_ready()


# Legacy exports for tests / Docker
build_or_load_index = build_store


@app.get("/health")
def health():
    store = get_store()
    return {
        "status": "ok",
        "chain_ready": _chain_ready(),
        "record_count": _record_count(),
        "chunk_count": store.chunk_count if store else 0,
        "embedding_model": EMBEDDING_MODEL,
    }


@app.get("/ready")
def ready():
    if not _chain_ready():
        raise HTTPException(503, "Index not loaded")
    return {"status": "ready", "record_count": _record_count()}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        return answer_question(req.question)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e)) from e


@app.get("/warmup")
def warmup():
    """Touch retrieval + LLM so the first user question is not a cold start."""
    try:
        status = _warm_models()
        if not status["chain_ready"]:
            raise HTTPException(503, "Index not loaded")
        if not status["reranker"]:
            raise HTTPException(503, "Reranker warmup failed")
        return {"status": "warm", **status}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(503, f"Warmup failed: {e}") from e


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    try:
        return StreamingResponse(stream_answer(req.question), media_type="text/event-stream")
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e)) from e


@app.post("/load")
async def load_csv(file: UploadFile = File(...)):
    os.makedirs(DATA_DIR, exist_ok=True)
    dest = os.path.join(DATA_DIR, file.filename or "upload.csv")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        build_store(dest)
        return {"message": f"Loaded {_record_count()} records from {file.filename}"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e)) from e


if SERVE_FRONTEND and os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
