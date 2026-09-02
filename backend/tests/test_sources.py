"""Engage Estero multi-source ingestion: record shape, merge, chunking, registry."""
from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _record(**kwargs):
    from sources.base import ContentRecord

    defaults = {
        "source_type": "website_article",
        "record_id": "post-1",
        "title": "Rail Trail Bond Passes",
        "publish_date": "2026-08-21",
        "url": "https://esterotoday.com/rail-trail/",
        "content": "The bond referendum passed with strong support.",
    }
    defaults.update(kwargs)
    return ContentRecord(**defaults)


def test_record_csv_round_trip(tmp_path):
    from sources.base import CONTENT_FIELDS, read_records, write_records

    path = str(tmp_path / "posts.csv")
    written = write_records(path, [_record(), _record(record_id="post-2", title="Second")])
    assert written == 2

    rows = read_records(path)
    assert [r.record_id for r in rows] == ["post-1", "post-2"]
    assert rows[0].title == "Rail Trail Bond Passes"
    assert rows[0].publish_date == "2026-08-21"
    # Every declared column survives the trip, including unused optional ones.
    assert set(rows[0].as_row()) == set(CONTENT_FIELDS)


def test_merge_is_append_only_by_record_id():
    from sources.base import merge_records

    existing = [_record(title="Original headline")]
    incoming = [
        _record(title="Upstream edited headline"),
        _record(record_id="post-2", title="Brand new", url="https://esterotoday.com/brand-new/"),
    ]
    merged, added = merge_records(existing, incoming)

    assert added == 1
    assert len(merged) == 2
    # An upstream edit must not rewrite a committed row.
    assert merged[0].title == "Original headline"
    assert merged[1].record_id == "post-2"


def test_merge_dedupes_legacy_rows_by_url():
    """Legacy article rows are keyed by URL; WordPress sync keys by post id."""
    from sources.base import merge_records

    legacy = [_record(record_id="https://esterotoday.com/rail-trail/")]
    from_api = [_record(record_id="post-1", url="https://esterotoday.com/rail-trail")]

    merged, added = merge_records(legacy, from_api)
    # Same article, different id scheme — must not be indexed twice.
    assert added == 0
    assert len(merged) == 1


def test_excluded_category_matching_is_slug_based():
    from sources.base import has_excluded_category

    excluded = {"limited"}
    assert has_excluded_category("limited", excluded)
    assert has_excluded_category("Council News; Limited", excluded)
    assert has_excluded_category("LIMITED", excluded)
    # Substring matches must not fire — "unlimited growth" is real coverage.
    assert not has_excluded_category("Unlimited Growth", excluded)
    assert not has_excluded_category("Council News", excluded)
    assert not has_excluded_category("", excluded)
    # No exclusions configured means nothing is filtered.
    assert not has_excluded_category("limited", set())


def test_excluded_category_rows_never_load(tmp_path, monkeypatch):
    """Person profiles in 'limited' must not reach the corpus, even if committed."""
    import sources
    from sources import SPECS_BY_KEY, load_records
    from sources.base import write_records

    base = str(tmp_path)
    write_records(
        os.path.join(base, "posts.csv"),
        [
            _record(record_id="post-1", category="Council News"),
            _record(record_id="post-2", category="limited", title="Mike Wasson",
                    url="https://esterotoday.com/mike-wasson/"),
        ],
    )
    monkeypatch.setattr(sources, "EXCLUDED_CATEGORY_SLUGS", {"limited"})

    kept = load_records(SPECS_BY_KEY["posts"], base)
    assert [r.record_id for r in kept] == ["post-1"]


def test_unusable_records_are_rejected():
    assert not _record(title="", content="").is_usable()
    assert not _record(record_id="").is_usable()
    assert _record().is_usable()


def test_documents_carry_source_type_and_date_headers():
    from sources.documents import record_to_documents

    docs = record_to_documents(_record())
    assert len(docs) == 1
    doc = docs[0]

    assert "SOURCE_TYPE: website_article" in doc.page_content
    assert "DATE: 2026-08-21" in doc.page_content
    assert doc.metadata["source_type"] == "website_article"
    assert doc.metadata["publish_date"] == "2026-08-21"
    assert doc.metadata["url"] == "https://esterotoday.com/rail-trail/"
    assert doc.metadata["chunk_id"] == "website_article-post-1-0"


def test_retrieval_resolves_supplemental_dates():
    """Recency ranking must see article/page/event dates, not just meeting_date."""
    from datetime import date

    from retrieval import document_meeting_date
    from sources.documents import record_to_documents

    doc = record_to_documents(_record())[0]
    assert document_meeting_date(doc) == date(2026, 8, 21)


def test_event_and_document_records_keep_type_specific_fields():
    from sources.documents import record_to_documents

    event = record_to_documents(
        _record(
            source_type="event",
            record_id="event-9",
            title="Water Summit",
            venue="Estero Community Park",
            publish_date="2026-07-31",
        )
    )[0]
    assert event.metadata["venue"] == "Estero Community Park"
    assert "SOURCE_TYPE: event" in event.page_content

    pdf = record_to_documents(
        _record(
            source_type="document",
            record_id="doc-4",
            title="GECR 2026 Qtr 2",
            document_url="https://esterotoday.com/wp-content/uploads/gecr.pdf",
            content="Quarterly community report.",
        )
    )[0]
    assert pdf.metadata["document_url"].endswith("gecr.pdf")
    # Citations need a link even when the record has no post URL.
    assert pdf.metadata["primary_source_url"]


def test_long_content_splits_but_repeats_header():
    from sources.documents import record_to_documents

    docs = record_to_documents(_record(content="Estero council discussion. " * 400))
    assert len(docs) > 1
    for doc in docs:
        assert "SOURCE_TYPE: website_article" in doc.page_content
        assert doc.metadata["record_id"] == "post-1"
    # Chunk ids stay unique so doc_by_id() and BM25 lookups do not collide.
    assert len({d.metadata["chunk_id"] for d in docs}) == len(docs)


def test_registry_covers_every_discovered_content_type():
    from sources import SOURCE_SPECS

    keys = {spec.key for spec in SOURCE_SPECS}
    assert keys == {"posts", "pages", "events", "documents"}
    # source_type values must be distinct — cards and prompts branch on them.
    types = [spec.source_type for spec in SOURCE_SPECS]
    assert len(set(types)) == len(types)


def test_events_are_synced_but_not_indexed():
    """events_path answers upcoming events live; indexing them would go stale."""
    from sources import SPECS_BY_KEY, enabled_specs

    assert SPECS_BY_KEY["events"].index_by_default is False
    assert "events" not in {spec.key for spec in enabled_specs()}
    assert {"posts", "pages", "documents"} <= {spec.key for spec in enabled_specs()}


def test_legacy_article_csv_is_backfilled(tmp_path):
    """Rows synced before the registry lack record_id/source_type."""
    import csv

    from sources import SPECS_BY_KEY, load_records

    legacy = tmp_path / "esterotoday_content.csv"
    with open(legacy, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["source_type", "title", "category", "publish_date", "url", "content"]
        )
        writer.writeheader()
        writer.writerow({
            "source_type": "",
            "title": "Old article",
            "category": "News",
            "publish_date": "2025-01-02",
            "url": "https://esterotoday.com/old/",
            "content": "Body text.",
        })

    spec = SPECS_BY_KEY["posts"]
    patched = type(spec)(
        key=spec.key,
        source_type=spec.source_type,
        label=spec.label,
        csv_name=spec.csv_name,
        legacy_csv=str(legacy),
    )
    records = load_records(patched, base_dir=str(tmp_path / "missing"))

    assert len(records) == 1
    assert records[0].source_type == "website_article"
    assert records[0].record_id == "https://esterotoday.com/old/"


def test_source_hashes_change_when_content_changes(tmp_path):
    from sources import SOURCE_SPECS, source_hashes
    from sources.base import write_records

    base = str(tmp_path)
    write_records(os.path.join(base, "posts.csv"), [_record()])
    first = source_hashes(base)
    assert "posts" in first

    write_records(os.path.join(base, "posts.csv"), [_record(), _record(record_id="post-2")])
    second = source_hashes(base)
    # The index manifest keys on these; equal hashes would skip a rebuild.
    assert first["posts"] != second["posts"]
    assert set(second) <= {spec.key for spec in SOURCE_SPECS}
