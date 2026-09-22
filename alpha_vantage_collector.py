"""
alpha_vantage_collector.py — REAL, working ingestion against the Alpha
Vantage API for real-time FX quotes.

IMPORTANT LIMIT: Alpha Vantage's free tier is 25 requests/day and 5/minute
— by far the tightest of every provider in this project. This collector
deliberately checks only 5 major FX pairs (using 5 of your 25 daily
requests) rather than trying to cover everything Finnhub/Twelve Data do.

Why these 5 specifically: they're the same symbols Finnhub and Twelve Data
already track, so Alpha Vantage becomes a genuine THIRD source feeding
verify_sources.py's cross-check — not just more data, but more confidence
in the data you already have.

Setup:
    1. Free API key: https://www.alphavantage.co/support/#api-key
    2. export ALPHA_VANTAGE_API_KEY="your_key_here"
    3. python alpha_vantage_collector.py

Docs: https://www.alphavantage.co/documentation/#fx
"""

import os
import sys
import time
from datetime import datetime, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_market_quote  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY")
BASE = "https://www.alphavantage.co/query"

# Deliberately small — see module docstring. (from, to) tuples.
FX_PAIRS = {
    "EUR/USD": ("EUR", "USD"),
    "GBP/USD": ("GBP", "USD"),
    "USD/JPY": ("USD", "JPY"),
    "USD/CAD": ("USD", "CAD"),
    "AUD/USD": ("AUD", "USD"),
}


def get_exchange_rate(from_ccy, to_ccy):
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_ccy,
        "to_currency": to_ccy,
        "apikey": ALPHA_VANTAGE_API_KEY,
    }
    resp = get_with_retry(BASE, params=params, timeout=15)
    data = resp.json()

    # Alpha Vantage's free tier returns HTTP 200 even when rate-limited —
    # the error shows up as an "Information" or "Note" key instead of the
    # real payload, so this has to be checked explicitly or it looks like
    # a crash with a confusing KeyError instead of a clear rate-limit message.
    if "Information" in data or "Note" in data:
        raise ValueError(data.get("Information") or data.get("Note"))

    quote = data.get("Realtime Currency Exchange Rate")
    if not quote:
        raise ValueError(f"Unexpected response shape: {data}")
    return float(quote["5. Exchange Rate"])


def run():
    if not ALPHA_VANTAGE_API_KEY:
        logger.error("ALPHA_VANTAGE_API_KEY not set. Free key: https://www.alphavantage.co/support/#api-key")
        return

    init_db()
    source_id = upsert_source(
        publisher="Alpha Vantage",
        url="https://www.alphavantage.co/query",
        category="markets",
        reliability_tier=2,
        country="Global",
    )

    stored = 0
    for pair, (from_ccy, to_ccy) in FX_PAIRS.items():
        try:
            rate = get_exchange_rate(from_ccy, to_ccy)
        except Exception as e:
            logger.warning("Skipping %s: %s", pair, e)
            continue

        quote_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        insert_market_quote(
            provider="alphavantage", symbol=pair, asset_class="fx",
            price=rate, change_pct=None, quote_time=quote_time, source_id=source_id,
        )
        stored += 1
        time.sleep(13)  # 5 requests/minute limit — stay safely under it

    logger.info("Alpha Vantage FX quotes: %d/%d pairs stored (daily budget: 25 requests, used %d here)",
                stored, len(FX_PAIRS), len(FX_PAIRS))


if __name__ == "__main__":
    run()
