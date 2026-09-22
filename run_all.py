"""
run_all.py — runs every collector in sequence and prints a summary.

Usage:
    export FRED_API_KEY="..."          # required — https://fred.stlouisfed.org/docs/api/api_key.html
    export SOCRATA_APP_TOKEN="..."     # optional — raises CFTC rate limit
    export FINNHUB_API_KEY="..."       # required for Finnhub — https://finnhub.io/register
    export TWELVEDATA_API_KEY="..."    # required for Twelve Data — https://twelvedata.com/pricing
    export NEWSDATA_API_KEY="..."      # required for NewsData.io — https://newsdata.io/register
    export DATABASE_URL="..."          # optional — Postgres URL; omit for local SQLite
    export HEALTHCHECK_URL="..."       # optional — dead-man's switch, e.g. https://hc-ping.com/your-uuid
    # GDELT and Stooq need no key.
    pip install -r requirements.txt
    python run_all.py
"""

import sys
import os

sys.path.append(os.path.dirname(__file__))

# Phase 6: load secrets from .env locally (no-op in production hosts like
# Railway/Render, which inject real env vars directly — dotenv only fills
# gaps, never overwrites a variable that's already set).
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from db import init_db  # noqa: E402
from logging_config import setup_logging, ping_healthcheck  # noqa: E402
import verify_sources  # noqa: E402
from collectors import (  # noqa: E402
    fred_collector,
    cftc_cot_collector,
    central_bank_rss_collector,
    finnhub_collector,
    twelvedata_collector,
    alpha_vantage_collector,
    gdelt_collector,
    newsdata_collector,
    seasonality_collector,
)

logger = setup_logging()


def main():
    ping_healthcheck("/start")  # signals "a run has begun" — flags crashes that never reach the end
    try:
        init_db()

        logger.info("=== FRED (macro indicators, policy rates, yields) ===")
        fred_collector.run()

        logger.info("=== CFTC Commitments of Traders (positioning) ===")
        cftc_cot_collector.run()

        logger.info("=== Central Bank RSS (statements & speeches) ===")
        central_bank_rss_collector.run(only_confirmed=True)

        logger.info("=== Finnhub (market data — recommended #1) ===")
        finnhub_collector.run()

        logger.info("=== Twelve Data (market data — recommended #2 / backup) ===")
        twelvedata_collector.run()

        logger.info("=== Alpha Vantage (market data — real-time FX, 3rd source) ===")
        alpha_vantage_collector.run()

        logger.info("=== Cross-source verification (compares Finnhub vs Twelve Data vs Alpha Vantage) ===")
        verify_sources.run()

        logger.info("=== GDELT (news — recommended #1) ===")
        gdelt_collector.run()

        logger.info("=== NewsData.io (news — recommended #2 / backup) ===")
        newsdata_collector.run()

        logger.info("=== Seasonality (Stooq + FRED backup, replaces EquityClock) ===")
        seasonality_collector.run()

        logger.info("All collectors finished.")
        ping_healthcheck()  # success ping — no suffix
    except Exception:
        logger.exception("run_all.py crashed")
        ping_healthcheck("/fail")
        raise


if __name__ == "__main__":
    main()
