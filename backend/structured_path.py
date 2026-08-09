"""Deterministic pandas filters for counts and list-style questions."""
from __future__ import annotations

import re

import pandas as pd

from models import ChatResponse, ProjectOut, RouteKind
from schema_aliases import pick_column, row_value

YEAR_RE = re.compile(r"\b(20\d{2})\b")
STATUS_MAP = {
    "approved": "Approved",
    "denied": "Denied",
    "continued": "Continued",
    "rezoning": "rezoning",
}

# Generic words to skip when hunting for the location term in a question, so a
# query like "how many projects at Coconut Point" filters on the place, not on
# the noun "projects". Statuses (approved/denied/…) are already applied as
# explicit filters above, so they're skipped here too.
_LOCATION_STOPWORDS = {
    "how", "many", "what", "were", "was", "the", "show", "list", "estero",
    "project", "projects", "decision", "decisions", "record", "records",
    "meeting", "meetings", "development", "developments", "happening", "there",
    "about", "tell", "give", "have", "with", "near", "around", "along",
    "approved", "denied", "continued", "rezoning", "status", "update", "updates",
}


def _location_tokens(q: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]{4,}", q) if t not in _LOCATION_STOPWORDS]


def _clip_at_sentence(text: str, max_len: int = 480) -> str:
    """Hard-cap display length without mid-word/mid-sentence cuts when possible."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    chunk = text[:max_len]
    last = max(chunk.rfind(". "), chunk.rfind("! "), chunk.rfind("? "))
    if last >= max_len // 3:
        return chunk[: last + 1].strip()
    space = chunk.rfind(" ")
    if space >= max_len // 2:
        return chunk[:space].rstrip(",;: ") + "…"
    return chunk.rstrip() + "…"


def _to_coord(value) -> float | None:
    """Parse a lat/lng cell to a float, treating blanks and 0/NaN as missing."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num == 0.0:  # NaN or an unset 0,0 placeholder
        return None
    return num


def _row_to_project(row: dict) -> ProjectOut:
    outcome = row_value(row, "outcome", "action_taken", "status")
    status = "No decision recorded"
    low = outcome.lower()
    if "approved" in low:
        status = "Approved"
    elif "denied" in low:
        status = "Denied"
    elif "continued" in low:
        status = "Continued"
    title = _clip_at_sentence(row_value(row, "project_name"), 140)
    return ProjectOut(
        title=title,
        id=row_value(row, "application_id"),
        location=row_value(row, "location"),
        summary=_clip_at_sentence(row_value(row, "summary", "outcome", "action_taken"), 480),
        status=status,
        date=row_value(row, "meeting_date"),
        document_url=row_value(row, "document_url"),
        lat=_to_coord(row_value(row, "latitude")),
        lng=_to_coord(row_value(row, "longitude")),
    )


def attach_coords(projects: list[ProjectOut], df: pd.DataFrame) -> None:
    """Backfill lat/lng on projects that don't already have them (RAG results,
    whose fields come from the LLM, not a dataframe row). Matches a project to a
    source record by application id first, then exact document URL, then title,
    and copies that record's coordinates. Mutates *projects* in place.
    """
    if df is None or df.empty:
        return
    needing = [p for p in projects if p.lat is None or p.lng is None]
    if not needing:
        return
    rows = df.to_dict(orient="records")
    by_id: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    for row in rows:
        lat, lng = _to_coord(row_value(row, "latitude")), _to_coord(row_value(row, "longitude"))
        if lat is None or lng is None:
            continue
        for index, key in (
            (by_id, row_value(row, "application_id")),
            (by_url, row_value(row, "document_url")),
            (by_title, row_value(row, "project_name")),
        ):
            key = (key or "").strip().lower()
            if key:
                index.setdefault(key, row)
    for project in needing:
        match = (
            by_id.get((project.id or "").strip().lower())
            or by_url.get((project.document_url or "").strip().lower())
            or by_title.get((project.title or "").strip().lower())
        )
        if match:
            project.lat = _to_coord(row_value(match, "latitude"))
            project.lng = _to_coord(row_value(match, "longitude"))


def _apply_filters(df: pd.DataFrame, question: str) -> pd.DataFrame:
    out = df.copy()
    q = question.lower()
    year_m = YEAR_RE.search(question)
    year_col = pick_column(out, "meeting_year")
    if year_m and year_col:
        out = out[out[year_col].astype(str) == year_m.group(1)]
    outcome_col = pick_column(out, "outcome")
    if "approved" in q and outcome_col:
        out = out[out[outcome_col].astype(str).str.contains("Approved", case=False, na=False)]
    elif "denied" in q and outcome_col:
        out = out[out[outcome_col].astype(str).str.contains("Denied", case=False, na=False)]
    elif "continued" in q and outcome_col:
        out = out[out[outcome_col].astype(str).str.contains("Continued", case=False, na=False)]
    if "rezoning" in q:
        for key in ("project_name", "summary", "outcome"):
            col = pick_column(out, key)
            if col:
                out = out[out[col].astype(str).str.contains("rezon", case=False, na=False)]
                break
    # street / location token
    for token in _location_tokens(q):
        mask = pd.Series(False, index=out.index)
        for key in ("location", "project_name", "summary"):
            col = pick_column(out, key)
            if col:
                mask |= out[col].astype(str).str.contains(token, case=False, na=False)
        if mask.any():
            out = out[mask]
            break
    return out


def answer_structured(df: pd.DataFrame, question: str) -> ChatResponse:
    filtered = _apply_filters(df, question)
    n = len(filtered)
    if n == 0:
        return ChatResponse(
            summary="I don't have records on that.",
            projects=[],
            answer="I don't have records on that.",
            route=RouteKind.STRUCTURED.value,
            meta={"matched_rows": 0},
        )
    projects = [_row_to_project(r) for r in filtered.head(10).to_dict(orient="records")]
    summary = f"Found {n} matching record{'s' if n != 1 else ''} in the official dataset."
    return ChatResponse(
        summary=summary,
        projects=projects,
        answer=summary,
        route=RouteKind.STRUCTURED.value,
        meta={"matched_rows": n},
    )
