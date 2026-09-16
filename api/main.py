import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))  # repo root, so "import db" works
sys.path.append(os.path.dirname(__file__))  # api's own folder, so "import connection_check" works

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import db
from connection_check import check_provider, PROVIDER_URLS

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Mac Economics API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.get("/api/central-banks/{code}/statements")
@limiter.limit("60/minute")
def central_bank_statements(request, code: str, limit: int = Query(20, le=100)):
    return db.get_policy_statements(central_bank=code.upper(), limit=limit)


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
def cot(request, market: str | None = Query(None)):
    return db.get_cot_reports(market_and_exchange=market)


@app.get("/api/seasonality")
@limiter.limit("60/minute")
def seasonality(request, symbol: str | None = Query(None)):
    return db.get_seasonality(symbol=symbol)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/test-connection")
@limiter.limit("30/minute")
def test_connection(request, payload: dict = Body(...)):
    provider = payload.get("provider", "")
    key = payload.get("key", "")
    if provider not in PROVIDER_URLS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")
    return check_provider(provider, key)
