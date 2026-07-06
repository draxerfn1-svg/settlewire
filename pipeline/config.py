"""
SettleWire pipeline config.
Edit KEYWORDS and FEEDS to tune coverage — no code changes needed.
"""

# ------------------------------------------------------------------
# Keywords. An article must match at least one to enter the pipeline.
# Matched case-insensitively against title + summary.
# ------------------------------------------------------------------
KEYWORDS = [
    "prediction market", "prediction markets",
    "event contract", "event contracts",
    "kalshi", "polymarket",
    "cftc",
    "sports event contracts", "election betting", "election odds",
    "binary options exchange",
    "crypto.com sports", "robinhood event",
    "forecastex", "railbird exchange",
]

# Keywords that must ALSO be near-market context to count
# (avoids e.g. generic "CFTC fines bank" forex stories).
CONTEXT_REQUIRED = {"cftc"}
CONTEXT_TERMS = [
    "prediction", "event contract", "kalshi", "polymarket",
    "sports contract", "election", "wager", "betting", "odds",
]

# ------------------------------------------------------------------
# Feeds. Two kinds:
#   - Google News RSS queries (free, no key, covers the whole Tier 1-6
#     source census from the teardown in one shot)
#   - Direct RSS from high-signal outlets for speed/reliability
# ------------------------------------------------------------------
GOOGLE_NEWS_QUERIES = [
    "prediction markets",
    "Kalshi",
    "Polymarket",
    "CFTC event contracts",
    "sports event contracts",
]

DIRECT_FEEDS = [
    # Crypto tier
    ("CoinDesk",       "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph",  "https://cointelegraph.com/rss"),
    ("The Block",      "https://www.theblock.co/rss.xml"),
    ("Decrypt",        "https://decrypt.co/feed"),
    # Finance tier
    ("CNBC Markets",   "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("Benzinga",       "https://www.benzinga.com/feed"),
    # Gaming trade tier
    ("SBC News",       "https://sbcnews.co.uk/feed/"),
    ("iGaming Business","https://igamingbusiness.com/feed/"),
]

def google_news_url(query: str) -> str:
    from urllib.parse import quote
    return f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"

# ------------------------------------------------------------------
# Categories the LLM may assign (must match index.html tag classes)
# ------------------------------------------------------------------
CATEGORIES = ["Trading", "Legal", "Deals", "Data", "Tech", "Global", "Opinion", "Stocks"]

# ------------------------------------------------------------------
# Pipeline behavior
# ------------------------------------------------------------------
MAX_STORIES_KEPT   = 120     # stories.json rolling window
MAX_NEW_PER_RUN    = 12      # cap LLM calls per hourly run (cost control)
SIMILARITY_CUTOFF  = 0.62    # title similarity above this = same story cluster
MODEL              = "claude-sonnet-4-6"
STORIES_PATH       = "site/stories.json"
