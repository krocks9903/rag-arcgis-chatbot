"""Score golden Q&A for RAG response optimization (no RAGAS required).

Examples:
  python scripts/eval_quality.py
  python scripts/eval_quality.py --retrieve-only --limit 10
  python scripts/eval_quality.py --variant concise
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from config import DEFAULT_CSV_PATH, EVAL_REPORTS_DIR, PROMPT_VARIANT
from prompt_loader import clear_prompt_cache
from store import build_store, get_store


def _load_golden(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm_id(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _ids_from_text(*parts: str) -> set[str]:
    import re

    blob = " ".join(parts)
    found = set()
    for m in re.finditer(r"\b((?:DOS|DCI|LDO|ADD|CPA|REZ)\d{4}-[A-Z]\d{3})\b", blob, re.I):
        found.add(_norm_id(m.group(1)))
    return found


def _score_case(case: dict[str, Any], *, retrieve_only: bool) -> dict[str, Any]:
    from orchestrator import answer_question
    from router import route_question

    question = case["question"]
    t0 = time.perf_counter()
    expected_route = case.get("route")
    got_route = route_question(question).value
    route_ok = expected_route is None or got_route == expected_route

    project_ids: list[str] = []
    summary = ""
    context = ""
    meta: dict[str, Any] = {}

    if retrieve_only:
        store = get_store()
        from retrieval import format_docs, hits_meta, hybrid_retrieve

        hits = hybrid_retrieve(store, question)
        context = format_docs(hits)
        meta = hits_meta(hits)
        for doc, _ in hits:
            cid = str(doc.metadata.get("chunk_id") or "")
            project_ids.extend(_ids_from_text(doc.page_content, cid))
            import re

            for m in re.finditer(r"application_id:\s*([^\n]+)", doc.page_content or "", re.I):
                project_ids.append(_norm_id(m.group(1)))
        summary = context[:2000]
        latency_ms = round((time.perf_counter() - t0) * 1000)
    else:
        result = answer_question(question)
        summary = result.summary or result.answer or ""
        project_ids = [_norm_id(p.id) for p in result.projects if p.id]
        project_ids.extend(_ids_from_text(summary))
        meta = dict(result.meta or {})
        got_route = result.route or got_route
        route_ok = expected_route is None or got_route == expected_route
        latency_ms = int(meta.get("latency_ms") or round((time.perf_counter() - t0) * 1000))

    got_ids = {i for i in project_ids if i}
    expect_ids = {_norm_id(x) for x in case.get("expect_ids") or []}
    id_hits = expect_ids & got_ids if expect_ids else set()
    id_recall = (len(id_hits) / len(expect_ids)) if expect_ids else None
    id_precision = (len(id_hits) / len(got_ids)) if expect_ids and got_ids else (1.0 if expect_ids == set() else None)

    must = [str(s) for s in case.get("must_contain") or []]
    must_not = [str(s) for s in case.get("must_not_contain") or []]
    blob = summary.lower()
    must_ok = all(s.lower() in blob for s in must) if must else True
    must_not_ok = all(s.lower() not in blob for s in must_not) if must_not else True

    checks = {
        "route_ok": route_ok,
        "must_contain_ok": must_ok,
        "must_not_contain_ok": must_not_ok,
    }
    if id_recall is not None:
        checks["id_recall"] = id_recall
        checks["id_hit"] = id_recall >= 1.0

    passed = all(
        v is True or v is None or (isinstance(v, float) and v >= 1.0)
        for k, v in checks.items()
        if k != "id_recall"
    )
    # Soft pass when no expect_ids: route + must_* only
    if expect_ids:
        passed = route_ok and must_ok and must_not_ok and (id_recall or 0) >= 1.0
    else:
        passed = route_ok and must_ok and must_not_ok

    return {
        "question": question,
        "expected_route": expected_route,
        "got_route": got_route,
        "passed": passed,
        "checks": checks,
        "expect_ids": sorted(expect_ids),
        "got_ids": sorted(got_ids)[:20],
        "id_recall": id_recall,
        "id_precision": id_precision,
        "latency_ms": latency_ms,
        "summary_preview": summary[:240],
        "meta": {
            "retrieved": meta.get("retrieved"),
            "best_score": meta.get("best_score"),
            "llm_mode": meta.get("llm_mode"),
            "prompt_variant": meta.get("prompt_variant") or PROMPT_VARIANT,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate golden Q&A quality")
    parser.add_argument("--retrieve-only", action="store_true", help="Skip LLM; score retrieval IDs only")
    parser.add_argument("--limit", type=int, default=0, help="Max cases (0 = all)")
    parser.add_argument("--variant", type=str, default="", help="PROMPT_VARIANT override")
    parser.add_argument(
        "--golden",
        type=Path,
        default=ROOT / "backend" / "tests" / "golden_qa.json",
    )
    args = parser.parse_args()

    if args.variant:
        os.environ["PROMPT_VARIANT"] = args.variant
        # Reload config constant used by rag_path via prompt_loader (reads env each call via PROMPT_VARIANT import)
        import config as cfg

        cfg.PROMPT_VARIANT = args.variant
        clear_prompt_cache()

    cases = _load_golden(args.golden)
    if args.limit > 0:
        cases = cases[: args.limit]

    if not Path(DEFAULT_CSV_PATH).exists():
        print(f"CSV missing: {DEFAULT_CSV_PATH}")
        return 1

    build_store(DEFAULT_CSV_PATH)
    rows = []
    for case in cases:
        # retrieve-only still scores expect_ids from chunks; skip pure structured without IDs if desired — keep all
        try:
            rows.append(_score_case(case, retrieve_only=args.retrieve_only))
        except Exception as e:
            rows.append(
                {
                    "question": case.get("question"),
                    "passed": False,
                    "error": str(e),
                }
            )

    n = len(rows)
    n_pass = sum(1 for r in rows if r.get("passed"))
    recalls = [r["id_recall"] for r in rows if isinstance(r.get("id_recall"), (int, float))]
    mean_recall = sum(recalls) / len(recalls) if recalls else None
    mean_latency = sum(r.get("latency_ms") or 0 for r in rows) / n if n else 0

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt_variant": os.getenv("PROMPT_VARIANT") or PROMPT_VARIANT,
        "retrieve_only": args.retrieve_only,
        "summary": {
            "cases": n,
            "passed": n_pass,
            "pass_rate": round(n_pass / n, 4) if n else 0,
            "mean_id_recall": round(mean_recall, 4) if mean_recall is not None else None,
            "mean_latency_ms": round(mean_latency, 1),
        },
        "cases": rows,
    }

    out_dir = Path(EVAL_REPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"quality_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2))
    print(f"Wrote {out_path}")
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
