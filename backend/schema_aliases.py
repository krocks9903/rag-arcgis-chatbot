"""Map legacy arcgis CSV columns to meetings_ai_public gold schema."""
from __future__ import annotations

from typing import Any

import pandas as pd

# First matching column name wins.
ALIASES: dict[str, tuple[str, ...]] = {
    "application_id": ("ApplicationID", "ApplicationId"),
    "project_name": ("ProjectName", "ProjectTitle"),
    "location": ("Location", "LocationName", "AddressNormalized", "AddressRaw"),
    "meeting_date": ("MeetingDate",),
    "meeting_year": ("MeetingYear",),
    "summary": ("Summary", "CitationText"),
    "action_taken": ("ActionTaken", "Outcome"),
    "outcome": ("Outcome", "ActionTaken"),
    "status": ("Status",),
    "document_url": ("Document_Link", "PrimarySourceUrl"),
    "filename": ("Filename", "SourceFilename"),
    "latitude": ("Latitude", "Lat"),
    "longitude": ("Longitude", "Lon", "Lng"),
    "board": ("Board", "SourceBoard"),
}


def pick_column(df: pd.DataFrame, *logical_names: str) -> str | None:
    """Return the first physical column present for any logical alias group."""
    for name in logical_names:
        for col in ALIASES.get(name, (name,)):
            if col in df.columns:
                return col
    return None


def search_columns(df: pd.DataFrame) -> list[str]:
    """Columns to scan for keyword / token search."""
    keys = (
        "application_id",
        "project_name",
        "location",
        "filename",
        "summary",
    )
    cols: list[str] = []
    for key in keys:
        col = pick_column(df, key)
        if col and col not in cols:
            cols.append(col)
    return cols


def row_value(row: dict[str, Any], *logical_names: str, default: str = "") -> str:
    for name in logical_names:
        for col in ALIASES.get(name, (name,)):
            val = row.get(col)
            if val is not None and str(val).strip() and str(val).lower() != "nan":
                return str(val)
    return default
