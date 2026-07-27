#!/usr/bin/env python3
"""Sweep retrieval knobs against golden expect_ids (retrieve-only).

Example:
  python scripts/sweep_retrieval.py --limit 12
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_eval(env: dict[str, str], limit: int) -> dict:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "eval_quality.py"),
        "--retrieve-only",
    ]
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    # Parse last report path from stdout
    out = proc.stdout or ""
    report_path = None
    for line in out.splitlines():
        if line.startswith("Wrote "):
            report_path = line.replace("Wrote ", "").strip()
    if not report_path or not Path(report_path).is_file():
        raise RuntimeError(f"eval failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(Path(report_path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep retrieval hyperparameters")
    parser.add_argument("--limit", type=int, default=0, help="Limit golden cases (0=all)")
    args = parser.parse_args()

    grid = {
        "RERANK_K": ["3", "5", "8"],
        "SCORE_THRESHOLD": ["0.15", "0.25", "0.35"],
        "RECENCY_BOOST": ["0.0", "0.35", "0.5"],
        "CRAG_MAX_ITERS": ["1", "2"],
    }
    # Keep the grid small: product of first two full + fix others, plus a few combined — 
    # Full product is 3*3*3*2=54 which is slow. Use a reduced cartesian of key axes.
    keys = ["RERANK_K", "SCORE_THRESHOLD", "RECENCY_BOOST", "CRAG_MAX_ITERS"]
    combos = list(itertools.product(*(grid[k] for k in keys)))
    # Cap at 18 by stepping
    if len(combos) > 18:
        combos = combos[:: max(1, len(combos) // 18)][:18]

    base_env = os.environ.copy()
    ranked = []
    for values in combos:
        cfg = dict(zip(keys, values))
        env = base_env.copy()
        env.update(cfg)
        print(f"Running {cfg} …", flush=True)
        try:
            report = _run_eval(env, args.limit)
            summary = report.get("summary") or {}
            ranked.append(
                {
                    "config": cfg,
                    "pass_rate": summary.get("pass_rate"),
                    "mean_id_recall": summary.get("mean_id_recall"),
                    "mean_latency_ms": summary.get("mean_latency_ms"),
                    "passed": summary.get("passed"),
                    "cases": summary.get("cases"),
                }
            )
        except Exception as e:
            ranked.append({"config": cfg, "error": str(e), "mean_id_recall": -1, "pass_rate": -1})

    def sort_key(row: dict):
        return (
            row.get("mean_id_recall") if row.get("mean_id_recall") is not None else -1,
            row.get("pass_rate") if row.get("pass_rate") is not None else -1,
            -(row.get("mean_latency_ms") or 0),
        )

    ranked.sort(key=sort_key, reverse=True)
    print("\n=== Top configs ===")
    for i, row in enumerate(ranked[:8], 1):
        print(f"{i}. {json.dumps(row, indent=None)}")

    out = ROOT / "backend" / "data" / "eval_reports" / "sweep_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ranked": ranked}, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
