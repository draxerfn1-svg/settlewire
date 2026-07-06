# SettleWire — automated prediction-markets news aggregator

Hourly pipeline: **RSS ingest → keyword filter → cluster same-story coverage →
Claude writes an original headline + "why this matters" → publish to static site.**

We store headline/link/source/timestamp metadata only and link out to every
source — article bodies are never stored or republished. The analysis layer is
original generated text. Keep it that way; it's the whole legal posture.

## Deploy (one-time, ~5 minutes)

1. Push this folder to a new GitHub repo (`main` branch).
2. Repo → Settings → Secrets and variables → Actions → add secret
   `ANTHROPIC_API_KEY`.
3. Repo → Settings → Pages → Source: **GitHub Actions**.
4. Actions tab → run the `publish` workflow manually once.

Done. The workflow then runs hourly at :07, commits an updated
`site/stories.json`, and redeploys Pages. The site shows fallback sample
content until the first successful run.

## Run locally

```bash
pip install requests feedparser
python pipeline/run.py --dry-run                      # no LLM calls
ANTHROPIC_API_KEY=sk-... python pipeline/run.py       # full run
python -m http.server -d site 8000                    # view at localhost:8000
```

## Tune coverage

Everything lives in `pipeline/config.py`:

| Setting | What it does |
|---|---|
| `KEYWORDS` | article must match one to enter the pipeline |
| `CONTEXT_REQUIRED` / `CONTEXT_TERMS` | noisy keywords (e.g. `cftc`) need a second market term nearby |
| `GOOGLE_NEWS_QUERIES` | free wide-net coverage of the whole Tier 1–6 source pool |
| `DIRECT_FEEDS` | high-signal outlets polled directly |
| `MAX_NEW_PER_RUN` | LLM-call cap per hour (cost control, default 12) |
| `SIMILARITY_CUTOFF` | title-overlap threshold for clustering (default 0.62) |
| `MAX_STORIES_KEPT` | rolling archive size in stories.json |

## Cost

Worst case 12 Sonnet calls/hour × ~700 tokens ≈ pennies/day. Typical hours
produce 0–3 new clusters.

## How dedupe works across runs

`stories.json` is both the site feed and the pipeline's memory. Each run
re-reads it; incoming items that match an existing story get appended to that
story's "also covered by" list instead of creating duplicates.

## Phase 2 roadmap (in order of impact)

1. **Live odds ticker** — client-side fetch of Kalshi (`api.elections.kalshi.com`)
   and Polymarket (`gamma-api.polymarket.com`) public endpoints; replace the
   static `TICKS` array.
2. **Entity pages** — `/entity/kalshi.html` generated from the `entities`
   field already in every story.
3. **Newsletter** — point the subscribe forms at Beehiiv/Loops; a second
   workflow compiles the top-5 stories into the daily send.
4. **Events calendar** — a small `events.json` maintained by hand or scraped
   from court dockets/earnings calendars.
5. **Odds-on-story** — have the LLM also return a related market ticker, then
   hydrate the probability chip live from the Kalshi API.
