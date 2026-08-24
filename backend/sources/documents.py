"""Turn ContentRecord rows into retrievable LangChain documents.

Chunk text carries `DATE:` and `SOURCE_TYPE:` header lines because
`retrieval.document_meeting_date()` falls back to reading them out of the body
when metadata is unavailable, and the reranker sees the source kind.
"""
from __future__ import annotations

from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter

from sources.base import ContentRecord

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def _search_line(record: ContentRecord) -> str:
    parts = [record.title, record.category, record.venue, record.location]
    return " | ".join(p for p in parts if p)


def _header(record: ContentRecord) -> str:
    lines = []
    if record.publish_date:
        lines.append(f"DATE: {record.publish_date}")
    lines.append(f"SOURCE_TYPE: {record.source_type}")
    lines.append(f"TITLE: {record.title}")
    search = _search_line(record)
    if search:
        lines.append(f"SEARCH: {search}")
    link = record.url or record.document_url
    if link:
        lines.append(f"TRUE_URL: {link}")
    return "\n".join(lines)


def _body(record: ContentRecord) -> str:
    parts = []
    if record.venue:
        parts.append(f"venue: {record.venue}")
    if record.location:
        parts.append(f"location: {record.location}")
    if record.category:
        parts.append(f"category: {record.category}")
    if record.content:
        parts.append(record.content)
    return "\n".join(parts)


def record_to_documents(record: ContentRecord) -> list[Document]:
    """Split one record into chunks, repeating the header on each piece."""
    if not record.is_usable():
        return []

    header = _header(record)
    body = _body(record)
    pieces = _splitter.split_text(body) if len(body) > CHUNK_SIZE else [body]
    pieces = [p for p in pieces if p.strip()] or [""]

    docs: list[Document] = []
    for i, piece in enumerate(pieces):
        metadata = {
            "chunk_id": f"{record.source_type}-{record.record_id}-{i}",
            "chunk_type": "content",
            "source_type": record.source_type,
            "record_id": record.record_id,
            "title": record.title,
            "project_name": record.title,
            "category": record.category,
            "publish_date": record.publish_date,
            "date": record.publish_date,
            "url": record.url,
            "document_url": record.document_url,
            "primary_source_url": record.url or record.document_url,
            "venue": record.venue,
            "location": record.location,
        }
        docs.append(Document(page_content=f"{header}\n\n{piece}".strip(), metadata=metadata))
    return docs


def records_to_documents(records: list[ContentRecord]) -> list[Document]:
    docs: list[Document] = []
    for record in records:
        docs.extend(record_to_documents(record))
    return docs
