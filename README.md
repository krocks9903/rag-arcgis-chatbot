# RAG ArcGIS Chatbot

Router-first Q&A for Estero planning & zoning records: structured filters, keyword lookup, and corrective RAG with hybrid retrieval.

## What you need

- Python 3.11 **or** Docker Desktop
- Groq API key (`GROQ_API_KEY`)
- Optional: GCP account for Cloud Run deploy

## Quick start (Docker — recommended)

```powershell
cd T:\eagleGIS\rag-arcgis-chatbot
copy backend\.env.example backend\.env   # add GROQ_API_KEY
docker compose up --build
```

- Frontend: http://localhost:3000  
- API: http://localhost:8080/docs  

Full local + Google Cloud instructions: **[docs/DEPLOY_DOCKER.md](docs/DEPLOY_DOCKER.md)**

## Quick start (Python only)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # add GROQ_API_KEY
uvicorn app:app --reload --port 8000
```

Open http://localhost:8000 — the API serves the frontend when `SERVE_FRONTEND=true` (default).

Or serve the frontend separately on port 3000 (`python -m http.server 3000` in `frontend/`).

## Pipeline architecture

```text
Question → Router
  ├─ structured  → pandas filters (counts, year, status, location)
  ├─ keyword     → ApplicationID / minutes / token search
  ├─ mixed       → keyword first, else RAG
  └─ rag         → BM25 + FAISS (RRF) → reranker → CRAG → Groq JSON
```

| Component | Default |
|-----------|---------|
| Embeddings | `BAAI/bge-small-en-v1.5` (`EMBEDDING_MODEL`) |
| Reranker | `BAAI/bge-reranker-base` (`RERANKER_MODEL`) |
| Score threshold | `0.35` |
| CRAG max iterations | `2` |

Production Docker/Cloud Run can set `EMBEDDING_MODEL=BAAI/bge-m3` for higher quality.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness + index stats |
| `GET /ready` | Readiness (index loaded) |
| `POST /chat` | Structured JSON answer |
| `POST /chat/stream` | SSE stream (`meta` → `token` → `done`) |
| `POST /load` | Upload replacement CSV |

## Project structure

```text
rag-arcgis-chatbot/
├── .github/workflows/
│   ├── ci.yml           # lint + pytest + optional RAGAS
│   ├── deploy.yml       # Cloud Run (opt-in)
│   └── sync-data.yml    # weekly EagleGIS gold CSV sync
├── backend/
│   ├── app.py           # FastAPI + static frontend
│   ├── config.py
│   ├── store.py         # FAISS + BM25 index
│   ├── retrieval.py     # hybrid RRF + rerank
│   ├── router.py
│   ├── structured_path.py
│   ├── keyword_path.py
│   ├── rag_path.py      # CRAG + Groq
│   ├── orchestrator.py
│   └── tests/golden_qa.json
├── frontend/
│   ├── config.js        # API_BASE for Docker / Cloud Run
│   └── ...
├── docker-compose.yml   # local free stack (api + nginx)
├── docs/DEPLOY_DOCKER.md
└── scripts/eval_ragas.py
```

## CI / data sync

- **CI** runs router + golden Q&A tests without a Groq key.
- **sync-data.yml** pulls `meetings_ai_public.csv` from EagleGIS every Monday.
- **RAGAS eval** (`workflow_dispatch` + `GROQ_API_KEY`): `python scripts/eval_ragas.py`

## Production frontend

`frontend/config.js` sets `API_BASE`. For Cloud Run, point it at your service URL:

```javascript
window.API_BASE = "https://your-service-abc.run.app";
```

See [docs/DEPLOY_DOCKER.md](docs/DEPLOY_DOCKER.md) for the full Google setup.

## Notes

- Index is rebuilt when `data.csv` changes (hash in `faiss_index/manifest.json`).
- Set `SERVE_FRONTEND=false` in Cloud Run when frontend is hosted elsewhere.
- Optional tracing: `OTEL_ENABLED=true` + `pip install -r requirements-eval.txt`
