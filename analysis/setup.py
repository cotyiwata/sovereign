"""
analysis/setup.py — Sovereign Intelligence System
Deterministic setup detection + scoring. P1 upgrade — Session 66.

Pure stateless functions. No I/O, no LLM calls, no file access.

Setup types:
  PULLBACK_TO_LEVEL      — trend + pullback to rising MA/support
  COMPRESSION_BREAKOUT   — BBW compression coil + directional break
  MOMENTUM_CONTINUATION  — full MA stack aligned, RS positive, pause
  NO_SETUP               — no qualifying condition met

Scoring rubric (0-10):
  Daily MA alignment    3 pts  (direction-aware)
  Weekly MA alignment   2 pts  (weekly_ma20 vs current price)
  RSI zone              2 pts  (direction-aware, 40-60 = healthy LONG)
  Volume confirmation   2 pts  (rel_vol threshold)
  R/R minimum           1 pt   (>= 1.5)

Enforcement tiers (applied in n08_plays.py):
  7-10 → EXECUTABLE, full leverage per conviction table
  5-6  → EXECUTABLE, 1x leverage cap
  3-4  → DEVELOPING, no sizing shown
  0-2  → DROP, do not surface

Backward compat: BREAK_AND_RETEST and COMPRESSION_COIL are
aliased to the new names so existing call sites still work
until updated.
"""
from __future__ import annotations

from typing import Optional
import pandas as pd

# Setup type constants
PULLBACK_TO_LEVEL     = "PULLBACK_TO_LEVEL"
COMPRESSION_BREAKOUT  = "COMPRESSION_BREAKOUT"
MOMENTUM_CONTINUATION = "MOMENTUM_CONTINUATION"
NO_SETUP              = "NO_SETUP"

# Backward-compat aliases — old names still resolve
BREAK_AND_RETEST = PULLBACK_TO_LEVEL
COMPRESSION_COIL = COMPRESSION_BREAKOUT


def _has_cols(df: pd.DataFrame, cols: list[str]) -> bool:
    return all(c in df.columns for c in cols)


# ── Setup type detectors ──────────────────────────────────────────────────────

def _detect_pullback_to_level(df: pd.DataFrame, resistance: float) -> bool:
    """Price broke above resistance in last 5 bars, pulled back to retest zone."""
    if len(df) < 10:
        return False
    if not _has_cols(df, ["close", "ma20", "high", "low"]):
        return False

    recent = df.tail(5)
    if not (recent["high"] > resistance).any():
        return False

    current_close = float(df["close"].iloc[-1])
    current_low   = float(df["low"].iloc[-1])
    band_low  = resistance * 0.985
    band_high = resistance * 1.015
    pulled_back = (
        (band_low <= current_low   <= band_high) or
        (band_low <= current_close <= band_high)
    )
    if not pulled_back:
        return False

    current_ma20 = float(df["ma20"].iloc[-1])
    if current_close < current_ma20:
        return False

    if len(df) >= 6 and current_ma20 <= float(df["ma20"].iloc[-6]):
        return False

    return True


def _detect_compression_breakout(df: pd.DataFrame) -> bool:
    """ATR contracting ≥25%, tight 5-bar range <3%, MAs converging <1%."""
    if len(df) < 11:
        return False
    if not _has_cols(df, ["close", "ma9", "ma20", "atr", "high", "low"]):
        return False

    atr_now  = float(df["atr"].iloc[-1])
    atr_prev = float(df["atr"].iloc[-10])
    if atr_prev <= 0 or atr_now >= atr_prev * 0.75:
        return False

    last5 = df.tail(5)
    current_price = float(df["close"].iloc[-1])
    if current_price <= 0:
        return False
    range_pct = (float(last5["high"].max()) - float(last5["low"].min())) / current_price
    if range_pct >= 0.03:
        return False

    ma9  = float(df["ma9"].iloc[-1])
    ma20 = float(df["ma20"].iloc[-1])
    if abs(ma9 - ma20) / current_price >= 0.01:
        return False

    return True


def _detect_momentum_continuation(
    df: pd.DataFrame,
    rs_vs_spy: Optional[float] = None,
) -> bool:
    """Full bull MA stack, MA20 rising, RS positive vs SPY."""
    if len(df) < 20:
        return False
    if not _has_cols(df, ["close", "ma9", "ma20", "ma200"]):
        return False

    price = float(df["close"].iloc[-1])
    ma9   = float(df["ma9"].iloc[-1])
    ma20  = float(df["ma20"].iloc[-1])
    ma200 = float(df["ma200"].iloc[-1])

    if not (price > ma9 > ma20 > ma200):
        return False

    if len(df) >= 6 and float(df["ma20"].iloc[-1]) <= float(df["ma20"].iloc[-6]):
        return False

    if rs_vs_spy is not None and rs_vs_spy < 0:
        return False

    return True


# ── Scoring components ────────────────────────────────────────────────────────

def _score_daily_ma(df: pd.DataFrame, direction: str) -> int:
    """3 pts. Direction-aware daily MA alignment."""
    if not _has_cols(df, ["close", "ma9", "ma20", "ma200"]) or len(df) < 6:
        return 0

    price = float(df["close"].iloc[-1])
    ma9   = float(df["ma9"].iloc[-1])
    ma20  = float(df["ma20"].iloc[-1])
    ma200 = float(df["ma200"].iloc[-1])
    ma9_rising  = ma9  > float(df["ma9"].iloc[-6])
    ma20_rising = ma20 > float(df["ma20"].iloc[-6])

    if direction == "LONG":
        if price > ma9 > ma20 > ma200 and ma9_rising and ma20_rising:
            return 3
        if ma9_rising and ma20_rising:
            return 2
        if ma9_rising:
            return 1
        return 0
    else:  # SHORT
        ma9_falling  = ma9  < float(df["ma9"].iloc[-6])
        ma20_falling = ma20 < float(df["ma20"].iloc[-6])
        if price < ma9 < ma20 < ma200 and ma9_falling and ma20_falling:
            return 3
        if ma9_falling and ma20_falling:
            return 2
        if ma9_falling:
            return 1
        return 0


def _score_weekly_ma(
    direction: str,
    current_price: float,
    weekly_ma20: Optional[float],
) -> int:
    """2 pts. Weekly MA20 trend alignment. Returns 1 (neutral) if no data."""
    if weekly_ma20 is None or weekly_ma20 <= 0:
        return 1
    if direction == "LONG":
        return 2 if current_price > weekly_ma20 else 0
    else:
        return 2 if current_price < weekly_ma20 else 0


def _score_rsi(rsi: Optional[float], direction: str) -> int:
    """2 pts. RSI zone, direction-aware. LONG favors 40-60 (momentum, not extended)."""
    if rsi is None:
        return 0
    if direction == "LONG":
        if 40 <= rsi <= 60:
            return 2
        if (30 <= rsi < 40) or (60 < rsi <= 70):
            return 1
        return 0
    else:  # SHORT — mirror: 40-60 is also valid (mean reversion room)
        if 40 <= rsi <= 60:
            return 2
        if (30 <= rsi < 40) or (60 < rsi <= 70):
            return 1
        return 0


def _score_volume(rel_vol: Optional[float]) -> int:
    """2 pts. Relative volume confirmation."""
    if rel_vol is None:
        return 0
    if rel_vol >= 1.2:
        return 2
    if rel_vol >= 0.8:
        return 1
    return 0


def _score_rr(rr: Optional[float]) -> int:
    """1 pt. R/R minimum gate."""
    if rr is None:
        return 0
    return 1 if rr >= 1.5 else 0


def compute_score(
    df: pd.DataFrame,
    direction: str = "LONG",
    weekly_ma20: Optional[float] = None,
    rel_vol: Optional[float] = None,
    rr: Optional[float] = None,
) -> int:
    """
    Compute deterministic setup score 0-10.

    Parameters
    ----------
    df          : pd.DataFrame with close, ma9, ma20, ma200, rsi columns
    direction   : "LONG" or "SHORT"
    weekly_ma20 : weekly 20-period MA price
    rel_vol     : relative volume vs 20-day average
    rr          : risk/reward ratio as float (e.g. 2.1)

    Returns
    -------
    int 0-10
    """
    if df is None or len(df) == 0:
        return 0

    rsi = None
    if "rsi" in df.columns:
        raw = df["rsi"].dropna()
        if not raw.empty:
            rsi = float(raw.iloc[-1])

    current_price = float(df["close"].iloc[-1]) if len(df) > 0 else 0.0

    score  = _score_daily_ma(df, direction)
    score += _score_weekly_ma(direction, current_price, weekly_ma20)
    score += _score_rsi(rsi, direction)
    score += _score_volume(rel_vol)
    score += _score_rr(rr)

    return min(10, max(0, score))


# ── Public API ────────────────────────────────────────────────────────────────

def get_setup(
    ticker: str,
    df: pd.DataFrame,
    levels: dict,
    direction: str = "LONG",
    weekly_ma20: Optional[float] = None,
    rel_vol: Optional[float] = None,
    rr: Optional[float] = None,
    rs_vs_spy: Optional[float] = None,
) -> dict:
    """
    Detect setup type and compute deterministic score.

    Parameters
    ----------
    ticker      : str — used for future logging
    df          : pd.DataFrame — close, ma9, ma20, ma200, atr, high, low
    levels      : dict — {"resistance": float, "support": float}
    direction   : "LONG" or "SHORT"
    weekly_ma20 : weekly 20-period MA (from EnrichedTicker)
    rel_vol     : relative volume vs 20-day average
    rr          : risk/reward as float (e.g. 2.1)
    rs_vs_spy   : relative strength vs SPY (daily % delta)

    Returns
    -------
    dict:
        "type"  : PULLBACK_TO_LEVEL | COMPRESSION_BREAKOUT |
                  MOMENTUM_CONTINUATION | NO_SETUP
        "score" : int 0-10 (deterministic)
    """
    if df is None or len(df) == 0:
        return {"type": NO_SETUP, "score": 0}

    resistance = levels.get("resistance") if isinstance(levels, dict) else None
    setup_type = NO_SETUP

    if resistance and resistance > 0:
        try:
            if _detect_pullback_to_level(df, float(resistance)):
                setup_type = PULLBACK_TO_LEVEL
        except (ValueError, TypeError, KeyError):
            pass

    if setup_type == NO_SETUP:
        try:
            if _detect_compression_breakout(df):
                setup_type = COMPRESSION_BREAKOUT
        except (ValueError, TypeError, KeyError):
            pass

    if setup_type == NO_SETUP:
        try:
            if _detect_momentum_continuation(df, rs_vs_spy):
                setup_type = MOMENTUM_CONTINUATION
        except (ValueError, TypeError, KeyError):
            pass

    score = compute_score(df, direction, weekly_ma20, rel_vol, rr)

    return {"type": setup_type, "score": score}
