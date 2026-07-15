"""Environment-driven configuration for the RAG pipeline."""
from __future__ import annotations

import os

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")

INDEX_DIR = os.path.join(BACKEND_DIR, "faiss_index")
MANIFEST_FILE = os.path.join(INDEX_DIR, "manifest.json")
BM25_FILE = os.path.join(INDEX_DIR, "bm25_corpus.json")
DATA_DIR = os.path.join(BACKEND_DIR, "data")
GOLD_CSV_PATH = os.path.join(DATA_DIR, "gold", "meetings_ai_public.csv")
DEFAULT_CSV_PATH = os.getenv("CSV_PATH", GOLD_CSV_PATH)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
# MiniLM cross-encoder is ~5–10× faster on Cloud Run CPU than bge-reranker-base.
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

DENSE_K = int(os.getenv("DENSE_K", "12"))
SPARSE_K = int(os.getenv("SPARSE_K", "12"))
RERANK_K = int(os.getenv("RERANK_K", "5"))
# How many fused hits to score with the cross-encoder (biggest CPU cost).
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "4"))
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.25"))
# One retrieve pass by default; set 2 to enable CRAG rewrite retry.
CRAG_MAX_ITERS = int(os.getenv("CRAG_MAX_ITERS", "1"))
CHUNK_SUMMARY_MIN = int(os.getenv("CHUNK_SUMMARY_MIN", "200"))
ENABLE_RERANKER = os.getenv("ENABLE_RERANKER", "true").lower() not in {"0", "false", "no"}

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "").lower() in {"1", "true", "yes"}
SERVE_FRONTEND = os.getenv("SERVE_FRONTEND", "true").lower() not in {"0", "false", "no"}
