#!/usr/bin/env python3
"""Stdio entrypoint for engage-estero-sync (local Cursor MCP).

Cloud Run serves the same tools over Streamable HTTP via backend/app.py (/mcp).
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agent_sync_mcp import main, mcp  # noqa: E402

__all__ = ["main", "mcp"]

if __name__ == "__main__":
    main()
