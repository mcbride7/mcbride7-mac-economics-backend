# Adding a new data source (website/API) to run alongside the existing ones

This is the repeatable pattern every collector in this project follows. Follow it once and any new source slots in without touching anything else — same reason Alpha Vantage was addable without rewriting Finnhub or Twelve Data.

## Step 1 — Confirm it's actually addable

Before writing code, check:
- Does it have an **official API**? (preferred always — see `LEGAL_NOTES.md`'s method-priority order: API → RSS → dataset → licensed feed → scraping only as a last resort, and only where ToS/robots.txt allow it)
- What's the **free-tier rate limit**? (You'll need this for the `time.sleep()` pacing and the cron schedule.)
- What **fields** does it return that map to your existing tables (`market_quotes`, `news_articles`, `economic_releases`, `policy_statements`, `cot_reports`)? If it's a genuinely new kind of data, it may need a new table — see Step 5.

## Step 2 — Copy the closest existing collector as a template

Pick whichever existing file is structurally closest to the new source:
- Another price/quote API → copy `collectors/finnhub_collector.py` or `collectors/twelvedata_collector.py`
- Another news API → copy `collectors/newsdata_collector.py`
- An RSS feed → copy the pattern inside `collectors/central_bank_rss_collector.py`
- A CSV/bulk-download source → copy `collectors/seasonality_collector.py`'s Stooq-fetching pattern

Every collector follows the same shape:
```python
import os, sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_<whatever>  # noqa: E402
from http_utils import get_with_retry  # noqa: E402       # retries + backoff, free
from logging_config import setup_logging  # noqa: E402    # logs to console + logs/ingestion.log

logger = setup_logging()
API_KEY = os.environ.get("YOUR_NEW_SOURCE_API_KEY")
BASE = "https://api.example.com/v1"

def run():
    if not API_KEY:
        logger.error("YOUR_NEW_SOURCE_API_KEY not set.")
        return
    init_db()
    source_id = upsert_source(publisher="...", url=BASE, category="...", reliability_tier=2, country="...")
    # fetch, parse, insert_<whatever>(..., source_id=source_id) per item
    logger.info("Done: %d items stored", count)

if __name__ == "__main__":
    run()
```

## Step 3 — Wire it into `run_all.py`

```python
from collectors import ..., your_new_collector

# inside main():
logger.info("=== Your New Source ===")
your_new_collector.run()
```

If it provides the **same kind of data** another provider already covers (e.g., another FX price source), also add its symbols to `verify_sources.py`'s `TOLERANCES_PCT` dict — this is what makes cross-checking automatic (see Step 4).

## Step 4 — Cross-source verification happens automatically

You don't write new verification code per source. `verify_sources.py` already compares **every pair** of providers that share a symbol in `market_quotes` — adding a third or fourth provider for the same symbol just adds more pairs to check, for free. Confirm your new collector writes to `market_quotes` using the **same `symbol` string** the existing providers use (e.g., `"EUR/USD"`, not `"EURUSD"` or `"EUR_USD"`) — the matching is a literal string match.

## Step 5 — If it's a genuinely new kind of data

If nothing existing fits (say, options data, or ESG scores), add a table to `db.py`:
1. Add the `CREATE TABLE` block to both the Postgres and SQLite branches of `init_db()`
2. Add an `insert_<thing>()` function following the existing pattern (upsert with `ON CONFLICT`, dedup key that makes sense)
3. Add a matching `get_<thing>()` read function
4. Expose it via a new endpoint in `api/main.py`
5. Wire the dashboard page to it, following the exact pattern in any of the already-wired pages (`loadMarkets`, `loadBonds`, etc. in `mac-economics.html`) — fetch, render on success, fall back to sample with a clear label on failure

## Step 6 — Add the key and redeploy

1. Add `YOUR_NEW_SOURCE_API_KEY` to `.env.example` (documentation only — never put the real key there)
2. Add the real key to Railway's Variables tab (same as the existing 5 keys)
3. Push the new/changed files to GitHub the same way as before
4. Railway redeploys automatically on a new commit (or click Redeploy)
5. If you want it on a schedule, add a line to `crontab.example` or a new GitHub Actions cron step, matching the source's rate limit (see `crontab.example`'s comments for the reasoning behind each existing schedule)

That's the whole pattern — every source in this project (FRED, CFTC, Finnhub, Twelve Data, Alpha Vantage, GDELT, NewsData.io, Stooq, 11 central bank RSS feeds) was added exactly this way.
