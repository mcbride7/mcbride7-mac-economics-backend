"""
twelvedata_collector.py — REAL, working ingestion against the Twelve Data API.

Recommended pick #2 for market data (backup to Finnhub): 800 free calls/day,
clean unified symbol format across FX, indices, and commodities in one
endpoint, which Finnhub's free tier splits across several. Data is delayed
~4 hours on the free tier — fine for a daily macro dashboard, not for
scalping — so it complements Finnhub's faster free tier rather than
duplicating it.

Setup:
    1. Free API key: https://twelvedata.com/pricing (Basic/free plan)
    2. export TWELVEDATA_API_KEY="your_key_here"
    3. python twelvedata_collector.py

Docs: https://twelvedata.com/docs
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

TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")
BASE = "https://api.twelvedata.com"

SYMBOLS = {
    "S&P 500": ("SPX", "equity_index"),
    "Nasdaq 100": ("NDX", "equity_index"),
    "Gold": ("XAU/USD", "commodity"),
    "Silver": ("XAG/USD", "commodity"),
    "WTI Crude": ("WTI/USD", "commodity"),
    "Bitcoin": ("BTC/USD", "crypto"),
}


def get_quote(symbol):
    resp = get_with_retry(
        f"{BASE}/quote", params={"symbol": symbol, "apikey": TWELVEDATA_API_KEY}, timeout=15
    )
    return resp.json()


def run():
    if not TWELVEDATA_API_KEY:
        logger.error("TWELVEDATA_API_KEY not set. Free key: https://twelvedata.com/pricing")
        return

    init_db()
    source_id = upsert_source(
        publisher="Twelve Data",
        url="https://api.twelvedata.com",
        category="markets",
        reliability_tier=2,
        country="Global",
    )

    stored = 0
    for name, (symbol, asset_class) in SYMBOLS.items():
        try:
            q = get_quote(symbol)
        except requests.RequestException as e:
            logger.warning("Skipping %s: request failed after retries — %s", name, e)
            continue

        if q.get("status") == "error" or "close" not in q:
            logger.warning("Skipping %s: %s", name, q.get("message", "no data returned"))
            continue

        quote_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        change_pct = float(q["percent_change"]) if q.get("percent_change") else None
        insert_market_quote(
            provider="twelvedata",
            symbol=name,
            asset_class=asset_class,
            price=float(q["close"]),
            change_pct=change_pct,
            quote_time=quote_time,
            source_id=source_id,
        )
        stored += 1
        time.sleep(0.3)  # 800/day budget — no need to rush, but be polite

    logger.info("Twelve Data quotes: %d symbols stored", stored)
    logger.info("Twelve Data ingestion complete.")


if __name__ == "__main__":
    run()
