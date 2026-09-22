# Phase 4 & 5 worked examples — hosting + scheduling

## Hosting the API (Railway example — Render/Fly.io follow the same shape)

`Procfile` (put in `ingestion/`):
```
web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

Deploy:
```bash
npm install -g @railway/cli      # one-time
railway login
railway init
railway add                       # choose "Postgres" plugin here — gives you DATABASE_URL automatically
railway variables set FRED_API_KEY=your_key FINNHUB_API_KEY=your_key TWELVEDATA_API_KEY=your_key NEWSDATA_API_KEY=your_key
railway up
```

Railway prints a live URL like `https://mac-economics-api-production.up.railway.app`
when it finishes. That's `API_BASE` in the dashboard wiring example.

## Hosting the dashboard (Netlify example — Vercel is nearly identical)

```bash
npm install -g netlify-cli        # one-time
netlify deploy --prod --dir .     # run from the folder containing mac-economics.html
```

Before deploying, update `API_BASE` in the HTML to your real Railway URL from
the step above (not `localhost`) — this is the one manual edit each time the
API's URL changes, until you wire up an environment-variable build step.

## Scheduling ingestion (GitHub Actions — no server to babysit)

This runs the collectors on a schedule using GitHub's free CI minutes,
writing straight to your hosted Postgres — no cron server needed at all.

`.github/workflows/ingest.yml`:
```yaml
name: Scheduled ingestion

on:
  schedule:
    - cron: "0 * * * *"   # every hour — tune per collector cost/rate limits
  workflow_dispatch: {}     # lets you trigger it manually from the Actions tab

jobs:
  ingest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r ingestion/requirements.txt
      - run: python ingestion/run_all.py
        working-directory: ingestion
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
          SOCRATA_APP_TOKEN: ${{ secrets.SOCRATA_APP_TOKEN }}
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          TWELVEDATA_API_KEY: ${{ secrets.TWELVEDATA_API_KEY }}
          NEWSDATA_API_KEY: ${{ secrets.NEWSDATA_API_KEY }}
```

Set the secrets once: GitHub repo → Settings → Secrets and variables →
Actions → New repository secret, one per line above.

Different collectors want different cadences — split into multiple
workflow files if hourly is wasteful for some (e.g. COT data only updates
weekly on Fridays):
```yaml
    - cron: "30 20 * * 5"   # Fridays 20:30 UTC, matches CFTC's weekly release
```

## A minimal Dockerfile (if a host wants a container instead of buildpacks)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t mac-economics-api .
docker run -p 8000:8000 --env-file .env mac-economics-api
```
