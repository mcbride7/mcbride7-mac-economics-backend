# Mac Economics — Ingestion Pipeline (real, runnable code)

Real collectors against verified official/free sources, a dual-backend database
(SQLite for local dev, Postgres for production), retry/backoff on every HTTP
call, structured logging, an optional dead-man's switch, and a FastAPI layer
that serves it all to the dashboard.

## Production-hardening additions

| Concern | Where it's handled |
|---|---|
| Real database | `db.py` — set `DATABASE_URL` to a Postgres URL (Supabase/Neon/RDS/Railway/etc.) and every collector writes there instead of SQLite, with zero other code changes. Explicit indexes + unique constraints included — see `db.py`'s `indexes` list. |
| Backups | `BACKUPS.md` |
| Dependencies | `requirements.txt` — install into a venv or use `Dockerfile` |
| Hosting | `DEPLOY.md` |
| Scheduling | `crontab.example` (VPS) or the GitHub Actions workflow in `DEPLOY.md` (no server to maintain) |
| Retries, timeouts, backoff | `http_utils.py` — every collector's HTTP calls go through `get_with_retry()`, which retries transient failures (timeouts, 429/500/502/503/504) with exponential backoff, honoring a `Retry-After` header when a provider sends one |
| Logging | `logging_config.py` — every collector logs to console + a rotating file at `logs/ingestion.log` instead of `print()` |
| Dead-man's switch | `logging_config.py`'s `ping_healthcheck()`, wired into `run_all.py` — set `HEALTHCHECK_URL` (free at healthchecks.io) to get alerted if a scheduled run stops happening at all, not just if it errors |
| Security | `.gitignore` (never commit `.env`), non-root `Dockerfile`, Postgres connections use `sslmode=require` |
| Monitoring | log file + Healthchecks.io ping above; point any log-shipping tool (Papertrail, Datadog, etc.) at `logs/ingestion.log` |
| Legal / API terms | `LEGAL_NOTES.md` — storage, redistribution, attribution, and commercial-use terms per provider, based on verified current terms where checked |

## What's actually live vs. what needs a URL swap

| Collector | Status | Source |
|---|---|---|
| `fred_collector.py` | ✅ Ready — needs your free API key | FRED official API |
| `cftc_cot_collector.py` | ✅ Ready — no key required | CFTC official Socrata API |
| `central_bank_rss_collector.py` (FED, ECB) | ✅ Confirmed feed URLs | Fed & ECB official RSS |
| `central_bank_rss_collector.py` (BOE, BOJ, RBA, BOC, SNB, RBNZ, SARB, PBOC, BANXICO) | ⚠️ Wired up, URLs need verification | See comments in the file |
| `finnhub_collector.py` | ✅ Ready — needs free API key | Finnhub (market data, pick #1) |
| `twelvedata_collector.py` | ✅ Ready — needs free API key | Twelve Data (market data, pick #2 / backup) |
| `gdelt_collector.py` | ✅ Ready — no key needed | GDELT Project (news, pick #1) |
| `newsdata_collector.py` | ✅ Ready — needs free API key | NewsData.io (news, pick #2 / backup) |
| `seasonality_collector.py` | ✅ Ready — no key needed | Stooq (primary) + FRED local data (backup), replacing EquityClock |

I could not execute live HTTP requests from the sandbox that built this (no outbound network access there), so I verified the Fed and ECB feed URLs directly via web fetch during this session, but the remaining nine banks' feed URLs are my best-informed placeholders, not confirmed hits — visit each bank's own site, find their real RSS/subscribe link, and swap it in. The script is written so an unverified URL fails loudly (a clear `[FAIL]` line) rather than silently ingesting nothing or garbage.

## Setup

```bash
pip install -r requirements.txt

export FRED_API_KEY="free: https://fred.stlouisfed.org/docs/api/api_key.html"
export SOCRATA_APP_TOKEN="optional — raises CFTC's anonymous rate limit"
export FINNHUB_API_KEY="free, no card: https://finnhub.io/register"
export TWELVEDATA_API_KEY="free: https://twelvedata.com/pricing"
export NEWSDATA_API_KEY="free: https://newsdata.io/register"
# GDELT and Stooq need no key at all.

python run_all.py
```

Every external collector can also be run individually, e.g. `python collectors/finnhub_collector.py`.

Inspect what landed:
```bash
sqlite3 data/mac_economics.db "SELECT indicator, reference_date, value FROM economic_releases ORDER BY reference_date DESC LIMIT 10;"
sqlite3 data/mac_economics.db "SELECT central_bank, title, published_at FROM policy_statements ORDER BY published_at DESC LIMIT 10;"
sqlite3 data/mac_economics.db "SELECT market_and_exchange, report_date, noncommercial_long, noncommercial_short FROM cot_reports;"
sqlite3 data/mac_economics.db "SELECT provider, symbol, price, change_pct FROM market_quotes ORDER BY quote_time DESC;"
sqlite3 data/mac_economics.db "SELECT provider, title, published_at, tone, sentiment FROM news_articles ORDER BY published_at DESC LIMIT 10;"
sqlite3 data/mac_economics.db "SELECT provider, symbol, month, avg_return_pct, positive_rate_pct FROM seasonality_stats ORDER BY symbol, month;"
```

### Why two providers per category

Each pair gives you a recommended primary plus a genuinely independent backup — not just a fallback for downtime, but different tradeoffs worth having both for:

- **Market data:** Finnhub (60 calls/min, fast) + Twelve Data (800/day, ~4hr delayed but broader unified symbol coverage across FX/indices/commodities/crypto in one call shape)
- **News:** GDELT (unlimited, metadata + sentiment tone, research-grade) + NewsData.io (cleaner business-category headlines with labeled sentiment, and one of the few free tiers that explicitly permits commercial use)
- **Seasonality:** Stooq (primary — free historical daily closes for the actual assets) + FRED local data (backup — reuses what `fred_collector.py` already stored, so it's a second, independent source for zero extra network calls)

## Why these sources and not the others you listed

Per the ingestion policy in the architecture doc (prefer official API → RSS → dataset → licensed feed → scraping-as-last-resort, never bypass ToS/paywalls/CAPTCHAs), here's the reasoning per source you listed:

**Used directly, as-is:**
- **FRED** — the actual best source on your list. One official API covers US rates/yields/CPI/GDP/unemployment plus many foreign policy rates (ECB, UK, Japan) already mirrored on it.
- **CFTC COT page** — don't scrape the HTML; the CFTC itself publishes the identical data via a structured, free, official API (`publicreporting.cftc.gov`). Used that instead.
- **Central bank sites (ECB, BOE, RBA, BOC, PBOC, BOJ, SNB, Banxico, SARB)** — all publish (or almost certainly publish) official RSS feeds for statements/speeches. Framework is ready; see the verification table above. One correction: the RBNZ link you gave (bnz.co.nz) is "Bank of New Zealand," a commercial retail bank — the actual central bank is at **rbnz.govt.nz**.

**Not scraped — here's what to use instead, and why:**

| You listed | Issue | Better free/legal alternative |
|---|---|---|
| TradingEconomics (yields, indicators) | ToS explicitly prohibits scraping; they sell this exact data as a paid API | FRED for US yields (`DGS2`/`DGS10`/`DGS30`); each country's own debt-management office or central bank for others (UK DMO, ECB SDW, Japan MOF) |
| ForexFactory (calendar) | Long-standing anti-scraping stance; calendar is their core product | Build the calendar from official release schedules — BLS/BEA publish their own release calendar APIs; FRED also exposes a release-dates endpoint |
| Reuters, Yahoo News | Both prohibit automated scraping in ToS; news content is copyrighted | Central banks' own RSS (first-party, zero copyright issue) for policy news; GDELT Project (free, huge, built for this) or NewsAPI.org's free developer tier for broader financial news |
| EquityClock (seasonality) | Seasonality charts are their commercial product | Compute seasonality yourself from free historical price data (e.g., via Stooq's free CSV downloads or a market-data API's historical endpoint) — same insight, fully yours, no ToS question |
| FinancialJuice, investinglive.com | Real-time news products with no public API/ToS allowance found | GDELT / official RSS as above; if budget allows, a licensed news API |
| myfxbook Community Outlook | Retail sentiment page isn't an offered public API; scraping it isn't clearly permitted | CFTC COT (already integrated) is the closest legitimate, free, structured positioning proxy — it's slower (weekly) but authoritative |
| Yahoo/general "market data" | Yahoo's endpoints are unofficial and access-restricted by ToS | Twelve Data, Alpha Vantage, or Finnhub — all have real free developer tiers built for exactly this |

## Migrating to Postgres

Everything routes through `db.py`. To move to the production schema in the architecture doc, replace the `sqlite3` calls in that one file with `psycopg2`/SQLAlchemy against the `sources` / `economic_releases` / `policy_statements` / `cot_reports` tables already defined there — no collector script needs to change.
