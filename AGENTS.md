# Engage Estero — Ask chatbot (`rag-arcgis-chatbot`)

## Stack

- Backend: FastAPI under `backend/`
- Frontend: React Pulse under `frontend-react/`
- Pipeline: `pipeline/` (Lee County / deliverables)

## Team

| Person | Focus |
| --- | --- |
| Nolan (`noleysc`) | QA, tests, events sources, release checklist |
| Ethan | Backend RAG, retrieval, chat routing |
| Krish | Frontend / Pulse UI |

## Coordination

Use the **`engage-estero-sync`** MCP:

- **Local (stdio):** `.cursor/mcp.json` → `tools/engage-estero-mcp/server.py`
- **Cloud Run (Streamable HTTP):** `https://<service>.run.app/mcp` with
  `Authorization: Bearer <MCP_API_KEY|ADMIN_API_KEY>`.
  Copy `.cursor/mcp.cloud.example.json` into `.cursor/mcp.json` and set
  `ENGAGE_MCP_URL` + `ENGAGE_MCP_TOKEN`.

Tools: `get_shared_context` / `update_shared_context` / `get_conventions` /
`list_handoffs` / `write_handoff` / `claim_area` / `release_area`

Shared seed state is git-tracked under `agent-sync/` (baked into the image;
runtime writes go to `/tmp/agent-sync` on Cloud Run). Pull before starting;
commit `agent-sync/` when priorities/handoffs should survive the next deploy.

## Hard constraints

- Events chat must not hijack planning/zoning questions.
- Soft-fail third-party event scrapers; do not add brittle Hertz/Amerant scrapers.
- Prefer `langchain_core.documents.Document` over `langchain.schema`.
- Only commit/push when the human asks.
