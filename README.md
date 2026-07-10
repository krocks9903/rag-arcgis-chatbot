# RAG ArcGIS Chatbot

Router-first Q&A for Estero planning & zoning records: structured filters, keyword lookup, and corrective RAG with hybrid retrieval. The EagleGIS PDF extraction pipeline lives in this repo and produces the chatbot corpus.

## What you need

- Python 3.11 **or** Docker Desktop
- Groq API key (`GROQ_API_KEY`)
- Optional: GCP account for Cloud Run deploy
- Pipeline rebuild: Tesseract OCR (`apt install tesseract-ocr` on Linux)

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

## Pipeline architecture

```text
Question → Router
  ├─ structured  → pandas filters (counts, year, status, location)
  ├─ keyword     → ApplicationID / minutes / token search
  ├─ mixed         → keyword first, else RAG
  └─ rag           → BM25 + FAISS (RRF) → reranker → CRAG → Groq JSON
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
| `POST /load` | Upload replacement CSV (dev only) |

## Project structure

```text
rag-arcgis-chatbot/
├── .github/workflows/
│   ├── ci.yml                  # lint + backend pytest
│   ├── deploy.yml              # Cloud Run (opt-in)
│   ├── pipeline-ci.yml         # pipeline tests + rebuild guard
│   ├── pipeline-refresh.yml    # weekly scrape + rebuild + commit
│   └── pipeline-drift-watch.yml
├── backend/
│   ├── app.py                  # FastAPI + static frontend
│   ├── config.py
│   ├── store.py                # FAISS + BM25 index
│   ├── data/
│   │   ├── bronze/             # hand-curated geocode overrides, URL lookup
│   │   ├── silver/             # relational tables + QA triage
│   │   └── gold/
│   │       └── meetings_ai_public.csv   # chatbot corpus (~2,600 agenda items)
│   └── tests/golden_qa.json
├── frontend/
│   ├── config.js               # API_BASE for split local stack
│   └── ...
├── pipeline/                   # EagleGIS PDF-extraction pipeline
│   ├── build.py
│   ├── discover.py
│   ├── verify.py
│   └── tests/
├── pdfs/                       # source meeting-minute PDFs
├── docker-compose.yml
└── docs/DEPLOY_DOCKER.md
```

## Data pipeline

The `pipeline/` directory parses meeting-minute PDFs from `pdfs/` into medallion CSVs under `backend/data/`, resolves locations against Lee County parcel data, and exports `backend/data/gold/meetings_ai_public.csv` — the file the chatbot indexes.

```bash
pip install -r pipeline/requirements.txt
python pipeline/build.py --pdf-dir pdfs --source-csv pdfs/Estero_Meetings_Final.csv --out-dir backend/data
python pipeline/verify.py
python -m pytest pipeline/tests -q
```

See [`pipeline/README.md`](pipeline/README.md) for full details.

**Autonomous updates:** `pipeline-refresh.yml` runs weekly (and on new PDFs): scrapes estero-fl.gov for new minutes, rebuilds, verifies against Lee County parcels, and commits `backend/data/`. `pipeline-ci.yml` fails any PR whose committed data doesn't match a fresh rebuild.

## CI

- **ci.yml** — ruff + backend router/golden/smoke tests (no Groq key required)
- **pipeline-ci.yml** — pipeline pytest + deliverables up-to-date guard
- **pipeline-refresh.yml** — weekly data refresh from source PDFs
- **deploy.yml** — Cloud Run deploy when `ENABLE_DEPLOY=true`

## Production

Cloud Run serves the frontend and API from one container (`SERVE_FRONTEND=true`). The UI uses same-origin API calls; no `config.js` changes needed on Cloud Run.

Set `ENABLE_DEPLOY=true` and GCP secrets/vars per [docs/DEPLOY_DOCKER.md](docs/DEPLOY_DOCKER.md).

## Notes

- Corpus path: `backend/data/gold/meetings_ai_public.csv` (override with `CSV_PATH`)
- Index is rebuilt when the CSV changes (hash in `faiss_index/manifest.json`)
- Optional tracing: `OTEL_ENABLED=true` + `pip install -r requirements-eval.txt`
