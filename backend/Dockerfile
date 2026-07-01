# Container image for the RAG ArcGIS chatbot backend (FastAPI + FAISS + Groq).
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

# Pre-download embedding model and bake FAISS index so Cloud Run cold starts are fast.
RUN mkdir -p /app/.cache/huggingface /app/.cache/sentence-transformers \
    && python -c "from app import build_or_load_index, DEFAULT_CSV_PATH; build_or_load_index(DEFAULT_CSV_PATH)"

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
