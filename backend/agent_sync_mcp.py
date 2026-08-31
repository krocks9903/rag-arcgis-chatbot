"""Engage Estero agent-sync MCP (stdio + Streamable HTTP for Cloud Run).

Git-backed coordination for Nolan / Ethan / Krish Cursor agents.
On Cloud Run, seed files are baked into the image and copied to a writable
runtime dir (default /tmp/agent-sync). Commits of agent-sync/ remain the
durable cross-deploy source of truth.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
_DEFAULT_SEED = _REPO_ROOT / "agent-sync"
if not _DEFAULT_SEED.is_dir():
    # Docker layout: /app/backend + /app/agent-sync
    alt = _BACKEND_DIR.parent / "agent-sync"
    if alt.is_dir():
        _DEFAULT_SEED = alt


def _runtime_sync_dir() -> Path:
    return Path(
        os.environ.get("AGENT_SYNC_DIR")
        or (
            "/tmp/agent-sync"
            if os.environ.get("K_SERVICE")  # Cloud Run
            else str(_DEFAULT_SEED)
        )
    )


def _seed_sync_dir() -> Path:
    return Path(os.environ.get("AGENT_SYNC_SEED_DIR") or str(_DEFAULT_SEED))


def prepare_runtime_sync_dir() -> Path:
    """Ensure writable AGENT_SYNC_DIR, seeded from image/repo copy when empty."""
    dest = _runtime_sync_dir()
    seed = _seed_sync_dir()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "handoffs").mkdir(parents=True, exist_ok=True)
    if seed.is_dir() and seed.resolve() != dest.resolve():
        for name in ("shared-context.json", "conventions.md"):
            src = seed / name
            dst = dest / name
            if src.is_file() and not dst.is_file():
                shutil.copy2(src, dst)
        seed_h = seed / "handoffs"
        if seed_h.is_dir():
            for path in seed_h.glob("*.md"):
                target = dest / "handoffs" / path.name
                if not target.is_file():
                    shutil.copy2(path, target)
    logger.info("agent-sync runtime dir=%s seed=%s", dest, seed)
    return dest


def _paths() -> tuple[Path, Path, Path]:
    sync = _runtime_sync_dir()
    return sync, sync / "shared-context.json", sync / "handoffs"


_HTTP_MODE = os.getenv("ENABLE_MCP_HTTP", "false").lower() not in {"0", "false", "no"}

mcp = FastMCP(
    "engage-estero-sync",
    instructions=(
        "Engage Estero multi-agent coordination. Call get_shared_context before "
        "non-trivial work; write_handoff before ending a session with unfinished work; "
        "claim_area/release_area for soft locks. Commit agent-sync/ after local updates; "
        "Cloud Run serves the same tools over Streamable HTTP at /mcp."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        # Cloud Run host is *.run.app; disable Host allowlist for remote MCP.
        enable_dns_rebinding_protection=not _HTTP_MODE,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"] if not _HTTP_MODE else [],
        allowed_origins=(
            ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"] if not _HTTP_MODE else []
        ),
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_context() -> dict[str, Any]:
    _, context_path, _ = _paths()
    if not context_path.is_file():
        return {
            "project": "rag-arcgis-chatbot",
            "updated_at": _now(),
            "priorities": [],
            "owners": {},
            "claims": [],
            "blockers": [],
            "do_not": [],
        }
    return json.loads(context_path.read_text(encoding="utf-8"))


def _write_context(data: dict[str, Any]) -> None:
    sync, context_path, _ = _paths()
    sync.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    context_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "agent"


@mcp.tool()
def get_shared_context() -> str:
    """Read Engage Estero shared priorities, owners, claims, and blockers."""
    return json.dumps(_read_context(), indent=2)


@mcp.tool()
def get_conventions() -> str:
    """Return team coding and process conventions for this repo."""
    sync, _, _ = _paths()
    conventions = sync / "conventions.md"
    if not conventions.is_file():
        return "No conventions.md found under agent-sync/."
    return conventions.read_text(encoding="utf-8")


@mcp.tool()
def list_handoffs(limit: int = 10) -> str:
    """List recent handoff notes (newest first)."""
    _, _, handoffs_dir = _paths()
    handoffs_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [p for p in handoffs_dir.glob("*.md") if p.name.upper() != "README.MD"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[: max(1, min(limit, 50))]
    if not files:
        return "No handoffs yet."
    parts: list[str] = []
    for path in files:
        parts.append(f"## {path.name}\n\n{path.read_text(encoding='utf-8').strip()}\n")
    return "\n---\n\n".join(parts)


@mcp.tool()
def write_handoff(from_agent: str, to_agent: str, summary: str) -> str:
    """Write a handoff note for the next agent. Commit agent-sync/ afterward when local."""
    sync, _, handoffs_dir = _paths()
    handoffs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = handoffs_dir / f"{stamp}-{_slug(from_agent)}.md"
    if path.exists():
        path = handoffs_dir / (
            f"{stamp}-{_slug(from_agent)}-{datetime.now(timezone.utc).strftime('%H%M%S')}.md"
        )
    body = (
        f"# Handoff — {from_agent} → {to_agent}\n\n"
        f"_Written {_now()}_\n\n"
        f"{summary.strip()}\n"
    )
    path.write_text(body, encoding="utf-8")
    ctx = _read_context()
    ctx["updated_by"] = _slug(from_agent)
    _write_context(ctx)
    try:
        rel = path.relative_to(sync.parent)
    except ValueError:
        rel = path
    return f"Wrote {rel}"


@mcp.tool()
def update_shared_context(
    active_focus: str | None = None,
    priorities_json: str | None = None,
    blocker_add: str | None = None,
    blocker_clear_id: str | None = None,
    updated_by: str = "agent",
) -> str:
    """Patch shared context. Pass priorities_json as a JSON array of strings."""
    ctx = _read_context()
    if active_focus is not None:
        ctx["active_focus"] = active_focus
    if priorities_json:
        parsed = json.loads(priorities_json)
        if not isinstance(parsed, list):
            raise ValueError("priorities_json must be a JSON array of strings")
        ctx["priorities"] = parsed
    blockers = list(ctx.get("blockers") or [])
    if blocker_clear_id:
        blockers = [b for b in blockers if b.get("id") != blocker_clear_id]
    if blocker_add:
        blockers.append(
            {
                "id": _slug(blocker_add)[:40],
                "summary": blocker_add,
                "owner": updated_by,
            }
        )
    ctx["blockers"] = blockers
    ctx["updated_by"] = _slug(updated_by)
    _write_context(ctx)
    return json.dumps(ctx, indent=2)


@mcp.tool()
def claim_area(area: str, agent: str, note: str = "") -> str:
    """Soft-claim a work area so other agents avoid colliding. Not a hard lock."""
    ctx = _read_context()
    claims = list(ctx.get("claims") or [])
    area_key = area.strip()
    for claim in claims:
        if claim.get("area") == area_key and claim.get("agent") != _slug(agent):
            return (
                f"BLOCKED: {area_key} already claimed by {claim.get('agent')} "
                f"({claim.get('note') or 'no note'}). Coordinate before editing."
            )
    claims = [c for c in claims if c.get("area") != area_key]
    claims.append(
        {
            "area": area_key,
            "agent": _slug(agent),
            "note": note,
            "claimed_at": _now(),
        }
    )
    ctx["claims"] = claims
    ctx["updated_by"] = _slug(agent)
    _write_context(ctx)
    return json.dumps({"ok": True, "claims": claims}, indent=2)


@mcp.tool()
def release_area(area: str, agent: str) -> str:
    """Release a soft claim on a work area."""
    ctx = _read_context()
    before = list(ctx.get("claims") or [])
    after = [
        c
        for c in before
        if not (c.get("area") == area.strip() and c.get("agent") == _slug(agent))
    ]
    ctx["claims"] = after
    ctx["updated_by"] = _slug(agent)
    _write_context(ctx)
    return json.dumps({"released": len(before) - len(after), "claims": after}, indent=2)


@mcp.resource("agent-sync://context")
def resource_context() -> str:
    """Shared priorities / owners / blockers."""
    return json.dumps(_read_context(), indent=2)


@mcp.resource("agent-sync://conventions")
def resource_conventions() -> str:
    """Team conventions markdown."""
    sync, _, _ = _paths()
    conventions = sync / "conventions.md"
    if not conventions.is_file():
        return ""
    return conventions.read_text(encoding="utf-8")


def mcp_http_enabled() -> bool:
    return os.getenv("ENABLE_MCP_HTTP", "false").lower() not in {"0", "false", "no"}


def mcp_bearer_token() -> str:
    """Token for Streamable HTTP MCP (MCP_API_KEY, else ADMIN_API_KEY)."""
    return (os.getenv("MCP_API_KEY") or os.getenv("ADMIN_API_KEY") or "").strip()


def main() -> None:
    prepare_runtime_sync_dir()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
