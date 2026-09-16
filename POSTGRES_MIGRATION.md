# Phase 3 worked example — moving from SQLite to Postgres

## Step 1 — run Postgres locally to test the migration before touching production

`docker-compose.yml` (put this in the `ingestion/` folder):

```yaml
version: "3.8"
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: mac_economics
      POSTGRES_USER: mac_economics
      POSTGRES_PASSWORD: localdevpassword
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
```

Run it: `docker-compose up -d`. You now have a real Postgres instance at
`postgresql://mac_economics:localdevpassword@localhost:5432/mac_economics`.

## Step 2 — the actual code change (db.py only — no collector changes)

Add `psycopg2-binary` to `requirements.txt`, then replace the top of `db.py`:

```python
# BEFORE
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "mac_economics.db")

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
```

```python
# AFTER
import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]  # postgresql://user:pass@host:5432/dbname

def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.cursor_factory = psycopg2.extras.DictCursor
    return conn
```

The two syntax differences to handle across the rest of the file:
- SQLite uses `?` placeholders; psycopg2 uses `%s`. A quick way to migrate every
  query at once without hand-editing each one:
  ```python
  def _pg(query):
      return query.replace("?", "%s")
  # then wrap every cur.execute(query, params) as cur.execute(_pg(query), params)
  ```
  (Cleaner long-term: rewrite each query natively — there are only ~15 in `db.py`.)
- SQLite's `INSERT ... ON CONFLICT(...) DO UPDATE` syntax is already
  Postgres-compatible (SQLite borrowed Postgres's upsert syntax), so those
  clauses need **no changes** — this was a deliberate choice when `db.py`
  was written, specifically to make this migration painless.

## Step 3 — create the schema in Postgres

```bash
export DATABASE_URL="postgresql://mac_economics:localdevpassword@localhost:5432/mac_economics"
python -c "from db import init_db; init_db()"
```

## Step 4 — verify with a real run

```bash
export FRED_API_KEY="..."
python run_all.py
psql "$DATABASE_URL" -c "SELECT indicator, reference_date, value FROM economic_releases LIMIT 5;"
```

## Step 5 — for production, use a managed Postgres instead of self-hosting

Free tiers as of writing (confirm current limits before committing — these
change often):
- **Neon** — serverless Postgres, generous free tier, scales to zero when idle
- **Supabase** — free Postgres + built-in REST API generation (could even
  replace some of `api/main.py` if you want less to maintain)
- **Railway** — free trial credit, then usage-based; convenient if you're
  also hosting the API there (see `DEPLOY.md`)

Whichever you pick, you'll get a `DATABASE_URL` connection string from their
dashboard — set it as an environment variable on your host (never commit it
to the repo) and everything above works unchanged.
