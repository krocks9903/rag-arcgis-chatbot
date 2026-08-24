"""Registry of Engage Estero content sources.

Adding a source means appending one `SourceSpec` here plus a fetch function in
`wordpress.py`. The sync CLI, index wiring, and cache invalidation all iterate
this registry, so nothing else needs to learn about the new source.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from config import ENGAGE_ESTERO_DIR, ENABLED_SOURCE_KEYS, LEGACY_ARTICLES_CSV
from sources.base import ContentRecord, SourceSpec, file_hash, read_records

if TYPE_CHECKING:  # LangChain is only needed to build documents, not to sync.
    from langchain.schema import Document

SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        key="posts",
        source_type="website_article",
        label="EsteroToday news posts",
        csv_name="posts.csv",
        legacy_csv=LEGACY_ARTICLES_CSV,
    ),
    SourceSpec(
        key="pages",
        source_type="website_page",
        label="Evergreen site pages",
        csv_name="pages.csv",
    ),
    SourceSpec(
        key="events",
        source_type="event",
        label="Community and Village events",
        csv_name="events.csv",
    ),
    SourceSpec(
        key="documents",
        source_type="document",
        label="Published PDF documents",
        csv_name="documents.csv",
    ),
)

SPECS_BY_KEY = {spec.key: spec for spec in SOURCE_SPECS}


def enabled_specs() -> list[SourceSpec]:
    """Specs allowed by ENABLED_SOURCE_KEYS (empty setting means all)."""
    if not ENABLED_SOURCE_KEYS:
        return list(SOURCE_SPECS)
    return [spec for spec in SOURCE_SPECS if spec.key in ENABLED_SOURCE_KEYS]


def resolve_csv(spec: SourceSpec, base_dir: str = ENGAGE_ESTERO_DIR) -> str | None:
    """Preferred CSV for a source, falling back to its legacy file."""
    path = spec.csv_path(base_dir)
    if os.path.exists(path):
        return path
    if spec.legacy_csv and os.path.exists(spec.legacy_csv):
        return spec.legacy_csv
    return None


def load_records(spec: SourceSpec, base_dir: str = ENGAGE_ESTERO_DIR) -> list[ContentRecord]:
    path = resolve_csv(spec, base_dir)
    if not path:
        return []
    records = read_records(path)
    for record in records:
        # Legacy article rows predate record_id/source_type; backfill so they
        # merge and chunk identically to freshly synced rows.
        if not record.record_id:
            record.record_id = record.url or record.title
        if not record.source_type:
            record.source_type = spec.source_type
    return records


def source_hashes(base_dir: str = ENGAGE_ESTERO_DIR) -> dict[str, str]:
    """Content hash per enabled source, for index cache invalidation."""
    hashes: dict[str, str] = {}
    for spec in enabled_specs():
        path = resolve_csv(spec, base_dir)
        digest = file_hash(path) if path else None
        if digest:
            hashes[spec.key] = digest
    return hashes


def load_documents(base_dir: str = ENGAGE_ESTERO_DIR) -> list["Document"]:
    """Every enabled source's records as retrievable chunks.

    Imports the chunker here so the sync CLI can run with only `requests`
    installed, without pulling the LangChain stack.
    """
    from sources.documents import records_to_documents

    docs: list["Document"] = []
    for spec in enabled_specs():
        records = load_records(spec, base_dir)
        if not records:
            continue
        source_docs = records_to_documents(records)
        docs.extend(source_docs)
        print(f"  {spec.label}: {len(records)} records → {len(source_docs)} chunks")
    return docs
