# Ask Engage Estero

A RAG chatbot for the Village of Estero's Planning, Zoning & Design Board records and
EsteroToday.com community news, with a live ArcGIS map and a "Community Pulse" dashboard
(upcoming meetings, latest news, recent board decisions).

## Stack

- **Backend**: FastAPI + LangChain, single unified FAISS index, HuggingFace
  `sentence-transformers/all-MiniLM-L6-v2` embeddings, Groq `llama-3.1-8b-instant` for
  answer generation. Cards (board record / article) are built server-side from CSV metadata,
  never from LLM-generated text — see [Architecture](#architecture) below.
- **Frontend**: Vite + React 18 + TypeScript, using `@arcgis/core` as ES modules. This
  replaced an earlier vanilla JS / CDN-AMD-loader frontend (still present at `frontend/` for
  reference, untouched, works standalone) — the ESM rewrite exists specifically because mixing
  a CDN-script ArcGIS load with certain other CDN scripts caused AMD-loader collisions.
- **Data**: two CSVs merged into one FAISS index — `backend/data/data.csv` (321 Planning
  Zoning & Design Board + Village Council records) and `backend/data/esterotoday_content.csv`
  (628 EsteroToday.com articles), chunked into ~3,900 vectors. Single-pass dense retrieval
  (no separate per-source indexes, no router).

## Setup

### Backend

Requires **Python 3.11** (3.13 breaks wheel builds for some pinned ML dependencies here —
use 3.11 specifically).

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # add GROQ_API_KEY
uvicorn app:app --reload --port 8000
```

First startup builds the FAISS index from the two CSVs (~30–60s); subsequent startups hit
the MD5-based cache (see below) and load instantly unless the CSVs or chunking logic changed.

### Frontend

```powershell
cd frontend-react
npm install
npm run dev
```

Serves on `http://localhost:5173` and talks to the backend via `VITE_API_BASE` (defaults to
`http://localhost:8000` — see `frontend-react/.env.example`). There is **no dev-server proxy**;
the frontend calls the backend's absolute URL directly, so both must be running for local dev.

## Environment variables

| Variable | Where | Required | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | `backend/.env` | Yes | Groq API key for answer generation |
| `SCORE_THRESHOLD` | `backend/.env` (optional) | No (default `0.35`) | Minimum relevance score for a retrieved chunk to be used as context/card source |
| `RETRIEVE_K` | `backend/.env` (optional) | No (default `12`) | Number of chunks pulled per query before threshold filtering |
| `VITE_API_BASE` | `frontend-react/.env.local` | No (default `http://localhost:8000`) | Backend base URL the React app calls |

## Architecture

```
CSV rows (board + article) → ingest.py → per-record chunks with rich metadata
                                              (RecordId, dates, URLs, lat/lng, ...)
                                          ↓
                              FAISS index (MD5+version cached — see below)
                                          ↓
question → dense similarity search → relevance-score threshold
             │                                    │
             │ (nothing clears threshold)         │ (passes)
             ↓                                    ↓
     keyword-match fallback              Groq generates prose only
     (handles bare/short queries                  │
      like "wawa")                                ↓
                                    card built server-side from the winning
                                    chunk's metadata (never from LLM text)
                                          ↓
                          answer = prose + ```json card fence, unchanged
                          API contract the frontend already parses
```

Key points:

- **Unified index, single retrieval pass.** Board records and articles are chunked and
  embedded together; there's no separate router deciding which source to query.
- **`SEARCH:` enrichment headers.** Every chunk's embedded text is prefixed with a
  `SEARCH: <title> | <id> | <location> | ...` line so short, bare-name queries (e.g. "wawa")
  still retrieve well despite being diluted by everything else in a longer chunk. A DATE:
  header is prepended too, used both for retrieval and for the prompt's recency rules. These
  headers (`DATE:`, `SEARCH:`, `SOURCE_TYPE:`) are stripped before any text reaches the user
  (`ingest.strip_header_lines`) — they're retrieval aids, never shown.
- **Cards are metadata-driven, not LLM-authored.** The LLM only writes prose; a card's fields
  (title, application ID, dates, PDF link, lat/lng) are copied verbatim from the winning
  chunk's metadata in Python. This is a deliberate anti-hallucination measure — a board record
  card can only be emitted if the top-scoring chunk actually has a real `RecordId`.
- **MD5+version-cached index build.** `backend/faiss_index/manifest.json` stores an MD5 digest
  of both source CSVs plus a `CACHE_VERSION` string; the index only rebuilds when the CSVs
  change or `CACHE_VERSION` is bumped (bump it whenever the chunk/metadata schema changes).

## API

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | `{status, index_loaded}` |
| `/chat` | POST | `{question}` → `{answer, sources}`. `answer` is prose plus an optional trailing ` ```json ` card block. |
| `/load` | POST | `{csv_path}` — rebuilds the index with a different board CSV (website content always stays included) |
| `/recent-decisions` | GET | 5 most recent board decisions with a `ProjectName`, for the dashboard's Recent Decisions widget |

The frontend also attempts `POST /chat/stream` first and falls back to `/chat` on failure —
that streaming endpoint doesn't exist on this backend today, so it always falls back; the
fallback path is what's actually exercised.

## Repo layout

```
backend/
  app.py              FastAPI app: index build/cache, retrieval, card building, routes
  ingest.py           CSV → chunks with metadata (board rows + section-aware article chunking)
  schema_aliases.py   Column-name aliases for the board CSV's gold schema
  diagnose_retrieval.py  Standalone script: prints top-8 retrieval scores for probe queries
  data/               data.csv (board records), esterotoday_content.csv (articles)
  faiss_index/         gitignored — rebuilt from CSVs on first run
  config.py, models.py, store.py, retrieval.py, router.py, structured_path.py,
  keyword_path.py, rag_path.py, orchestrator.py, tracing.py, chunking.py
                      an earlier "router-first" architecture (structured/keyword/RAG
                      routing, BM25+FAISS hybrid retrieval, CRAG). Not imported by the
                      current app.py — kept for reference, not wired in.
frontend-react/       Active frontend — Vite + React + TypeScript (see its own README.md)
frontend/             Legacy vanilla JS frontend — untouched, still works standalone
docker-compose.yml, docs/DEPLOY_DOCKER.md, scripts/eval_ragas.py, .github/workflows/
                      Docker/Cloud Run/CI assets from the router-first architecture —
                      predate the current backend rewrite and haven't been re-verified
                      against it.
```

## Notes

- `backend/venv/`, `frontend-react/node_modules/`, `.env` files, and `backend/faiss_index/`
  are all gitignored — the index rebuilds locally on first run, no need to commit it.
- The CI workflow (`ci.yml`) and `backend/tests/golden_qa.json` target the older
  orchestrator/router modules, not the current `app.py` — treat CI status as unverified for
  the current backend until that's revisited.
