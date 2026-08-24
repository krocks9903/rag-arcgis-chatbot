"""Index-side wiring for supplemental sources: cache keys and metadata survival."""
from __future__ import annotations

import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def test_bm25_sidecar_round_trips_metadata(tmp_path, monkeypatch):
    """Cache reloads must not lose url/source_type, or citations break."""
    import store

    monkeypatch.setattr(store, "INDEX_DIR", str(tmp_path))
    monkeypatch.setattr(store, "BM25_FILE", str(tmp_path / "bm25_corpus.json"))

    metas = [
        {"chunk_id": "website_article-post-1-0", "source_type": "website_article",
         "url": "https://esterotoday.com/rail-trail/", "publish_date": "2026-08-21"},
        {"chunk_id": "0-meta", "meeting_date": "2019-04-03"},
    ]
    store._save_bm25(["website_article-post-1-0", "0-meta"], ["article text", "meeting text"], metas)

    ids, corpus, loaded = store._load_bm25()
    assert ids[0] == "website_article-post-1-0"
    assert corpus[1] == "meeting text"
    assert loaded == metas


def test_legacy_sidecar_without_metadata_still_loads(tmp_path, monkeypatch):
    """Caches written before metadata persistence must not crash on load."""
    import store

    path = tmp_path / "bm25_corpus.json"
    monkeypatch.setattr(store, "BM25_FILE", str(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ids": ["0-meta"], "corpus": ["meeting_date: 2019-04-03"]}, f)

    ids, corpus, metas = store._load_bm25()
    assert ids == ["0-meta"]
    assert metas is None
    # The regex fallback still recovers dates for the meetings corpus.
    recovered = store._metadata_from_chunk(ids[0], corpus[0])
    assert recovered["meeting_date"] == "2019-04-03"


def test_supplemental_sources_can_be_disabled(monkeypatch):
    import store

    monkeypatch.setattr(store, "ENABLE_SUPPLEMENTAL_SOURCES", False)
    docs, hashes = store._supplemental_documents()
    assert docs == []
    assert hashes == {}


def test_supplemental_load_failure_does_not_block_meetings(monkeypatch):
    """A broken source must degrade to meetings-only, never crash startup."""
    import sources
    import store

    monkeypatch.setattr(store, "ENABLE_SUPPLEMENTAL_SOURCES", True)

    def boom(*_args, **_kwargs):
        raise RuntimeError("corrupt CSV")

    monkeypatch.setattr(sources, "source_hashes", boom)
    docs, hashes = store._supplemental_documents()
    assert docs == []
    assert hashes == {}


def test_supplemental_documents_reports_docs_and_hashes(monkeypatch, tmp_path):
    """A synced source must produce chunks and a hash the manifest can key on."""
    import sources
    import store
    from sources.base import ContentRecord, write_records

    base = str(tmp_path)
    write_records(
        os.path.join(base, "posts.csv"),
        [ContentRecord(
            source_type="website_article",
            record_id="post-1",
            title="Rail Trail",
            publish_date="2026-08-21",
            url="https://esterotoday.com/rail-trail/",
            content="Bond passed.",
        )],
    )
    monkeypatch.setattr(store, "ENABLE_SUPPLEMENTAL_SOURCES", True)
    monkeypatch.setattr(sources, "source_hashes", lambda: _hash_for(base))
    monkeypatch.setattr(sources, "load_documents", lambda: _docs_for(base))

    docs, hashes = store._supplemental_documents()
    assert hashes.get("posts")
    assert any(d.metadata["source_type"] == "website_article" for d in docs)


def _hash_for(base):
    from sources.base import file_hash

    return {"posts": file_hash(os.path.join(base, "posts.csv"))}


def _docs_for(base):
    from sources import SPECS_BY_KEY, load_records
    from sources.documents import records_to_documents

    return records_to_documents(load_records(SPECS_BY_KEY["posts"], base))
