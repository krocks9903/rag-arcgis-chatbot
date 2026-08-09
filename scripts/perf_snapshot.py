"""Snapshot API latency for before/after speed checks.

Usage:
  python scripts/perf_snapshot.py [--base-url URL] [--out path]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def timed_get(url: str, timeout: float = 120.0) -> dict:
    t0 = time.perf_counter()
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        status = resp.status
    ms = round((time.perf_counter() - t0) * 1000)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body[:200]}
    return {"url": url, "status": status, "ms": ms, "body": payload}


def timed_post_chat(url: str, question: str, timeout: float = 180.0) -> dict:
    t0 = time.perf_counter()
    data = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        status = resp.status
    total_ms = round((time.perf_counter() - t0) * 1000)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}
    meta = payload.get("meta") or {}
    return {
        "url": url,
        "status": status,
        "total_ms": total_ms,
        "retrieve_ms": meta.get("retrieve_ms"),
        "generate_ms": meta.get("generate_ms"),
        "latency_ms": meta.get("latency_ms"),
        "route": payload.get("route"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="https://rag-arcgis-chatbot-ygecps65ja-uc.a.run.app",
    )
    parser.add_argument("--out", default="scripts/perf_baseline.json")
    parser.add_argument("--label", default="before")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    snapshot = {
        "label": args.label,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "ready": None,
        "warmup": None,
        "chat": None,
        "notes": {
            "hero_png_bytes_before": 1311375,
            "hero_webp_bytes_after": 90504,
            "ready_ttfb_s_before_warm_instance": 0.167,
        },
    }
    try:
        snapshot["ready"] = timed_get(f"{base}/ready")
        snapshot["warmup"] = timed_get(f"{base}/warmup", timeout=300.0)
        snapshot["chat"] = timed_post_chat(
            f"{base}/chat",
            "What was approved on Corkscrew Road?",
        )
    except urllib.error.URLError as e:
        snapshot["error"] = str(e)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
