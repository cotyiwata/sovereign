"""
analysis/regime.py — Sovereign Intelligence System
Market regime classification from price and moving average data.

Pure stateless functions — no I/O, no LLM calls, no file access.
Takes a DataFrame, returns a string. Trivially unit-testable.

Regimes:
  STRONG_UPTREND    — MA20 > MA200, price > MA20, MA20 slope positive
  STRONG_DOWNTREND  — MA20 < MA200, price < MA20, MA20 slope negative
  CHOPPY            — ATR elevated, no clear directional bias
  CONSOLIDATING     — ATR low, price in tight range, MAs flat

Usage:
    from analysis.regime import get_regime
    regime = get_regime("BTC", df)
"""
import pandas as pd

# Slope thresholds (5-candle MA20 delta / MA20, normalized)
SLOPE_UP_THRESHOLD   =  0.001   # +0.1%
SLOPE_DOWN_THRESHOLD = -0.001   # -0.1%

# ATR as % of price
ATR_CHOPPY_THRESHOLD        = 0.030   # > 3%  → choppy
ATR_CONSOLIDATING_THRESHOLD = 0.015   # < 1.5% → consolidating

STRONG_UPTREND   = "STRONG_UPTREND"
STRONG_DOWNTREND = "STRONG_DOWNTREND"
CHOPPY           = "CHOPPY"
CONSOLIDATING    = "CONSOLIDATING"


def _ma20_slope(df: pd.DataFrame) -> float:
    """5-candle MA20 slope as fraction of MA20 (normalized)."""
    if len(df) < 5:
        return 0.0
    base = df["ma20"].iloc[-5]
    if base == 0:
        return 0.0
    return (df["ma20"].iloc[-1] - base) / base


def get_regime(ticker: str, df: pd.DataFrame) -> str:
    """
    Classify current market regime for ticker.

    Parameters
    ----------
    ticker : str
        Used only for log output.
    df : pd.DataFrame
        Must contain columns: close, ma20, ma200, atr.
        Rows in chronological order (oldest → newest).

    Returns
    -------
    str — one of STRONG_UPTREND | STRONG_DOWNTREND | CHOPPY | CONSOLIDATING
    """
    required = {"close", "ma20", "ma200", "atr"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"[regime] {ticker}: missing columns {missing}")

    if len(df) < 5:
        print(f"[regime] {ticker}: insufficient data, defaulting CHOPPY")
        return CHOPPY

    price   = df["close"].iloc[-1]
    ma20    = df["ma20"].iloc[-1]
    ma200   = df["ma200"].iloc[-1]
    atr     = df["atr"].iloc[-1]
    slope   = _ma20_slope(df)
    atr_pct = atr / price if price > 0 else 0.0

    # ATR-based structural override checked first
    if atr_pct > ATR_CHOPPY_THRESHOLD:
        regime = CHOPPY
    elif atr_pct < ATR_CONSOLIDATING_THRESHOLD and abs(slope) < SLOPE_UP_THRESHOLD:
        regime = CONSOLIDATING
    elif ma20 > ma200 and price > ma20 and slope > SLOPE_UP_THRESHOLD:
        regime = STRONG_UPTREND
    elif ma20 < ma200 and price < ma20 and slope < SLOPE_DOWN_THRESHOLD:
        regime = STRONG_DOWNTREND
    else:
        regime = CHOPPY

    print(
        f"[regime] {ticker}: {regime} "
        f"(price={price:.2f} ma20={ma20:.2f} ma200={ma200:.2f} "
        f"slope={slope:.4f} atr_pct={atr_pct:.3f})"
    )
    return regime
