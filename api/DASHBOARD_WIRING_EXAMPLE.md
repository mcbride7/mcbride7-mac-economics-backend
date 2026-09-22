# Phase 2 worked example — wiring the dashboard to the live API

This shows the *exact* before/after for one tab (Markets), so you can repeat
the same pattern for the others. It's deliberately the simplest tab to start
with — no scoring/AI logic, just "fetch numbers, render them."

## Before (current `mac-economics.html` — hardcoded)

```js
const MARKETS = {
  fx:[["EUR/USD",1.0842,-0.31], ["GBP/USD",1.2715,0.18], ...],
  indices:[["S&P 500",6412,0.32], ...],
  ...
};

function pageMarkets(){
  const row=(arr)=>arr.map(([n,p,c])=>`<tr>...</tr>`).join("");
  content.innerHTML = `... ${row(MARKETS.fx)} ...`;
}
```

## After — fetch from the API instead

```js
const API_BASE = "https://your-deployed-api.example.com";  // http://localhost:8000 for local dev

async function fetchMarkets(assetClass) {
  const res = await fetch(`${API_BASE}/api/markets?asset_class=${assetClass}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function pageMarkets(){
  // Show a loading state immediately — API calls take time
  content.innerHTML = `
    <div class="page-head">
      <div class="eyebrow">04 · Markets</div>
      <h1>Cross-Asset Market Data</h1>
    </div>
    <div class="card">Loading live quotes…</div>
  `;

  let fx = [];
  try {
    fx = await fetchMarkets("fx");
  } catch (e) {
    content.innerHTML = `<div class="card">Couldn't load market data: ${e.message}</div>`;
    return;
  }

  const row = fx.map(q => `
    <tr>
      <td>${q.symbol}</td>
      <td>${q.price}</td>
      <td class="${q.change_pct >= 0 ? 'up' : 'down'}">${q.change_pct}%</td>
    </tr>`).join("");

  content.innerHTML = `
    <div class="page-head">
      <div class="eyebrow">04 · Markets</div>
      <h1>Cross-Asset Market Data</h1>
    </div>
    <div class="card">
      <div class="card-title">FX <span class="tag">Live — Finnhub</span></div>
      <table><tr><th>Pair</th><th>Price</th><th>Chg</th></tr>${row}</table>
    </div>
  `;
}
```

## What changed, mechanically

1. `pageMarkets()` becomes `async` — it now waits on a network call instead of reading a local object.
2. `MARKETS` (the hardcoded object) is deleted entirely for the sections you've wired up.
3. Add a loading state and an error state — the dashboard's `notice()` sample-data
   disclaimer can be deleted once a section is live, since the data is no longer sample data.
4. `ROUTES.markets` in the router at the bottom of the file doesn't need to change —
   it already just calls `pageMarkets()`; making that function `async` is transparent to the caller.

## Doing this for every tab

Repeat this pattern per tab, pointing at the matching endpoint:

| Tab | Endpoint |
|---|---|
| Markets | `GET /api/markets?asset_class=fx\|equity_index\|commodity\|crypto` |
| Central Banks (statements list) | `GET /api/central-banks/{CODE}/statements` |
| News Intelligence | `GET /api/news` |
| Positioning | `GET /api/positioning/cot?market=...` |
| Economic Calendar / macro data | `GET /api/economic-data/{indicator}` e.g. `US_10Y_YIELD` |
| (new) Seasonality | `GET /api/seasonality?symbol=Gold` |

**Honest gap:** the Central Bank *profile* view (policy score, hawkish/dovish drivers,
rate probabilities) isn't backed by real data yet — that requires the AI scoring engine
from the architecture doc's §10–11, which hasn't been built. Wiring `/statements` gets
you the real press-release list; the score/drivers panel should stay clearly marked as
illustrative until that engine exists, rather than quietly mixing real and fake numbers
in the same view.
