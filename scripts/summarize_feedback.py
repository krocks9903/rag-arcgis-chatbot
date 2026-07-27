"""Summarize thumbs feedback for prompt/golden iteration."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from config import FEEDBACK_FILE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=Path(FEEDBACK_FILE))
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    if not args.path.is_file():
        print(f"No feedback file at {args.path}")
        return 0

    rows = []
    for line in args.path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    ratings = Counter(r.get("rating") for r in rows)
    down = [r for r in rows if r.get("rating") == "down"]
    # Most common downvoted questions
    q_counts = Counter((r.get("question") or "").strip() for r in down)
    print(json.dumps({
        "total": len(rows),
        "ratings": dict(ratings),
        "top_downvoted_questions": [
            {"question": q, "count": c} for q, c in q_counts.most_common(args.top) if q
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
