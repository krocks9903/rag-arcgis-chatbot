"""FAISS index build/cache — split out from app.py so it can be baked into the
Docker image at build time without importing llm_provider.py (which
constructs an LLM client at import time and would need API keys present at
build time otherwise). app.py and bake_index.py both import this module;
app.py's routes read indexer.vectorstore / indexer.board_df directly.
"""
from __future__ import annotations

import hashlib
import json
import os

import pandas as pd
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

import ingest
from reranker import get_reranker

RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() not in {"0", "false", "no"}

vectorstore = None
board_df: "pd.DataFrame | None" = None
_embeddings = None

# PZDB board records come from the same gold export as Village Council
# (meetings_ai_public.csv, 334 PZDB rows) instead of the older, pre-filtered
# data.csv (103 rows, AiReady==True only). This intentionally includes
# unreviewed rows (AiReady is not checked here — see board_documents() in
# ingest.py) for broader coverage; data.csv is no longer read by default.
DATA_DIR = "data"
BOARD_CSV = "data/meetings_ai_public.csv"
WEBSITE_CSV = "data/esterotoday_content.csv"
VILLAGE_COUNCIL_CSV = "data/meetings_ai_public.csv"
INDEX_DIR = "faiss_index"
MANIFEST_FILE = os.path.join(INDEX_DIR, "manifest.json")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Bump this whenever the chunk schema/metadata shape changes so cached indexes
# from before the change are treated as stale and rebuilt.
CACHE_VERSION = "v5-pzdb-from-gold"


def get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
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

    if RERANK_ENABLED:
        get_reranker()  # load once at startup, not on the first request

    print("RAG backend ready.")
