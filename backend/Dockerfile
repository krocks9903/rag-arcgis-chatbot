# Container image for the RAG ArcGIS chatbot backend (FastAPI + FAISS + Anthropic/Groq).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download the embedding + reranker model weights (the slow, network-bound
# part of a cold start). Deliberately does NOT import app.py here:
# llm_provider.py constructs its LLM client at import time, which would
# require API keys to be present at *build* time (a secret baked into an
# image layer) rather than passed at deploy time via
# `--set-env-vars`/`--set-secrets`. Model names below must match the
# defaults in indexer.py (EMBEDDING_MODEL) and reranker.py (RERANKER_MODEL).
RUN mkdir -p /app/.cache/huggingface /app/.cache/sentence-transformers \
    && python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Build the FAISS index at image-build time instead of at Cloud Run cold
# start: embedding ~4,300 CSV chunks on Cloud Run's throttled cold-start CPU
# is slow enough to blow past a startup probe (see bake_index.py). Bakes
# faiss_index/ into this layer; app.py's startup event then hits the
# manifest-digest cache and just loads it from disk.
RUN python bake_index.py

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
