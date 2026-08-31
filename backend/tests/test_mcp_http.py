"""Agent-sync MCP helpers + Cloud Run HTTP auth gate."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_prepare_runtime_sync_dir_seeds(tmp_path, monkeypatch):
    from agent_sync_mcp import prepare_runtime_sync_dir

    seed = tmp_path / "seed"
    runtime = tmp_path / "runtime"
    seed.mkdir()
    (seed / "handoffs").mkdir()
    (seed / "shared-context.json").write_text('{"project":"t","claims":[]}\n', encoding="utf-8")
    (seed / "conventions.md").write_text("# hi\n", encoding="utf-8")
    (seed / "handoffs" / "note.md").write_text("handoff\n", encoding="utf-8")

    monkeypatch.setenv("AGENT_SYNC_SEED_DIR", str(seed))
    monkeypatch.setenv("AGENT_SYNC_DIR", str(runtime))

    out = prepare_runtime_sync_dir()
    assert out == runtime
    assert (runtime / "shared-context.json").is_file()
    assert (runtime / "conventions.md").read_text(encoding="utf-8").startswith("# hi")
    assert (runtime / "handoffs" / "note.md").is_file()


def test_mcp_bearer_prefers_mcp_api_key(monkeypatch):
    from agent_sync_mcp import mcp_bearer_token

    monkeypatch.setenv("ADMIN_API_KEY", "admin")
    monkeypatch.setenv("MCP_API_KEY", "mcp-only")
    assert mcp_bearer_token() == "mcp-only"
    monkeypatch.delenv("MCP_API_KEY")
    assert mcp_bearer_token() == "admin"


def test_mcp_http_auth_middleware(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "secret-token")

    from agent_sync_mcp import mcp_bearer_token

    class McpAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path.startswith("/mcp"):
                expected = mcp_bearer_token()
                auth = request.headers.get("authorization") or ""
                if not auth.startswith("Bearer ") or auth[len("Bearer ") :].strip() != expected:
                    return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(McpAuthMiddleware)

    @app.get("/mcp")
    def ok():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer secret-token"}).status_code == 200
