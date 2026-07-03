#!/usr/bin/env python3
"""
btc_log_trend.py — BTC Power-Law Fair Value Model
Sovereign Intelligence System

Replicates the GMI Compounding Machine logic:
  - Log-linear regression of BTC price vs days since genesis block
  - Outputs: fair value, % deviation, σ reading, signal label
  - Uses yfinance for historical data + CoinGecko for live price

Run standalone: python3 btc_log_trend.py
Or import: from btc_log_trend import fetch_btc_log_trend
"""

import sys
import json
import requests
import numpy as np
from datetime import datetime, date

# yfinance optional — falls back to CoinGecko history if unavailable
try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

# ── CONFIG ─────────────────────────────────────────────────────────────────────

# Bitcoin genesis block: January 3, 2009
GENESIS_DATE = date(2009, 1, 3)

# How many years of history to fit the regression on
HISTORY_YEARS = 10

# Signal thresholds (σ from trend)
SIGNAL_BANDS = [
    (-2.0, "🔥 EXTREME BUY — Deep value. Max accumulate."),
    (-1.0, "✅ BUY ZONE — Below fair value. Add."),
    ( 0.0, "〰️ FAIR VALUE — Neutral. Hold, no add."),
    ( 1.0, "⚠️  ELEVATED — Above fair value. Trim optional."),
    ( 2.0, "🚨 OVERBOUGHT — Significantly extended. Trim."),
    ( 9.9, "🔴 EXTREME SELL — Blow-off territory. Full trim."),
]

HEADERS = {
    "User-Agent": "SovereignScout/1.0 (personal research)"
}

# ── DATA FETCH ─────────────────────────────────────────────────────────────────

def fetch_btc_history_yf(years: int = HISTORY_YEARS) -> tuple[list, list]:
    """
    Pull BTC-USD daily close from yfinance.
    Returns (dates_list, prices_list) — both aligned.
    """
    import yfinance as yf
    period = f"{years}y"
    raw = yf.download("BTC-USD", period=period, progress=False, auto_adjust=True)["Close"]
    # yfinance ≥0.2.x returns MultiIndex columns for single ticker — squeeze to Series
    if hasattr(raw, "squeeze"):
        df = raw.squeeze()
    else:
        df = raw
    df = df.dropna()
    dates = [d.date() for d in df.index]
    prices = [float(p) for p in df.values.flatten()]
    return dates, prices


def fetch_btc_live_price() -> float | None:
    """CoinGecko free tier — live BTC/USD price."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            headers=HEADERS,
            timeout=10
        )
        r.raise_for_status()
        return float(r.json()["bitcoin"]["usd"])
    except Exception as e:
        print(f"  ⚠️  CoinGecko live price failed: {e}")
        return None


# ── REGRESSION ─────────────────────────────────────────────────────────────────

def days_since_genesis(d: date) -> int:
    return (d - GENESIS_DATE).days


def fit_log_regression(dates: list, prices: list) -> tuple[float, float, float]:
    """
    Fit: log(price) = a * log(days) + b
    This is the power-law form used by the GMI model.
    Returns (a, b, residual_std_dev).
    """
    log_days   = np.log([days_since_genesis(d) for d in dates])
    log_prices = np.log(prices)

    # Linear regression in log-log space
    coeffs = np.polyfit(log_days, log_prices, 1)
    a, b = coeffs[0], coeffs[1]

    # Residuals in log space — std dev = 1σ band width
    predicted_log = a * log_days + b
    residuals = log_prices - predicted_log
    std_dev = float(np.std(residuals))

    return a, b, std_dev


def compute_fair_value(current_date: date, a: float, b: float) -> float:
    """
    Fair value at any date from the fitted regression.
    fair_value = exp(a * log(days) + b)
    """
    log_days = np.log(days_since_genesis(current_date))
    return float(np.exp(a * log_days + b))


def compute_sigma(current_price: float, fair_value: float, std_dev: float) -> float:
    """
    σ deviation = (log(current) - log(fair_value)) / std_dev
    Negative = below fair value (buy zone).
    Positive = above fair value (sell zone).
    """
    log_ratio = np.log(current_price / fair_value)
    return float(log_ratio / std_dev)


def get_signal_label(sigma: float) -> str:
    """Map σ reading to a human-readable signal."""
    for threshold, label in SIGNAL_BANDS:
        if sigma <= threshold:
            return label
    return SIGNAL_BANDS[-1][1]


# ── MAIN FUNCTION ──────────────────────────────────────────────────────────────

def fetch_btc_log_trend() -> dict:
    """
    Core function — returns the full GMI-style log trend analysis.
    Importable by n01_scout.py for pipeline integration (Phase B).

    Returns dict with keys:
        fair_value      — model fair value in USD
        current_price   — live BTC price
        deviation_pct   — % above/below fair value
        sigma           — standard deviations from trend
        signal          — human-readable signal label
        model_note      — regression metadata
        status          — 'live' | 'error'
    """
    print("  📐 [BTC LOG TREND] Fetching historical data...")

    # 1. Pull historical BTC prices
    try:
        if YF_AVAILABLE:
            dates, prices = fetch_btc_history_yf(HISTORY_YEARS)
            data_source = "yfinance"
        else:
            raise RuntimeError("yfinance not available")
    except Exception as e:
        return {"status": "error", "error": f"History fetch failed: {e}"}

    if len(dates) < 365:
        return {"status": "error", "error": f"Insufficient history: {len(dates)} days"}

    print(f"  📐 [BTC LOG TREND] {len(dates)} days of history loaded ({data_source})")

    # 2. Fit the regression
    try:
        a, b, std_dev = fit_log_regression(dates, prices)
    except Exception as e:
        return {"status": "error", "error": f"Regression failed: {e}"}

    # 3. Compute fair value for today
    today = date.today()
    fair_value = compute_fair_value(today, a, b)

    # 4. Get live price
    live_price = fetch_btc_live_price()
    if live_price is None:
        # Fall back to most recent historical close
        live_price = prices[-1]
        price_source = "historical_close"
        print(f"  ⚠️  Using last historical close: ${live_price:,.0f}")
    else:
        price_source = "live_coingecko"

    # 5. Compute σ and signal
    sigma = compute_sigma(live_price, fair_value, std_dev)
    deviation_pct = (live_price - fair_value) / fair_value * 100
    signal = get_signal_label(sigma)

    # 6. Compute 1σ band boundaries for context
    upper_1sigma = fair_value * np.exp(std_dev)
    lower_1sigma = fair_value * np.exp(-std_dev)
    upper_2sigma = fair_value * np.exp(2 * std_dev)
    lower_2sigma = fair_value * np.exp(-2 * std_dev)

    result = {
        "status": "live",
        "current_price": round(live_price, 2),
        "fair_value": round(fair_value, 2),
        "deviation_pct": round(deviation_pct, 1),
        "sigma": round(sigma, 2),
        "signal": signal,
        "bands": {
            "lower_2sigma": round(lower_2sigma, 0),
            "lower_1sigma": round(lower_1sigma, 0),
            "upper_1sigma": round(upper_1sigma, 0),
            "upper_2sigma": round(upper_2sigma, 0),
        },
        "model_meta": {
            "data_source": data_source,
            "price_source": price_source,
            "history_days": len(dates),
            "regression_std_dev": round(std_dev, 4),
            "power_law_exponent": round(a, 4),
            "genesis_date": str(GENESIS_DATE),
            "fit_date": str(today),
        }
    }

    return result


# ── CLI OUTPUT ─────────────────────────────────────────────────────────────────

def print_report(r: dict) -> None:
    """Terminal-formatted GMI-style output."""
    if r.get("status") != "live":
        print(f"\n❌ [BTC LOG TREND] Error: {r.get('error', 'unknown')}")
        return

    sigma = r["sigma"]
    dev = r["deviation_pct"]
    dev_str = f"{dev:+.1f}%"
    sigma_str = f"{sigma:+.2f}σ"
    direction = "below" if dev < 0 else "above"

    print()
    print("━" * 52)
    print("  BTC POWER-LAW FAIR VALUE MODEL")
    print("  (GMI Compounding Machine — local replica)")
    print("━" * 52)
    print(f"  Current Price :  ${r['current_price']:>12,.0f}")
    print(f"  Fair Value    :  ${r['fair_value']:>12,.0f}")
    print(f"  Deviation     :  {dev_str:>8}  ({direction} fair value)")
    print(f"  σ Reading     :  {sigma_str:>8}")
    print()
    print(f"  SIGNAL: {r['signal']}")
    print()
    print("  ── BAND REFERENCE ────────────────────────────")
    bands = r["bands"]
    print(f"  +2σ (trim zone) :  ${bands['upper_2sigma']:>10,.0f}")
    print(f"  +1σ (elevated)  :  ${bands['upper_1sigma']:>10,.0f}")
    print(f"   0  (fair value):  ${r['fair_value']:>10,.0f}")
    print(f"  -1σ (buy zone)  :  ${bands['lower_1sigma']:>10,.0f}")
    print(f"  -2σ (deep value):  ${bands['lower_2sigma']:>10,.0f}")
    print()
    meta = r["model_meta"]
    print(f"  Model: {meta['history_days']}d history | "
          f"exponent={meta['power_law_exponent']} | "
          f"std={meta['regression_std_dev']}")
    print(f"  Price source: {meta['price_source']}")
    print("━" * 52)
    print()


if __name__ == "__main__":
    result = fetch_btc_log_trend()
    print_report(result)

    # Optional: dump full JSON for inspection
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))