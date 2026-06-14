"""
intraday_check.py — Sovereign Intelligence System
Node: Intraday (on-demand) v2.3

Changes from v2.2:
- Plays status: loads today's Plays_*.json sidecar on startup
- Per-ticker status: TRIGGERED / PENDING / INVALIDATED vs generation price
- Macro shift detection: compares current pulse to context.json morning snapshot
- Synthesis extended: go/no-go paragraph + new intraday setups (same model call)
- num_predict 400 -> 700 to accommodate extended synthesis

Sections: PULSE | PLAYS STATUS | SECTOR PULSE | SCAN | SYNTHESIS
Output: Intraday_YYYY-MM-DD_HHMM.md + .html
"""

import os
import json
import re
import shutil
import subprocess
import feedparser
import requests
import yfinance as yf
import yaml
import sys
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from core.config import VAULT_ROOT, load_config
from core.llm import query_ollama
from core.style import SOVEREIGN_CSS

CONFIG     = load_config()
STATE_PATH = VAULT_ROOT / "Output" / "intraday_state.json"
OUTPUT_DIR = VAULT_ROOT / "02-Market-Intel" / "Intraday"
BRIEFS_DIR = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"

FAST_MODEL   = CONFIG.get("ollama_model", "gemma3:12b")
HEADERS      = {"User-Agent": "Mozilla/5.0 (compatible; SovereignBot/2.3)"}
CHECK_LABELS = CONFIG.get("section_names", {}).get("check", {
    "pulse": "PULSE", "sector": "SECTOR PULSE",
    "scan": "SCAN", "synthesis": "SYNTHESIS"
})



# ── RSS Feeds ─────────────────────────────────────────────────────────────────

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
    "Crypto":  ["https://cointelegraph.com/rss", "https://decrypt.co/feed"],
    "Energy":  ["https://oilprice.com/rss/main", "https://www.energymonitor.ai/feed/"],
    "Cyber":   ["https://therecord.media/feed", "https://krebsonsecurity.com/feed/"],
}

RELEVANCE_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
    "defi", "stablecoin", "exchange", "hack", "exploit", "breach",
    "fed", "fomc", "inflation", "cpi", "rate", "macro", "recession", "treasury",
    "nvidia", "ai", "llm", "model", "openai", "anthropic", "gemini", "gpu",
    "micron", "amd", "broadcom", "semiconductor", "chip",
    "vistra", "constellation", "vertiv", "nuclear", "grid", "power",
    "microsoft", "sony", "take-two", "gta", "gaming",
    "energy", "oil", "tariff", "sanction", "geopolitical", "war",
    "china", "russia", "taiwan", "sec", "regulation", "earnings",
    "unemployment", "jobs", "payroll", "labor",
    "tsla", "tesla", "elon musk", "ev market", "gigafactory",
    "autonomy", "fsd", "robotaxi", "supercharger", "optimus",
}

NVDA_MAJOR_KEYWORDS = {
    "blackwell", "jensen huang", "h100", "h200", "gb200",
    "export ban", "export control", "antitrust", "earnings",
    "guidance", "partnership", "acquisition", "revenue", "forecast"
}

TSLA_MAJOR_KEYWORDS = {
    "elon musk", "fsd", "robotaxi", "delivery numbers", "v12",
    "gigafactory", "supercharger network", "margins", "earnings",
    "guidance", "master plan", "energy storage", "megapack"
}


HEADLINE_TICKERS = {
    "BTC":  {"bitcoin", "btc", "crypto clarity act", "coinbase"},
    "ETH":  {"ethereum", "eth"},
    "SOL":  {"solana", "sol"},
    "NVDA": {"nvidia", "blackwell", "jensen huang", "h100", "h200", "gb200"},
    "TSLA": {"tesla", "tsla", "elon musk", "fsd", "robotaxi", "optimus"},
    "VST":  {"vistra"},
    "CEG":  {"constellation energy"},
    "VRT":  {"vertiv"},
    "WATT": {"watt"},
}

def tag_headline(title: str) -> list:
    lower = title.lower()
    return [ticker for ticker, kws in HEADLINE_TICKERS.items() if any(kw in lower for kw in kws)]


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
        last_dt = datetime.fromisoformat(state["last_check"])
        if last_dt.date() < datetime.now().date():
            anchor = datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)
            state["last_check"] = anchor.isoformat()
        return state
    anchor = datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)
    return {"last_check": anchor.isoformat(), "runs_today": 0}

def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = str(STATE_PATH) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    shutil.move(tmp, STATE_PATH)

# ── MORNING ANCHOR ────────────────────────────────────────────────────────────

def load_morning_anchor() -> dict:
    """
    Find today's earliest Brief_*.md and extract the morning baseline posture.
    Returns dict with: morning_posture, dominant_narrative, btc, spy, fear_greed, time
    Returns empty dict if no brief found — intraday runs standalone.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        files = sorted([
            f for f in os.listdir(BRIEFS_DIR)
            if f.startswith(f"Brief_{today}") and f.endswith(".md")
        ])
    except FileNotFoundError:
        return {}

    if not files:
        return {}

    brief_path = os.path.join(BRIEFS_DIR, files[0])  # earliest = morning brief

    try:
        with open(brief_path) as f:
            content = f.read()
    except Exception:
        return {}

    anchor = {"source_file": files[0]}

    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            anchor["btc"]                = str(fm.get("btc", "N/A"))
            anchor["fear_greed"]         = str(fm.get("fear_greed", "N/A"))
            anchor["dominant_narrative"] = str(fm.get("dominant_narrative", ""))[:120]
            anchor["time"]               = str(fm.get("time", "07:00"))
        except Exception:
            pass

    synth_match = re.search(r"## SYNTHESIS\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if synth_match:
        for line in reversed(synth_match.group(1).strip().split("\n")):
            word = line.strip().lower()
            if word in ("hold", "watch", "opportunity"):
                anchor["morning_posture"] = word.capitalize()
                break

    spy_match = re.search(r"SPY[:\s]+\$?([\d,\.]+)", content)
    anchor["spy"] = f"${spy_match.group(1)}" if spy_match else "N/A"

    return anchor


# ── PLAYS INTEGRATION (v2.3) ──────────────────────────────────────────────────

def load_morning_plays() -> dict:
    """
    Load today's most recent Plays_*.json sidecar from Daily-Briefs.
    Returns {} if not found or parse fails.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        files = sorted([
            f for f in os.listdir(BRIEFS_DIR)
            if f.startswith(f"Plays_{today}") and f.endswith(".json")
        ])
    except FileNotFoundError:
        return {}

    if not files:
        return {}

    path = os.path.join(BRIEFS_DIR, files[-1])  # most recent plays
    try:
        with open(path) as f:
            data = json.load(f)
        data["_source"] = files[-1]
        print(f"  Plays sidecar: {files[-1]} ({len(data.get('actives', []))} active trades)")
        return data
    except Exception as e:
        print(f"  ⚠️  plays sidecar load failed: {e}")
        return {}


def fetch_plays_prices(actives: list) -> dict:
    """
    Fetch current live prices for active trade tickers.
    Crypto via CoinGecko, equities via yfinance.
    Returns {ticker: float_price}.
    """
    result = {}
    crypto_map = {"BTC": "bitcoin", "SOL": "solana", "ETH": "ethereum"}

    crypto_tickers = [p.get("ticker", "").upper() for p in actives
                      if p.get("ticker", "").upper() in crypto_map]
    if crypto_tickers:
        try:
            ids = ",".join(crypto_map[t] for t in set(crypto_tickers))
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ids, "vs_currencies": "usd"},
                headers=HEADERS, timeout=10
            )
            for t in set(crypto_tickers):
                cg_id = crypto_map[t]
                price = r.json().get(cg_id, {}).get("usd")
                if price:
                    result[t] = float(price)
        except Exception as e:
            print(f"  ⚠️  crypto price fetch: {e}")

    equity_tickers = list(set(
        p.get("ticker", "").upper() for p in actives
        if p.get("ticker", "").upper() not in crypto_map
        and p.get("ticker", "").upper()
    ))
    if equity_tickers:
        try:
            tickers_str = " ".join(equity_tickers)
            df = yf.download(equity_tickers, period="1d", progress=False,
                             auto_adjust=True)["Close"]
            for t in equity_tickers:
                if t in df.columns:
                    series = df[t].dropna()
                    if not series.empty:
                        result[t] = float(series.iloc[-1])
        except Exception as e:
            print(f"  ⚠️  equity price fetch: {e}")

    return result


def evaluate_plays_status(plays_data: dict, current_prices: dict) -> list:
    """
    TRIGGERED: price moved >= threshold in trade direction from generation price.
    INVALIDATED: stop was hit.
    PENDING: neither condition met.
    Thresholds: crypto 1.5%, equity 0.8%.
    Returns list of status dicts.
    """
    CRYPTO = {"BTC", "SOL", "ETH"}
    actives = plays_data.get("actives", [])
    result = []

    for play in actives:
        ticker    = play.get("ticker", "").upper()
        direction = play.get("direction", "LONG").upper()
        gen_price = play.get("current")
        stop      = play.get("stop")
        target    = play.get("target")
        conviction= play.get("conviction", "MED")
        timeframe = play.get("timeframe", "—")
        rr        = play.get("rr", "N/A")
        current   = current_prices.get(ticker)

        entry = {
            "ticker":     ticker,
            "direction":  direction,
            "conviction": conviction,
            "timeframe":  timeframe,
            "rr":         rr,
            "gen_price":  gen_price,
            "current":    current,
            "stop":       stop,
            "target":     target,
            "status":     "NO DATA",
            "delta_pct":  None,
        }

        if gen_price and current:
            delta_pct = (current - gen_price) / gen_price * 100
            entry["delta_pct"] = round(delta_pct, 2)
            threshold = 1.5 if ticker in CRYPTO else 0.8

            if direction == "LONG":
                if stop and current <= float(stop):
                    entry["status"] = "INVALIDATED"
                elif delta_pct >= threshold:
                    entry["status"] = "TRIGGERED"
                else:
                    entry["status"] = "PENDING"
            else:  # SHORT
                if stop and current >= float(stop):
                    entry["status"] = "INVALIDATED"
                elif delta_pct <= -threshold:
                    entry["status"] = "TRIGGERED"
                else:
                    entry["status"] = "PENDING"

        result.append(entry)

    return result


def check_macro_shift(current_pulse: dict) -> list:
    """
    Compare current intraday pulse to morning context.json snapshot.
    Triggers: DXY/Gold trend flip, Fear & Greed delta >= 5 points.
    Returns list of shift strings (empty if nothing material).
    """
    shifts = []
    ctx_path = os.path.join(VAULT_ROOT, "Output", "context.json")
    if not os.path.exists(ctx_path):
        return shifts

    try:
        with open(ctx_path) as f:
            ctx = json.load(f)
    except Exception:
        return shifts

    morning_core = ctx.get("market", {}).get("core", {})
    morning_fg   = ctx.get("market", {}).get("fear_greed", {})

    def extract_trend(signal_str: str) -> str:
        s = signal_str.lower()
        if "rising" in s:  return "rising"
        if "falling" in s: return "falling"
        return "neutral"

    # Gold trend flip
    morning_gold_trend  = morning_core.get("Gold", {}).get("trend", "neutral")
    current_gold_trend  = extract_trend(current_pulse.get("Gold", {}).get("signal", ""))
    if (morning_gold_trend != "neutral" and current_gold_trend != "neutral"
            and morning_gold_trend != current_gold_trend):
        shifts.append(
            f"Gold flipped {morning_gold_trend} → {current_gold_trend} "
            f"({'risk-off intensifying' if current_gold_trend == 'rising' else 'risk-off easing'})"
        )

    # DXY trend flip
    morning_dxy_trend  = morning_core.get("DXY", {}).get("trend", "neutral")
    current_dxy_trend  = extract_trend(current_pulse.get("DXY", {}).get("signal", ""))
    if (morning_dxy_trend != "neutral" and current_dxy_trend != "neutral"
            and morning_dxy_trend != current_dxy_trend):
        shifts.append(
            f"DXY flipped {morning_dxy_trend} → {current_dxy_trend} "
            f"({'crypto headwind building' if current_dxy_trend == 'rising' else 'dollar weakness — crypto tailwind'})"
        )

    # Fear & Greed absolute delta
    try:
        morning_fg_val = int(morning_fg.get("value", 50))
        current_fg_val = int(current_pulse.get("fear_greed", {}).get("value", 50))
        delta = current_fg_val - morning_fg_val
        if abs(delta) >= 5:
            direction = "improved" if delta > 0 else "deteriorated"
            shifts.append(
                f"Fear & Greed {direction} {delta:+d} pts intraday "
                f"({morning_fg_val} → {current_fg_val})"
            )
    except Exception:
        pass

    return shifts


# ── PULSE ─────────────────────────────────────────────────────────────────────

def fetch_pulse() -> dict:
    result = {}
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            headers=HEADERS, timeout=10
        )
        data = r.json()
        btc_change = data["bitcoin"].get("usd_24h_change", 0)
        result["BTC"] = {
            "price": f"${data['bitcoin']['usd']:,.0f}",
            "change_24h": f"{'▲' if btc_change >= 0 else '▼'} {abs(btc_change):.2f}%",
            "change_pct": btc_change
        }
        result["ETH"] = {"price": f"${data['ethereum']['usd']:,.0f}"}
        result["SOL"] = {"price": f"${data['solana']['usd']:,.2f}"}
    except Exception as e:
        result["BTC"] = {"price": "N/A", "change_24h": "N/A"}

    try:
        df = yf.download(["SPY", "GLD", "DX-Y.NYB"], period="5d",
                         progress=False, auto_adjust=True)["Close"].ffill()
        for ticker, label, mode in [
            ("SPY", "SPY", "price"), ("GLD", "Gold", "signal"), ("DX-Y.NYB", "DXY", "signal")
        ]:
            current = float(df[ticker].dropna().iloc[-1])
            prev    = float(df[ticker].dropna().iloc[-2])
            chg     = (current - prev) / prev * 100
            direction = "▲" if chg >= 0 else "▼"
            if mode == "price":
                result[label] = {"price": f"${current:,.2f}", "change_24h": f"{direction} {abs(chg):.2f}%", "change_pct": round(chg, 2)}
            else:
                trend = "rising" if chg > 0.3 else "falling" if chg < -0.3 else "neutral"
                result[label] = {"signal": f"{direction} {trend} ({chg:+.2f}%)", "change_pct": round(chg, 2), "trend": trend}
    except Exception as e:
        print(f"  [pulse] yfinance error: {e}")

    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", headers=HEADERS, timeout=10)
        entry = r.json()["data"][0]
        result["fear_greed"] = {"value": int(entry["value"]), "classification": entry["value_classification"]}
    except Exception:
        result["fear_greed"] = {"value": "N/A", "classification": "N/A"}

    return result

# ── SECTOR PULSE ──────────────────────────────────────────────────────────────

def fetch_sector_pulse() -> dict:
    sectors_cfg     = CONFIG.get("sectors", {})
    etf_map         = sectors_cfg.get("etfs", {"XLK": "Technology", "SOXX": "Semiconductors", "XLE": "Energy", "XLU": "Utilities", "XLF": "Financials"})
    flag_threshold  = sectors_cfg.get("flag_threshold", 0.015)
    div_threshold   = sectors_cfg.get("divergence_threshold", 0.02)
    result          = {"etfs": {}, "alerts": [], "divergences": [], "spy_change_pct": 0.0}

    try:
        df          = yf.download(list(etf_map.keys()) + ["SPY"], period="5d", progress=False, auto_adjust=True)["Close"].ffill()
        spy_change  = (float(df["SPY"].dropna().iloc[-1]) - float(df["SPY"].dropna().iloc[-2])) / float(df["SPY"].dropna().iloc[-2]) * 100
        result["spy_change_pct"] = round(spy_change, 2)

        for ticker, sector_name in etf_map.items():
            current   = float(df[ticker].dropna().iloc[-1])
            prev      = float(df[ticker].dropna().iloc[-2])
            chg       = (current - prev) / prev * 100
            direction = "▲" if chg >= 0 else "▼"
            flagged   = abs(chg / 100) >= flag_threshold
            signal    = ""
            if chg > spy_change + (div_threshold * 100):
                signal = f"outperforming SPY by {chg - spy_change:+.1f}%"
            elif chg < spy_change - (div_threshold * 100):
                signal = f"underperforming SPY by {chg - spy_change:.1f}%"
            result["etfs"][ticker] = {"sector": sector_name, "change_24h": f"{direction} {abs(chg):.2f}%", "change_pct": round(chg, 2), "flagged": flagged, "signal": signal}
            if flagged:
                result["alerts"].append(f"{ticker} ({sector_name}) {direction} {abs(chg):.2f}%")
            if signal:
                result["divergences"].append(f"{sector_name}: {signal}")
    except Exception as e:
        print(f"  [sector] fetch error: {e}")

    return result

# ── SCAN ──────────────────────────────────────────────────────────────────────

def fetch_equity_scan() -> dict:
    equities_cfg = CONFIG.get("equities", {})
    groups       = ["semiconductors", "ai_energy_nexus", "high_noise", "gaming", "casual_signal"]
    all_tickers  = []
    ticker_meta  = {}

    for group in groups:
        grp = equities_cfg.get(group, {})
        if isinstance(grp, dict) and grp.get("tickers"):
            threshold = grp.get("flag_threshold", 0.02)
            for t in grp["tickers"]:
                t = t.upper()
                all_tickers.append(t)
                ticker_meta[t] = {"group": group, "threshold": threshold}

    result = {"movers": [], "flat": [], "earnings_today": []}
    if not all_tickers:
        return result

    today = datetime.now().date()
    for ticker in all_tickers:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None or cal.empty:
                continue
            if "Earnings Date" in cal.index:
                val   = cal.loc["Earnings Date"]
                dates = val if hasattr(val, '__iter__') else [val]
                for d in dates:
                    edate = d.date() if hasattr(d, 'date') else d
                    if edate == today:
                        result["earnings_today"].append(ticker)
        except Exception:
            continue

    try:
        df = yf.download(all_tickers, period="5d", progress=False, auto_adjust=True)["Close"].ffill()
        for ticker in all_tickers:
            meta      = ticker_meta[ticker]
            series    = df[ticker] if len(all_tickers) > 1 else df
            current   = float(series.dropna().iloc[-1])
            prev      = float(series.dropna().iloc[-2])
            chg       = (current - prev) / prev * 100
            direction = "▲" if chg >= 0 else "▼"
            flagged   = abs(chg / 100) >= meta["threshold"]
            entry     = {"ticker": ticker, "group": meta["group"], "price": f"${current:,.2f}", "change_24h": f"{direction} {abs(chg):.2f}%", "change_pct": round(chg, 2), "flagged": flagged}
            if flagged:
                result["movers"].append(entry)
            else:
                result["flat"].append(entry)
    except Exception as e:
        print(f"  [scan] equity fetch error: {e}")

    result["movers"].sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return result

# ── HEADLINES ─────────────────────────────────────────────────────────────────

def parse_entry_time(entry) -> datetime:
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return parsedate_to_datetime(val).astimezone().replace(tzinfo=None)
            except Exception:
                pass
    return datetime(1970, 1, 1)

def fetch_new_headlines(since: datetime) -> dict:
    results = {}
    for category, feeds in RSS_FEEDS.items():
        headlines = []
        seen = set()
        for url in feeds:
            try:
                feed = feedparser.parse(url, request_headers=HEADERS)
                for entry in feed.entries[:15]:
                    title = entry.get("title", "").strip()
                    if not title or title in seen:
                        continue
                    lower = title.lower()
                    if "nvidia" in lower or "nvda" in lower:
                        if not any(kw in lower for kw in NVDA_MAJOR_KEYWORDS):
                            continue
                    if not any(kw in lower for kw in RELEVANCE_KEYWORDS):
                        continue
                    pub_dt = parse_entry_time(entry)
                    if pub_dt <= since:
                        continue
                    seen.add(title)
                    headlines.append({"title": title, "published": pub_dt.strftime("%H:%M"), "link": entry.get("link", ""), "tickers": tag_headline(title)})
            except Exception:
                pass
        if headlines:
            results[category] = headlines
    return results

# ── SYNTHESIS ─────────────────────────────────────────────────────────────────

def build_check_prompt(pulse, sectors, scan, headlines, since, timestamp,
                       anchor: dict = None,
                       plays_statuses: list = None,
                       macro_shifts: list = None) -> str:
    btc  = pulse.get("BTC", {})
    spy  = pulse.get("SPY", {})
    gold = pulse.get("Gold", {})
    dxy  = pulse.get("DXY", {})
    fg   = pulse.get("fear_greed", {})

    sector_block = " | ".join(sectors.get("alerts", [])) or "No sector moves above threshold."
    if sectors.get("divergences"):
        sector_block += "\n" + " | ".join(sectors["divergences"])

    movers = scan.get("movers", [])
    scan_block = " | ".join(f"{m['ticker']} {m['change_24h']}" for m in movers) or "No significant equity movers."
    if scan.get("earnings_today"):
        scan_block += f"\n⚠ EARNINGS TODAY: {', '.join(scan['earnings_today'])}"

    headline_lines = []
    for cat, items in headlines.items():
        for h in items[:3]:
            headline_lines.append(f"  [{cat}] {h['published']} — {h['title']}")
    headline_block = "\n".join(headline_lines) or "No net-new headlines above threshold."

    # Morning anchor block
    if anchor and anchor.get("morning_posture"):
        anchor_block = f"""MORNING BASELINE (as of {anchor.get('time', '07:00')})
BTC: {anchor.get('btc', 'N/A')} | SPY: {anchor.get('spy', 'N/A')} | Fear & Greed: {anchor.get('fear_greed', 'N/A')}
Morning posture: {anchor.get('morning_posture', 'N/A')}
Morning narrative: {anchor.get('dominant_narrative', 'N/A')}

Lead with what has CHANGED since this morning. If nothing material changed, say so directly. Do not repeat the morning summary."""
    else:
        anchor_block = "MORNING BASELINE: Not available — treat this as a standalone snapshot."

    # Plays status block (v2.3)
    plays_block = ""
    if plays_statuses:
        def fmt_price(v):
            if v is None: return "N/A"
            try: return f"${float(v):,.2f}"
            except: return str(v)
        def delta_str(pct):
            if pct is None: return ""
            arrow = "▲" if pct >= 0 else "▼"
            return f"{arrow}{abs(pct):.1f}%"

        lines = []
        for s in plays_statuses:
            line = (f"  {s['ticker']} {s['direction']} {s['timeframe']} → {s['status']} "
                    f"({delta_str(s['delta_pct'])} since generation | "
                    f"gen: {fmt_price(s['gen_price'])} now: {fmt_price(s['current'])} | "
                    f"stop: {fmt_price(s['stop'])} target: {fmt_price(s['target'])} R/R: {s['rr']})")
            lines.append(line)
        plays_block = "MORNING PLAYS STATUS:\n" + "\n".join(lines)
        if macro_shifts:
            plays_block += "\n\nMACRO SHIFTS SINCE MORNING:\n" + "\n".join(f"  ⚠ {s}" for s in macro_shifts)
        else:
            plays_block += "\n\nMACRO SHIFTS: None detected."
    else:
        plays_block = "MORNING PLAYS: No plays sidecar found — skip plays assessment."

    sl = CHECK_LABELS
    return f"""You are a sharp trading intelligence analyst. Write a tactical intraday check-in — signal-dense, no filler.

{anchor_block}

CURRENT SNAPSHOT ({timestamp.strftime('%H:%M')})
BTC: {btc.get('price','N/A')} ({btc.get('change_24h','N/A')}) | SPY: {spy.get('price','N/A')} ({spy.get('change_24h','N/A')})
Gold: {gold.get('signal','N/A')} | DXY: {dxy.get('signal','N/A')}
Fear & Greed: {fg.get('value','N/A')} — {fg.get('classification','N/A')}

SECTOR PULSE
{sector_block}

SCAN (movers since {since.strftime('%H:%M')})
{scan_block}

NET-NEW HEADLINES (since {since.strftime('%H:%M')})
{headline_block}

{plays_block}

Now write:
{sl.get('synthesis','SYNTHESIS')}
2-4 sentences. Lead with what changed vs this morning (or confirm nothing material changed). Is this noise or signal worth acting on?

GO/NO-GO: For each active trade give a concrete ACTION directive — one sentence max:
- TRIGGERED: State HOLD, TRAIL STOP TO BREAK-EVEN, or CLOSE (partial/full) and one clause why.
- PENDING: State the exact price level or condition needed to enter, or PASS if setup has degraded.
- INVALIDATED: Note the level that was hit. State whether the thesis is broken or re-entry is valid after cooling period.
Flag any play with R/R below 1:1 explicitly — call it skip or minimum size only.
If a fresh intraday setup appeared that wasn't in the morning plays, flag it last with one line.
No filler. No restating what is already in the plays status table above.

End with exactly one word on its own line: Hold, Watch, or Opportunity."""



# ── OUTPUT ────────────────────────────────────────────────────────────────────



def atomic_write(content: str, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    shutil.move(tmp, path)

def extract_posture(synthesis: str) -> str:
    for line in reversed(synthesis.strip().split("\n")):
        word = line.strip().lower()
        if word in ("hold", "watch", "opportunity"):
            return word.capitalize()
    return ""

def render_text_block(text: str) -> str:
    if not text:
        return "<p>—</p>"
    html = ""
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^([A-Z][A-Z &]+:)", r"<span class='label'>\1</span>", line)
        line = line.replace("→", "<span class='arrow'>→</span>")
        line = line.replace("▲", "<span class='up'>▲</span>")
        line = line.replace("▼", "<span class='down'>▼</span>")
        if line.startswith("•"):
            html += f"<li>{line[1:].strip()}</li>"
        else:
            html += f"<p>{line}</p>"
    return html

def clean_synthesis_text(synthesis: str, section_label: str) -> str:
    lines = synthesis.strip().split("\n")
    if lines and lines[0].strip().upper() == section_label.upper():
        lines = lines[1:]
    if lines:
        last = lines[-1].strip().lower()
        if last in ("hold", "watch", "opportunity"):
            lines = lines[:-1]
    return "\n".join(lines).strip()

def render_anchor_banner(anchor: dict) -> str:
    if not anchor or not anchor.get("morning_posture"):
        return ""
    posture = anchor.get("morning_posture", "")
    posture_color = {"Hold": "#b87333", "Watch": "#c8a84b", "Opportunity": "#5a9e6f"}.get(posture, "#7a6a50")
    narrative = anchor.get("dominant_narrative", "")
    narrative_html = f'<span style="color:#6a5a38;font-style:italic;">{narrative}</span>' if narrative else ""
    return f"""
  <div style="background:#1a1712;border:1px solid #2e2820;border-left:3px solid #6b5a30;
              border-radius:4px;padding:10px 18px;margin:12px 0;font-size:11px;color:#7a6a50;
              display:flex;align-items:center;gap:18px;flex-wrap:wrap;">
    <span style="color:#5a4a28;letter-spacing:0.1em;font-size:10px;white-space:nowrap;">MORNING BASELINE {anchor.get('time','07:00')}</span>
    <span>BTC {anchor.get('btc','N/A')} &nbsp;|&nbsp; SPY {anchor.get('spy','N/A')}</span>
    <span>Posture: <strong style="color:{posture_color}">{posture}</strong></span>
    {narrative_html}
  </div>"""


def render_plays_status_html(plays_statuses: list, macro_shifts: list) -> str:
    """Render the plays status section — compact grid with status badges."""
    if not plays_statuses:
        return ""

    STATUS_COLOR = {
        "TRIGGERED":  ("#4ade80", "rgba(74,222,128,0.08)",  "rgba(74,222,128,0.3)"),
        "PENDING":    ("#f5a623", "rgba(245,166,35,0.08)",  "rgba(245,166,35,0.3)"),
        "INVALIDATED":("#f87171", "rgba(248,113,113,0.08)", "rgba(248,113,113,0.3)"),
        "NO DATA":    ("#5a5248", "rgba(90,82,72,0.08)",    "rgba(90,82,72,0.3)"),
    }

    def fmt_price(v):
        if v is None: return "—"
        try:
            f = float(v)
            return f"${f:,.0f}" if f > 1000 else f"${f:,.2f}"
        except: return str(v)

    def delta_badge(pct, direction):
        if pct is None: return '<span style="color:#5a5248">—</span>'
        color = "#4ade80" if (pct >= 0 and direction == "LONG") or (pct < 0 and direction == "SHORT") \
                else "#f87171"
        arrow = "▲" if pct >= 0 else "▼"
        return f'<span style="color:{color};font-weight:600">{arrow}{abs(pct):.1f}%</span>'

    rows = []
    for s in plays_statuses:
        status  = s.get("status", "NO DATA")
        color, bg, border = STATUS_COLOR.get(status, STATUS_COLOR["NO DATA"])
        ticker    = s.get("ticker", "?")
        direction = s.get("direction", "LONG")
        timeframe = s.get("timeframe", "—")
        tf_short  = timeframe.split("·")[0].strip() if "·" in timeframe else timeframe
        rr        = s.get("rr", "—")
        try:
            rr_val = float(str(rr).split(":")[-1])
            rr_warn = rr_val < 1.0
        except Exception:
            rr_warn = False
        rr_display = f'<span style="color:#f87171;font-weight:600">{rr} ⚠ SUB 1:1</span>' if rr_warn else str(rr)

        dir_color = "#4ade80" if direction == "LONG" else "#f87171"

        rows.append(f"""<div style="display:flex;align-items:center;gap:12px;padding:10px 0;
                border-bottom:1px solid #1e1c18;flex-wrap:wrap;">
  <span style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;
        color:#e8e0d0;min-width:52px">{ticker}</span>
  <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
        color:{dir_color};padding:2px 8px;border:1px solid {dir_color}33;
        background:{dir_color}0d;border-radius:3px">{direction}</span>
  <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
        color:{color};padding:2px 10px;border:1px solid {border};
        background:{bg};border-radius:3px;letter-spacing:0.08em">{status}</span>
  <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;">
        {delta_badge(s.get('delta_pct'), direction)}</span>
  <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#5a5248;margin-left:auto">
    gen {fmt_price(s.get('gen_price'))} · now {fmt_price(s.get('current'))} · 
    stop {fmt_price(s.get('stop'))} · target {fmt_price(s.get('target'))} · R/R {rr_display} · {tf_short}
  </span>
</div>""")

    rows_html = "\n".join(rows)

    shifts_html = ""
    if macro_shifts:
        shift_items = "".join(
            f'<div style="padding:6px 0;font-size:12px;color:#f5a623;border-bottom:1px solid #1e1c18;">'
            f'⚠ {s}</div>'
            for s in macro_shifts
        )
        shifts_html = f"""<div style="margin-top:14px;padding:10px 14px;background:#1a1208;
            border:1px solid #3a2808;border-radius:6px;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#a89068;
              letter-spacing:0.12em;text-transform:uppercase;margin-bottom:6px">Macro Shifts</div>
  {shift_items}
</div>"""

    return f"""<div class="section" style="border-left:3px solid #f5a623;">
    <div class="section-header">
      <span class="section-title" style="color:#f5a623">⚡ PLAYS STATUS</span>
    </div>
    <div style="padding:4px 16px 16px">
      {rows_html}
      {shifts_html}
    </div>
  </div>"""


def render_check_html(pulse, sectors, scan, headlines, synthesis, since, timestamp,
                      anchor: dict = None,
                      plays_statuses: list = None,
                      macro_shifts: list = None) -> str:
    btc  = pulse.get("BTC", {})
    spy  = pulse.get("SPY", {})
    gold = pulse.get("Gold", {})
    dxy  = pulse.get("DXY", {})
    fg   = pulse.get("fear_greed", {})
    sl   = CHECK_LABELS

    posture      = extract_posture(synthesis)
    posture_cls  = f"posture-{posture.lower()}" if posture else "posture-hold"
    posture_html = f'<div class="posture {posture_cls}">{posture}</div>' if posture else ""

    if anchor and anchor.get("morning_posture") and posture and anchor["morning_posture"] != posture:
        posture_drift = f'<div style="font-size:11px;color:#7a6a50;margin-top:6px;letter-spacing:0.08em;">morning: {anchor["morning_posture"]} → now: {posture}</div>'
    else:
        posture_drift = ""

    anchor_banner     = render_anchor_banner(anchor)
    plays_status_html = render_plays_status_html(plays_statuses or [], macro_shifts or [])

    sector_lines = sectors.get("alerts", []) + sectors.get("divergences", [])
    sector_html  = render_text_block("\n".join(sector_lines) or "No sector moves above threshold.")

    movers = scan.get("movers", [])
    scan_lines = [f"{m['ticker']} {m['change_24h']}" for m in movers] or ["No significant equity movers."]
    if scan.get("earnings_today"):
        scan_lines.append(f"⚠ EARNINGS TODAY: {', '.join(scan['earnings_today'])}")
    scan_html = render_text_block("\n".join(scan_lines))

    h_lines = []
    for cat, items in headlines.items():
        for h in items[:3]:
            h_lines.append(f"[{cat}] {h['published']} — {h['title']}")
    headlines_html = render_text_block("\n".join(h_lines) or "No net-new headlines.")

    synthesis_clean = clean_synthesis_text(synthesis, sl.get('synthesis', 'SYNTHESIS'))
    synth_html = render_text_block(synthesis_clean)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sovereign Check — {timestamp.strftime('%Y-%m-%d %H:%M')}</title>
  <style>{SOVEREIGN_CSS}</style>
</head>
<body>
  <div class="header">
    <div class="header-top">
      <div>
        <div class="system-name">Sovereign Intelligence System</div>
        <div class="brief-title">Intraday Check</div>
      </div>
      <div class="brief-meta">
        <div>{timestamp.strftime('%Y-%m-%d %H:%M')}</div>
        <div>Signal window: {since.strftime('%H:%M')} → {timestamp.strftime('%H:%M')}</div>
      </div>
    </div>
    <div class="stat-bar">
      <div class="stat"><div class="stat-label">BTC</div><div class="stat-value">{btc.get('price','N/A')}</div></div>
      <div class="stat"><div class="stat-label">SPY</div><div class="stat-value">{spy.get('price','N/A')}</div></div>
      <div class="stat"><div class="stat-label">Gold</div><div class="stat-value accent">{gold.get('signal','N/A')}</div></div>
      <div class="stat"><div class="stat-label">DXY</div><div class="stat-value accent">{dxy.get('signal','N/A')}</div></div>
      <div class="stat"><div class="stat-label">Fear &amp; Greed</div><div class="stat-value fear">{fg.get('value','N/A')} — {fg.get('classification','N/A')}</div></div>
    </div>
  </div>

  {anchor_banner}

  {plays_status_html}

  <div class="section">
    <div class="section-header"><span class="section-title">{sl.get('sector','SECTOR PULSE')}</span></div>
    {sector_html}
  </div>

  <div class="section scan-block">
    <div class="section-header"><span class="section-title">{sl.get('scan','SCAN')}</span></div>
    {scan_html}
  </div>

  <div class="section">
    <div class="section-header"><span class="section-title">NET-NEW HEADLINES</span></div>
    {headlines_html}
  </div>

  <div class="section">
    <div class="section-header"><span class="section-title">{sl.get('synthesis','SYNTHESIS')}</span></div>
    <div class="synthesis-block">
      {synth_html}
      {posture_html}
      {posture_drift}
    </div>
  </div>

  <div class="footer">
    <span>SOVEREIGN INTELLIGENCE SYSTEM</span>
    <span>Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
  </div>
</body>
</html>"""


def write_check_outputs(synthesis, pulse, sectors, scan, headlines, since, timestamp,
                        anchor: dict = None,
                        plays_statuses: list = None,
                        macro_shifts: list = None) -> str:
    btc = pulse.get("BTC", {})
    fg  = pulse.get("fear_greed", {})
    sl  = CHECK_LABELS

    movers_count  = len(scan.get("movers", []))
    sector_alerts = len(sectors.get("alerts", []))
    h_count       = sum(len(v) for v in headlines.values())
    spy           = pulse.get("SPY", {})
    gold          = pulse.get("Gold", {})
    dxy           = pulse.get("DXY", {})
    movers        = scan.get("movers", [])
    earnings      = scan.get("earnings_today", [])
    morning_posture = anchor.get("morning_posture", "N/A") if anchor else "N/A"

    frontmatter = f"""---
date: {timestamp.strftime('%Y-%m-%d')}
time: {timestamp.strftime('%H:%M')}
type: intraday-check
since: "{since.strftime('%H:%M')}"
morning_posture: "{morning_posture}"
btc: "{btc.get('price','N/A')}"
fear_greed: "{fg.get('value','N/A')} ({fg.get('classification','N/A')})"
equity_movers: {movers_count}
sector_alerts: {sector_alerts}
new_headlines: {h_count}
tags: [market-intel, intraday]
---"""

    pulse_block  = (f"BTC: {btc.get('price','N/A')} {btc.get('change_24h','')}  |  "
                    f"SPY: {spy.get('price','N/A')} {spy.get('change_24h','')}\n"
                    f"Gold: {gold.get('signal','N/A')}  |  DXY: {dxy.get('signal','N/A')}\n"
                    f"Fear & Greed: {fg.get('value','N/A')} — {fg.get('classification','N/A')}")

    anchor_note = ""
    if anchor and anchor.get("morning_posture"):
        anchor_note = f"\n> Morning baseline ({anchor.get('time','07:00')}): {anchor['morning_posture']} | BTC {anchor.get('btc','N/A')} | SPY {anchor.get('spy','N/A')}\n"

    # Plays status block for MD
    plays_md = ""
    if plays_statuses:
        def fmt_p(v):
            if v is None: return "N/A"
            try:
                f = float(v)
                return f"${f:,.0f}" if f > 1000 else f"${f:,.2f}"
            except: return str(v)
        def d_str(pct):
            if pct is None: return "N/A"
            arrow = "▲" if pct >= 0 else "▼"
            return f"{arrow}{abs(pct):.1f}%"
        lines = []
        for s in plays_statuses:
            lines.append(
                f"  {s['ticker']} {s['direction']} → **{s['status']}** "
                f"{d_str(s['delta_pct'])} | gen {fmt_p(s['gen_price'])} → now {fmt_p(s['current'])} | "
                f"stop {fmt_p(s['stop'])} | target {fmt_p(s['target'])} | R/R {s['rr']}"
            )
        plays_md = "\n## PLAYS STATUS\n" + "\n".join(lines)
        if macro_shifts:
            plays_md += "\n\n**Macro shifts:**\n" + "\n".join(f"  ⚠ {s}" for s in macro_shifts)

    sector_lines = sectors.get("alerts", []) + sectors.get("divergences", [])
    sector_block = "  " + "\n  ".join(sector_lines) if sector_lines else "  No sector moves above threshold."

    scan_lines   = [f"{m['ticker']} {m['change_24h']}" for m in movers] or ["No significant movers."]
    if earnings:
        scan_lines.append(f"⚠ EARNINGS TODAY: {', '.join(earnings)}")
    scan_block   = "  " + "\n  ".join(scan_lines)

    md = f"""{frontmatter}
{anchor_note}{plays_md}

## {sl.get('pulse','PULSE')}
{pulse_block}

## {sl.get('sector','SECTOR PULSE')}
{sector_block}

## {sl.get('scan','SCAN')}
{scan_block}

{synthesis}
"""

    filename = f"Intraday_{timestamp.strftime('%Y-%m-%d_%H%M')}"
    md_path  = os.path.join(OUTPUT_DIR, filename + ".md")
    atomic_write(md, md_path)

    html = render_check_html(pulse, sectors, scan, headlines, synthesis, since, timestamp,
                             anchor=anchor, plays_statuses=plays_statuses, macro_shifts=macro_shifts)
    html_path = os.path.join(OUTPUT_DIR, filename + ".html")
    atomic_write(html, html_path)

    return filename

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    ingest_path = os.path.join(VAULT_ROOT, "Scripts", "tools", "ingest_processor.py")
    venv_python = os.path.join(VAULT_ROOT, "Scripts", ".venv", "bin", "python3")
    subprocess.run([venv_python, ingest_path], capture_output=True)

    timestamp = datetime.now()
    divider   = "─" * 55

    print(f"\n{divider}")
    print(f"  SOVEREIGN — INTRADAY CHECK v2.3  {timestamp.strftime('%Y-%m-%d %H:%M')}")
    print(divider)

    state = load_state()
    since = datetime.fromisoformat(state["last_check"])
    print(f"  Signal window: {since.strftime('%H:%M')} → {timestamp.strftime('%H:%M')}")

    print("  Loading morning anchor...")
    anchor = load_morning_anchor()
    if anchor.get("morning_posture"):
        print(f"  Morning posture: {anchor['morning_posture']} (from {anchor.get('source_file','brief')})")
    else:
        print("  No morning brief found — running standalone.")

    # v2.3 — plays integration
    print("  Loading morning plays...")
    plays_data = load_morning_plays()
    plays_statuses = []
    macro_shifts   = []

    print("  Fetching pulse...")
    pulse = fetch_pulse()

    if plays_data.get("actives"):
        print("  Fetching live prices for active plays...")
        current_prices = fetch_plays_prices(plays_data["actives"])
        plays_statuses = evaluate_plays_status(plays_data, current_prices)
        for s in plays_statuses:
            icon = {"TRIGGERED": "✅", "PENDING": "⏳", "INVALIDATED": "🚫", "NO DATA": "❓"}.get(s["status"], "·")
            delta = f" ({'+' if (s['delta_pct'] or 0) >= 0 else ''}{s['delta_pct']:.1f}%)" if s["delta_pct"] is not None else ""
            print(f"    {icon} {s['ticker']} {s['direction']} → {s['status']}{delta}")

        print("  Checking macro shifts...")
        macro_shifts = check_macro_shift(pulse)
        if macro_shifts:
            for shift in macro_shifts:
                print(f"    ⚠  {shift}")
        else:
            print("    No material macro shifts detected.")
    else:
        print("  No plays sidecar — skipping status check.")

    print("  Fetching sector ETFs...")
    sectors = fetch_sector_pulse()
    print("  Scanning equity movers...")
    scan = fetch_equity_scan()
    print("  Scanning net-new headlines...")
    headlines = fetch_new_headlines(since)
    total_headlines = sum(len(v) for v in headlines.values())

    print(f"  Synthesizing via {FAST_MODEL}...")
    prompt    = build_check_prompt(pulse, sectors, scan, headlines, since, timestamp,
                                   anchor=anchor, plays_statuses=plays_statuses,
                                   macro_shifts=macro_shifts)
    try:
        synthesis = query_ollama(prompt, FAST_MODEL, temperature=0.35, max_tokens=700, timeout=120)
    except Exception as e:
        synthesis = f"[Ollama error: {e}]"

    filename  = write_check_outputs(synthesis, pulse, sectors, scan, headlines, since, timestamp,
                                    anchor=anchor, plays_statuses=plays_statuses,
                                    macro_shifts=macro_shifts)

    state["last_check"] = timestamp.isoformat()
    state["runs_today"] = state.get("runs_today", 0) + 1
    save_state(state)

    # Terminal summary
    btc  = pulse.get("BTC", {})
    spy  = pulse.get("SPY", {})
    gold = pulse.get("Gold", {})
    dxy  = pulse.get("DXY", {})
    fg   = pulse.get("fear_greed", {})
    sl   = CHECK_LABELS

    print(f"\n{divider}")
    if anchor and anchor.get("morning_posture"):
        print(f"  BASELINE  {anchor.get('time','07:00')} → {anchor['morning_posture']}")
    print(f"  {sl.get('pulse','PULSE')}")
    print(f"  BTC {btc.get('price','N/A')} {btc.get('change_24h','')}  |  SPY {spy.get('price','N/A')}")
    print(f"  Gold: {gold.get('signal','N/A')}  |  DXY: {dxy.get('signal','N/A')}")
    print(f"  Fear & Greed: {fg.get('value','N/A')} — {fg.get('classification','N/A')}")

    if plays_statuses:
        print(f"\n  PLAYS STATUS")
        for s in plays_statuses:
            icon = {"TRIGGERED": "✅", "PENDING": "⏳", "INVALIDATED": "🚫"}.get(s["status"], "·")
            delta = f" {'+' if (s['delta_pct'] or 0) >= 0 else ''}{s['delta_pct']:.1f}%" if s["delta_pct"] is not None else ""
            print(f"  {icon} {s['ticker']} {s['direction']} → {s['status']}{delta}")
        if macro_shifts:
            print(f"\n  MACRO SHIFTS")
            for shift in macro_shifts:
                print(f"  ⚠  {shift}")

    if sectors.get("alerts") or sectors.get("divergences"):
        print(f"\n  {sl.get('sector','SECTOR PULSE')}")
        for a in sectors.get("alerts", []):
            print(f"  🚨 {a}")
        for d in sectors.get("divergences", []):
            print(f"  🔄 {d}")

    if scan.get("movers") or scan.get("earnings_today"):
        print(f"\n  {sl.get('scan','SCAN')}")
        for m in scan.get("movers", []):
            print(f"  {m['ticker']}: {m['change_24h']}")
        for t in scan.get("earnings_today", []):
            print(f"  ⚠ EARNINGS TODAY: {t}")

    print(f"\n{synthesis}")
    print(f"{divider}")
    print(f"  Written: 02-Market-Intel/Intraday/{filename}.md + .html")
    print(f"  Net-new headlines: {total_headlines}\n")


if __name__ == "__main__":
    main()