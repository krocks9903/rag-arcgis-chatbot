"""Bearer-token gate for administrator endpoints."""
from __future__ import annotations

import os

from fastapi import Header, HTTPException

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not ADMIN_API_KEY:
        raise HTTPException(
            503,
            "Admin access is not configured. Set ADMIN_API_KEY in the server environment.",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[len("Bearer ") :].strip()
    if token != ADMIN_API_KEY:
        raise HTTPException(401, "Unauthorized")
