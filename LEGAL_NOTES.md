# Legal / API Terms — what to check before relying on this in production

Terms change more often than code does — treat this as a starting point for
your own review, not a substitute for reading each provider's current terms
directly before going live commercially.

| Provider | Storage | Redistribution | Attribution | Commercial use |
|---|---|---|---|---|
| **FRED** (Federal Reserve Bank of St. Louis) | Yes — US government data, public domain | Yes | Not required, but FRED requests a citation/link when republishing charts or data | Yes |
| **CFTC** (Commitments of Traders) | Yes — US government data, public domain | Yes | Not required | Yes |
| **Finnhub** | ⚠️ Free-tier data is tied to an active subscription — their FAQ states you must delete their proprietary data if you stop paying/using the service, to stay compliant with their terms and the underlying exchanges' agreements | Commercial licensing is handled separately (contact their sales team) — the free tier is positioned for evaluation/development, not built for redistributing raw data downstream | Not required for typical use | Free tier: evaluate only; confirm a commercial license before shipping a paid product on it |
| **Twelve Data** | Check current terms at signup — confirm before long-term storage | Check current terms | Not required for typical use | Free tier historically usable for small apps; confirm before commercial use |
| **GDELT Project** | Yes — explicitly built for research/monitoring use, no key required | Generally yes for metadata (titles, URLs, tone scores) — this project never stores full article text, which avoids most redistribution concerns entirely | Appreciated, citing "GDELT Project" is good practice | Generally permitted; GDELT is positioned as an open research resource |
| **NewsData.io** | Yes | Full article republishing may need separate terms — this project only stores title/URL/metadata, not full body text, which is the safer lane | Not required on the free tier | ✅ Free tier explicitly permits commercial use — this is actually unusual; most competitors (NewsAPI.org, GNews, mediastack) restrict their free tiers to non-commercial/development use only |
| **Stooq** | Historical daily price data, widely used for exactly this kind of computation | Check current terms before commercial redistribution of derived seasonality figures | Not required for typical use | Generally used freely for personal/research tooling; confirm before commercial redistribution |
| **Central bank RSS feeds** (Fed, ECB, etc.) | Yes — these are official government/public-institution communications | Yes — this is literally what a press-release RSS feed is for | Citing the bank as the source (already done via the `sources` table) is good practice | Yes |

## The practical takeaway for this project specifically

- This pipeline stores **metadata** (prices, headline text, URLs, tone/sentiment scores) rather than full article bodies or proprietary datasets — that's the least legally exposed lane across every provider above.
- The one to watch closest is **Finnhub**: re-read their current terms before you build anything where users see Finnhub-derived numbers after you'd stop paying/using their free tier, since their FAQ ties data retention to an active subscription.
- **NewsData.io** is currently the most commercial-use-friendly of the news sources here — that's exactly why it was picked as the backup provider earlier in this project.
- None of this is legal advice — for a real commercial product, especially one redistributing market data, a quick review by someone who actually reads ToS for a living is worth the cost before you have paying users.
