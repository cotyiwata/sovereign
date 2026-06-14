#!/usr/bin/env python3
"""
level_extractor.py — Sovereign Intelligence System
Runs daily after fetch_news.py, before chronicle.py.

Computes structural support/resistance for all tracked tickers
from 1y OHLC data and writes Data/watched_levels.yaml.
plays_html_renderer.py loads this file automatically — no manual
maintenance required.

Levels written per ticker:
  support     — 20-day rolling low (structural floor)
  resistance  — 20-day rolling high (structural ceiling)
  ma20        — 20-day moving average (dynamic S/R)
  ma200       — 200-day MA (long-term bias anchor)
  current     — last close
  note        — auto-generated context string
"""

import sys
import yaml
import yfinance as yf
from pathlib import Path
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT
WATCHED_LEVELS_PATH = VAULT_ROOT / "Data" / "watched_levels.yaml"

# ── Tickers ────────────────────────────────────────────────────────────────
# Display name → yfinance symbol
# Includes all plays universe tickers + macro reference (SPY, QQQ)
TICKERS = {
    "BTC":  "BTC-USD",
    "ETH":  "ETH-USD",
    "SOL":  "SOL-USD",
    "TSLA": "TSLA",
    "NVDA": "NVDA",
    "VST":  "VST",
    "CEG":  "CEG",
    "VRT":  "VRT",
    "SPY":  "SPY",
    "QQQ":  "QQQ",
}

def compute_levels(display: str, yf_symbol: str) -> dict | None:
    """
    Fetch 1y OHLC and compute structural levels.
    Returns dict with support/resistance (required by load_watched_levels)
    plus enrichment fields stored as comments in the YAML.
    """
    try:
        hist = yf.Ticker(yf_symbol).history(period="1y")
        if hist.empty or len(hist) < 20:
            print(f"    ⚠️  {display}: insufficient history")
            return None

        close     = hist["Close"]
        current   = round(float(close.iloc[-1]), 2)

        # Structural S/R — 20-day rolling extremes
        support    = round(float(hist["Low"].rolling(20).min().dropna().iloc[-1]), 2)
        resistance = round(float(hist["High"].rolling(20).max().dropna().iloc[-1]), 2)

        # Dynamic MAs
        ma20  = round(float(close.rolling(20).mean().dropna().iloc[-1]), 2) if len(hist) >= 20  else None
        ma200 = round(float(close.rolling(200).mean().dropna().iloc[-1]), 2) if len(hist) >= 200 else None

        # 52-week range context
        week52_low  = round(float(hist["Low"].min()), 2)
        week52_high = round(float(hist["High"].max()), 2)
        pct_from_high = round((current - week52_high) / week52_high * 100, 1)

        # Position relative to MA20 for auto-note
        if ma20:
            if current > ma20 * 1.02:
                ma_context = "above MA20"
            elif current < ma20 * 0.98:
                ma_context = "below MA20"
            else:
                ma_context = "at MA20"
        else:
            ma_context = "MA20 unavailable"

        # ATR14 for volatility context
        high_low   = hist["High"] - hist["Low"]
        high_close = (hist["High"] - hist["Close"].shift()).abs()
        low_close  = (hist["Low"]  - hist["Close"].shift()).abs()
        tr         = high_low.combine(high_close, max).combine(low_close, max)
        atr14      = round(float(tr.rolling(14).mean().dropna().iloc[-1]), 2) if len(hist) >= 14 else None

        note = (
            f"{current} | {ma_context} | "
            f"52w range {week52_low}–{week52_high} ({pct_from_high}% from high)"
        )

        return {
            "support":     support,
            "resistance":  resistance,
            "ma20":        ma20,
            "ma200":       ma200,
            "atr14":       atr14,
            "current":     current,
            "week52_low":  week52_low,
            "week52_high": week52_high,
            "note":        note,
        }

    except Exception as e:
        print(f"    ⚠️  {display} ({yf_symbol}): {e}")
        return None


def build_levels() -> dict:
    results = {}
    for display, symbol in TICKERS.items():
        print(f"    {display} ({symbol})...")
        data = compute_levels(display, symbol)
        if data:
            results[display] = data
            print(f"      support={data['support']}  resistance={data['resistance']}  current={data['current']}")
        else:
            print(f"      ⚠️  skipped")
    return results


def write_yaml(levels: dict) -> None:
    WATCHED_LEVELS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Build clean output — support/resistance always first for readability
    out = {}
    for ticker, data in levels.items():
        out[ticker] = {
            "support":     data["support"],
            "resistance":  data["resistance"],
            "ma20":        data.get("ma20"),
            "ma200":       data.get("ma200"),
            "atr14":       data.get("atr14"),
            "current":     data.get("current"),
            "week52_low":  data.get("week52_low"),
            "week52_high": data.get("week52_high"),
            "note":        data.get("note", ""),
        }

    header = (
        f"# watched_levels.yaml — auto-generated {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"# DO NOT EDIT MANUALLY — overwritten daily by level_extractor.py\n"
        f"# Source: yfinance 1y OHLC | support=20d rolling low | resistance=20d rolling high\n"
        f"# load_watched_levels() in plays_html_renderer.py reads support + resistance only.\n"
        f"# All other fields are reference context.\n\n"
    )

    with open(WATCHED_LEVELS_PATH, "w") as f:
        f.write(header)
        yaml.dump(out, f, default_flow_style=False, sort_keys=False)

    print(f"\n  ✓ wrote {WATCHED_LEVELS_PATH} ({len(out)} tickers)")


def main():
    print("\n📐 Level Extractor — building watched_levels.yaml")
    print(f"   Tickers: {', '.join(TICKERS.keys())}")
    print()

    levels = build_levels()

    if not levels:
        print("  ❌ No levels computed — skipping write")
        sys.exit(1)

    write_yaml(levels)
    print("  ✓ Level extraction complete\n")


if __name__ == "__main__":
    main()
