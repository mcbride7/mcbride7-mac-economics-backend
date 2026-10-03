"""
fred_release_calendar_collector.py — REAL, working ingestion against FRED's
releases/dates endpoint, giving genuine forward-looking release dates.

This is the piece the Calendar page's honest scope note was missing:
previously the dashboard could only show the latest ALREADY-INGESTED value
per indicator (a backward-looking "recent releases" view). This collector
instead asks FRED directly "when is each data release scheduled", which
includes real future dates — not just past ones.

Important, deliberate limitation: FRED gives real SCHEDULE dates, but not
consensus/forecast figures or surprise scoring — that's a different kind
of data (analyst consensus estimates) that isn't published by any free
government source. A true "consensus forecast" column would require a
paid data vendor. This collector gives you the honest half of a real
calendar: WHEN things happen, not WHAT analysts expect.

Docs: https://fred.stlouisfed.org/docs/api/fred/releases_dates.html
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_release_date  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

FRED_API_KEY = os.environ.get("FRED_API_KEY")
BASE = "https://api.stlouisfed.org/fred/releases/dates"

# How far ahead to pull the schedule. 45 days covers most monthly releases
# comfortably (CPI, payrolls, GDP, etc. all publish at least monthly).
DAYS_AHEAD = 45


def fetch_upcoming(start_date, end_date):
    params = {
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "realtime_start": start_date,
        "realtime_end": end_date,
        # FRED excludes not-yet-published (future) dates by default — this
        # flag is what actually makes "upcoming" releases show up at all.
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "limit": 200,
    }
    resp = get_with_retry(BASE, params=params, timeout=20)
    return resp.json().get("release_dates", [])


def run():
    if not FRED_API_KEY:
        logger.error("FRED_API_KEY not set. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
        return

    init_db()
    source_id = upsert_source(
        publisher="Federal Reserve Bank of St. Louis (FRED)",
        url=BASE,
        category="statistics_agency",
        reliability_tier=1,
        country="United States",
    )

    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=DAYS_AHEAD)

    try:
        dates = fetch_upcoming(today.isoformat(), end.isoformat())
    except Exception as e:
        logger.warning("Release calendar fetch failed: %s", e)
        return

    stored = 0
    for d in dates:
        release_id = d.get("release_id")
        release_name = d.get("release_name", "")
        release_date = d.get("date", "")
        if not (release_id and release_date):
            continue
        insert_release_date(release_id, release_name, release_date, source_id)
        stored += 1

    logger.info("Release calendar: %d upcoming release dates stored (next %d days)", stored, DAYS_AHEAD)


if __name__ == "__main__":
    run()
