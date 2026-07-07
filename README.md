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

## SEO & GEO

The pipeline now generates everything search engines and AI answer engines
need, every run: keyword-slug article URLs, NewsArticle JSON-LD on every
story, `sitemap.xml`, a Google-compliant 48-hour `news-sitemap.xml`,
`rss.xml`, `robots.txt` (search + AI crawlers explicitly allowed), and
`llms.txt`.

**One-time setup (in order of impact):**

1. **Set `SITE_URL` in `pipeline/config.py`** to your live URL — canonicals
   and sitemaps are built from it. Nothing works right until this is set.
2. **Get a custom domain** (~$10/yr) and attach it in repo Settings → Pages.
   On `username.github.io/settlewire` your robots.txt sits at a subpath
   crawlers never read, and you share domain reputation with every other
   github.io project. A custom domain fixes both and is the single
   highest-impact SEO action for this site. Update `SITE_URL` after.
3. **Google Search Console** (search.google.com/search-console): verify the
   site, then submit `sitemap.xml` and `news-sitemap.xml` under Sitemaps.
   This works even before the custom domain.
4. **Bing Webmaster Tools**: same drill — Bing powers ChatGPT's web search,
   so this is a GEO play, not just Bing traffic.
5. **Google Publisher Center** (publishercenter.google.com): add the site so
   it's eligible for Google News surfaces.

**Standing rules that protect rankings:**

- Don't loosen the keyword gate to chase volume. Google's scaled-content
  policy targets mass-produced pages with little added value; the strict
  topical focus + original analysis layer is the defense.
- Never artificially freshen dates — `updated` is only set when new outlet
  coverage genuinely lands.
- The `why` analysis is the differentiator Google and AI engines cite. If
  quality drops there, fix the prompt before anything else.

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
