"""
core/enrichment.py — Active trade and position watch enrichment.

In v2.7.1, enrich_actives is one ~100-line function that does 8 things:
  1. Section assignment
  2. Level field copy
  3. MA position flags
  4. Regime + setup classification
  5. Direction-aware stop/target selection
  6. ATR forward-target gate (Fix 1)
  7. R/R recalculation per direction
  8. R/R quality gate
  9. Leverage table lookup
 10. Counter-trend SHORT gate (Fix 2)

Each is now a separate function. Each takes a play dict and a level dict,
returns a modified play dict. Ordering matters and is composed in
enrich_active_play(). Each function is independently unit-testable.

External contract preserved: enrich_actives(plays, levels) returns the same
shape as v2.7.1 — same keys, same flag semantics, same leverage values.
"""
from typing import Optional

from .constants import (
    TICKER_SECTION, CRYPTO_TICKERS,
    ATR_GATE_RESISTANCE_PROXIMITY, ATR_GATE_TARGET_PROXIMITY,
    ATR_MULT_CRYPTO, ATR_MULT_EQUITY,
    RR_VERIFY_THRESHOLD, RR_HIGH_CONV_MIN, RR_MED_CONV_MIN,
    RR_LEVERAGE_BOOST_LEVEL,
    LEVERAGE_TABLE, LEVERAGE_RR_ADJUSTED, LEVERAGE_VERIFY,
    LEVERAGE_DEFAULT, LEVERAGE_COUNTER_TREND_CAP,
    PW_ZONE_MAX_WIDTH_PCT, VALID_POSTURES,
)


# ── Helpers ──────────────────────────────────────────────────────────────
def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _is_crypto(ticker: str) -> bool:
    return ticker.upper() in CRYPTO_TICKERS


# ── Stage 1: Copy levels onto play ────────────────────────────────────────
def attach_levels(play: dict, lvl: dict) -> dict:
    """Copy support/resistance/MAs/RSI/MACD/vol from levels onto the play."""
    play["section"]    = TICKER_SECTION.get(play.get("ticker", "").upper(), "UNKNOWN")
    current = lvl.get("current")
    play["current"]    = current
    play["support"]    = lvl.get("support")
    play["resistance"] = lvl.get("resistance")
    play["ma9"]        = lvl.get("ma9")
    play["ma20"]       = lvl.get("ma20")
    play["ma200"]      = lvl.get("ma200")
    play["rsi"]        = lvl.get("rsi")
    play["macd_bull"]  = lvl.get("macd_bull")
    play["rel_vol"]    = lvl.get("rel_vol")

    cur = current or 0
    play["ma9_above"]   = (cur > lvl["ma9"])   if lvl.get("ma9")   else None
    play["ma20_above"]  = (cur > lvl["ma20"])  if lvl.get("ma20")  else None
    play["ma200_above"] = (cur > lvl["ma200"]) if lvl.get("ma200") else None
    return play


# ── Stage 2: Regime + setup classification (Session 41) ──────────────────
def attach_regime_and_setup(play: dict, lvl: dict) -> dict:
    """Run regime_detector + setup_detector against the shared _df.
    Both detectors are imported lazily so this module can be unit-tested
    without their dependencies."""
    df = lvl.get("_df")
    ticker = play.get("ticker", "?")
    if df is None or len(df) == 0:
        play["regime"] = "UNKNOWN"
        play["setup"]  = "NO_SETUP"
        return play

    try:
        from regime_detector import get_regime
        play["regime"] = get_regime(ticker, df)
    except Exception as e:
        print(f"    ⚠️  regime({ticker}): {e}")
        play["regime"] = "UNKNOWN"

    try:
        from setup_detector import get_setup
        play["setup"] = get_setup(ticker, df, lvl)
    except Exception as e:
        print(f"    ⚠️  setup({ticker}): {e}")
        play["setup"] = "NO_SETUP"

    return play


# ── Stage 3: Direction-aware stop/target + ATR forward gate ──────────────
def assign_stop_and_target(play: dict, lvl: dict) -> dict:
    """Pick stop and target based on direction.
    LONG path includes the ATR forward-target gate (Fix 1 from v2.7.1)."""
    direction = play.get("direction", "LONG").upper()

    if direction == "SHORT":
        play["stop"]   = lvl.get("short_stop")
        play["target"] = lvl.get("target_short")
        return play

    # LONG path — check ATR forward-target gate
    base_target = lvl.get("target_long")
    current     = _safe_float(lvl.get("current"))
    resistance  = _safe_float(lvl.get("resistance"))
    atr14       = lvl.get("atr14")

    triggers_atr_gate = (
        atr14 and resistance
        and current >= resistance * ATR_GATE_RESISTANCE_PROXIMITY
        and base_target and current >= base_target * ATR_GATE_TARGET_PROXIMITY
    )

    if triggers_atr_gate:
        ticker = play.get("ticker", "").upper()
        mult = ATR_MULT_CRYPTO if _is_crypto(ticker) else ATR_MULT_EQUITY
        play["target"]       = round(current + (atr14 * mult), 2)
        play["_atr_target"]  = True
    else:
        play["target"] = base_target

    play["stop"] = lvl.get("long_stop")
    return play


# ── Stage 4: R/R recalculation + quality gate ────────────────────────────
def compute_rr(play: dict) -> dict:
    """Recompute R/R from current/stop/target, then apply quality gate."""
    current   = _safe_float(play.get("current"))
    stop      = _safe_float(play.get("stop"))
    target    = _safe_float(play.get("target"))
    direction = play.get("direction", "LONG").upper()

    if direction == "SHORT":
        risk, reward = stop - current, current - target
    else:
        risk, reward = current - stop, target - current

    rr_calc = round(reward / risk, 1) if risk > 0.01 else 0
    play["rr"] = f"1:{rr_calc}" if rr_calc > 0 else "N/A"

    # Quality gate — sets rr_flagged or VERIFY ⚠
    if rr_calc > RR_VERIFY_THRESHOLD:
        play["rr"] = "VERIFY ⚠"
        play["rr_flagged"] = True
    else:
        conv = play.get("conviction", "MED").upper()
        if (conv == "HIGH" and rr_calc < RR_HIGH_CONV_MIN) or \
           (conv == "MED"  and rr_calc < RR_MED_CONV_MIN):
            play["rr_flagged"] = True

    return play


# ── Stage 5: Leverage table lookup ───────────────────────────────────────
def assign_leverage(play: dict) -> dict:
    """Resolve leverage from conviction × R/R tier × asset class."""
    rr_str = str(play.get("rr", ""))
    rr_verify = rr_str.startswith("VERIFY")
    rr_adjusted = play.get("rr_flagged", False) and not rr_verify

    if rr_verify:
        play["leverage"] = LEVERAGE_VERIFY
        return play
    if rr_adjusted:
        play["leverage"] = LEVERAGE_RR_ADJUSTED
        return play

    conv = play.get("conviction", "MED").upper()
    is_crypto = _is_crypto(play.get("ticker", ""))

    try:
        rr_num = float(rr_str.replace("1:", ""))
    except (ValueError, TypeError):
        rr_num = 1.0

    rr_tier = "boosted" if (conv == "HIGH" and rr_num >= RR_LEVERAGE_BOOST_LEVEL) \
              else "base" if conv == "HIGH" \
              else "any"

    play["leverage"] = LEVERAGE_TABLE.get(
        (conv, rr_tier, is_crypto), LEVERAGE_DEFAULT
    )
    return play


# ── Stage 6: Counter-trend SHORT gate (Fix 2) ────────────────────────────
def apply_counter_trend_gate(play: dict) -> dict:
    """If SHORT generated on fully bullish TA stack, flag and downgrade."""
    if play.get("direction", "LONG").upper() != "SHORT":
        return play
    bullish_stack = (
        play.get("ma9_above")  is True and
        play.get("ma20_above") is True and
        play.get("macd_bull")  is True
    )
    if not bullish_stack:
        return play

    play["_counter_trend"] = True
    if play.get("conviction", "").upper() == "HIGH":
        play["conviction"] = "MED"
    play["leverage"] = LEVERAGE_COUNTER_TREND_CAP
    return play


# ── Stage 7: Setup score (Session 41) ────────────────────────────────────
def compute_setup_score(play: dict) -> int:
    """0-10 quality score. Pulled out of render_play_card so it's testable."""
    from .constants import (
        SCORE_REGIME_ALIGN, SCORE_SETUP_PRESENT, SCORE_RR_THRESHOLD,
        SCORE_RR_BONUS, SCORE_HIGH_CONV, SCORE_MA_STACK,
    )
    score = 0
    direction = play.get("direction", "LONG").upper()
    regime    = play.get("regime", "UNKNOWN")
    setup     = play.get("setup", "NO_SETUP")
    conv      = play.get("conviction", "").upper()

    if (regime == "STRONG_UPTREND"   and direction == "LONG") or \
       (regime == "STRONG_DOWNTREND" and direction == "SHORT"):
        score += SCORE_REGIME_ALIGN

    if setup in ("BREAK_AND_RETEST", "COMPRESSION_COIL"):
        score += SCORE_SETUP_PRESENT

    rr_str = str(play.get("rr", ""))
    if ":" in rr_str and not rr_str.startswith("VERIFY"):
        try:
            if float(rr_str.split(":")[1]) >= SCORE_RR_THRESHOLD:
                score += SCORE_RR_BONUS
        except (ValueError, IndexError):
            pass

    if conv == "HIGH":
        score += SCORE_HIGH_CONV

    ma9, ma20, ma200 = (_safe_float(play.get(k)) for k in ("ma9", "ma20", "ma200"))
    if ma9 > 0 and ma20 > 0 and ma200 > 0:
        if direction == "LONG"  and ma9 > ma20 > ma200: score += SCORE_MA_STACK
        if direction == "SHORT" and ma9 < ma20 < ma200: score += SCORE_MA_STACK

    return score


# ── Composed pipeline ────────────────────────────────────────────────────
def enrich_active_play(play: dict, lvl: dict) -> dict:
    """Run all enrichment stages in order. Each stage is independently testable."""
    play["_kind"] = "active"
    play = attach_levels(play, lvl)
    play = attach_regime_and_setup(play, lvl)
    play = assign_stop_and_target(play, lvl)
    play = compute_rr(play)
    play = assign_leverage(play)
    play = apply_counter_trend_gate(play)
    play["_setup_score"] = compute_setup_score(play)
    return play


def enrich_actives(plays: list, levels: dict) -> list:
    """Drop-in replacement for v2.7.1's enrich_actives."""
    for p in plays:
        ticker = p.get("ticker", "").upper()
        enrich_active_play(p, levels.get(ticker, {}))
    return plays


# ── Position Watch enrichment ────────────────────────────────────────────
def _cap_zone_width(zone: list, current: float) -> tuple[list, bool]:
    """Enforce PW_ZONE_MAX_WIDTH_PCT. Returns (new_zone, was_capped)."""
    if not (isinstance(zone, list) and len(zone) == 2):
        return None, False
    try:
        zone_lo, zone_hi = float(zone[0]), float(zone[1])
    except (TypeError, ValueError):
        return None, False
    if current <= 0 or zone_lo <= 0:
        return [round(zone_lo, 2), round(zone_hi, 2)], False

    max_width = current * PW_ZONE_MAX_WIDTH_PCT
    actual    = zone_hi - zone_lo
    if actual > max_width:
        midpoint = (zone_lo + zone_hi) / 2
        half     = max_width / 2
        return [round(midpoint - half, 2), round(midpoint + half, 2)], True
    return [round(zone_lo, 2), round(zone_hi, 2)], False


def _normalize_user_levels(value) -> list:
    """User S/R can be a single float or a list. Normalize to list."""
    if isinstance(value, (int, float)):
        return [value]
    return value or []


def _resolve_posture(p: dict, lvl: dict, watched_entry: dict) -> tuple[str, dict]:
    """Resolve final posture. User override wins; otherwise apply correction
    rule (price in zone + MA20 up → ACCUMULATING)."""
    posture = p.get("posture", "WATCHING").upper()
    if posture not in VALID_POSTURES:
        posture = "WATCHING"

    flags = {}
    po = watched_entry.get("posture_override")
    if po and po in VALID_POSTURES:
        return po, {"_posture_overridden": True}

    zone = p.get("accumulate_zone")
    current = lvl.get("current")
    if (zone and isinstance(zone, list) and len(zone) == 2 and current is not None):
        try:
            curr_f = float(current)
            in_zone = zone[0] <= curr_f <= zone[1]
            above_zone = curr_f > zone[1]
            ma20_up = p.get("ma20_above") is True
            if in_zone and ma20_up and posture == "WATCHING":
                return "ACCUMULATING", {"_posture_corrected": True}
            if above_zone and posture == "ACCUMULATING":
                return "WATCHING", {"_posture_corrected": True}
        except (TypeError, ValueError):
            pass

    return posture, flags


def enrich_position_watch(items: list, levels: dict, watched: dict, sector: str) -> list:
    """Drop-in replacement for v2.7.1's enrich_position_watch."""
    for p in items:
        p["_kind"]   = "position_watch"
        p["_sector"] = sector
        ticker = p.get("ticker", "").upper()
        lvl = levels.get(ticker, {})

        # Reuse attach_levels for the TA fields
        attach_levels(p, lvl)
        # attach_levels sets a section based on TICKER_SECTION; strip it here —
        # position watch tickers (NVDA, VST, CEG, VRT) aren't in that map.
        p["section"] = sector.upper()

        # User-defined S/R + notes
        w = watched.get(ticker, {})
        p["user_support"]    = _normalize_user_levels(w.get("support"))
        p["user_resistance"] = _normalize_user_levels(w.get("resistance"))
        p["notes"]           = w.get("notes", "")

        # Cap accumulate_zone width
        new_zone, capped = _cap_zone_width(p.get("accumulate_zone"), _safe_float(lvl.get("current")))
        p["accumulate_zone"] = new_zone
        if capped:
            p["_zone_capped"] = True

        # Resolve final posture
        posture, posture_flags = _resolve_posture(p, lvl, w)
        p["posture"] = posture
        p.update(posture_flags)

    return items
