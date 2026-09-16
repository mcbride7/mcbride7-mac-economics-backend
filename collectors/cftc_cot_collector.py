"""
cftc_cot_collector.py — REAL, working ingestion against the CFTC's official
Commitments of Traders (COT) Socrata Open Data API.

This replaces scraping https://www.cftc.gov/MarketReports/CommitmentsofTraders/
entirely: the CFTC itself publishes the same weekly data through a
structured, documented, free public API at publicreporting.cftc.gov, so
there's no HTML to parse and no ToS question at all.

No API key is required for light use. For sustained/production polling,
get a free Socrata "app token" (raises your rate limit) at:
https://publicreporting.cftc.gov/profile/edit/developer_settings
and set SOCRATA_APP_TOKEN below.

Dataset IDs (confirmed via CFTC's own API documentation):
    Legacy, Futures Only          -> 6dca-aqww
    Disaggregated, Combined       -> kh3c-gbw2
    Traders in Financial Futures, Futures Only -> gpe5-46if
"""

import os
import sys
import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_cot_report  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")  # optional but recommended
CFTC_BASE = "https://publicreporting.cftc.gov/resource"

DATASETS = {
    "legacy_futures_only": "6dca-aqww",
    "financial_futures_only": "gpe5-46if",
}

# Contracts to track, matched against the `market_and_exchange_names` field.
# Adjust freely — the dataset covers every reportable futures market.
WATCHED_MARKETS = [
    "GOLD - COMMODITY EXCHANGE INC.",
    "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "U.S. DOLLAR INDEX - ICE FUTURES U.S.",
    "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
]


def fetch_cot(dataset_key, limit=500):
    dataset_id = DATASETS[dataset_key]
    url = f"{CFTC_BASE}/{dataset_id}.json"
    params = {"$limit": limit, "$order": "report_date_as_yyyy_mm_dd DESC"}
    headers = {"X-App-Token": SOCRATA_APP_TOKEN} if SOCRATA_APP_TOKEN else {}
    resp = get_with_retry(url, params=params, headers=headers, timeout=30)
    return resp.json()


def run():
    init_db()
    source_id = upsert_source(
        publisher="CFTC Public Reporting Environment",
        url="https://publicreporting.cftc.gov/",
        category="positioning",
        reliability_tier=1,
        country="United States",
    )

    for dataset_key in DATASETS:
        try:
            rows = fetch_cot(dataset_key)
        except requests.RequestException as e:
            logger.warning("Skipping %s: request failed after retries — %s", dataset_key, e)
            continue

        stored = 0
        for row in rows:
            market = row.get("market_and_exchange_names", "")
            if not any(w in market for w in WATCHED_MARKETS):
                continue

            def f(field):
                v = row.get(field)
                return float(v) if v not in (None, "") else None

            insert_cot_report(
                market_and_exchange=market,
                report_date=row.get("report_date_as_yyyy_mm_dd", "")[:10],
                noncomm_long=f("noncomm_positions_long_all"),
                noncomm_short=f("noncomm_positions_short_all"),
                comm_long=f("comm_positions_long_all"),
                comm_short=f("comm_positions_short_all"),
                open_interest=f("open_interest_all"),
                source_id=source_id,
            )
            stored += 1
        logger.info("%s: %d watched-market rows stored", dataset_key, stored)

    logger.info("CFTC COT ingestion complete.")


if __name__ == "__main__":
    run()
