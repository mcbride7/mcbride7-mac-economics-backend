"""
finnhub_collector.py — REAL, working ingestion against the Finnhub API.

Recommended pick #1 for market data: most generous general-purpose free
tier available today (60 calls/min, no credit card required). Covers FX
quotes and general market news, which fills the gap FRED doesn't cover
(FRED has almost no live FX or equity index pricing).

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

# Finnhub forex symbols use the OANDA:XXX_YYY convention on the free tier.
FX_SYMBOLS = {
    "EUR/USD": "OANDA:EUR_USD",
    "GBP/USD": "OANDA:GBP_USD",
    "USD/JPY": "OANDA:USD_JPY",
    "USD/CAD": "OANDA:USD_CAD",
    "AUD/USD": "OANDA:AUD_USD",
    "NZD/USD": "OANDA:NZD_USD",
    "USD/ZAR": "OANDA:USD_ZAR",
    "USD/MXN": "OANDA:USD_MXN",
    "USD/CHF": "OANDA:USD_CHF",
}


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
        country="Global",
    )

    stored = 0
    for pair, symbol in FX_SYMBOLS.items():
        try:
            q = get_quote(symbol)
        except requests.RequestException as e:
            logger.warning("Skipping %s: request failed after retries — %s", pair, e)
            continue

        if q.get("c") in (None, 0):
            logger.warning("Skipping %s: no data returned for %s", pair, symbol)
            continue

        quote_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        insert_market_quote(
            provider="finnhub",
            symbol=pair,
            asset_class="fx",
            price=q["c"],
            change_pct=q.get("dp"),
            quote_time=quote_time,
            source_id=source_id,
        )
        stored += 1
        time.sleep(1.1)  # stay comfortably under 60 calls/min

    logger.info("Finnhub FX quotes: %d pairs stored", stored)
    logger.info("Finnhub ingestion complete.")


if __name__ == "__main__":
    run()
