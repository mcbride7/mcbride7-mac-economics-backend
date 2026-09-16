"""
gdelt_collector.py — REAL, working ingestion against the GDELT Project's
free DOC 2.0 API.

Recommended pick #1 for news: completely free, no API key, no published
rate limit, monitors global news across 100+ countries and updates every
15 minutes. Returns article metadata (title, URL, domain, tone/sentiment)
rather than full text — exactly the shape needed for the News Intelligence
engine's sentiment + narrative-delta pipeline, and it avoids ever storing
or reproducing copyrighted article bodies.

Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""

import os
import sys
from urllib.parse import quote

import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_news_article  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

BASE = "https://api.gdeltproject.org/api/v2/doc/doc"

QUERIES = [
    "Federal Reserve interest rate",
    "European Central Bank policy",
    "Bank of England inflation",
    "gold prices macro",
    "US dollar central bank",
]


def search(query, max_records=25):
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": max_records,
        "format": "json",
        "sort": "datedesc",
    }
    resp = get_with_retry(BASE, params=params, timeout=20, headers={"User-Agent": "MacEconomicsBot/1.0"})
    # GDELT occasionally returns non-JSON (HTML error page) under load; guard for that.
    try:
        return resp.json().get("articles", [])
    except ValueError:
        return []


def run():
    init_db()
    source_id = upsert_source(
        publisher="GDELT Project",
        url="https://api.gdeltproject.org/api/v2/doc/doc",
        category="news",
        reliability_tier=3,
        country="Global",
    )

    total_new = 0
    for query in QUERIES:
        try:
            articles = search(query)
        except requests.RequestException as e:
            logger.warning("Skipping '%s': request failed after retries — %s", query, e)
            continue

        new_count = 0
        for a in articles:
            tone = None
            try:
                tone = float(a.get("tone")) if a.get("tone") not in (None, "") else None
            except ValueError:
                pass

            inserted = insert_news_article(
                provider="gdelt",
                title=a.get("title", "").strip(),
                url=a.get("url", ""),
                published_at=a.get("seendate", ""),
                domain=a.get("domain", ""),
                tone=tone,
                sentiment=None,
                source_id=source_id,
            )
            if inserted:
                new_count += 1
        total_new += new_count
        logger.info("'%s': %d new articles (%d returned)", query, new_count, len(articles))

    logger.info("GDELT ingestion complete. %d new articles total.", total_new)


if __name__ == "__main__":
    run()
