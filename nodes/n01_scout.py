# fetch_news.py — Node 01: The Scout v4.0
# Sovereign Intelligence System
# Data sources: CoinGecko (free), Alternative.me (free), RSS feeds (free), yfinance (free)
# Zero API keys required.

import json
import os
import re
import shutil
import tempfile
import feedparser
import requests
import yfinance as yf
import yaml
from datetime import datetime, timedelta

# --- HARDENED PATHS ---
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT, CONTEXT_FILE, CONFIG_PATH, load_config
from core.market_data import enrich_ticker_extended
OUTPUT_DIR = str(VAULT_ROOT / "Output")

HEADERS = {
    "User-Agent": "SovereignScout/1.0 (Intelligence Node; personal research)"
}

CONFIG = load_config()


# ── SIGNAL FEEDS ───────────────────────────────────────────────────────────────

FRESHNESS_CUTOFF_HOURS = 36  # Drop articles older than this before writing to context.json

RSS_FEEDS = {
    "AI_Tech": [
        "https://feeds.feedburner.com/venturebeat/SWIIX",
        "https://techcrunch.com/feed/",
        "https://huggingface.co/blog/feed.xml",
        "https://openai.com/news/rss.xml",
    ],
    "Macro_Policy": [
        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/Reuters/worldNews",
    ],
    "Crypto": [
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
    ],
    "Energy": [
        "https://oilprice.com/rss/main",
        "https://www.energymonitor.ai/feed/",
        "https://www.powermag.com/feed/",
        "https://www.utilitydive.com/feeds/news/",
        "https://nuclearenergyinsider.com/feed/",
        "https://www.spglobal.com/commodityinsights/en/rss-feed/natural-gas",
    ],
    "Cyber": [
        "https://therecord.media/feed",
        "https://krebsonsecurity.com/feed/",
        "https://rekt.news/rss/",
    ]
}

RELEVANCE_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
    "defi", "stablecoin", "exchange", "hack", "exploit", "breach",
    "fed", "federal reserve", "interest rate", "inflation", "cpi",
    "recession", "macro", "gdp", "liquidity", "treasury", "markets",
    "ai", "llm", "model", "gpt", "claude", "gemini", "llama", "mistral",
    "nvidia", "gpu", "inference", "open source", "local model", "release",
    "anthropic", "openai", "deepmind", "hugging face", "artificial intelligence",
    "machine learning", "benchmark", "agent", "robotics", "chip", "semiconductor",
    "micron", "amd", "broadcom", "applied materials", "avgo", "amat",
    "vistra", "constellation energy", "vertiv", "quanta", "nuclear", "smr",
    "small modular reactor", "nuscale", "first solar", "enphase", "nextera",
    "renewable", "solar", "uranium", "data center power", "ai energy",
    "electricity demand", "grid capacity", "power purchase agreement", "ppa",
    "hyperscaler", "nne", "enph", "nee", "fslr", "watt",
    "microsoft", "sony", "take-two", "gta", "gaming", "xbox", "playstation",
    "oil", "energy", "grid", "power", "gas", "pipeline",
    "china", "taiwan", "russia", "ukraine", "opec", "sanctions",
    "geopolit", "conflict", "trade", "tariff",
    "ransomware", "malware", "vulnerability", "zero-day", "attack",
    "infrastructure", "nation-state", "cyber", "hacker", "phishing",
    "data breach", "espionage", "spyware", "botnet", "ddos",
    "security", "stolen", "leaked", "exposed", "compromised",
    "patch", "cve", "intrusion", "incident",
    "earnings", "revenue", "guidance", "beat", "miss", "eps",
    "unemployment", "jobs", "payroll", "labor", "fomc", "rate cut", "rate hike", "tsla", "tesla", "elon musk", "ev market", "gigafactory", 
    "autonomy", "fsd", "robotaxi", "supercharger", "optimus"
}

# NVDA major news keywords — only surface NVDA if one of these appears
NVDA_MAJOR_KEYWORDS = {
    "nvda", "nvidia", "blackwell", "jensen huang", "h100", "h200", "gb200",
    "export ban", "export control", "antitrust", "earnings", "guidance",
    "partnership", "acquisition", "deal", "lawsuit", "revenue", "forecast"
}


# ── MARKET CORE ────────────────────────────────────────────────────────────────

def fetch_crypto_prices() -> dict:
    """CoinGecko — BTC, ETH, SOL with 24h change."""
    print("  📊 Fetching crypto prices via CoinGecko...")
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_market_cap": "true"
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        def fmt_price(coin_id):
            d = data.get(coin_id, {})
            price = d.get("usd", 0)
            change = d.get("usd_24h_change", 0)
            mcap = d.get("usd_market_cap", 0)
            direction = "▲" if change >= 0 else "▼"
            return {
                "price": f"${price:,.2f}",
                "change_24h": f"{direction} {abs(change):.2f}%",
                "change_pct": change,
                "market_cap": f"${mcap / 1e9:.1f}B",
                "display_mode": "price"
            }

        return {
            "BTC": fmt_price("bitcoin"),
            "ETH": fmt_price("ethereum"),
            "SOL": fmt_price("solana"),
            "source": "CoinGecko",
            "status": "live"
        }

    except Exception as e:
        print(f"  ⚠️  CoinGecko failed: {e}")
        return {"status": "unavailable", "error": str(e)}


def fetch_market_core() -> dict:
    """
    Fetch SPY, GLD, DXY via yfinance.
    Applies display_mode from config — 'price' or 'signal' (trend direction only).
    """
    print("  📈 Fetching market core (SPY, TSLA, GLD, DXY)...")
    core_cfg = CONFIG.get("market_core", {})
    tickers = ["SPY", "TSLA", "GLD", "DX-Y.NYB", "TLT", "USO"]
    labels = core_cfg.get("labels", {})
    display_modes = core_cfg.get("display_mode", {})
    spy_threshold = core_cfg.get("spy_flag_threshold", 0.015)

    result = {}

    try:
        df = yf.download(tickers, period="5d", progress=False, auto_adjust=True)["Close"]
        df = df.ffill()

        for ticker in tickers:
            try:
                label = labels.get(ticker, ticker)
                mode = display_modes.get(ticker, "price")

                series = df[ticker] if len(tickers) > 1 else df
                clean = series.dropna()
                prev = float(clean.iloc[-1]) if len(clean) >= 2 else None

                # Pre-market fallback: fast_info delayed quote → prev close
                current = None
                price_label_suffix = ""
                try:
                    raw = clean.iloc[-1] if len(clean) else None
                    if raw is not None and not (raw != raw):  # NaN check
                        current = float(raw)
                except Exception:
                    pass

                if current is None or current == 0:
                    try:
                        fi = yf.Ticker(ticker).fast_info
                        current = float(fi["last_price"])
                        price_label_suffix = " *"
                    except Exception:
                        pass

                if current is None and prev is not None:
                    current = prev
                    price_label_suffix = " (prev)"

                if current is None:
                    raise ValueError("No price available")

                prev = float(clean.iloc[-2]) if len(clean) >= 2 else current
                change_pct = (current - prev) / prev * 100
                direction = "▲" if change_pct >= 0 else "▼"

                if mode == "signal":
                    # Trend signal only — no raw price
                    if abs(change_pct) < 0.3:
                        trend = "neutral"
                    elif change_pct > 0:
                        trend = "rising"
                    else:
                        trend = "falling"

                    result[label] = {
                        "trend": trend,
                        "direction": direction,
                        "change_pct": round(change_pct, 2),
                        "signal": f"{direction} {trend} ({change_pct:+.2f}%)",
                        "display_mode": "signal",
                        "price": round(current, 2)
                    }
                else:
                    # Full price display
                    flagged = abs(change_pct / 100) >= spy_threshold
                    result[label] = {
                        "price": f"${current:,.2f}{price_label_suffix}",
                        "change_24h": f"{direction} {abs(change_pct):.2f}%",
                        "change_pct": round(change_pct, 2),
                        "flagged": flagged,
                        "display_mode": "price"
                    }

            except Exception as e:
                result[labels.get(ticker, ticker)] = {
                    "status": "unavailable",
                    "error": str(e)
                }
                  
        print(f"  ✅ Market core: SPY {result.get('SPY', {}).get('price', 'N/A')} | "
              f"TSLA {result.get('TSLA', {}).get('price', 'N/A')} | "
              f"Gold {result.get('Gold', {}).get('signal', 'N/A')} | "
              f"DXY {result.get('DXY', {}).get('signal', 'N/A')} | "
              f"TLT {result.get('TLT', {}).get('signal', 'N/A')} | "
              f"Oil {result.get('Oil', {}).get('signal', 'N/A')}")

    except Exception as e:
        print(f"  ⚠️  Market core fetch failed: {e}")

    return result


# ── EQUITY GROUPS ──────────────────────────────────────────────────────────────

def fetch_equity_groups() -> dict:
    """
    Fetch all equity groups from config with per-group thresholds.
    NVDA applies noise filter — only flagged at 4%+ or major news.
    Returns structured dict by group with flagged movers.
    """
    equities_cfg = CONFIG.get("equities", {})
    groups = ["semiconductors", "ai_energy_nexus", "high_noise", "gaming", "casual_signal"]

    all_tickers = []
    ticker_to_group = {}
    group_configs = {}

    for group in groups:
        grp = equities_cfg.get(group, {})
        if isinstance(grp, dict) and grp.get("tickers"):
            tickers = [t.upper() for t in grp["tickers"]]
            threshold = grp.get("flag_threshold", 0.02)
            group_configs[group] = {"tickers": tickers, "threshold": threshold}
            for t in tickers:
                all_tickers.append(t)
                ticker_to_group[t] = group

    # Skip biotech if inactive
    biotech = equities_cfg.get("biotech_watch", {})
    if isinstance(biotech, dict) and biotech.get("active") and biotech.get("tickers"):
        tickers = [t.upper() for t in biotech["tickers"]]
        threshold = biotech.get("flag_threshold", 0.03)
        group_configs["biotech_watch"] = {"tickers": tickers, "threshold": threshold}
        for t in tickers:
            all_tickers.append(t)
            ticker_to_group[t] = "biotech_watch"

    if not all_tickers:
        print("  ⚡ Equities: no tickers configured")
        return {}

    print(f"  ⚡ Fetching {len(all_tickers)} equity tickers...")

    # Fetch all at once
    raw_data = {}
    try:
        df = yf.download(all_tickers, period="5d", progress=False, auto_adjust=True)["Close"]
        df = df.ffill()

        for ticker in all_tickers:
            try:
                series = df[ticker] if len(all_tickers) > 1 else df
                current = float(series.dropna().iloc[-1])
                prev = float(series.iloc[-2])
                change_pct = (current - prev) / prev * 100
                direction = "▲" if change_pct >= 0 else "▼"
                raw_data[ticker] = {
                    "price": f"${current:,.2f}",
                    "change_24h": f"{direction} {abs(change_pct):.2f}%",
                    "change_pct": round(change_pct, 2),
                    "status": "live"
                }
            except Exception as e:
                raw_data[ticker] = {"status": "unavailable", "error": str(e)}

    except Exception as e:
        print(f"  ⚠️  Equity fetch failed: {e}")
        return {}

    # Structure by group with flagging logic
    result = {}
    for group, gcfg in group_configs.items():
        group_result = {}
        threshold = gcfg["threshold"]

        for ticker in gcfg["tickers"]:
            data = raw_data.get(ticker, {"status": "unavailable"})
            if data.get("status") == "unavailable":
                group_result[ticker] = data
                continue

            change_pct = data.get("change_pct", 0)
            flagged = abs(change_pct / 100) >= threshold

            # NVDA noise filter — only flag at threshold (4%), mark for news check
            if group == "high_noise":
                data["nvda_news_filter"] = True
                data["flag_threshold"] = threshold

            data["flagged"] = flagged
            group_result[ticker] = data

        result[group] = group_result

    # Summary log
    flagged_summary = []
    for group, stocks in result.items():
        for ticker, data in stocks.items():
            if data.get("flagged"):
                flagged_summary.append(f"{ticker} {data.get('change_24h', '')}")

    if flagged_summary:
        print(f"  🚨 Flagged movers: {' | '.join(flagged_summary)}")
    else:
        print("  ✅ Equities: no significant movers today")

    return result


# ── SECTOR ETFs ────────────────────────────────────────────────────────────────

def fetch_sector_etfs() -> dict:
    """
    Fetch sector ETF performance. Flag sectors moving >1.5%.
    Detect divergence vs SPY — flags rotation signals.
    """
    sectors_cfg = CONFIG.get("sectors", {})
    etf_map = sectors_cfg.get("etfs", {
        "XLK": "Technology", "SOXX": "Semiconductors",
        "XLE": "Energy", "XLU": "Utilities",
        "XLF": "Financials", "XBI": "Biotech"
    })
    flag_threshold = sectors_cfg.get("flag_threshold", 0.015)
    divergence_threshold = sectors_cfg.get("divergence_threshold", 0.02)

    tickers = list(etf_map.keys()) + ["SPY"]
    print(f"  🏭 Fetching sector ETFs: {', '.join(etf_map.keys())}...")

    result = {}
    spy_change = 0.0

    try:
        df = yf.download(tickers, period="5d", progress=False, auto_adjust=True)["Close"]
        df = df.ffill()

        # Get SPY baseline first
        try:
            spy_series = df["SPY"]
            spy_current = float(spy_series.dropna().iloc[-1])
            spy_prev = float(spy_series.iloc[-2])
            spy_change = (spy_current - spy_prev) / spy_prev * 100
        except Exception:
            spy_change = 0.0

        for ticker, sector_name in etf_map.items():
            try:
                series = df[ticker]
                current = float(series.dropna().iloc[-1])
                prev = float(series.iloc[-2])
                change_pct = (current - prev) / prev * 100
                direction = "▲" if change_pct >= 0 else "▼"

                flagged = abs(change_pct / 100) >= flag_threshold
                divergence = abs((change_pct - spy_change) / 100) >= divergence_threshold
                outperforming = change_pct > spy_change + (divergence_threshold * 100)
                underperforming = change_pct < spy_change - (divergence_threshold * 100)

                signal = ""
                if outperforming:
                    signal = f"outperforming SPY by {change_pct - spy_change:+.1f}% — rotation into {sector_name.lower()}"
                elif underperforming:
                    signal = f"underperforming SPY by {change_pct - spy_change:.1f}% — {sector_name.lower()} under pressure"

                result[ticker] = {
                    "sector": sector_name,
                    "change_24h": f"{direction} {abs(change_pct):.2f}%",
                    "change_pct": round(change_pct, 2),
                    "flagged": flagged,
                    "divergence": divergence,
                    "outperforming": outperforming,
                    "underperforming": underperforming,
                    "signal": signal,
                    "status": "live"
                }

            except Exception as e:
                result[ticker] = {"sector": sector_name, "status": "unavailable", "error": str(e)}

        # Summary
        alerts = [f"{t} {v['change_24h']}" for t, v in result.items() if v.get("flagged")]
        divergences = [v["signal"] for v in result.values() if v.get("signal")]

        if alerts:
            print(f"  🚨 Sector alerts: {' | '.join(alerts)}")
        if divergences:
            print(f"  🔄 Divergence: {divergences[0]}")
        if not alerts and not divergences:
            print("  ✅ Sectors: no significant moves")

    except Exception as e:
        print(f"  ⚠️  Sector ETF fetch failed: {e}")

    return {"etfs": result, "spy_change_pct": round(spy_change, 2)}


# ── EARNINGS CALENDAR ──────────────────────────────────────────────────────────

def fetch_earnings_calendar() -> dict:
    """
    Check upcoming earnings for all tracked tickers within lookahead window.
    Uses yfinance calendar data. Flags day-of earnings separately.
    """
    earnings_cfg = CONFIG.get("earnings", {})
    lookahead = earnings_cfg.get("lookahead_days", 7)
    day_of_alert = earnings_cfg.get("day_of_alert", True)

    # Collect all tracked tickers
    equities_cfg = CONFIG.get("equities", {})
    all_tickers = []
    for group in ["semiconductors", "ai_energy_nexus", "high_noise", "gaming", "casual_signal"]:
        grp = equities_cfg.get(group, {})
        if isinstance(grp, dict):
            all_tickers.extend([t.upper() for t in grp.get("tickers", [])])

    print(f"  📅 Checking earnings calendar for {len(all_tickers)} tickers...")

    today = datetime.now().date()
    lookahead_date = today + timedelta(days=lookahead)

    upcoming = []
    day_of = []

    for ticker in all_tickers:
        try:
            stock = yf.Ticker(ticker)
            cal = stock.calendar
            if cal is None or cal.empty:
                continue

            # Earnings date can be a range or single date
            earnings_dates = []
            if "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"]
                if hasattr(val, '__iter__'):
                    earnings_dates = [v.date() if hasattr(v, 'date') else v for v in val]
                else:
                    earnings_dates = [val.date() if hasattr(val, 'date') else val]

            for edate in earnings_dates:
                if today <= edate <= lookahead_date:
                    days_away = (edate - today).days
                    entry = {
                        "ticker": ticker,
                        "date": edate.strftime("%Y-%m-%d"),
                        "days_away": days_away
                    }
                    if days_away == 0 and day_of_alert:
                        day_of.append(entry)
                    else:
                        upcoming.append(entry)

        except Exception:
            continue  # Silent — most tickers won't have calendar data

    # Sort by days away
    upcoming.sort(key=lambda x: x["days_away"])

    if day_of:
        print(f"  🔔 Earnings TODAY: {', '.join(e['ticker'] for e in day_of)}")
    if upcoming:
        upcoming_str = ", ".join(e["ticker"] + " in " + str(e["days_away"]) + "d" for e in upcoming)
        print(f"  📅 Upcoming earnings: {upcoming_str}")
    if not day_of and not upcoming:
        print("  ✅ Earnings: none in window")

    return {
        "day_of": day_of,
        "upcoming": upcoming,
        "lookahead_days": lookahead,
        "status": "live"
    }


# ── MACRO REGIME ───────────────────────────────────────────────────────────────

def fetch_macro_regime() -> dict:
    """
    Fetch macro regime signals from news RSS.
    Extracts Fed, inflation, and labor market signals from recent headlines.
    Falls back to manual_override from config if fetch fails.
    """
    macro_cfg = CONFIG.get("macro_regime", {})
    manual = macro_cfg.get("manual_override", {
        "fed": "HOLD", "inflation": "cooling", "labor": "tight"
    })

    print("  🏛️  Fetching macro regime signals...")

    fed_keywords = {
        "rate cut": "CUT", "cuts rates": "CUT", "rate hike": "HIKE",
        "hikes rates": "HIKE", "rate pause": "HOLD", "holds rates": "HOLD",
        "hold steady": "HOLD", "unchanged": "HOLD", "fomc": None,
        "federal reserve": None, "powell": None
    }
    inflation_keywords = {
        "inflation rises": "rising", "inflation surges": "rising",
        "cpi rises": "rising", "cpi hot": "rising",
        "inflation falls": "cooling", "inflation cools": "cooling",
        "cpi falls": "cooling", "deflation": "cooling",
        "inflation steady": "stable", "inflation stable": "stable"
    }
    labor_keywords = {
        "jobs added": "strong", "payrolls beat": "strong", "unemployment falls": "strong",
        "layoffs": "softening", "job cuts": "softening", "unemployment rises": "softening",
        "labor market cools": "softening", "hiring slows": "softening"
    }

    headlines = []
    sources = macro_cfg.get("sources", [
        "https://feeds.reuters.com/reuters/businessNews"
    ])

    for feed_url in sources:
        try:
            feed = feedparser.parse(
                feed_url,
                agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            )
            for entry in feed.entries[:15]:
                title = entry.get("title", "").lower().strip()
                if title:
                    headlines.append(title)
        except Exception:
            continue

    # Extract signals
    fed_signal = manual.get("fed", "HOLD")
    inflation_signal = manual.get("inflation", "cooling")
    labor_signal = manual.get("labor", "tight")
    fed_headline = ""
    source_used = "manual_override"

    if headlines:
        for headline in headlines:
            for kw, signal in fed_keywords.items():
                if kw in headline and signal:
                    fed_signal = signal
                    fed_headline = headline[:80]
                    source_used = "news_fetch"
                    break
            for kw, signal in inflation_keywords.items():
                if kw in headline and signal:
                    inflation_signal = signal
                    source_used = "news_fetch"
                    break
            for kw, signal in labor_keywords.items():
                if kw in headline and signal:
                    labor_signal = signal
                    source_used = "news_fetch"
                    break

    display_fmt = macro_cfg.get(
        "display_format",
        "Fed: {fed} | Inflation: {inflation} | Labor: {labor}"
    )
    display = display_fmt.format(
        fed=fed_signal,
        inflation=inflation_signal,
        labor=labor_signal
    )

    print(f"  ✅ Macro regime: {display} [{source_used}]")

    return {
        "fed": fed_signal,
        "inflation": inflation_signal,
        "labor": labor_signal,
        "display": display,
        "fed_headline": fed_headline,
        "source": source_used,
        "status": "live"
    }


# ── FEAR & GREED ───────────────────────────────────────────────────────────────

def fetch_fear_greed() -> dict:
    """Alternative.me Fear & Greed Index."""
    print("  😱 Fetching Fear & Greed Index...")
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=2",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        entries = r.json().get("data", [])
        current = entries[0] if entries else {}
        previous = entries[1] if len(entries) > 1 else {}

        current_val = int(current.get("value", 0))
        prev_val = int(previous.get("value", 0))
        delta = current_val - prev_val
        delta_str = f"+{delta}" if delta >= 0 else str(delta)

        return {
            "value": current_val,
            "classification": current.get("value_classification", "Unknown"),
            "delta_24h": delta_str,
            "reading": f"{current_val} ({current.get('value_classification', '?')}) | Δ24h: {delta_str}",
            "source": "Alternative.me",
            "status": "live"
        }
    except Exception as e:
        print(f"  ⚠️  Fear & Greed fetch failed: {e}")
        return {"status": "unavailable", "error": str(e)}


# ── GLOBAL MARKET META ─────────────────────────────────────────────────────────

def fetch_global_market_meta() -> dict:
    """CoinGecko global crypto market data."""
    print("  🌐 Fetching global crypto market meta...")
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            headers=HEADERS, timeout=15
        )
        r.raise_for_status()
        d = r.json().get("data", {})

        total_mcap = d.get("total_market_cap", {}).get("usd", 0)
        total_vol = d.get("total_volume", {}).get("usd", 0)
        btc_dom = d.get("market_cap_percentage", {}).get("btc", 0)
        eth_dom = d.get("market_cap_percentage", {}).get("eth", 0)
        change = d.get("market_cap_change_percentage_24h_usd", 0)

        return {
            "total_market_cap": f"${total_mcap / 1e12:.2f}T",
            "total_volume_24h": f"${total_vol / 1e9:.1f}B",
            "btc_dominance": f"{btc_dom:.1f}%",
            "eth_dominance": f"{eth_dom:.1f}%",
            "market_cap_change_24h": f"{change:+.2f}%",
            "status": "live"
        }
    except Exception as e:
        print(f"  ⚠️  Global market meta failed: {e}")
        return {"status": "unavailable", "error": str(e)}


# ── SIGNAL FEEDS ───────────────────────────────────────────────────────────────

def fetch_signals(max_per_category: int = 3) -> dict:
    """Harvest RSS feeds with keyword relevance filter."""
    print("  📡 Harvesting signal feeds...")
    signals = {}

    for category, feeds in RSS_FEEDS.items():
        category_signals = []
        for feed_url in feeds:
            try:
                feed = feedparser.parse(
                    feed_url,
                    agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36'
                )
                for entry in feed.entries[:10]:
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "")
                    published = entry.get("published", "")[:16] if entry.get("published") else ""

                    if not title:
                        continue

                    # ── Freshness gate ──────────────────────────────────────
                    _pp = entry.get("published_parsed")
                    if _pp:
                        try:
                            _pub_dt = datetime(*_pp[:6])   # UTC struct_time → naive UTC
                            _age_h = (datetime.utcnow() - _pub_dt).total_seconds() / 3600
                            if _age_h > FRESHNESS_CUTOFF_HOURS:
                                continue
                        except Exception:
                            pass  # Unparseable date — let it through

                    title_lower = title.lower()

                    # NVDA noise filter — skip low-signal NVDA mentions
                    if "nvidia" in title_lower or "nvda" in title_lower:
                        if not any(kw in title_lower for kw in NVDA_MAJOR_KEYWORDS - {"nvda", "nvidia"}):
                            continue

                    if not any(kw in title_lower for kw in RELEVANCE_KEYWORDS):
                        continue

                    category_signals.append({
                        "headline": title,
                        "source": feed.feed.get("title", feed_url),
                        "published": published,
                        "url": link
                    })
            except Exception as e:
                print(f"    ⚠️  Feed failed ({feed_url[:40]}...): {e}")
                continue

        seen = set()
        deduped = []
        for item in category_signals:
            if item["headline"] not in seen:
                seen.add(item["headline"])
                deduped.append(item)

        signals[category] = deduped[:max_per_category * 2]
        _surviving = len(signals[category])
        if _surviving < 2:
            print(f"  ⚠️  WARNING: [{category}] only {_surviving} fresh article(s) after freshness gate — possible dead or stale feed")

    return signals


def extract_flat_signals(signals: dict) -> list:
    """Flatten structured signals into a list for prompt injection."""
    flat = []
    for category, items in signals.items():
        for item in items:
            label = category.replace("_", "/")
            flat.append(f"[{label}] {item['headline']} — {item['source']}")
    return flat


# ── LORE ANCHOR ────────────────────────────────────────────────────────────────

def build_lore_anchor(fear_greed: dict, crypto: dict) -> str:
    """
    Maps current market conditions to active universe lore context.
    Reads from active universe lore state JSON — no hardcoding.
    """
    active = CONFIG.get("active_universe", "The-Lost-Net")
    _path_overrides = {
        "The-Vigil": "~/sovereign/Data/vigil/vigil_state.json",
    }
    lore_path = os.path.expanduser(
        _path_overrides.get(active, f"~/sovereign/Data/lore_state_{active.lower().replace('-', '')}.json")
    )

    try:
        with open(lore_path, "r") as f:
            lore = json.load(f)
    except Exception as e:
        print(f"  ⚠️  Lore state load failed: {e}")
        return "Lore anchor unavailable."

    anchor_map = lore.get("lore_anchor_map", {})
    labels = lore.get("lore_anchor_labels", {
        "state_label": "World State",
        "momentum_label": "Signal momentum"
    })

    fg_value = fear_greed.get("value", 50)
    world_state, field_dynamic = "", ""

    for band, content in anchor_map.items():
        lo, hi = map(int, band.split("-"))
        if lo <= fg_value <= hi:
            world_state = content["world_state"]
            field_dynamic = content["field_dynamic"]
            break

    btc_change = crypto.get("BTC", {}).get("change_24h", "")
    momentum = "advancing" if "▲" in btc_change else "retreating"
    state_label = labels["state_label"]
    momentum_label = labels["momentum_label"]

    return (
        f"{state_label}: {world_state} | "
        f"Field Dynamic: {field_dynamic} | "
        f"BTC {momentum} — {momentum_label} {momentum}."
    )


# ── ATOMIC WRITE ───────────────────────────────────────────────────────────────

def atomic_write_json(data: dict, target_path: str) -> None:
    """Write JSON atomically to prevent iCloud partial sync reads."""
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode='w', dir=target_dir, suffix='.tmp',
        delete=False, encoding='utf-8'
    ) as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp_path = tmp.name

    shutil.move(tmp_path, target_path)


# ── MAIN ───────────────────────────────────────────────────────────────────────


def fetch_catalyst_calendar() -> dict:
    """
    Upcoming scheduled macro catalysts — FOMC decisions and CPI releases.
    Hardcoded 2026 schedule. Verify annually:
      FOMC: federalreserve.gov/monetarypolicy/fomccalendars.htm
      CPI:  bls.gov/schedule/news_release/cpi.htm
    Returns events within a 14-day lookahead window.
    """
    from datetime import date, timedelta

    # FOMC decision days 2026 (day after 2-day meeting)
    FOMC_DATES = [
        "2026-01-28", "2026-03-18", "2026-04-29",
        "2026-06-10", "2026-07-29", "2026-09-16",
        "2026-10-28", "2026-12-09",
    ]

    # CPI release dates 2026 (BLS schedule — 8:30am ET)
    CPI_DATES = [
        "2026-01-15", "2026-02-12", "2026-03-12", "2026-04-10",
        "2026-05-13", "2026-06-11", "2026-07-15", "2026-08-13",
        "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-11",
    ]

    LOOKAHEAD_DAYS = 14
    today = date.today()
    lookahead = today + timedelta(days=LOOKAHEAD_DAYS)

    upcoming = []

    for d_str in FOMC_DATES:
        d = date.fromisoformat(d_str)
        if today <= d <= lookahead:
            days_away = (d - today).days
            upcoming.append({"event": "FOMC Decision", "date": d_str, "days_away": days_away, "type": "fomc"})

    for d_str in CPI_DATES:
        d = date.fromisoformat(d_str)
        if today <= d <= lookahead:
            days_away = (d - today).days
            upcoming.append({"event": "CPI Release", "date": d_str, "days_away": days_away, "type": "cpi"})

    upcoming.sort(key=lambda x: x["days_away"])

    if upcoming:
        events_str = ", ".join(
            f"{e['event']} in {e['days_away']}d" if e['days_away'] > 0
            else f"{e['event']} TODAY"
            for e in upcoming
        )
        print(f"  🗓️  Catalysts: {events_str}")
    else:
        print(f"  ✅ Catalysts: none in {LOOKAHEAD_DAYS}-day window")

    return {"upcoming": upcoming, "lookahead_days": LOOKAHEAD_DAYS}


def fetch_truflation():
    """CPI YoY from FRED (CPIAUCSL). Monthly BLS — authoritative market signal."""
    import urllib.request as _req, json as _json, yaml as _yaml, os as _os
    try:
        cfg_path = _os.path.join(_os.path.dirname(__file__), '..', 'config.yaml')
        with open(cfg_path, 'r') as f:
            cfg = _yaml.safe_load(f)
        api_key = cfg.get('fred_api_key', '')
        if not api_key:
            return {"rate": None, "label": None, "status": "no FRED key in config.yaml"}
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=CPIAUCSL&api_key={api_key}&file_type=json"
            f"&sort_order=desc&limit=13"
        )
        with _req.urlopen(url, timeout=8) as r:
            data = _json.loads(r.read().decode())
        obs = [o for o in data.get("observations", []) if o.get("value") != "."]
        if len(obs) < 2:
            return {"rate": None, "label": None, "status": "insufficient FRED data"}
        latest = float(obs[0]["value"])
        year_ago = float(obs[-1]["value"])
        yoy = round((latest - year_ago) / year_ago * 100, 2)
        period = obs[0].get("date", "unknown")
        return {"rate": yoy, "label": f"CPI {yoy}% YoY ({period})", "status": "live"}
    except Exception as e:
        return {"rate": None, "label": None, "status": f"unavailable: {e}"}

def fetch_extended_enrichment() -> dict:
    """
    P0: Extended enrichment for all active tickers.
    Runs after market_core so SPY change context is available.
    """
    ENRICH_TICKERS = ["BTC", "SOL", "ETH", "TSLA", "NVDA", "VST", "CEG", "VRT"]
    # Compute SPY daily change for RS calculation
    spy_change = 0.0
    try:
        import yfinance as yf
        spy_hist = yf.Ticker("SPY").history(period="5d", interval="1d")
        if len(spy_hist) >= 2:
            s_prev = float(spy_hist["Close"].iloc[-2])
            s_curr = float(spy_hist["Close"].iloc[-1])
            if s_prev > 0:
                spy_change = (s_curr - s_prev) / s_prev * 100
    except Exception:
        pass

    print(f"  🔬 Extended enrichment for {len(ENRICH_TICKERS)} tickers...")
    enriched = {}
    for ticker in ENRICH_TICKERS:
        try:
            enriched[ticker] = enrich_ticker_extended(ticker, spy_change_pct=spy_change)
        except Exception as e:
            print(f"    ⚠️  enrich({ticker}): {e}")
            enriched[ticker] = {}
    return enriched


def fetch_calendar_alerts() -> list:
    """
    P4: Read weekly_calendar from config.yaml, return events within 72-hour window.
    Owner maintains config.weekly_calendar manually each Sunday.
    """
    from datetime import datetime as _dt, timedelta
    calendar = CONFIG.get("weekly_calendar", [])
    if not calendar:
        return []
    now     = _dt.now()
    cutoff  = now + timedelta(hours=72)
    alerts  = []
    for event in calendar:
        try:
            time_str = str(event.get("time", "00:00")).replace(" ET", "").strip()
            event_dt = _dt.strptime(f"{event['date']} {time_str}", "%Y-%m-%d %H:%M")
            if now <= event_dt <= cutoff:
                alerts.append(event)
        except Exception:
            continue
    if alerts:
        print(f"  🗓️  Calendar alerts (72h): {[e.get('name','?') for e in alerts]}")
    else:
        print("  ✅ Calendar: no events in 72h window")
    return alerts



def fetch_intraday_data() -> dict:
    """
    Session B: 1H intraday layer for active trading tickers + SPY reference.
    Computes VWAP, ATR14, PDH/PDL, and 20-bar S/R per ticker.
    Stored in context.json under market.intraday.
    """
    from datetime import date as _date
    INTRADAY_TICKERS = [
        "BTC-USD", "ETH-USD", "SOL-USD",
        "TSLA", "NVDA", "VST", "CEG", "VRT", "WATT", "SPY"
    ]
    print(f"  📊 Fetching 1H intraday data for {len(INTRADAY_TICKERS)} tickers...")
    result = {}

    try:
        df_raw = yf.download(
            INTRADAY_TICKERS, interval="1h", period="5d",
            progress=False, auto_adjust=True
        )
        today_str = _date.today().isoformat()

        for ticker in INTRADAY_TICKERS:
            try:
                close  = df_raw["Close"][ticker].dropna()
                high   = df_raw["High"][ticker].dropna()
                low    = df_raw["Low"][ticker].dropna()
                volume = df_raw["Volume"][ticker].dropna()

                if len(close) < 2:
                    result[ticker] = {"status": "insufficient_data"}
                    continue

                # --- VWAP: today's bars only ---
                today_mask = close.index.date == _date.today()
                if today_mask.sum() >= 1:
                    t_close  = close[today_mask]
                    t_high   = high[today_mask]
                    t_low    = low[today_mask]
                    t_vol    = volume[today_mask]
                    typical  = (t_high + t_low + t_close) / 3
                    vwap = float((typical * t_vol).sum() / t_vol.sum()) if t_vol.sum() > 0 else None
                else:
                    vwap = None

                # --- ATR14: last 14 1H bars ---
                tr_bars = min(15, len(close))
                h14 = high.iloc[-tr_bars:]
                l14 = low.iloc[-tr_bars:]
                c14 = close.iloc[-tr_bars:]
                prev_close = c14.shift(1)
                tr = (h14 - l14).combine(
                    (h14 - prev_close).abs(), max
                ).combine(
                    (l14 - prev_close).abs(), max
                )
                atr14 = float(tr.dropna().tail(14).mean()) if len(tr.dropna()) >= 1 else None

                # --- PDH / PDL: all bars from yesterday ---
                import datetime as _dt
                yesterday = (_date.today() - _dt.timedelta(days=1))
                yday_mask = close.index.date == yesterday
                if yday_mask.sum() >= 1:
                    pdh = float(high[yday_mask].max())
                    pdl = float(low[yday_mask].min())
                else:
                    pdh = pdl = None

                # --- S/R: 20-bar rolling high/low ---
                sr_high = float(high.iloc[-20:].max())
                sr_low  = float(low.iloc[-20:].min())

                result[ticker] = {
                    "vwap":    round(vwap,    2) if vwap    is not None else None,
                    "atr14":   round(atr14,   2) if atr14   is not None else None,
                    "pdh":     round(pdh,     2) if pdh     is not None else None,
                    "pdl":     round(pdl,     2) if pdl     is not None else None,
                    "sr_high": round(sr_high, 2),
                    "sr_low":  round(sr_low,  2),
                    "status":  "live"
                }

            except Exception as e:
                result[ticker] = {"status": "unavailable", "error": str(e)}

    except Exception as e:
        print(f"  ⚠️  Intraday fetch failed: {e}")
        return {}

    live_count = sum(1 for v in result.values() if v.get("status") == "live")
    print(f"  ✅ Intraday: {live_count}/{len(INTRADAY_TICKERS)} tickers live")
    return result

def main():
    start = datetime.now()
    print(f"\n⚡ [NODE: SCOUT v4.0] Harvest cycle initiated — {start.strftime('%Y-%m-%d %H:%M')}")
    print("─" * 60)

    # Fetch all data
    crypto         = fetch_crypto_prices()
    market_core    = fetch_market_core()
    equities       = fetch_equity_groups()
    sectors        = fetch_sector_etfs()
    earnings       = fetch_earnings_calendar()
    catalysts      = fetch_catalyst_calendar()
    macro_regime   = fetch_macro_regime()
    fear_greed     = fetch_fear_greed()
    truflation     = fetch_truflation()
    global_meta    = fetch_global_market_meta()
    signals        = fetch_signals(max_per_category=3)
    flat_signals   = extract_flat_signals(signals)
    lore_anchor    = build_lore_anchor(fear_greed, crypto)
    enriched        = fetch_extended_enrichment()
    calendar_alerts = fetch_calendar_alerts()
    intraday       = fetch_intraday_data()

    # Assemble context packet
    context = {
        "timestamp": start.strftime("%Y-%m-%d %H:%M"),
        "harvest_duration_s": None,
        "market": {
            "crypto": crypto,
            "core": market_core,
            "fear_greed": fear_greed,
            "global_meta": global_meta,
            "macro_regime": macro_regime,
            "truflation": truflation,
            "intraday": intraday,
        },
        "equities": equities,
        "sectors": sectors,
        "earnings": earnings,
        "catalysts": catalysts,
        "signals": {
            "structured": signals,
            "flat": flat_signals
        },
        "lore_anchor": lore_anchor,
        "enriched": enriched,
        "calendar_alerts": calendar_alerts,
        "system_meta": {
            "node": "Scout-01",
            "version": "4.0",
            "next_node": "Archivist-02 (chronicle.py)"
        }
    }

    # Record harvest duration
    duration = (datetime.now() - start).total_seconds()
    context["harvest_duration_s"] = round(duration, 2)

    # Atomic write
    try:
        atomic_write_json(context, CONTEXT_FILE)
        print("─" * 60)
        print(f"✅ [SCOUT] Context packet synced → {CONTEXT_FILE}")
        print(f"   BTC:        {crypto.get('BTC', {}).get('price', 'N/A')} "
              f"({crypto.get('BTC', {}).get('change_24h', '?')})")
        print(f"   SPY:        {market_core.get('SPY', {}).get('price', 'N/A')}")
        print(f"   Gold:       {market_core.get('Gold', {}).get('signal', 'N/A')}")
        print(f"   DXY:        {market_core.get('DXY', {}).get('signal', 'N/A')}")
        print(f"   Macro:      {macro_regime.get('display', 'N/A')}")
        print(f"   Fear/Greed: {fear_greed.get('reading', 'N/A')}")
        print(f"   Signals:    {len(flat_signals)} harvested")
        print(f"   Earnings:   {len(earnings.get('day_of', []))} today | "
              f"{len(earnings.get('upcoming', []))} in {earnings.get('lookahead_days', 7)}d window")
        print(f"   Catalysts:  {len(catalysts.get('upcoming', []))} in "
              f"{catalysts.get('lookahead_days', 14)}d window")
        print(f"   Duration:   {duration:.1f}s")
        print("─" * 60)
        print("🔁 [SCOUT → STRATEGIST] Context ready. Run daily_ideas.py.\n")

    except Exception as e:
        print(f"❌ [SCOUT] Atomic write failed: {e}")
        emergency = os.path.expanduser(
            f"~/Desktop/EMERGENCY_context_{start.strftime('%Y%m%d_%H%M')}.json"
        )
        with open(emergency, "w") as f:
            json.dump(context, f, indent=2)
        print(f"💾 [EMERGENCY] Context dumped to Desktop: {emergency}")


if __name__ == "__main__":
    main()
