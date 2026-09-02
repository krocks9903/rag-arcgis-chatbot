"""Agent-sync MCP helpers + Cloud Run HTTP auth gate."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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


def test_get_session_brief_compact(tmp_path, monkeypatch):
    import json

    from agent_sync_mcp import get_session_brief, get_shared_context

    sync = tmp_path / "sync"
    sync.mkdir()
    (sync / "handoffs").mkdir()
    ctx = {
        "project": "t",
        "updated_at": "2026-09-02T00:00:00Z",
        "active_focus": "ci",
        "priorities": ["ship"],
        "claims": [],
        "blockers": [{"id": "dns", "summary": "waiting"}],
        "do_not": ["force-push main"],
    }
    (sync / "shared-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    (sync / "handoffs" / "2026-09-01-a.md").write_text("# Old\n\nstale\n", encoding="utf-8")
    new = sync / "handoffs" / "2026-09-02-b.md"
    new.write_text("# New\n\nfresh handoff body\n", encoding="utf-8")
    now = time.time()
    os.utime(sync / "handoffs" / "2026-09-01-a.md", (now - 60, now - 60))
    os.utime(new, (now, now))

    monkeypatch.setenv("AGENT_SYNC_DIR", str(sync))

    brief = json.loads(get_session_brief())
    assert brief["version"] == "2026-09-02T00:00:00Z"
    assert brief["active_focus"] == "ci"
    assert brief["blockers"] == [{"id": "dns", "summary": "waiting"}]
    assert brief["latest_handoff"]["file"] == "2026-09-02-b.md"
    assert "fresh" in brief["latest_handoff"]["excerpt"]
    assert "team" not in brief

    full = json.loads(get_shared_context())
    assert full["project"] == "t"
    assert "\n" not in get_shared_context()


def test_list_handoffs_excerpt_and_get_handoff(tmp_path, monkeypatch):
    import json

    from agent_sync_mcp import get_handoff, list_handoffs

    sync = tmp_path / "sync"
    (sync / "handoffs").mkdir(parents=True)
    long_body = "x" * 500
    (sync / "handoffs" / "note.md").write_text(f"# Title\n\n{long_body}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_SYNC_DIR", str(sync))

    listed = json.loads(list_handoffs(limit=1, excerpt_chars=50))
    assert len(listed["handoffs"]) == 1
    assert len(listed["handoffs"][0]["excerpt"]) <= 50
    assert listed["handoffs"][0]["file"] == "note.md"

    full = get_handoff("note.md")
    assert long_body in full
    assert json.loads(get_handoff("../etc/passwd"))["error"] == "invalid filename"


def test_write_tools_return_compact_ack(tmp_path, monkeypatch):
    import json

    from agent_sync_mcp import claim_area, release_area, update_shared_context

    sync = tmp_path / "sync"
    sync.mkdir()
    (sync / "shared-context.json").write_text('{"claims":[],"blockers":[]}\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_SYNC_DIR", str(sync))

    claimed = json.loads(claim_area("backend/tests", "nolan", note="pytest"))
    assert claimed == {"ok": True, "area": "backend/tests"}

    blocked = json.loads(claim_area("backend/tests", "ethan"))
    assert blocked["ok"] is False
    assert blocked["error"] == "blocked"

    updated = json.loads(update_shared_context(active_focus="tests", updated_by="nolan"))
    assert updated["ok"] is True
    assert "version" in updated
    assert "priorities" not in updated

    released = json.loads(release_area("backend/tests", "nolan"))
    assert released == {"ok": True, "released": 1}
