"""
news_feeds.py — single definition of RSS feeds and relevance keywords.
Imported by fetch_news.py AND intraday_check.py.
Add feeds here once; both nodes get them automatically.
"""

RSS_FEEDS = {
    "AI_Tech": [
        "https://feeds.feedburner.com/venturebeat/SWIIX",
        "https://techcrunch.com/feed/",
        "https://huggingface.co/blog/feed.xml",
        "https://openai.com/news/rss.xml",
    ],
    "Macro_Policy": [...],  # full list from fetch_news.py
    "Crypto": ["https://cointelegraph.com/rss", "https://decrypt.co/feed"],
    "Energy": [
        "https://oilprice.com/rss/main",
        "https://www.energymonitor.ai/feed/",
        "https://www.powermag.com/feed/",
        "https://www.utilitydive.com/feeds/news/",
        "https://nuclearenergyinsider.com/feed/",
        # add once here; both fetch_news and intraday_check get it
    ],
    "Cyber": [
        "https://therecord.media/feed",
        "https://krebsonsecurity.com/feed/",
        "https://rekt.news/rss/",
    ],
}

RELEVANCE_KEYWORDS = {
    # canonical list — both nodes import this
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "nvidia", "tsla", "tesla", "vistra", "constellation energy",
    "fed", "fomc", "inflation", "cpi", "rate cut", "rate hike",
    # ... complete list; intraday_check no longer needs its own copy
}

NVDA_MAJOR_KEYWORDS = {
    "nvda", "nvidia", "blackwell", "jensen huang",
    "h100", "h200", "export ban", "earnings",
}