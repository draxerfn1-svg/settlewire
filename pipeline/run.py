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
PROMPT = """You are the editor of a prediction-markets news aggregator. Coverage of one news event is listed below as headlines from various outlets (metadata only).

Write an ORIGINAL story card in strict JSON (no markdown fences, no preamble):
{{
  "headline": "your own headline, max 12 words, punchy, no clickbait",
  "lede": "one original sentence stating what happened, max 30 words",
  "why": "2 sentences of original analysis: why this matters for prediction-market traders, operators, or regulation watchers. Be concrete about second-order effects.",
  "category": "one of: {cats}",
  "entities": ["up to 4 relevant entities e.g. Kalshi, Polymarket, CFTC"]
}}

Rules: write everything in your own words — never copy source phrasing. If the event is not genuinely about prediction markets / event contracts, return exactly {{"skip": true}}.

Coverage:
{coverage}"""


def contextualize(cluster_items, api_key, dry_run=False):
    coverage = "\n".join(f"- [{i['source']}] {i['title']}" for i in cluster_items[:8])
    if dry_run:
        return {
            "headline": cluster_items[0]["title"][:70],
            "lede": "Dry-run placeholder lede.",
            "why": "Dry-run placeholder analysis.",
            "category": "Trading",
            "entities": [],
        }
    body = {
        "model": config.MODEL,
        "max_tokens": 500,
        "messages": [{"role": "user", "content": PROMPT.format(
            cats=", ".join(config.CATEGORIES), coverage=coverage)}],
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


def run(dry_run=False):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not dry_run:
        sys.exit("Set ANTHROPIC_API_KEY or use --dry-run")

    path = config.STORIES_PATH
    existing = []
    if os.path.exists(path):
        existing = json.load(open(path)).get("stories", [])

    items = [i for i in ingest() if matches_keywords(i)]
    print(f"[filter] {len(items)} match keywords")

    clusters = cluster(items, existing)
    clusters = clusters[: config.MAX_NEW_PER_RUN]
    print(f"[cluster] {len(clusters)} new story clusters")

    new_stories = []
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
        new_stories.append({
            "id": story_id(primary["url"]),
            "headline": ctx["headline"],
            "lede": ctx["lede"],
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
    all_stories = all_stories[: config.MAX_STORIES_KEPT]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(
        {"generated": NOW.isoformat(), "stories": all_stories},
        open(path, "w"), indent=1)
    print(f"[publish] {len(new_stories)} new / {len(all_stories)} total -> {path}")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
