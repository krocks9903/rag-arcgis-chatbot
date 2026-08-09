"""Incrementally sync backend/data/esterotoday_content.csv from esterotoday.com.

Reads the site's post-sitemap.xml (linked from robots.txt — the site allows
crawling everything), scrapes any article URL not already present in the
CSV, and appends new rows. Existing rows are never modified, re-scraped, or
removed — this only adds articles published since the CSV was last synced.

Per-article extraction uses the page's own Yoast SEO JSON-LD `Article` node
(headline / datePublished / articleSection) plus the `.entry-content` text —
both verified byte-for-byte against several existing CSV rows before this
script was written (see the sync workflow / PR description for details).

Usage:
    backend/venv/Scripts/python.exe backend/scripts/sync_esterotoday.py
    backend/venv/Scripts/python.exe backend/scripts/sync_esterotoday.py --dry-run
    backend/venv/Scripts/python.exe backend/scripts/sync_esterotoday.py --limit 5

Run in CI: see .github/workflows/sync-esterotoday.yml
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SITE = "https://esterotoday.com"
SITEMAP_URL = f"{SITE}/post-sitemap.xml"
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "esterotoday_content.csv"
CSV_FIELDS = ["source_type", "title", "category", "publish_date", "url", "content"]
USER_AGENT = (
    "EngageEsteroBot/1.0 "
    "(+https://github.com/krocks9903/rag-arcgis-chatbot; "
    "weekly data sync for the Engage Estero chatbot)"
)
REQUEST_DELAY_SECONDS = 0.75
REQUEST_TIMEOUT = 20

_LD_JSON_RE = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL)
_SITEMAP_LOC_RE = re.compile(r"<loc>(.*?)</loc>")


def fetch_sitemap_urls() -> list[str]:
    resp = requests.get(SITEMAP_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return _SITEMAP_LOC_RE.findall(resp.text)


def load_existing_rows() -> tuple[list[dict], set[str]]:
    if not CSV_PATH.exists():
        return [], set()
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows, {r["url"] for r in rows}


def _extract_article_node(page_html: str) -> dict | None:
    """Find the Yoast-emitted `Article` node inside the page's @graph JSON-LD
    block(s). A page can have multiple ld+json scripts (events widgets, etc.)
    — only one carries @type == "Article"."""
    for block in _LD_JSON_RE.findall(page_html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph", []) if isinstance(data, dict) else []
        for node in graph:
            if isinstance(node, dict) and node.get("@type") == "Article":
                return node
    return None


def scrape_article(url: str) -> dict | None:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    if not resp.ok:
        print(f"  skip (HTTP {resp.status_code}): {url}")
        return None
    resp.encoding = resp.apparent_encoding or resp.encoding
    page_html = resp.text

    article = _extract_article_node(page_html)
    if article is None:
        print(f"  skip (no Article schema found — probably not a news post): {url}")
        return None

    # Yoast's JSON-LD embeds these fields HTML-entity-encoded (e.g. "&#8217;"
    # for a right single quote) even though it's a JSON string value, not
    # HTML — unescape so the CSV stores the actual character, matching the
    # existing rows (verified against several: they hold the real Unicode
    # apostrophe, not the literal entity text).
    title = html.unescape((article.get("headline") or "").strip())
    if not title:
        print(f"  skip (no headline): {url}")
        return None

    soup = BeautifulSoup(page_html, "html.parser")
    content_nodes = soup.select(".entry-content")
    if not content_nodes:
        print(f"  skip (no .entry-content found): {url}")
        return None
    content = content_nodes[0].get_text(" ", strip=True)
    if not content:
        print(f"  skip (empty content): {url}")
        return None

    sections = article.get("articleSection") or []
    if isinstance(sections, str):
        sections = [sections]
    category = "; ".join(html.unescape(s) for s in sections if s)

    published = article.get("datePublished") or ""
    publish_date = published[:10] if published else ""

    return {
        "source_type": "website_article",
        "title": title,
        "category": category,
        "publish_date": publish_date,
        "url": url,
        "content": content,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Scrape and report, but never write the CSV.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N new URLs (for testing).")
    args = parser.parse_args()

    print(f"Fetching sitemap: {SITEMAP_URL}")
    sitemap_urls = fetch_sitemap_urls()
    print(f"  {len(sitemap_urls)} URLs in sitemap")

    existing_rows, known_urls = load_existing_rows()
    print(f"  {len(known_urls)} articles already in {CSV_PATH.name}")

    new_urls = [u for u in sitemap_urls if u not in known_urls]
    if args.limit is not None:
        new_urls = new_urls[: args.limit]
    print(f"  {len(new_urls)} candidate new URL(s) to scrape")

    if not new_urls:
        print("Nothing to do.")
        return 0

    new_rows: list[dict] = []
    for i, url in enumerate(new_urls, 1):
        print(f"[{i}/{len(new_urls)}] {url}")
        try:
            row = scrape_article(url)
        except requests.RequestException as exc:
            print(f"  skip (request failed): {exc}")
            row = None
        if row:
            new_rows.append(row)
        if i < len(new_urls):
            time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Scraped {len(new_rows)}/{len(new_urls)} new article(s) successfully.")
    if not new_rows:
        return 0

    if args.dry_run:
        print("--dry-run set: not writing the CSV. Sample of what would be added:")
        for row in new_rows[:3]:
            print(f"  - {row['publish_date']} | {row['category']!r} | {row['title']}")
        return 0

    all_rows = existing_rows + new_rows
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Wrote {len(all_rows)} total rows to {CSV_PATH} ({len(new_rows)} new).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
