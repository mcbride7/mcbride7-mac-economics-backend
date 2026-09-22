"""
api/main.py — Phase 2: the missing link between the ingestion pipeline
and the dashboard. Serves what's actually in the database as JSON.

I could not install/run FastAPI in the sandbox that wrote this (no
internet access there to `pip install`), so this file is NOT verified by
execution the way db.py's functions are — I tested the read functions it
calls directly (see db.py's get_* functions, confirmed working above).
The FastAPI code itself follows the standard, well-documented pattern
exactly — run the two commands below to confirm it in your own environment
in under a minute:

    pip install fastapi uvicorn
    uvicorn api.main:app --reload --port 8000

Then open http://localhost:8000/docs — FastAPI auto-generates an
interactive test page for every endpoint below.
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import db
from connection_check import check_provider, PROVIDER_URLS

# Phase 6: basic rate limiting on your own API, independent of whatever
# limits the upstream providers (Finnhub, FRED, etc.) impose on ingestion.
# This protects your server from being hammered by a client, not from the
# ingestion side — those limits are handled inside each collector instead.
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Mac Economics API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Allow the dashboard (served from a different origin during local dev,
# e.g. file:// or a Vite/static dev server) to call this API.
# Tighten this to your real frontend domain before going live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # TODO: restrict to your deployed frontend URL
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/economic-data/{indicator}")
@limiter.limit("60/minute")
def economic_data(request, indicator: str, limit: int = Query(50, le=500)):
    rows = db.get_economic_releases(indicator=indicator, limit=limit)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for indicator '{indicator}'")
    return rows


@app.get("/api/economic-data/recent/all")
@limiter.limit("60/minute")
def economic_data_recent(request):
    """Latest value per distinct indicator — powers the Calendar page's
    'Recent Releases' view without the frontend needing to know indicator
    names ahead of time."""
    return db.get_recent_releases_all()


@app.get("/api/data-quality/conflicts")
@limiter.limit("60/minute")
def data_conflicts(request, limit: int = Query(30, le=100)):
    """Cross-source verification log from verify_sources.py — every time
    two providers were compared for the same symbol, agree or conflict."""
    return db.get_recent_conflicts(limit=limit)


@app.get("/api/central-banks/{code}/statements")
@limiter.limit("60/minute")
def central_bank_statements(request, code: str, limit: int = Query(20, le=100)):
    rows = db.get_policy_statements(central_bank=code.upper(), limit=limit)
    return rows  # empty list is a valid response — bank just has no ingested statements yet


@app.get("/api/markets")
@limiter.limit("60/minute")
def markets(request, asset_class: str | None = Query(None)):
    return db.get_market_quotes(asset_class=asset_class)


@app.get("/api/news")
@limiter.limit("60/minute")
def news(request, limit: int = Query(20, le=100)):
    return db.get_news(limit=limit)


@app.get("/api/positioning/cot")
@limiter.limit("60/minute")
def cot(request, market: str | None = Query(None, description="e.g. 'GOLD - COMMODITY EXCHANGE INC.'")):
    return db.get_cot_reports(market_and_exchange=market)


@app.get("/api/seasonality")
@limiter.limit("60/minute")
def seasonality(request, symbol: str | None = Query(None)):
    return db.get_seasonality(symbol=symbol)


@app.get("/api/health")
def health():
    """Simple liveness check — point your host's health-check config at this."""
    return {"status": "ok"}


@app.post("/api/test-connection")
@limiter.limit("30/minute")
def test_connection(request, payload: dict = Body(...)):
    """
    THE FIX FOR THE 'Blocked' CORS PROBLEM.

    The dashboard's API Settings page calls this endpoint instead of
    calling Finnhub/FRED/Twelve Data/NewsData.io directly from the
    browser. Because this request runs server-side (via `requests`,
    not the browser's `fetch`), CORS never enters the picture — CORS is
    a browser-only restriction on cross-origin `fetch`/`XMLHttpRequest`
    calls; it does not apply to server-to-server HTTP requests at all.

    Body: {"provider": "fred" | "finnhub" | "twelvedata" | "newsdata", "key": "..."}
    Returns the same {status, message} shape the frontend already renders,
    so no other UI code needs to change — only which URL it calls.
    """
    provider = payload.get("provider", "")
    key = payload.get("key", "")
    if provider not in PROVIDER_URLS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")
    return check_provider(provider, key)
