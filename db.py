"""
db.py — storage layer for the Mac Economics ingestion pipeline.

DUAL BACKEND:
    - No DATABASE_URL set  -> SQLite at data/mac_economics.db (zero-setup dev mode)
    - DATABASE_URL set to a postgres:// / postgresql:// URL -> real Postgres

Every collector calls the same functions (init_db, upsert_source,
insert_policy_statement, insert_market_quote, insert_news_article, etc.)
regardless of backend — nothing in collectors/ changes based on which
database is active. This is the "real database" swap: point DATABASE_URL
at Supabase/Neon/RDS/Railway/etc. and every script in this project starts
writing there instead, with zero code changes anywhere else.

psycopg2 is only imported when DATABASE_URL is actually a Postgres URL, so
the SQLite dev path has no hard dependency on it (this is deliberate — it's
why this file is fully testable without psycopg2 installed).
"""

import os
import sqlite3
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "mac_economics.db")

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    DUPLICATE_ERRORS = (psycopg2.errors.UniqueViolation,)
else:
    DUPLICATE_ERRORS = (sqlite3.IntegrityError,)


def get_conn():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _q(query):
    """Translate SQLite's '?' placeholders to Postgres's '%s'. Both dialects
    already share the same ON CONFLICT upsert syntax, so this one substitution
    is the only difference between the two query paths."""
    return query.replace("?", "%s") if USE_POSTGRES else query


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    if USE_POSTGRES:
        ddl = """
        CREATE TABLE IF NOT EXISTS sources (
            id SERIAL PRIMARY KEY,
            url TEXT NOT NULL,
            publisher TEXT NOT NULL,
            country TEXT,
            category TEXT NOT NULL,
            reliability_tier INTEGER NOT NULL,
            retrieved_at TIMESTAMPTZ NOT NULL,
            UNIQUE(url, publisher)
        );
        CREATE TABLE IF NOT EXISTS policy_statements (
            id SERIAL PRIMARY KEY,
            central_bank TEXT NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT,
            link TEXT UNIQUE,
            summary TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS economic_releases (
            id SERIAL PRIMARY KEY,
            country TEXT NOT NULL,
            indicator TEXT NOT NULL,
            reference_date TEXT NOT NULL,
            value DOUBLE PRECISION,
            unit TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TIMESTAMPTZ NOT NULL,
            UNIQUE(country, indicator, reference_date)
        );
        CREATE TABLE IF NOT EXISTS market_quotes (
            id SERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT,
            price DOUBLE PRECISION,
            change_pct DOUBLE PRECISION,
            quote_time TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TIMESTAMPTZ NOT NULL,
            UNIQUE(provider, symbol, quote_time)
        );
        CREATE TABLE IF NOT EXISTS news_articles (
            id SERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT UNIQUE,
            published_at TEXT,
            domain TEXT,
            tone DOUBLE PRECISION,
            sentiment TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seasonality_stats (
            id SERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            symbol TEXT NOT NULL,
            month INTEGER NOT NULL,
            avg_return_pct DOUBLE PRECISION,
            positive_rate_pct DOUBLE PRECISION,
            years_sampled INTEGER,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TIMESTAMPTZ NOT NULL,
            UNIQUE(provider, symbol, month)
        );
        CREATE TABLE IF NOT EXISTS cot_reports (
            id SERIAL PRIMARY KEY,
            market_and_exchange TEXT NOT NULL,
            report_date TEXT NOT NULL,
            noncommercial_long DOUBLE PRECISION,
            noncommercial_short DOUBLE PRECISION,
            commercial_long DOUBLE PRECISION,
            commercial_short DOUBLE PRECISION,
            open_interest DOUBLE PRECISION,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TIMESTAMPTZ NOT NULL,
            UNIQUE(market_and_exchange, report_date)
        );
        """
    else:
        ddl = """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            publisher TEXT NOT NULL,
            country TEXT,
            category TEXT NOT NULL,
            reliability_tier INTEGER NOT NULL,
            retrieved_at TEXT NOT NULL,
            UNIQUE(url, publisher)
        );
        CREATE TABLE IF NOT EXISTS policy_statements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            central_bank TEXT NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT,
            link TEXT UNIQUE,
            summary TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS economic_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country TEXT NOT NULL,
            indicator TEXT NOT NULL,
            reference_date TEXT NOT NULL,
            value REAL,
            unit TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TEXT NOT NULL,
            UNIQUE(country, indicator, reference_date)
        );
        CREATE TABLE IF NOT EXISTS market_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT,
            price REAL,
            change_pct REAL,
            quote_time TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TEXT NOT NULL,
            UNIQUE(provider, symbol, quote_time)
        );
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT UNIQUE,
            published_at TEXT,
            domain TEXT,
            tone REAL,
            sentiment TEXT,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seasonality_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            symbol TEXT NOT NULL,
            month INTEGER NOT NULL,
            avg_return_pct REAL,
            positive_rate_pct REAL,
            years_sampled INTEGER,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TEXT NOT NULL,
            UNIQUE(provider, symbol, month)
        );
        CREATE TABLE IF NOT EXISTS cot_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_and_exchange TEXT NOT NULL,
            report_date TEXT NOT NULL,
            noncommercial_long REAL,
            noncommercial_short REAL,
            commercial_long REAL,
            commercial_short REAL,
            open_interest REAL,
            source_id INTEGER REFERENCES sources(id),
            ingested_at TEXT NOT NULL,
            UNIQUE(market_and_exchange, report_date)
        );
        """

    # Explicit indexes beyond what the UNIQUE constraints already create —
    # these speed up the query patterns api/main.py actually uses: filtering
    # by symbol/indicator/bank and ordering by date/time. IF NOT EXISTS is
    # valid in both SQLite and Postgres, so this list is shared.
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_sources_category ON sources(category);",
        "CREATE INDEX IF NOT EXISTS idx_policy_statements_bank_date "
        "ON policy_statements(central_bank, published_at);",
        "CREATE INDEX IF NOT EXISTS idx_economic_releases_indicator_date "
        "ON economic_releases(indicator, reference_date);",
        "CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_time "
        "ON market_quotes(symbol, quote_time);",
        "CREATE INDEX IF NOT EXISTS idx_market_quotes_asset_class "
        "ON market_quotes(asset_class);",
        "CREATE INDEX IF NOT EXISTS idx_news_articles_published "
        "ON news_articles(published_at);",
        "CREATE INDEX IF NOT EXISTS idx_cot_market_date "
        "ON cot_reports(market_and_exchange, report_date);",
        "CREATE INDEX IF NOT EXISTS idx_seasonality_symbol "
        "ON seasonality_stats(symbol);",
    ]

    if USE_POSTGRES:
        cur.execute(ddl)
    else:
        cur.executescript(ddl)
    for stmt in indexes:
        cur.execute(stmt)

    conn.commit()
    conn.close()


def upsert_source(publisher, url, category, reliability_tier, country=None):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        _q("""
        INSERT INTO sources (url, publisher, country, category, reliability_tier, retrieved_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url, publisher) DO UPDATE SET retrieved_at=excluded.retrieved_at
        """),
        (url, publisher, country, category, reliability_tier, now),
    )
    conn.commit()
    cur.execute(_q("SELECT id FROM sources WHERE url=? AND publisher=?"), (url, publisher))
    row = cur.fetchone()
    conn.close()
    return row[0]


def insert_policy_statement(central_bank, title, published_at, link, summary, source_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur.execute(
            _q("""
            INSERT INTO policy_statements
                (central_bank, title, published_at, link, summary, source_id, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """),
            (central_bank, title, published_at, link, summary, source_id, now),
        )
        conn.commit()
        return True
    except DUPLICATE_ERRORS:
        conn.rollback()  # required on Postgres — clears the aborted-transaction state
        return False
    finally:
        conn.close()


def insert_economic_release(country, indicator, reference_date, value, unit, source_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        _q("""
        INSERT INTO economic_releases
            (country, indicator, reference_date, value, unit, source_id, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(country, indicator, reference_date) DO UPDATE SET
            value=excluded.value, ingested_at=excluded.ingested_at
        """),
        (country, indicator, reference_date, value, unit, source_id, now),
    )
    conn.commit()
    conn.close()


def insert_market_quote(provider, symbol, asset_class, price, change_pct, quote_time, source_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        _q("""
        INSERT INTO market_quotes
            (provider, symbol, asset_class, price, change_pct, quote_time, source_id, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, symbol, quote_time) DO UPDATE SET
            price=excluded.price, change_pct=excluded.change_pct, ingested_at=excluded.ingested_at
        """),
        (provider, symbol, asset_class, price, change_pct, quote_time, source_id, now),
    )
    conn.commit()
    conn.close()


def insert_news_article(provider, title, url, published_at, domain, tone, sentiment, source_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur.execute(
            _q("""
            INSERT INTO news_articles
                (provider, title, url, published_at, domain, tone, sentiment, source_id, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """),
            (provider, title, url, published_at, domain, tone, sentiment, source_id, now),
        )
        conn.commit()
        return True
    except DUPLICATE_ERRORS:
        conn.rollback()
        return False
    finally:
        conn.close()


def insert_seasonality_stat(provider, symbol, month, avg_return_pct, positive_rate_pct,
                             years_sampled, source_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        _q("""
        INSERT INTO seasonality_stats
            (provider, symbol, month, avg_return_pct, positive_rate_pct, years_sampled,
             source_id, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, symbol, month) DO UPDATE SET
            avg_return_pct=excluded.avg_return_pct,
            positive_rate_pct=excluded.positive_rate_pct,
            years_sampled=excluded.years_sampled,
            ingested_at=excluded.ingested_at
        """),
        (provider, symbol, month, avg_return_pct, positive_rate_pct, years_sampled,
         source_id, now),
    )
    conn.commit()
    conn.close()


def insert_cot_report(market_and_exchange, report_date, noncomm_long, noncomm_short,
                       comm_long, comm_short, open_interest, source_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        _q("""
        INSERT INTO cot_reports
            (market_and_exchange, report_date, noncommercial_long, noncommercial_short,
             commercial_long, commercial_short, open_interest, source_id, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_and_exchange, report_date) DO UPDATE SET
            noncommercial_long=excluded.noncommercial_long,
            noncommercial_short=excluded.noncommercial_short,
            commercial_long=excluded.commercial_long,
            commercial_short=excluded.commercial_short,
            open_interest=excluded.open_interest,
            ingested_at=excluded.ingested_at
        """),
        (market_and_exchange, report_date, noncomm_long, noncomm_short,
         comm_long, comm_short, open_interest, source_id, now),
    )
    conn.commit()
    conn.close()


# ============================================================
# READ FUNCTIONS — used by the API layer (api/main.py)
# ============================================================

def _rows_to_dicts(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_economic_releases(indicator=None, limit=50):
    conn = get_conn()
    cur = conn.cursor()
    if indicator:
        cur.execute(
            _q("SELECT country, indicator, reference_date, value, unit FROM economic_releases "
               "WHERE indicator = ? ORDER BY reference_date DESC LIMIT ?"),
            (indicator, limit),
        )
    else:
        cur.execute(
            _q("SELECT country, indicator, reference_date, value, unit FROM economic_releases "
               "ORDER BY reference_date DESC LIMIT ?"),
            (limit,),
        )
    result = _rows_to_dicts(cur)
    conn.close()
    return result


def get_policy_statements(central_bank=None, limit=20):
    conn = get_conn()
    cur = conn.cursor()
    if central_bank:
        cur.execute(
            _q("SELECT central_bank, title, published_at, link, summary FROM policy_statements "
               "WHERE central_bank = ? ORDER BY published_at DESC LIMIT ?"),
            (central_bank, limit),
        )
    else:
        cur.execute(
            _q("SELECT central_bank, title, published_at, link, summary FROM policy_statements "
               "ORDER BY published_at DESC LIMIT ?"),
            (limit,),
        )
    result = _rows_to_dicts(cur)
    conn.close()
    return result


def get_market_quotes(asset_class=None):
    conn = get_conn()
    cur = conn.cursor()
    if asset_class:
        cur.execute(
            _q("SELECT provider, symbol, asset_class, price, change_pct, quote_time "
               "FROM market_quotes WHERE asset_class = ? "
               "AND quote_time = (SELECT MAX(quote_time) FROM market_quotes WHERE symbol = market_quotes.symbol) "
               "ORDER BY symbol"),
            (asset_class,),
        )
    else:
        cur.execute(
            _q("SELECT provider, symbol, asset_class, price, change_pct, quote_time "
               "FROM market_quotes WHERE quote_time = "
               "(SELECT MAX(quote_time) FROM market_quotes m2 WHERE m2.symbol = market_quotes.symbol) "
               "ORDER BY asset_class, symbol")
        )
    result = _rows_to_dicts(cur)
    conn.close()
    return result


def get_news(limit=20):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        _q("SELECT provider, title, url, published_at, domain, tone, sentiment "
           "FROM news_articles ORDER BY published_at DESC LIMIT ?"),
        (limit,),
    )
    result = _rows_to_dicts(cur)
    conn.close()
    return result


def get_cot_reports(market_and_exchange=None):
    conn = get_conn()
    cur = conn.cursor()
    if market_and_exchange:
        cur.execute(
            _q("SELECT market_and_exchange, report_date, noncommercial_long, noncommercial_short, "
               "commercial_long, commercial_short, open_interest FROM cot_reports "
               "WHERE market_and_exchange = ? ORDER BY report_date DESC LIMIT 12"),
            (market_and_exchange,),
        )
    else:
        cur.execute(
            _q("SELECT market_and_exchange, report_date, noncommercial_long, noncommercial_short, "
               "commercial_long, commercial_short, open_interest FROM cot_reports "
               "ORDER BY report_date DESC LIMIT 50")
        )
    result = _rows_to_dicts(cur)
    conn.close()
    return result


def get_seasonality(symbol=None):
    conn = get_conn()
    cur = conn.cursor()
    if symbol:
        cur.execute(
            _q("SELECT provider, symbol, month, avg_return_pct, positive_rate_pct, years_sampled "
               "FROM seasonality_stats WHERE symbol = ? ORDER BY month"),
            (symbol,),
        )
    else:
        cur.execute(
            _q("SELECT provider, symbol, month, avg_return_pct, positive_rate_pct, years_sampled "
               "FROM seasonality_stats ORDER BY symbol, month")
        )
    result = _rows_to_dicts(cur)
    conn.close()
    return result
