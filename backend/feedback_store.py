"""Persist chat thumbs feedback as JSONL for response optimization."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from config import FEEDBACK_DIR, FEEDBACK_FILE
from models import FeedbackRequest


def append_feedback(req: FeedbackRequest) -> dict[str, Any]:
    os.makedirs(FEEDBACK_DIR, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": req.session_id,
        "question": req.question,
        "rating": req.rating,
        "comment": req.comment or "",
        "route": req.route or "",
        "summary": (req.summary or "")[:2000],
        "project_ids": list(req.project_ids or []),
        "meta": req.meta or {},
    }
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "path": FEEDBACK_FILE}
