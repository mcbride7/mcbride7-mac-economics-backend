"""
newsdata_collector.py — REAL, working ingestion against the NewsData.io API.

Recommended pick #2 for news (backup to GDELT): unlike GDELT's metadata-only,
research-grade shape, NewsData.io returns cleaner business/finance-category
headlines with a labeled sentiment field, and — notably — its free tier is
one of the few that explicitly allows commercial use (many competitors,
like NewsAPI.org, restrict free-tier use to localhost/non-commercial).
Free tier: 200 credits/day, ~10 articles per request.

Setup:
    1. Free API key: https://newsdata.io/register
    2. export NEWSDATA_API_KEY="your_key_here"
    3. python newsdata_collector.py

Docs: https://newsdata.io/documentation
"""

import os
import sys

import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_news_article  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

NEWSDATA_API_KEY = os.environ.get("NEWSDATA_API_KEY")
BASE = "https://newsdata.io/api/1/latest"

QUERIES = [
    "central bank interest rate",
    "inflation report",
    "currency market",
]


def search(query):
    params = {
        "apikey": NEWSDATA_API_KEY,
        "q": query,
        "category": "business",
        "language": "en",
    }
    resp = get_with_retry(BASE, params=params, timeout=20)
    return resp.json()


def run():
    if not NEWSDATA_API_KEY:
        logger.error("NEWSDATA_API_KEY not set. Free key: https://newsdata.io/register")
        return

    init_db()
    source_id = upsert_source(
        publisher="NewsData.io",
        url="https://newsdata.io/api/1/latest",
        category="news",
        reliability_tier=3,
        country="Global",
    )

    total_new = 0
    for query in QUERIES:
        try:
            data = search(query)
        except requests.RequestException as e:
            logger.warning("Skipping '%s': request failed after retries — %s", query, e)
            continue

        if data.get("status") != "success":
            logger.warning("Skipping '%s': %s", query, data.get("results", {}).get("message", "API error"))
            continue

        results = data.get("results", [])
        new_count = 0
        for a in results:
            inserted = insert_news_article(
                provider="newsdata",
                title=(a.get("title") or "").strip(),
                url=a.get("link", ""),
                published_at=a.get("pubDate", ""),
                domain=a.get("source_id", ""),
                tone=None,
                sentiment=a.get("sentiment"),
                source_id=source_id,
            )
            if inserted:
                new_count += 1
        total_new += new_count
        logger.info("'%s': %d new articles (%d returned)", query, new_count, len(results))

    logger.info("NewsData.io ingestion complete. %d new articles total.", total_new)


if __name__ == "__main__":
    run()
