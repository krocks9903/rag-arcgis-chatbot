"""Load the meetings CSV and normalize column names.

The EagleGIS gold export (meetings_ai_public.csv) renamed several columns
the retrieval code was written against. Alias them back on load so both
the old and new CSV schemas work.
"""
from __future__ import annotations

import pandas as pd

COLUMN_ALIASES = {
    "ApplicationId": "ApplicationID",
    "AddressNormalized": "Location",
    "PrimarySourceUrl": "Document_Link",
    "SourceFilename": "Filename",
}


def load_dataframe(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, encoding="utf-8")
    renames = {
        old: new
        for old, new in COLUMN_ALIASES.items()
        if old in df.columns and new not in df.columns
    }
    return df.rename(columns=renames)
