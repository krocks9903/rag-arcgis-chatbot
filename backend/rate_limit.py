"""In-memory sliding-window rate limits keyed by device id and client IP."""
from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Literal

from fastapi import HTTPException, Request

Scope = Literal["chat", "public_write"]

_DEVICE_RE = re.compile(r"^[A-Za-z0-9_.-]{8,128}$")
_DEVICE_HEADER = "x-device-id"

ENABLE_RATE_LIMIT = os.getenv("ENABLE_RATE_LIMIT", "true").lower() not in {"0", "false", "no"}

# Chat is read-heavy and cheap to allow more of; report/feedback writes are
# capped tighter since each one persists to disk and shows up in the admin
# inbox for a human to read.
RATE_LIMIT_CHAT_DEVICE = int(os.getenv("RATE_LIMIT_CHAT_DEVICE", "30"))
RATE_LIMIT_CHAT_IP = int(os.getenv("RATE_LIMIT_CHAT_IP", "60"))
RATE_LIMIT_CHAT_WINDOW_S = int(os.getenv("RATE_LIMIT_CHAT_WINDOW_S", "60"))
RATE_LIMIT_WRITE_DEVICE = int(os.getenv("RATE_LIMIT_WRITE_DEVICE", "5"))
RATE_LIMIT_WRITE_IP = int(os.getenv("RATE_LIMIT_WRITE_IP", "10"))
RATE_LIMIT_WRITE_WINDOW_S = int(os.getenv("RATE_LIMIT_WRITE_WINDOW_S", "300"))


class SlidingWindowLimiter:
    """Process-local sliding window: at most `limit` events per `window_s`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def check(self, key: str, limit: int, window_s: float) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). Records the hit when allowed."""
        if limit <= 0 or window_s <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_s
        with self._lock:
            q = self._events[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, int(window_s - (now - q[0])) + 1)
                return False, retry
            q.append(now)
            return True, 0


_limiter = SlidingWindowLimiter()


def get_limiter() -> SlidingWindowLimiter:
    return _limiter


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        # Cloud Run / proxies: leftmost is the original client.
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def device_id(request: Request) -> str | None:
    raw = (request.headers.get(_DEVICE_HEADER) or "").strip()
    if not raw or not _DEVICE_RE.fullmatch(raw):
        return None
    return raw


def _limits_for(scope: Scope) -> tuple[int, int, int]:
    if scope == "chat":
        return (RATE_LIMIT_CHAT_DEVICE, RATE_LIMIT_CHAT_IP, RATE_LIMIT_CHAT_WINDOW_S)
    return (RATE_LIMIT_WRITE_DEVICE, RATE_LIMIT_WRITE_IP, RATE_LIMIT_WRITE_WINDOW_S)


def enforce_rate_limit(scope: Scope) -> Callable[[Request], None]:
    """FastAPI dependency factory for chat or public write endpoints."""

    def _dependency(request: Request) -> None:
        if not ENABLE_RATE_LIMIT:
            return

        device_limit, ip_limit, window_s = _limits_for(scope)
        ip = client_ip(request)
        device = device_id(request)
        limiter = get_limiter()

        # Device bucket first when present (records the hit on success).
        if device is not None:
            ok, retry = limiter.check(f"{scope}:device:{device}", device_limit, window_s)
            if not ok:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Try again in {retry} seconds.",
                    headers={"Retry-After": str(retry)},
                )

        ok, retry = limiter.check(f"{scope}:ip:{ip}", ip_limit, window_s)
        if not ok:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again in {retry} seconds.",
                headers={"Retry-After": str(retry)},
            )

    return _dependency
