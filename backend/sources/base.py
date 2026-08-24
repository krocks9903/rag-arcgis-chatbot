"""Shared record shape and CSV I/O for Engage Estero content sources.

Every non-meeting source (news posts, pages, events, PDF documents) normalizes
to one flat row so the whole ingestion path — CSV reader, chunker, metadata
contract — stays identical no matter how many sources we add. Fields that do not
apply to a source stay empty instead of branching the schema.
"""
from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import dataclass, fields
from typing import Iterable

CONTENT_FIELDS = [
    "source_type",
    "record_id",
    "title",
    "category",
    "publish_date",
    "url",
    "document_url",
    "venue",
    "location",
    "content",
]


@dataclass
class ContentRecord:
    """One publishable item from the Engage Estero site."""

    source_type: str
    record_id: str
    title: str = ""
    category: str = ""
    publish_date: str = ""
    url: str = ""
    document_url: str = ""
    venue: str = ""
    location: str = ""
    content: str = ""

    def as_row(self) -> dict[str, str]:
        return {f.name: (getattr(self, f.name) or "") for f in fields(self)}

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "ContentRecord":
        known = {f.name for f in fields(cls)}
        return cls(**{k: (row.get(k) or "") for k in known})

    def is_usable(self) -> bool:
        """Enough substance to be worth embedding."""
        return bool(self.record_id and self.title and (self.content or self.url))


@dataclass(frozen=True)
class SourceSpec:
    """Registry entry describing one content source."""

    key: str
    source_type: str
    label: str
    csv_name: str
    # Older CSV kept for backwards compatibility, read when csv_name is absent.
    legacy_csv: str | None = None
    # Sync it, but keep it out of the RAG index unless asked for explicitly.
    index_by_default: bool = True

    def csv_path(self, base_dir: str) -> str:
        return os.path.join(base_dir, self.csv_name)


def read_records(path: str) -> list[ContentRecord]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [ContentRecord.from_row(row) for row in csv.DictReader(f)]


def write_records(path: str, records: Iterable[ContentRecord]) -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = [r.as_row() for r in records]
    with open(path, "w", encoding="utf-8", newline="") as f:
        # LF on every platform, matching the pipeline writer and .gitattributes
        # (csv defaults to CRLF, which would churn diffs).
        writer = csv.DictWriter(
            f,
            fieldnames=CONTENT_FIELDS,
            quoting=csv.QUOTE_ALL,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def merge_records(
    existing: list[ContentRecord], incoming: Iterable[ContentRecord]
) -> tuple[list[ContentRecord], int]:
    """Append-only merge keyed on record_id, with URL as a second identity.

    Existing rows win so an upstream edit never churns the committed corpus.
    The URL check matters for migration: legacy article rows predate WordPress
    ids and are keyed by URL, so id-only dedupe would re-add all of them.
    """
    seen_ids = {r.record_id for r in existing if r.record_id}
    seen_urls = {normalize_url(r.url) for r in existing if r.url}
    added: list[ContentRecord] = []
    for record in incoming:
        if not record.record_id or record.record_id in seen_ids:
            continue
        url_key = normalize_url(record.url)
        if url_key and url_key in seen_urls:
            continue
        seen_ids.add(record.record_id)
        if url_key:
            seen_urls.add(url_key)
        added.append(record)
    return existing + added, len(added)


def file_hash(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
