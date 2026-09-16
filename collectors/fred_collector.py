"""
fred_collector.py — REAL, working ingestion against the FRED official API.

FRED (Federal Reserve Bank of St. Louis) is the single best free source in
this project: one official, documented, free REST API covers US policy
rates, Treasury yields, CPI/PCE, GDP, unemployment, and thousands of other
official series — plus many non-US series (ECB rates, UK gilts, etc. are
mirrored on FRED too), so it substantially reduces how many separate
scrapers you actually need.

Setup:
    1. Get a free API key: https://fred.stlouisfed.org/docs/api/api_key.html
    2. export FRED_API_KEY="your_key_here"
    3. python fred_collector.py

Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

import os
import sys
import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_economic_release  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

FRED_API_KEY = os.environ.get("FRED_API_KEY")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# A representative set of series covering central-bank policy rates,
# sovereign yields, and headline macro indicators. FRED series IDs are
# stable identifiers — browse more at https://fred.stlouisfed.org/
SERIES = {
    # Policy rates / short-end
    "US_FED_FUNDS_RATE": "DFF",
    "US_2Y_YIELD": "DGS2",
    "US_10Y_YIELD": "DGS10",
    "US_30Y_YIELD": "DGS30",
    # Inflation
    "US_CPI_ALL_ITEMS": "CPIAUCSL",
    "US_CORE_CPI": "CPILFESL",
    "US_PCE_PRICE_INDEX": "PCEPI",
    # Growth & labour
    "US_REAL_GDP": "GDPC1",
    "US_UNEMPLOYMENT_RATE": "UNRATE",
    "US_NONFARM_PAYROLLS": "PAYEMS",
    # Non-US policy rates mirrored on FRED
    "ECB_DEPOSIT_RATE": "ECBDFR",
    "UK_BANK_RATE": "IUDSOIA",  # SONIA — UK's key overnight rate proxy; see note below
    "JAPAN_POLICY_RATE": "IRSTCI01JPM156N",
}


def fetch_series(series_id, limit=24):
    """Pull the most recent `limit` observations for a FRED series."""
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    resp = get_with_retry(FRED_BASE, params=params, timeout=20)
    return resp.json().get("observations", [])


def run():
    if not FRED_API_KEY:
        logger.error("FRED_API_KEY not set. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
        return

    init_db()
    source_id = upsert_source(
        publisher="Federal Reserve Bank of St. Louis (FRED)",
        url="https://api.stlouisfed.org/fred/series/observations",
        category="statistics_agency",
        reliability_tier=1,
        country="Multiple",
    )

    for label, series_id in SERIES.items():
        try:
            obs = fetch_series(series_id)
        except requests.RequestException as e:
            logger.warning("Skipping %s (%s): request failed after retries — %s", label, series_id, e)
            continue

        stored = 0
        for point in obs:
            if point["value"] == ".":  # FRED's marker for missing data
                continue
            insert_economic_release(
                country=label.split("_")[0],
                indicator=label,
                reference_date=point["date"],
                value=float(point["value"]),
                unit="percent" if "YIELD" in label or "RATE" in label else "index",
                source_id=source_id,
            )
            stored += 1
        logger.info("%s (%s): %d observations stored", label, series_id, stored)

    logger.info("FRED ingestion complete.")


if __name__ == "__main__":
    run()
