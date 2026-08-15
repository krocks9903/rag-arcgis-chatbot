"""Build the FAISS index at Docker image build time (see backend/Dockerfile).

Deliberately imports only indexer.py, never app.py — app.py imports
llm_provider, which constructs an LLM client at import time and would need
API keys present at *build* time (baked into an image layer) rather than
supplied at deploy time via --set-env-vars. indexer.build_rag_chain() writes
the same faiss_index/ + manifest.json that app.py's startup event checks on
boot, so a real container start becomes a fast cache-hit load instead of a
full rebuild — the rebuild took long enough on Cloud Run's cold-start CPU
allowance to cause "no available instance" failures under min-instances=0.
"""
from __future__ import annotations

from indexer import build_rag_chain

if __name__ == "__main__":
    build_rag_chain()
