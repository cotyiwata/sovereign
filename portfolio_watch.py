#!/usr/bin/env python3
"""
portfolio_watch.py — Sovereign Portfolio Trigger Monitor (Hermes script-job, no_agent mode)

Fetches LIVE prices and checks each holding against its trigger level.
  - Equities / macro : yfinance  (fast_info -> history fallback; pattern from n01 scout)
  - Crypto           : CoinGecko simple/price API (pattern from n01 scout)

OUTPUT CONTRACT (no_agent mode):
  - stdout is delivered VERBATIM to Telegram.
  - Empty stdout  -> Hermes stays SILENT. So "nothing near" prints NOTHING.
  - Non-zero exit -> Hermes sends an error alert. Used only when ALL price
    sources fail (so silence never falsely implies "all clear").

All diagnostics go to stderr so they never leak into the delivered message.

Runs standalone on the sovereign venv (has yfinance/requests). Does not import
pipeline internals — a monitor must run independently of node state.
"""

import sys
from datetime import datetime

import requests
import yfinance as yf

# ── Trigger config ──────────────────────────────────────────────────────────
# Levels lifted from the original Portfolio Watch prompt.
# NOTE: some levels are likely stale (BTC support, etc.) — refresh after
# delivery is proven working. Proximity check is symmetric (within N% either side).

CRITICAL_PCT = 2.0   # within 2% of level
WATCH_PCT    = 5.0   # within 5% of level

EQUITY_TRIGGERS = [
    {"ticker": "NVDA", "level": 195.0, "desc": "compression floor"},
    {"ticker": "MU",   "level": 285.0, "desc": "compression floor"},
    {"ticker": "MRVL", "level": 175.0, "desc": "thesis-break (0.75 shr left)"},
    {"ticker": "PLTR", "level": 115.0, "desc": "Zone 2 add"},
    {"ticker": "VRT",  "level": 295.0, "desc": "build zone $290-300"},
    {"ticker": "MSFT", "level": 450.0, "desc": "entry zone $440-460"},
    {"ticker": "LNG",  "level": 220.0, "desc": "Zone 1 $215-225"},
    {"ticker": "XOM",  "level": 140.0, "desc": "Zone 1 $138-142"},
]

CRYPTO_TRIGGERS = [
    {"id": "bitcoin",  "ticker": "BTC", "level": 58343.0, "desc": "support"},
    {"id": "ethereum", "ticker": "ETH", "level": 1650.0,  "desc": "stop"},
    {"id": "solana",   "ticker": "SOL", "level": 115.0,   "desc": "stop"},
]

# Absolute-threshold macro context (alert when value >= level).
MACRO_TRIGGERS = [
    {"ticker": "^VIX", "label": "VIX", "level": 22.0, "desc": "risk-off (VIX>22)"},
    {"ticker": "^TNX", "label": "10Y", "level": 4.5,  "desc": "rates headwind (10Y>4.5%)"},
]

HEADERS = {"User-Agent": "SovereignPortfolioWatch/1.0 (personal research)"}


# ── Fetchers (patterns copied from n01_scout) ───────────────────────────────

def get_equity_px(ticker: str):
    """Live equity/index price. fast_info first (freshest), history fallback."""
    try:
        fi = yf.Ticker(ticker).fast_info
        p = fi["last_price"]
        if p and float(p) > 0:
            return float(p)
    except Exception as e:
        print(f"# fast_info miss {ticker}: {e}", file=sys.stderr)
    try:
        c = yf.Ticker(ticker).history(period="5d")["Close"].dropna()
        if len(c):
            return float(c.iloc[-1])
    except Exception as e:
        print(f"# history miss {ticker}: {e}", file=sys.stderr)
    return None


def get_crypto_pxs():
    """All crypto prices in one CoinGecko call. {ticker: price} or {} on failure."""
    ids = ",".join(t["id"] for t in CRYPTO_TRIGGERS)
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ids, "vs_currencies": "usd"},
            headers=HEADERS, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        out = {}
        for t in CRYPTO_TRIGGERS:
            usd = data.get(t["id"], {}).get("usd")
            if usd:
                out[t["ticker"]] = float(usd)
        return out
    except Exception as e:
        print(f"# coingecko failed: {e}", file=sys.stderr)
        return {}


# ── Evaluation ──────────────────────────────────────────────────────────────

def classify(price: float, level: float):
    """Return 'critical' / 'watch' / None based on % distance from level."""
    dist = abs(price - level) / level * 100.0
    if dist <= CRITICAL_PCT:
        return "critical", dist
    if dist <= WATCH_PCT:
        return "watch", dist
    return None, dist


def main() -> int:
    critical, watch, macro = [], [], []
    got_any_price = False

    # Equities
    for trig in EQUITY_TRIGGERS:
        px = get_equity_px(trig["ticker"])
        if px is None:
            continue
        got_any_price = True
        tier, dist = classify(px, trig["level"])
        if tier:
            line = f"• {trig['ticker']} ${px:,.2f} — {trig['desc']} ${trig['level']:,.0f} ({dist:.1f}%)"
            (critical if tier == "critical" else watch).append(line)

    # Crypto
    crypto_pxs = get_crypto_pxs()
    for trig in CRYPTO_TRIGGERS:
        px = crypto_pxs.get(trig["ticker"])
        if px is None:
            continue
        got_any_price = True
        tier, dist = classify(px, trig["level"])
        if tier:
            line = f"• {trig['ticker']} ${px:,.2f} — {trig['desc']} ${trig['level']:,.0f} ({dist:.1f}%)"
            (critical if tier == "critical" else watch).append(line)

    # Macro (absolute threshold)
    for trig in MACRO_TRIGGERS:
        val = get_equity_px(trig["ticker"])
        if val is None:
            continue
        got_any_price = True
        # ^TNX is sometimes quoted x10 (e.g. 45.0 == 4.5%); normalize.
        if trig["ticker"] == "^TNX" and val > 20:
            val /= 10.0
        if val >= trig["level"]:
            macro.append(f"• {trig['label']} {val:.1f} — {trig['desc']}")

    # Total fetch failure -> error alert, never a false "all clear".
    if not got_any_price:
        print("Portfolio Watch: all price sources failed — could not evaluate triggers.",
              file=sys.stderr)
        return 1

    # Nothing near -> print NOTHING -> Hermes stays silent.
    if not (critical or watch or macro):
        return 0

    # Build the alert (this is the only thing that ever hits stdout).
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [f"⚠️ PORTFOLIO ALERT — {today}"]
    if critical:
        parts.append("\n🔴 CRITICAL (within 2%):\n" + "\n".join(critical))
    if watch:
        parts.append("\n🟡 WATCH (within 5%):\n" + "\n".join(watch))
    if macro:
        parts.append("\n📊 MACRO:\n" + "\n".join(macro))
    print("\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
