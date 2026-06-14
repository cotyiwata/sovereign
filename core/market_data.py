"""
core/market_data.py — Market data + technical indicators.

Refactor goals:
  1. Split compute_levels (was 70 lines doing 8 concerns) into focused functions.
  2. Each indicator computation is independently testable.
  3. Batch yfinance fetches — was 8 separate API calls per pipeline run, now 1.
  4. Output dicts keep the EXACT same keys as v2.7.1 so downstream code
     (enrich_actives, render_play_card, regime/setup detectors) is unchanged.

External contract preserved:
  - compute_all_levels(display_tickers) -> dict[ticker, level_dict]
  - Each level_dict has all v2.7.1 keys: current, support, resistance, long_stop,
    short_stop, target_long, target_short, atr14, stop, target, rr, ma9, ma20,
    ma200, rsi, macd_bull, rel_vol, _df.
"""
import pandas as pd
from typing import Optional

# yfinance is imported lazily inside the fetch functions so that pure
# functions (levels_from_history, _rsi, _atr_series, etc.) are testable
# without the dependency.

from .constants import (
    SR_WINDOW, SWING_WINDOW, RSI_WINDOW, ATR_WINDOW,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL, MA_WINDOWS, VOLUME_AVG_WIN,
    HISTORY_PERIOD,
    LONG_STOP_BUFFER, SHORT_STOP_BUFFER,
    LONG_TARGET_BUFFER, SHORT_TARGET_BUFFER,
)

# Display name → yfinance ticker
YF_TICKER = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "TSLA": "TSLA", "NVDA": "NVDA",
    "VST": "VST", "CEG": "CEG", "VRT": "VRT",
    "SPY": "SPY",  "QQQ": "QQQ",
}


# ── Indicator primitives (each independently testable) ────────────────────
def _rsi(close: pd.Series, window: int = RSI_WINDOW) -> Optional[float]:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, float("nan"))
    series = 100 - (100 / (1 + rs))
    series = series.dropna()
    return round(float(series.iloc[-1]), 1) if not series.empty else None


def _macd_is_bull(close: pd.Series) -> bool:
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return float(macd_line.iloc[-1]) > float(signal_line.iloc[-1])


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Average True Range as a series — caller decides whether to take .iloc[-1]
    or pass the full series to a DataFrame for regime/setup detectors."""
    high_low   = high - low
    high_close = (high - close.shift()).abs()
    low_close  = (low  - close.shift()).abs()
    tr = high_low.combine(high_close, max).combine(low_close, max)
    return tr.rolling(ATR_WINDOW).mean()


def _rolling_last(series: pd.Series, window: int) -> Optional[float]:
    """Last value of a rolling-window aggregate, or None if insufficient data."""
    if len(series) < window:
        return None
    val = series.dropna()
    return float(val.iloc[-1]) if not val.empty else None


def _rel_volume(volume: pd.Series) -> Optional[float]:
    avg = volume.rolling(VOLUME_AVG_WIN).mean().dropna()
    if avg.empty or float(avg.iloc[-1]) <= 0:
        return None
    return round(float(volume.iloc[-1]) / float(avg.iloc[-1]), 1)


# ── Level computation ─────────────────────────────────────────────────────
def _stops_and_targets(current: float, support: float, resistance: float,
                       swing_low_5d: float, swing_hi_5d: float) -> dict:
    """Pure function — given price levels, derive stops + targets + base R/R."""
    long_stop    = round(swing_low_5d * LONG_STOP_BUFFER,  2)
    short_stop   = round(swing_hi_5d  * SHORT_STOP_BUFFER, 2)
    target_long  = round(resistance   * LONG_TARGET_BUFFER, 2)
    target_short = round(support      * SHORT_TARGET_BUFFER, 2)
    risk_long    = current - long_stop
    rr_long      = round((target_long - current) / risk_long, 1) if risk_long > 0.01 else 0
    return {
        "long_stop":    long_stop,
        "short_stop":   short_stop,
        "target_long":  target_long,
        "target_short": target_short,
        "stop":         long_stop,        # default to LONG; enrich_actives flips for SHORT
        "target":       target_long,
        "rr":           f"1:{rr_long}" if rr_long > 0 else "N/A",
    }


def _build_indicator_df(hist: pd.DataFrame, atr_series: pd.Series) -> pd.DataFrame:
    """Normalized DataFrame consumed by regime_detector + setup_detector.
    Lowercase OHLC + MAs + ATR, single shared frame (Session 41 pattern)."""
    return pd.DataFrame({
        "close": hist["Close"].values,
        "high":  hist["High"].values,
        "low":   hist["Low"].values,
        "ma9":   hist["Close"].rolling(9).mean().values,
        "ma20":  hist["Close"].rolling(20).mean().values,
        "ma200": hist["Close"].rolling(200).mean().values,
        "atr":   atr_series.values,
    }, index=hist.index)


def levels_from_history(hist: pd.DataFrame) -> dict:
    """Compute the full level dict from a 1y OHLCV DataFrame.
    Returns {} on insufficient data — caller decides whether to log/skip."""
    if hist.empty or len(hist) < 10:
        return {}

    close, high, low, vol = hist["Close"], hist["High"], hist["Low"], hist["Volume"]

    current      = float(close.iloc[-1])
    support      = float(low.rolling(SR_WINDOW).min().dropna().iloc[-1])
    resistance   = float(high.rolling(SR_WINDOW).max().dropna().iloc[-1])
    swing_low_5d = float(low.rolling(SWING_WINDOW).min().dropna().iloc[-1])
    swing_hi_5d  = float(high.rolling(SWING_WINDOW).max().dropna().iloc[-1])

    ma_values = {f"ma{w}": _rolling_last(close.rolling(w).mean(), w) for w in MA_WINDOWS}
    ma_values = {k: round(v, 2) if v is not None else None for k, v in ma_values.items()}

    atr_series = _atr_series(high, low, close)
    atr14 = round(float(atr_series.dropna().iloc[-1]), 2) if not atr_series.dropna().empty else None

    out = {
        "current":    round(current, 2),
        "support":    round(support, 2),
        "resistance": round(resistance, 2),
        "atr14":      atr14,
        "rsi":        _rsi(close),
        "macd_bull":  _macd_is_bull(close),
        "rel_vol":    _rel_volume(vol),
        "_df":        _build_indicator_df(hist, atr_series),
        **ma_values,
        **_stops_and_targets(current, support, resistance, swing_low_5d, swing_hi_5d),
    }
    return out


# ── Public batched fetch (replaces compute_all_levels) ────────────────────
def compute_all_levels(display_tickers: list, batched: bool = True) -> dict:
    """Compute levels for a list of display names.

    batched=True (default): one yf.download call for all tickers → 5–10x faster
                            than the per-ticker fetch in v2.7.1.
    batched=False: fall back to per-ticker fetches (useful when batch shape
                   confuses crypto vs equity trading hours).

    Returns dict[display_ticker, level_dict].
    """
    if not display_tickers:
        return {}

    import yfinance as yf  # lazy import — keeps pure functions testable
    yf_symbols = [YF_TICKER.get(t, t) for t in display_tickers]

    if batched:
        try:
            # group_by="ticker" gives us a multi-index DataFrame
            data = yf.download(
                tickers=yf_symbols,
                period=HISTORY_PERIOD,
                group_by="ticker",
                progress=False,
                auto_adjust=True,
                threads=True,
            )
            results = {}
            for display, yft in zip(display_tickers, yf_symbols):
                try:
                    if len(yf_symbols) == 1:
                        hist = data
                    else:
                        hist = data[yft].dropna(how="all")
                    lvl = levels_from_history(hist)
                    if lvl:
                        results[display] = lvl
                    else:
                        print(f"    ⚠️  {display}: insufficient history")
                except Exception as e:
                    print(f"    ⚠️  levels({display}): {e}")
            return results
        except Exception as e:
            print(f"    ⚠️  batched fetch failed ({e}) — falling back to per-ticker")
            # fall through to per-ticker

    # Per-ticker fallback
    results = {}
    for display in display_tickers:
        try:
            yft = YF_TICKER.get(display, display)
            hist = yf.Ticker(yft).history(period=HISTORY_PERIOD)
            lvl = levels_from_history(hist)
            if lvl:
                results[display] = lvl
        except Exception as e:
            print(f"    ⚠️  levels({display}): {e}")
    return results


# ── Backward-compat shim — drop-in for the old compute_levels() ──────────
def compute_levels(yf_ticker: str) -> dict:
    """Single-ticker fetch matching the v2.7.1 signature.
    Prefer compute_all_levels() for pipeline use."""
    import yfinance as yf  # lazy
    try:
        hist = yf.Ticker(yf_ticker).history(period=HISTORY_PERIOD)
        return levels_from_history(hist)
    except Exception as e:
        print(f"    ⚠️  levels({yf_ticker}): {e}")
        return {}


# ── P0 Extended enrichment ───────────────────────────────────────────────────
def enrich_ticker_extended(display_ticker: str, spy_change_pct: float = 0.0) -> dict:
    """
    P0 enrichment — extended per-ticker data not in compute_all_levels.
    All fields default to None on failure so pipeline never halts.

    Fields:
      pdh/pdl/pdc           prior day OHLC
      pm_high/pm_low        pre-market high/low (1m, prepost=True)
      vwap                  session VWAP from 5-min candles
      weekly_ma20/ma50      weekly period MAs
      bbw                   Bollinger Band Width (20-period daily)
      bbw_20p_low           True if BBW at/below 20-period rolling min
      rs_vs_spy             ticker daily % Δ minus SPY daily % Δ
      earnings_date         next earnings YYYY-MM-DD (equities only)
      days_to_earnings      calendar days until earnings (equities only)
      high_52w              52-week high
      dist_52w_high_pct     % distance below 52w high (negative = below)
    """
    import yfinance as yf
    from datetime import date as _date

    yft = YF_TICKER.get(display_ticker, display_ticker)
    result = {
        "pdh": None, "pdl": None, "pdc": None,
        "pm_high": None, "pm_low": None,
        "vwap": None,
        "weekly_ma20": None, "weekly_ma50": None,
        "bbw": None, "bbw_20p_low": None,
        "rs_vs_spy": None,
        "earnings_date": None, "days_to_earnings": None,
        "high_52w": None, "dist_52w_high_pct": None,
    }

    is_equity = display_ticker not in ("BTC", "ETH", "SOL")

    try:
        t_obj = yf.Ticker(yft)

        # ── Daily history (5d) — PDH/PDL/PDC + RS vs SPY ──────────────────
        hist_d = t_obj.history(period="5d", interval="1d")
        if len(hist_d) >= 2:
            prev = hist_d.iloc[-2]
            result["pdh"] = round(float(prev["High"]),  2)
            result["pdl"] = round(float(prev["Low"]),   2)
            result["pdc"] = round(float(prev["Close"]), 2)
            # RS vs SPY: ticker daily change - spy change
            try:
                t_curr = float(hist_d["Close"].iloc[-1])
                t_prev = float(hist_d["Close"].iloc[-2])
                if t_prev > 0:
                    t_chg = (t_curr - t_prev) / t_prev * 100
                    result["rs_vs_spy"] = round(t_chg - spy_change_pct, 2)
            except Exception:
                pass

        # ── 1y daily — 52w high + BBW ──────────────────────────────────────
        hist_1y = t_obj.history(period="1y", interval="1d")
        if not hist_1y.empty:
            high_52w = float(hist_1y["High"].max())
            current  = float(hist_1y["Close"].iloc[-1])
            result["high_52w"] = round(high_52w, 2)
            if high_52w > 0:
                result["dist_52w_high_pct"] = round(
                    (current - high_52w) / high_52w * 100, 2
                )
            # Bollinger Band Width (20-period)
            close = hist_1y["Close"]
            if len(close) >= 20:
                ma20  = close.rolling(20).mean()
                std20 = close.rolling(20).std()
                upper = ma20 + 2 * std20
                lower = ma20 - 2 * std20
                bbw_s = ((upper - lower) / ma20).dropna()
                if not bbw_s.empty:
                    result["bbw"] = round(float(bbw_s.iloc[-1]), 4)
                    if len(bbw_s) >= 20:
                        bbw_min = float(bbw_s.rolling(20).min().dropna().iloc[-1])
                        result["bbw_20p_low"] = result["bbw"] <= bbw_min * 1.05

        # ── Weekly history — MA20 + MA50 ───────────────────────────────────
        try:
            hist_w = t_obj.history(period="2y", interval="1wk")
            close_w = hist_w["Close"]
            if len(close_w) >= 20:
                result["weekly_ma20"] = round(
                    float(close_w.rolling(20).mean().dropna().iloc[-1]), 2
                )
            if len(close_w) >= 50:
                result["weekly_ma50"] = round(
                    float(close_w.rolling(50).mean().dropna().iloc[-1]), 2
                )
        except Exception:
            pass

        # ── Pre-market (1m, prepost=True) ─────────────────────────────────
        try:
            hist_pm = t_obj.history(period="1d", interval="1m", prepost=True)
            if not hist_pm.empty:
                pm_data = hist_pm.between_time("04:00", "09:29")
                if not pm_data.empty:
                    result["pm_high"] = round(float(pm_data["High"].max()), 2)
                    result["pm_low"]  = round(float(pm_data["Low"].min()),  2)
        except Exception:
            pass

        # ── VWAP (5-min intraday) ─────────────────────────────────────────
        try:
            hist_5m = t_obj.history(period="1d", interval="5m")
            if not hist_5m.empty:
                tp  = (hist_5m["High"] + hist_5m["Low"] + hist_5m["Close"]) / 3
                vol = hist_5m["Volume"]
                cum_vol    = vol.cumsum()
                cum_tp_vol = (tp * vol).cumsum()
                last_vol = float(cum_vol.iloc[-1])
                if last_vol > 0:
                    result["vwap"] = round(float(cum_tp_vol.iloc[-1]) / last_vol, 2)
        except Exception:
            pass

        # ── Earnings (equity only) ────────────────────────────────────────
        if is_equity:
            try:
                cal   = t_obj.calendar
                today = _date.today()
                edates = []
                if cal is not None:
                    if hasattr(cal, "columns") and "Earnings Date" in cal.columns:
                        vals = cal["Earnings Date"].dropna()
                        edates = [v.date() if hasattr(v, "date") else v for v in vals]
                    elif hasattr(cal, "index") and "Earnings Date" in cal.index:
                        val = cal.loc["Earnings Date"]
                        if hasattr(val, "__iter__") and not isinstance(val, str):
                            edates = [v.date() if hasattr(v, "date") else v for v in val]
                        else:
                            edates = [val.date() if hasattr(val, "date") else val]
                future = [d for d in edates if d >= today]
                if future:
                    nxt = min(future)
                    result["earnings_date"]    = nxt.strftime("%Y-%m-%d")
                    result["days_to_earnings"] = (nxt - today).days
            except Exception:
                pass

    except Exception as e:
        print(f"    ⚠️  enrich_extended({display_ticker}): {e}")

    return result
