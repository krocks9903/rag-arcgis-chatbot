"""Regression eval for the cross-encoder reranker (see backend/reranker.py).

Runnable standalone against the built FAISS index — no server required:

    backend\\venv\\Scripts\\python.exe backend\\tests\\test_rerank_ranking.py

Also discoverable by pytest (each case is a plain test_* function). Loads the
live app.py pipeline (same build_rag_chain() the server uses) and, for three
known regression queries, prints a before/after (dense rank vs rerank rank)
table and asserts the correct chunk lands where it should after reranking.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as backend_app  # noqa: E402
from reranker import rerank  # noqa: E402

_built = False


def _ensure_index_built() -> None:
    global _built
    if not _built:
        backend_app.build_rag_chain()
        _built = True


def _dense_candidates(query: str, k: int | None = None) -> list[tuple]:
    """Same dense-search + dedupe as app.retrieve(), but always at the full
    RERANK_CANDIDATES width regardless of RERANK_ENABLED, so dense rank is
    comparable across runs."""
    k = k or backend_app.RERANK_CANDIDATES
    hits = backend_app.vectorstore.similarity_search_with_relevance_scores(query, k=k)
    hits.sort(key=lambda x: x[1], reverse=True)
    seen: set[tuple] = set()
    deduped = []
    for doc, score in hits:
        key = backend_app._dedupe_key(doc)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((doc, score))
    return deduped


def _record_label(doc) -> str:
    md = doc.metadata
    return md.get("record_id") or md.get("url") or "?"


def _print_before_after(query: str, dense: list[tuple], reranked_docs: list) -> None:
    dense_rank = {id(d): i + 1 for i, (d, _) in enumerate(dense)}
    rerank_rank = {id(d): i + 1 for i, d in enumerate(reranked_docs)}

    print(f"\n=== {query!r} ===")
    print(f"{'dense#':>7} {'rerank#':>8}  {'type':<16} record")
    for d in reranked_docs:
        md = d.metadata
        print(
            f"{dense_rank.get(id(d), '-'):>7} {rerank_rank[id(d)]:>8}  "
            f"{md.get('source_type', '?'):<16} {_record_label(d)}"
        )


def _run_case(query: str, top_k: int, is_expected):
    """Rerank the full dense candidate pool for `query`, print the before/after
    table, and assert some doc satisfying `is_expected` lands in the top_k
    positions after reranking. Returns the reranked doc list for callers that
    want to inspect further."""
    _ensure_index_built()
    dense = _dense_candidates(query)
    docs_only = [d for d, _ in dense]
    reranked_docs = rerank(query, docs_only, top_n=len(docs_only))
    _print_before_after(query, dense, reranked_docs)

    top = reranked_docs[:top_k]
    found = any(is_expected(d) for d in top)
    print(f"Expect a matching chunk in top-{top_k} after rerank: {'PASS' if found else 'FAIL'}")
    assert found, f"No expected chunk in top-{top_k} after rerank for {query!r}"
    return reranked_docs


def test_wawa_board_record_in_top3():
    def is_wawa(doc) -> bool:
        md = doc.metadata
        return md.get("source_type") == "board_record" and "wawa" in (md.get("project_name") or "").lower()

    _run_case("Wawa", top_k=3, is_expected=is_wawa)


def test_village_council_sewer_change_order_ranked_first():
    def is_village_council(doc) -> bool:
        return doc.metadata.get("source_type") == "village_council"

    _run_case(
        "What did the Village Council decide about the Estero Bay Village sewer testing change order?",
        top_k=1,
        is_expected=is_village_council,
    )


def test_corkscrew_pines_storage_board_record_in_top3():
    # NOTE: "Corkscrew Road" alone is not a usable regression query — it's an
    # address fragment shared by ~38 unrelated pzdb records (Wawa, Aldi,
    # Starbucks, ...), not a project name, so there's no single correct
    # top-3 answer for it. "Corkscrew Pines Self-Storage" (pzdb:42) is an
    # actual project name and a verified case: at dense rank alone it sits
    # at #7 (buried under 6 EsteroToday "East Corkscrew" corridor articles)
    # for this natural phrasing; reranking should recover it into the top-3.
    def is_corkscrew_pines(doc) -> bool:
        md = doc.metadata
        if md.get("source_type") != "board_record":
            return False
        haystack = f"{md.get('project_name') or ''}".lower()
        return "corkscrew" in haystack and "storage" in haystack

    _run_case(
        "Is a new storage facility coming to Corkscrew Road?",
        top_k=3,
        is_expected=is_corkscrew_pines,
    )


if __name__ == "__main__":
    failures = []
    for name, fn in [
        ("Wawa -> board record in top-3", test_wawa_board_record_in_top3),
        ("Sewer change order -> village_council #1", test_village_council_sewer_change_order_ranked_first),
        ("Corkscrew Pines storage -> board record in top-3", test_corkscrew_pines_storage_board_record_in_top3),
    ]:
        try:
            fn()
        except AssertionError as e:
            failures.append((name, str(e)))

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} case(s) FAILED:")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print("All rerank regression cases PASSED.")
