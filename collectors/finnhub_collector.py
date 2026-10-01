"""
finnhub_collector.py — REAL, working ingestion against the Finnhub API.

CORRECTED: the original version of this file tried to fetch forex quotes
(OANDA:EUR_USD etc.) via /quote. That's a mistake — Finnhub's free tier
does NOT include forex data at all; /forex/* and forex-symbol /quote calls
return HTTP 403 "You don't have access to this resource" regardless of how
valid the key is (confirmed against Finnhub's own GitHub issue tracker).
Free tier is real-time US EQUITIES ONLY, 60 calls/min.

So this now fetches major US-listed ETFs as liquid, real-time-tradeable
proxies for the indices/commodities the dashboard cares about — these are
ordinary US equities as far as Finnhub's API is concerned, so they work
perfectly on the free tier. They're intentionally stored under distinct
symbol names (e.g. "SPY (ETF)", not "S&P 500") rather than reused as if
interchangeable with the index/spot-commodity figures Twelve Data provides
— an ETF share price is not the same number as the index level or the spot
commodity price it tracks (SPY trades near 1/10th the S&P 500 index level,
GLD near 1/10th an ounce of gold), so merging them under the same symbol
would create false "conflicts" in verify_sources.py and misleading numbers
on the dashboard. Keeping them distinct avoids both problems honestly.

Setup:
    1. Free API key (no credit card): https://finnhub.io/register
    2. export FINNHUB_API_KEY="your_key_here"
    3. python finnhub_collector.py

Docs: https://finnhub.io/docs/api
"""

import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_market_quote  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
BASE = "https://finnhub.io/api/v1"

# Real US-listed equities/ETFs — genuinely covered by Finnhub's free tier.
# (label, ticker, asset_class) — label is deliberately distinct from the
# plain index/commodity names Twelve Data uses, for the reason explained above.
ETF_PROXIES = [
    ("SPY (S&P 500 ETF)", "SPY", "equity_index"),
    ("QQQ (Nasdaq 100 ETF)", "QQQ", "equity_index"),
    ("DIA (Dow Jones ETF)", "DIA", "equity_index"),
    ("GLD (Gold ETF)", "GLD", "commodity"),
    ("SLV (Silver ETF)", "SLV", "commodity"),
    ("USO (Oil ETF)", "USO", "commodity"),
]


def get_quote(symbol):
    resp = get_with_retry(f"{BASE}/quote", params={"symbol": symbol, "token": FINNHUB_API_KEY}, timeout=15)
    return resp.json()


def run():
    if not FINNHUB_API_KEY:
        logger.error("FINNHUB_API_KEY not set. Free key: https://finnhub.io/register")
        return

    init_db()
    source_id = upsert_source(
        publisher="Finnhub",
        url="https://finnhub.io/api/v1",
        category="markets",
        reliability_tier=2,
        country="United States",
    )

    stored = 0
    for label, ticker, asset_class in ETF_PROXIES:
        try:
            q = get_quote(ticker)
        except requests.RequestException as e:
            logger.warning("Skipping %s: request failed after retries — %s", label, e)
            continue

        if q.get("c") in (None, 0):
            logger.warning("Skipping %s: no data returned for %s (error: %s)", label, ticker, q.get("error"))
            continue

        quote_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        insert_market_quote(
            provider="finnhub",
            symbol=label,
            asset_class=asset_class,
            price=q["c"],
            change_pct=q.get("dp"),
            quote_time=quote_time,
            source_id=source_id,
        )
        stored += 1
        time.sleep(1.1)  # stay comfortably under 60 calls/min

    logger.info("Finnhub ETF quotes: %d/%d stored", stored, len(ETF_PROXIES))
    logger.info("Finnhub ingestion complete.")


if __name__ == "__main__":
    run()
