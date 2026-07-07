"""
SettleWire hourly pipeline.

  ingest -> keyword filter -> dedupe/cluster -> Claude contextualization -> stories.json

IP posture (do not change): we store headline, link, source name, and timestamp
only. Source article bodies are never stored or republished. The lede and
"why this matters" are original text generated from the headline signal, and
every story links out to the primary source.

Usage:
  ANTHROPIC_API_KEY=sk-... python pipeline/run.py
  python pipeline/run.py --dry-run     # no LLM calls, mock context
"""
import json, os, re, sys, time, hashlib
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests
import feedparser

sys.path.insert(0, os.path.dirname(__file__))
import config

UA = {"User-Agent": "SettleWireBot/1.0 (news aggregation; links out to sources)"}
NOW = datetime.now(timezone.utc)


# ----------------------------------------------------------------- ingest
def fetch_feed(name, url):
    """Fetch one RSS feed, return normalized items (metadata only)."""
    try:
        r = requests.get(url, headers=UA, timeout=20)
        parsed = feedparser.parse(r.content)
    except Exception as e:
        print(f"  [warn] {name}: {e}")
        return []
    items = []
    for e in parsed.entries[:40]:
        title = (e.get("title") or "").strip()
        link = e.get("link") or ""
        if not title or not link:
            continue
        # summary used ONLY as a keyword-matching signal, truncated, never stored
        signal = re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:300]
        # Google News titles arrive as "Headline - Outlet"
        source = name
        if name == "Google News" and " - " in title:
            title, source = title.rsplit(" - ", 1)
        published = None
        for k in ("published_parsed", "updated_parsed"):
            if e.get(k):
                published = datetime(*e[k][:6], tzinfo=timezone.utc).isoformat()
                break
        items.append({
            "title": title.strip(),
            "url": link,
            "source": source.strip(),
            "published": published or NOW.isoformat(),
            "_signal": signal,
        })
    return items


def ingest():
    raw = []
    for q in config.GOOGLE_NEWS_QUERIES:
        raw += fetch_feed("Google News", config.google_news_url(q))
    for name, url in config.DIRECT_FEEDS:
        raw += fetch_feed(name, url)
    print(f"[ingest] {len(raw)} items pulled")
    return raw


# ----------------------------------------------------------------- filter
def matches_keywords(item):
    text = f"{item['title']} {item['_signal']}".lower()
    for kw in config.KEYWORDS:
        if kw in text:
            if kw in config.CONTEXT_REQUIRED:
                if not any(t in text for t in config.CONTEXT_TERMS if t != kw):
                    continue
            return True
    return False


# ------------------------------------------------------------ dedupe/cluster
STOP = {"a","an","the","in","on","at","of","for","to","by","as","and","or",
        "is","are","its","it","with","after","amid","over","into","from"}

def norm_title(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def tokens(t):
    return {w for w in norm_title(t).split() if w not in STOP}


def similar(a, b):
    """Max of char-level ratio and stopword-filtered token overlap —
    catches both near-identical strings and reordered rewrites."""
    char = SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()
    ta, tb = tokens(a), tokens(b)
    tok = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0
    return max(char, tok)


def cluster(items, existing):
    """Group same-story coverage; drop anything already published."""
    existing_titles = [s["headline"] for s in existing] + [
        s.get("source_title", "") for s in existing
    ]
    clusters = []
    for it in sorted(items, key=lambda x: x["published"]):
        if any(similar(it["title"], t) > config.SIMILARITY_CUTOFF for t in existing_titles):
            # coverage of an already-published story -> append as "also"
            for s in existing:
                if similar(it["title"], s.get("source_title", s["headline"])) > config.SIMILARITY_CUTOFF:
                    also_urls = {a["url"] for a in s["also"]} | {s["primary_source"]["url"]}
                    if it["url"] not in also_urls and it["source"] != s["primary_source"]["name"]:
                        s["also"].append({"name": it["source"], "url": it["url"]})
                        s["updated"] = NOW.isoformat()
                    break
            continue
        placed = False
        for c in clusters:
            if similar(it["title"], c[0]["title"]) > config.SIMILARITY_CUTOFF:
                c.append(it)
                placed = True
                break
        if not placed:
            clusters.append([it])
    return clusters


# ------------------------------------------------------- contextualization
def fetch_article_signal(url, limit=2500):
    """Fetch the primary source page and return plain-text signal for the LLM.
    Used ONLY as reading input for original writing — never stored or shown."""
    try:
        r = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception:
        return ""


PROMPT = """You are the editor of a prediction-markets news site. Below is coverage of one news event: outlet headlines, and (if available) extracted text from the primary source page — that text is READING MATERIAL ONLY.

Write an ORIGINAL story in strict JSON (no markdown fences, no preamble):
{{
  "headline": "your own headline, max 12 words and 100 characters, key entity/topic words first, punchy, no clickbait",
  "lede": "one original sentence that fully answers WHAT HAPPENED on its own — who, what, how much, when — max 32 words. A reader (or an AI answer engine) should get the complete fact from this sentence alone.",
  "body": ["2-3 short paragraphs, ~60 words each: concrete details with specific numbers, dollar amounts, dates and named entities first, then industry context. Attribute facts to the source by name, e.g. 'according to {primary}'."],
  "why": "2-3 sentences of original analysis: why this matters for prediction-market traders, operators, or regulation watchers. Each sentence should be quotable standalone. Be concrete about second-order effects.",
  "category": "one of: {cats}",
  "entities": ["up to 4 relevant entities e.g. Kalshi, Polymarket, CFTC"]
}}

Hard rules:
- Every sentence must be written in YOUR OWN words. Never copy or closely paraphrase sentences or phrasing from the source text — restate facts, in a different structure and voice.
- STRICT RELEVANCE GATE: this site covers ONLY prediction markets and event contracts (Polymarket, Kalshi, ForecastEx, Crypto.com sports contracts, CFTC event-contract regulation, and direct competitors). If this story is not squarely about that world — e.g. general crypto, general sports betting, generic CFTC enforcement — return exactly {{"skip": true}}.

Coverage:
{coverage}

Primary source reading material (may be empty or contain page noise):
{signal}"""


def contextualize(cluster_items, api_key, dry_run=False):
    coverage = "\n".join(f"- [{i['source']}] {i['title']}" for i in cluster_items[:8])
    primary = cluster_items[0]
    if dry_run:
        return {
            "headline": primary["title"][:70],
            "lede": "Dry-run placeholder lede.",
            "body": ["Dry-run placeholder paragraph one.", "Dry-run placeholder paragraph two."],
            "why": "Dry-run placeholder analysis.",
            "category": "Trading",
            "entities": [],
        }
    signal = fetch_article_signal(primary["url"])
    body = {
        "model": config.MODEL,
        "max_tokens": 900,
        "messages": [{"role": "user", "content": PROMPT.format(
            cats=", ".join(config.CATEGORIES),
            primary=primary["source"],
            coverage=coverage,
            signal=signal or "(unavailable — write from the headlines only)")}],
    }
    for attempt in range(3):
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json=body, timeout=60)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json()["content"])
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
            data = json.loads(text)
            if data.get("skip"):
                return None
            if data.get("category") not in config.CATEGORIES:
                data["category"] = "Trading"
            return data
        except Exception as e:
            print(f"  [llm retry {attempt+1}] {e}")
            time.sleep(2 * (attempt + 1))
    return None


# ----------------------------------------------------------------- publish
def story_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def slugify(headline, sid, taken):
    """SEO slug from the headline: keyword-rich, unique, stable."""
    s = re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:80].rstrip("-")
    if not s:
        s = sid
    if s in taken:
        s = f"{s}-{sid[:6]}"
    taken.add(s)
    return s


STORY_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{headline} — {site}</title>
<meta name="description" content="{lede}">
<meta property="og:title" content="{headline}">
<meta property="og:description" content="{lede}">
<meta property="og:type" content="article">
<meta property="og:url" content="{canonical}">
<meta name="twitter:card" content="summary">
<link rel="canonical" href="{canonical}">
<meta name="robots" content="index,follow,max-image-preview:large">
<script type="application/ld+json">{jsonld}</script>
<meta name="theme-color" content="#3e6f9e">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='13.5' fill='%235fa4e0' stroke='%23142c40' stroke-width='2.5'/%3E%3Ctext x='16' y='21.5' font-family='monospace' font-size='15' font-weight='700' text-anchor='middle' fill='%23142c40' transform='rotate(-8 16 16)'%3E%25%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..800&family=Instrument+Sans:wght@400;500;600;700&family=Spline+Sans+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../assets/site.css">
</head><body>
<div class="progress" id="prog"></div>
<header class="site">
  <div class="hwrap">
    <a class="logo" href="../index.html"><span class="logo-mark">%</span>{site}</a>
    <nav class="main" aria-label="Primary">
      <a href="../index.html">Home</a><a href="../events.html">The Docket</a><a href="../all.html">Archive</a><a href="../newsletter.html">Newsletter</a>
    </nav>
    <a class="cta" href="../newsletter.html">Get the Brief</a>
  </div>
</header>
<main class="wrap narrow" style="padding-top:2.2rem">
<article class="ticket rv in">
<div class="thead">
<span class="stamp">{category}</span>
<h1>{headline}</h1>
<div class="meta">Published <time datetime="{pub_iso}">{published}</time> · {site} newsroom</div>
</div>
<div class="tbody">
<p class="lede">{lede}</p>
<div class="abody">{body_html}</div>
<div class="whybox"><h2>Why this matters</h2><p>{why}</p></div>
<div class="srcbox"><h2>First reported by</h2><a href="{src_url}" target="_blank" rel="noopener">{src_name} ↗</a>{also_html}
<p class="note" style="font-size:.8rem;color:var(--faint2);margin-top:.8rem">{site} contextualizes coverage in its own words — we don't republish source articles.</p></div>
{ents_html}
</div>
</article>
{related_html}
</main>
<footer class="site">
  <div class="fbase">© 2026 {site} · EDITORIAL SITE — NOT A REGULATED TRADING PLATFORM · NOTHING HERE IS INVESTMENT ADVICE</div>
</footer>
<script>
addEventListener('scroll',()=>{{
 const h=document.documentElement;
 document.getElementById('prog').style.width=(h.scrollTop/(h.scrollHeight-h.clientHeight)*100)+'%';
}},{{passive:true}});
</script>
</body></html>"""


def render_story_page(s, all_stories=None):
    import html as H
    e = lambda x: H.escape(str(x or ""))
    fname = s.get("slug") or s["id"]
    page_url = f"{config.SITE_URL}/story/{fname}.html"
    body_paras = s.get("body") or [s.get("lede", "")]
    body_html = "".join(f"<p>{e(p)}</p>" for p in body_paras)
    also_html = ""
    if s.get("also"):
        links = "".join(
            f'<a href="{e(a["url"])}" target="_blank" rel="noopener">{e(a["name"])}</a>'
            for a in s["also"][:12])
        also_html = f'<h2 style="margin-top:1.1rem">Also covered by</h2><div class="also">{links}</div>'
    ents_html = ""
    if s.get("entities"):
        ents_html = '<div class="ents">' + "".join(
            f"<span>{e(x)}</span>" for x in s["entities"][:6]) + "</div>"
    related_html = ""
    if all_stories:
        rel = [r for r in all_stories
               if r["id"] != s["id"] and r.get("category") == s.get("category")][:4]
        if rel:
            rows = "".join(
                f'<a class="lrow" href="{e(r.get("slug") or r["id"])}.html"><div><h3>{e(r["headline"])}</h3>'
                f'<div class="row"><span class="stamp">{e(r["category"])}</span></div></div></a>'
                for r in rel)
            related_html = (f'<div class="sec-h" style="margin-top:2.4rem"><h2>More {e(s["category"])} tickets</h2></div>'
                            f'<div class="latest" style="grid-template-columns:1fr">{rows}</div>')
    try:
        pub_dt = datetime.fromisoformat(s["published"])
        pub = pub_dt.strftime("%b %d, %Y · %H:%M UTC").upper()
        pub_iso = pub_dt.isoformat()
    except Exception:
        pub = e(s.get("published", ""))
        pub_iso = s.get("published", "")
    mod_iso = s.get("updated") or s.get("first_seen") or pub_iso
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": s["headline"][:110],
        "description": s.get("lede", ""),
        "datePublished": pub_iso,
        "dateModified": mod_iso,
        "mainEntityOfPage": {"@type": "WebPage", "@id": page_url},
        "url": page_url,
        "articleSection": s.get("category", ""),
        "author": {"@type": "Organization", "name": f"{config.SITE_NAME} Newsroom",
                   "url": config.SITE_URL},
        "publisher": {"@type": "Organization", "name": config.SITE_NAME,
                      "url": config.SITE_URL},
        "isBasedOn": s["primary_source"]["url"],
        "isAccessibleForFree": True,
    })
    page = STORY_TEMPLATE.format(
        site=config.SITE_NAME, headline=e(s["headline"]), lede=e(s["lede"]),
        category=e(s["category"]), published=pub, pub_iso=e(pub_iso),
        body_html=body_html, why=e(s["why"]),
        src_name=e(s["primary_source"]["name"]),
        src_url=e(s["primary_source"]["url"]),
        also_html=also_html, ents_html=ents_html, related_html=related_html,
        canonical=e(page_url), jsonld=jsonld)
    os.makedirs(config.STORY_DIR, exist_ok=True)
    with open(os.path.join(config.STORY_DIR, f"{fname}.html"), "w") as f:
        f.write(page)


def write_feeds(all_stories):
    """Generate sitemap.xml, news-sitemap.xml, rss.xml, robots.txt, llms.txt."""
    import html as H
    from email.utils import format_datetime
    e = lambda x: H.escape(str(x or ""), quote=True)
    base = config.SITE_URL.rstrip("/")
    surl = lambda s: f"{base}/story/{s.get('slug') or s['id']}.html"

    static_pages = ["index.html", "all.html", "events.html", "newsletter.html",
                    "about.html", "privacy.html", "terms.html", "risk.html"]
    cat_pages = [f"category.html?c={c}" for c in config.CATEGORIES]

    # ---- sitemap.xml (everything) ----
    rows = []
    for p in static_pages + cat_pages:
        rows.append(f"<url><loc>{e(base + '/' + p)}</loc><changefreq>hourly</changefreq></url>")
    for s in all_stories:
        lastmod = (s.get("updated") or s.get("first_seen") or s.get("published", ""))[:19]
        rows.append(f"<url><loc>{e(surl(s))}</loc><lastmod>{e(lastmod)}</lastmod></url>")
    with open("site/sitemap.xml", "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(rows) + "\n</urlset>\n")

    # ---- news-sitemap.xml (Google: only articles from the last 2 days) ----
    cutoff = NOW.timestamp() - config.NEWS_SITEMAP_HOURS * 3600
    fresh = []
    for s in all_stories:
        try:
            if datetime.fromisoformat(s["published"]).timestamp() < cutoff:
                continue
        except Exception:
            continue
        fresh.append(
            f"<url><loc>{e(surl(s))}</loc><news:news><news:publication>"
            f"<news:name>{e(config.SITE_NAME)}</news:name>"
            f"<news:language>{config.PUBLICATION_LANG}</news:language>"
            f"</news:publication>"
            f"<news:publication_date>{e(s['published'])}</news:publication_date>"
            f"<news:title>{e(s['headline'])}</news:title></news:news></url>")
    with open("site/news-sitemap.xml", "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">\n'
                + "\n".join(fresh) + "\n</urlset>\n")

    # ---- rss.xml (latest 30) ----
    items = []
    for s in all_stories[:30]:
        try:
            pub = format_datetime(datetime.fromisoformat(s["published"]))
        except Exception:
            pub = s.get("published", "")
        items.append(
            f"<item><title>{e(s['headline'])}</title><link>{e(surl(s))}</link>"
            f"<guid isPermaLink=\"true\">{e(surl(s))}</guid>"
            f"<pubDate>{e(pub)}</pubDate>"
            f"<category>{e(s.get('category',''))}</category>"
            f"<description>{e(s.get('lede',''))}</description></item>")
    with open("site/rss.xml", "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
                f"<title>{e(config.SITE_NAME)}</title><link>{e(base)}</link>"
                f"<description>Prediction markets news: regulation, volume, deals and data.</description>"
                f"<language>{config.PUBLICATION_LANG}</language>\n"
                + "\n".join(items) + "\n</channel></rss>\n")

    # ---- robots.txt (search + AI answer crawlers welcome) ----
    ai_bots = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot",
               "Claude-SearchBot", "anthropic-ai", "PerplexityBot",
               "Perplexity-User", "Google-Extended", "CCBot"]
    lines = ["User-agent: *", "Allow: /", ""]
    for b in ai_bots:
        lines += [f"User-agent: {b}", "Allow: /", ""]
    lines += [f"Sitemap: {base}/sitemap.xml", f"Sitemap: {base}/news-sitemap.xml"]
    with open("site/robots.txt", "w") as f:
        f.write("\n".join(lines) + "\n")

    # ---- llms.txt (site guide for AI systems) ----
    recent = "\n".join(f"- [{s['headline']}]({surl(s)}): {s.get('lede','')}"
                       for s in all_stories[:10])
    with open("site/llms.txt", "w") as f:
        f.write(f"""# {config.SITE_NAME}

> Prediction markets news, contextualized. We cluster coverage of each event
> from 90+ outlets, credit the first reporter, and add original analysis of
> why it matters for traders, operators, and regulation watchers. All article
> text is our own original writing; every story links to its primary source.

## Sections
- [Front page]({base}/index.html)
- [Story archive]({base}/all.html): every story, searchable
- [The Docket]({base}/events.html): upcoming court dates, earnings, deadlines
- [RSS feed]({base}/rss.xml)

## Latest stories
{recent}
""")
    print(f"[feeds] sitemap ({len(all_stories)} stories) · news-sitemap ({len(fresh)} fresh) · rss · robots · llms.txt")


def run(dry_run=False):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not dry_run:
        sys.exit("Set ANTHROPIC_API_KEY or use --dry-run")

    path = config.STORIES_PATH
    existing = []
    if os.path.exists(path):
        existing = json.load(open(path)).get("stories", [])

    items = [i for i in ingest() if matches_keywords(i)]
    cutoff = NOW.timestamp() - config.MAX_ITEM_AGE_DAYS * 86400
    def fresh(i):
        try:
            return datetime.fromisoformat(i["published"]).timestamp() > cutoff
        except Exception:
            return True
    items = [i for i in items if fresh(i)]
    print(f"[filter] {len(items)} match keywords & freshness window")

    clusters = cluster(items, existing)
    clusters = clusters[: config.MAX_NEW_PER_RUN]
    print(f"[cluster] {len(clusters)} new story clusters")

    new_stories = []
    taken_slugs = {s.get("slug", "") for s in existing}
    for c in clusters:
        ctx = contextualize(c, api_key, dry_run)
        if not ctx:
            continue
        primary = c[0]  # earliest-published item = first reporter
        also = []
        seen = {primary["url"]}
        for it in c[1:]:
            if it["url"] not in seen:
                also.append({"name": it["source"], "url": it["url"]})
                seen.add(it["url"])
        sid = story_id(primary["url"])
        new_stories.append({
            "id": sid,
            "slug": slugify(ctx["headline"], sid, taken_slugs),
            "headline": ctx["headline"],
            "lede": ctx["lede"],
            "body": ctx.get("body", []),
            "why": ctx["why"],
            "category": ctx["category"],
            "entities": ctx.get("entities", []),
            "primary_source": {"name": primary["source"], "url": primary["url"]},
            "source_title": primary["title"],   # kept for future clustering only
            "also": also,
            "published": primary["published"],
            "first_seen": NOW.isoformat(),
        })
        print(f"  + [{ctx['category']}] {ctx['headline']}")

    all_stories = new_stories + existing
    all_stories.sort(key=lambda s: s.get("published", ""), reverse=True)
    all_stories = all_stories[: config.MAX_STORIES_KEPT]

    # regenerate every story page (picks up new "also covered by" outlets too)
    for s in all_stories:
        render_story_page(s, all_stories)
    print(f"[pages] {len(all_stories)} article pages in {config.STORY_DIR}/")

    write_feeds(all_stories)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(
        {"generated": NOW.isoformat(), "stories": all_stories},
        open(path, "w"), indent=1)
    print(f"[publish] {len(new_stories)} new / {len(all_stories)} total -> {path}")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
