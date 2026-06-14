"""
Smoke tests for the new core modules. Run with: python -m pytest tests/test_core.py
or just: python tests/test_core.py

These are FAST tests — no network, no yfinance, no Ollama. They verify the
behavioral contracts the v2.7.1 enrichment was enforcing inline.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_atr_gate_triggers_when_price_at_resistance():
    """Fix 1 from v2.7.1: ATR forward target activates when price >= 0.98 * resistance
    AND base target is essentially hit. Crypto gets 3x ATR, equity gets 2x."""
    from core.enrichment import assign_stop_and_target

    play = {"direction": "LONG", "ticker": "BTC"}
    lvl = {
        "current": 100.0,
        "resistance": 100.0,    # price exactly at resistance
        "long_stop": 90.0,
        "target_long": 100.5,   # base target essentially hit
        "atr14": 5.0,
        "short_stop": None, "target_short": None,
    }
    result = assign_stop_and_target(play, lvl)
    assert result.get("_atr_target") is True, "Should trigger ATR gate"
    # Crypto multiplier = 3.0 → 100 + (5 * 3) = 115
    assert result["target"] == 115.0, f"Expected 115.0, got {result['target']}"


def test_atr_gate_skips_when_price_below_resistance():
    from core.enrichment import assign_stop_and_target
    play = {"direction": "LONG", "ticker": "TSLA"}
    lvl = {
        "current": 90.0,
        "resistance": 100.0,    # price well below resistance
        "long_stop": 80.0,
        "target_long": 101.5,
        "atr14": 3.0,
        "short_stop": None, "target_short": None,
    }
    result = assign_stop_and_target(play, lvl)
    assert result.get("_atr_target") is not True
    assert result["target"] == 101.5  # base target preserved


def test_counter_trend_short_gate_downgrades_high_to_med():
    """Fix 2: SHORT generated on bullish stack → HIGH→MED, leverage 1x."""
    from core.enrichment import apply_counter_trend_gate
    play = {
        "direction": "SHORT", "conviction": "HIGH",
        "ma9_above": True, "ma20_above": True, "macd_bull": True,
        "leverage": "5x",
    }
    result = apply_counter_trend_gate(play)
    assert result["_counter_trend"] is True
    assert result["conviction"] == "MED"
    assert result["leverage"] == "1x"


def test_counter_trend_skips_when_short_aligned_with_bearish_stack():
    from core.enrichment import apply_counter_trend_gate
    play = {
        "direction": "SHORT", "conviction": "HIGH",
        "ma9_above": False, "ma20_above": False, "macd_bull": False,
        "leverage": "5x",
    }
    result = apply_counter_trend_gate(play)
    assert "_counter_trend" not in result
    assert result["conviction"] == "HIGH"
    assert result["leverage"] == "5x"


def test_zone_width_capped_at_10pct():
    """Fix 3: zone width > 10% of current → tightened to 10%, centered on midpoint."""
    from core.enrichment import _cap_zone_width
    # Current $100, zone $50-$200 → 150% wide → must tighten to $10 around midpoint $125
    new_zone, capped = _cap_zone_width([50.0, 200.0], 100.0)
    assert capped is True
    assert new_zone == [120.0, 130.0]


def test_zone_width_preserved_when_under_cap():
    from core.enrichment import _cap_zone_width
    # Current $100, zone $94-$98 → 4% wide → preserved
    new_zone, capped = _cap_zone_width([94.0, 98.0], 100.0)
    assert capped is False
    assert new_zone == [94.0, 98.0]


def test_posture_correction_in_zone_with_ma20_up():
    """Fix 4: WATCHING posture corrected to ACCUMULATING when price in-zone + MA20 up."""
    from core.enrichment import _resolve_posture
    play = {"posture": "WATCHING", "accumulate_zone": [90.0, 100.0], "ma20_above": True}
    lvl  = {"current": 95.0}
    posture, flags = _resolve_posture(play, lvl, watched_entry={})
    assert posture == "ACCUMULATING"
    assert flags.get("_posture_corrected") is True


def test_user_posture_override_wins():
    from core.enrichment import _resolve_posture
    play = {"posture": "ACCUMULATING", "accumulate_zone": [90.0, 100.0], "ma20_above": True}
    lvl  = {"current": 95.0}
    posture, flags = _resolve_posture(play, lvl, watched_entry={"posture_override": "PAUSED"})
    assert posture == "PAUSED"
    assert flags.get("_posture_overridden") is True


def test_rr_quality_gate_flags_high_conviction_low_rr():
    from core.enrichment import compute_rr
    play = {"current": 100.0, "stop": 95.0, "target": 102.0,  # R/R = 0.4
            "direction": "LONG", "conviction": "HIGH"}
    result = compute_rr(play)
    assert result["rr_flagged"] is True
    assert result["rr"] == "1:0.4"


def test_rr_verify_at_extreme_value():
    from core.enrichment import compute_rr
    play = {"current": 100.0, "stop": 99.9, "target": 200.0,  # R/R ~ 1000
            "direction": "LONG", "conviction": "HIGH"}
    result = compute_rr(play)
    assert result["rr"] == "VERIFY ⚠"
    assert result["rr_flagged"] is True


def test_leverage_table_high_conv_boosted_crypto():
    from core.enrichment import assign_leverage
    play = {"conviction": "HIGH", "rr": "1:2.5", "ticker": "BTC"}
    result = assign_leverage(play)
    assert result["leverage"] == "10x"


def test_leverage_table_med_conv_equity():
    from core.enrichment import assign_leverage
    play = {"conviction": "MED", "rr": "1:1.5", "ticker": "TSLA"}
    result = assign_leverage(play)
    assert result["leverage"] == "2x"


def test_leverage_verify_blocks_all_leverage():
    from core.enrichment import assign_leverage
    play = {"conviction": "HIGH", "rr": "VERIFY ⚠", "ticker": "BTC"}
    result = assign_leverage(play)
    assert result["leverage"] == "NO LEVERAGE"


def test_setup_score_max():
    from core.enrichment import compute_setup_score
    play = {
        "direction": "LONG", "regime": "STRONG_UPTREND",
        "setup": "BREAK_AND_RETEST", "conviction": "HIGH",
        "rr": "1:3.0",
        "ma9": 110.0, "ma20": 105.0, "ma200": 100.0,
    }
    score = compute_setup_score(play)
    assert score == 10  # 3 + 3 + 2 + 1 + 1


def test_setup_score_zero_when_misaligned():
    from core.enrichment import compute_setup_score
    play = {
        "direction": "LONG", "regime": "STRONG_DOWNTREND",
        "setup": "NO_SETUP", "conviction": "MED",
        "rr": "1:0.8",
        "ma9": 90.0, "ma20": 100.0, "ma200": 110.0,  # bearish stack but LONG
    }
    score = compute_setup_score(play)
    assert score == 0


def test_json_array_extraction_handles_fences():
    from core.llm import parse_json_array
    raw = '```json\n[{"ticker": "BTC", "conviction": "HIGH"}]\n```'
    result = parse_json_array(raw)
    assert len(result) == 1
    assert result[0]["ticker"] == "BTC"


def test_json_array_extraction_handles_prose_wrapping():
    from core.llm import parse_json_array
    raw = 'Here is the analysis:\n[{"ticker": "SOL"}]\nLet me know if you need more.'
    result = parse_json_array(raw)
    assert len(result) == 1


def test_json_array_extraction_returns_empty_on_garbage():
    from core.llm import parse_json_array
    assert parse_json_array("complete nonsense", "test") == []


# ── Indicator computation tests (no yfinance — synthetic data) ────────────
def test_levels_from_synthetic_history():
    import pandas as pd
    import numpy as np
    from core.market_data import levels_from_history

    # 250 days of slowly rising synthetic data
    n = 250
    close = pd.Series(100 + np.cumsum(np.random.RandomState(42).randn(n) * 0.5))
    high  = close + 1
    low   = close - 1
    vol   = pd.Series([1_000_000] * n)
    idx   = pd.date_range("2025-01-01", periods=n)
    hist  = pd.DataFrame(
        {"Close": close.values, "High": high.values, "Low": low.values, "Volume": vol.values},
        index=idx
    )

    lvl = levels_from_history(hist)
    assert "current" in lvl
    assert "support" in lvl
    assert "resistance" in lvl
    assert "ma9" in lvl
    assert "ma20" in lvl
    assert "ma200" in lvl
    assert "rsi" in lvl
    assert "atr14" in lvl
    assert "_df" in lvl
    assert lvl["resistance"] >= lvl["support"]



def test_posture_forced_watching_when_price_above_zone():
    from core.enrichment import _resolve_posture
    play = {"posture": "ACCUMULATING", "accumulate_zone": [90.0, 100.0], "ma20_above": True}
    lvl  = {"current": 108.0}
    posture, flags = _resolve_posture(play, lvl, watched_entry={})
    assert posture == "WATCHING"
    assert flags.get("_posture_corrected") is True
if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"✓ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {t.__name__}: unexpected {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
