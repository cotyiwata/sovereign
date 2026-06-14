"""
tests/test_schema.py — Contract tests for core/schema.py.

Runs entirely offline — no yfinance, no Ollama, no ChromaDB.
All fixtures are copied from real output observed on 2026-04-26.

Run: python3 -m pytest tests/test_schema.py -v
"""

import pytest
from pydantic import ValidationError

# Adjust import path depending on where pytest is run from
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.schema import (
    ContextJSON, TradeEntry, PlayCard, PlaysSidecar,
    load_context, load_trade_log, load_plays_sidecar,
)


# ---------------------------------------------------------------------------
# Fixtures — real shapes from 2026-04-26 outputs
# ---------------------------------------------------------------------------

CONTEXT_FIXTURE: dict = {
    "timestamp": "2026-04-26 22:13",
    "harvest_duration_s": 29.13,
    "market": {
        "crypto": {
            "BTC": {"price": "$78,677.00", "change_24h": "▲ 0.83%",
                    "change_pct": 0.826, "market_cap": "$1576.8B", "display_mode": "price"},
            "ETH": {"price": "$2,368.88", "change_24h": "▲ 1.85%",
                    "change_pct": 1.853, "market_cap": "$286.7B", "display_mode": "price"},
            "SOL": {"price": "$87.09",   "change_24h": "▲ 0.67%",
                    "change_pct": 0.669, "market_cap": "$50.2B",  "display_mode": "price"},
            "source": "CoinGecko",
            "status": "live",
        },
        "core": {
            "SPY":  {"price": "$713.94", "change_24h": "▲ 0.00%", "change_pct": 0.0,
                     "flagged": False, "display_mode": "price"},
            "TSLA": {"price": "$376.30", "change_24h": "▲ 0.00%", "change_pct": 0.0,
                     "flagged": False, "display_mode": "price"},
            "Gold": {"trend": "neutral", "direction": "▲", "change_pct": 0.0,
                     "signal": "▲ neutral (+0.00%)", "display_mode": "signal"},
            "DXY":  {"trend": "neutral", "direction": "▼", "change_pct": -0.05,
                     "signal": "▼ neutral (-0.05%)", "display_mode": "signal"},
            "TLT":  {"trend": "neutral", "direction": "▲", "change_pct": 0.0,
                     "signal": "▲ neutral (+0.00%)", "display_mode": "signal"},
            "Oil":  {"trend": "neutral", "direction": "▲", "change_pct": 0.0,
                     "signal": "▲ neutral (+0.00%)", "display_mode": "signal"},
        },
        "fear_greed": {
            "value": 47, "classification": "Neutral", "delta_24h": "+14",
            "reading": "47 (Neutral) | Δ24h: +14", "source": "Alternative.me", "status": "live",
        },
        "global_meta": {
            "total_market_cap": "$2.71T", "total_volume_24h": "$71.2B",
            "btc_dominance": "58.2%", "eth_dominance": "10.6%",
            "market_cap_change_24h": "+1.11%", "status": "live",
        },
        "macro_regime": {
            "fed": "HOLD", "inflation": "cooling", "labor": "tight",
            "display": "Fed: HOLD | Inflation: cooling | Labor: tight",
            "fed_headline": "", "source": "manual_override", "status": "live",
        },
        "truflation": {"rate": 3.29, "label": "CPI 3.29% YoY (2026-03-01)", "status": "live"},
    },
    "equities": {
        "semiconductors": {
            "MU":   {"price": "$496.72", "change_24h": "▲ 3.11%", "change_pct": 3.11,
                     "status": "live", "flagged": True},
            "AVGO": {"price": "$422.76", "change_24h": "▲ 0.67%", "change_pct": 0.67,
                     "status": "live", "flagged": False},
        },
        "ai_energy_nexus": {
            "VST": {"price": "$164.35", "change_24h": "▲ 4.78%", "change_pct": 4.78,
                    "status": "live", "flagged": True},
        },
    },
    "daily_posture": "Watch",
}

TRADE_ENTRY_FIXTURE: dict = {
    "id": "2026-04-24-SOL",
    "date": "2026-04-24",
    "ticker": "SOL",
    "section": "CRYPTO",
    "conviction": "HIGH",
    "entry_price": 85.44,
    "support": 78.43,
    "resistance": 90.67,
    "stop": 82.23,
    "target": 92.03,
    "rr": "1:2.1",
    "timeframe": "DAY TRADE · <24hr",
    "why_now": "Falling DXY and stable TLT create a favorable macro environment.",
    "setup": "NO_SETUP",
    "taken": True,
    "taken_date": "2026-04-24 00:40",
    "close_date": None,
    "outcome_pct": None,
    "notes": "",
}

PLAY_CARD_FIXTURE: dict = {
    "ticker": "SOL",
    "conviction": "HIGH",
    "direction": "LONG",
    "why_now": "Dollar weakness and stable rates are boosting crypto sentiment.",
    "setup": "NO_SETUP",
    "watch": "A drop below $83.36 would negate the trade.",
    "timeframe": "DAY TRADE · <24hr",
    "narrative": "SOL oversold on RSI 60.5 with fresh MACD bull cross.",
    "section": "CRYPTO",
    "current": 86.42,
    "support": 78.43,
    "resistance": 90.67,
    "ma9": 85.89,
    "ma20": 85.45,
    "ma200": 121.29,
    "rsi": 60.5,
    "macd_bull": True,
    "rel_vol": 0.7,
    "ma9_above": True,
    "ma20_above": True,
    "ma200_above": False,
    "regime": "CHOPPY",
    "target": 92.03,
    "stop": 83.36,
    "rr": "1:1.8",
    "leverage": "5x",
    "flags": ["POSTURE_DRIFT"],
}

PLAYS_SIDECAR_FIXTURE: dict = {
    "generated": "2026-04-26 10:15 PM",
    "model": "gemma3:12b",
    "version": "2.7.1",
    "actives": [PLAY_CARD_FIXTURE],
}


# ---------------------------------------------------------------------------
# context.json tests
# ---------------------------------------------------------------------------

class TestContextJSON:
    def test_valid_fixture_parses(self):
        ctx = ContextJSON.model_validate(CONTEXT_FIXTURE)
        assert ctx.market.crypto.BTC.change_pct == pytest.approx(0.826)
        assert ctx.market.fear_greed.value == 47
        assert ctx.market.macro_regime.fed == "HOLD"
        assert ctx.market.truflation.rate == pytest.approx(3.29)
        assert ctx.daily_posture == "Watch"

    def test_daily_posture_optional(self):
        data = {**CONTEXT_FIXTURE}
        del data["daily_posture"]
        ctx = ContextJSON.model_validate(data)
        assert ctx.daily_posture is None

    def test_missing_btc_raises(self):
        data = {**CONTEXT_FIXTURE}
        crypto = {**data["market"]["crypto"]}
        del crypto["BTC"]
        data = {**data, "market": {**data["market"], "crypto": crypto}}
        with pytest.raises(ValidationError, match="BTC"):
            ContextJSON.model_validate(data)

    def test_fear_greed_out_of_range_raises(self):
        data = {**CONTEXT_FIXTURE}
        fg = {**data["market"]["fear_greed"], "value": 150}
        data = {**data, "market": {**data["market"], "fear_greed": fg}}
        with pytest.raises(ValidationError):
            ContextJSON.model_validate(data)

    def test_equity_section_parsed(self):
        ctx = ContextJSON.model_validate(CONTEXT_FIXTURE)
        assert ctx.equities["semiconductors"]["MU"].flagged is True
        assert ctx.equities["ai_energy_nexus"]["VST"].change_pct == pytest.approx(4.78)

    def test_signal_asset_no_price_field(self):
        ctx = ContextJSON.model_validate(CONTEXT_FIXTURE)
        dxy = ctx.market.core.DXY
        assert dxy.trend == "neutral"
        assert dxy.change_pct == pytest.approx(-0.05)
        assert not hasattr(dxy, "price") or dxy.__class__.__name__ == "SignalAsset"


# ---------------------------------------------------------------------------
# trade_log.json tests
# ---------------------------------------------------------------------------

class TestTradeEntry:
    def test_valid_entry_parses(self):
        entry = TradeEntry.model_validate(TRADE_ENTRY_FIXTURE)
        assert entry.ticker == "SOL"
        assert entry.conviction == "HIGH"
        assert entry.taken is True
        assert entry.rr == "1:2.1"

    def test_taken_none_allowed(self):
        data = {**TRADE_ENTRY_FIXTURE, "taken": None, "taken_date": None}
        entry = TradeEntry.model_validate(data)
        assert entry.taken is None

    def test_rr_must_be_ratio_string(self):
        data = {**TRADE_ENTRY_FIXTURE, "rr": 2.1}
        with pytest.raises(ValidationError):
            TradeEntry.model_validate(data)

    def test_rr_none_allowed(self):
        data = {**TRADE_ENTRY_FIXTURE, "rr": None}
        entry = TradeEntry.model_validate(data)
        assert entry.rr is None

    def test_date_format_enforced(self):
        data = {**TRADE_ENTRY_FIXTURE, "date": "April 24 2026"}
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            TradeEntry.model_validate(data)

    def test_invalid_conviction_raises(self):
        data = {**TRADE_ENTRY_FIXTURE, "conviction": "LOW"}
        with pytest.raises(ValidationError):
            TradeEntry.model_validate(data)

    def test_all_optional_fields_none(self):
        minimal = {
            "id": "2026-04-26-BTC", "date": "2026-04-26",
            "ticker": "BTC", "section": "CRYPTO",
            "conviction": "MED", "entry_price": 78000.0,
        }
        entry = TradeEntry.model_validate(minimal)
        assert entry.stop is None
        assert entry.outcome_pct is None
        assert entry.notes == ""


# ---------------------------------------------------------------------------
# plays sidecar tests
# ---------------------------------------------------------------------------

class TestPlayCard:
    def test_valid_card_parses(self):
        card = PlayCard.model_validate(PLAY_CARD_FIXTURE)
        assert card.ticker == "SOL"
        assert card.direction == "LONG"
        assert "POSTURE_DRIFT" in card.flags

    def test_unknown_gate_flag_raises(self):
        data = {**PLAY_CARD_FIXTURE, "flags": ["MYSTERY_GATE"]}
        with pytest.raises(ValidationError, match="Unknown gate flag"):
            PlayCard.model_validate(data)

    def test_long_stop_above_current_raises(self):
        data = {**PLAY_CARD_FIXTURE, "stop": 95.0}  # above current 86.42
        with pytest.raises(ValidationError, match="below current price"):
            PlayCard.model_validate(data)

    def test_short_stop_below_current_raises(self):
        short_card = {
            **PLAY_CARD_FIXTURE,
            "direction": "SHORT",
            "stop": 80.0,   # below current 86.42 — invalid for SHORT
        }
        with pytest.raises(ValidationError, match="above current price"):
            PlayCard.model_validate(short_card)

    def test_short_stop_above_current_valid(self):
        short_card = {
            **PLAY_CARD_FIXTURE,
            "direction": "SHORT",
            "stop": 92.0,   # above current 86.42 — valid
        }
        card = PlayCard.model_validate(short_card)
        assert card.direction == "SHORT"

    def test_rsi_out_of_range_raises(self):
        data = {**PLAY_CARD_FIXTURE, "rsi": 110.0}
        with pytest.raises(ValidationError):
            PlayCard.model_validate(data)

    def test_rr_float_raises(self):
        data = {**PLAY_CARD_FIXTURE, "rr": 1.8}
        with pytest.raises(ValidationError):
            PlayCard.model_validate(data)

    def test_optional_zone_fields_absent(self):
        card = PlayCard.model_validate(PLAY_CARD_FIXTURE)
        assert card.accumulate_zone is None
        assert card.watch_zone is None
        assert card.posture is None

    def test_flags_empty_list_default(self):
        data = {k: v for k, v in PLAY_CARD_FIXTURE.items() if k != "flags"}
        card = PlayCard.model_validate(data)
        assert card.flags == []


class TestPlaysSidecar:
    def test_full_sidecar_parses(self):
        sidecar = PlaysSidecar.model_validate(PLAYS_SIDECAR_FIXTURE)
        assert sidecar.version == "2.7.1"
        assert len(sidecar.actives) == 1
        assert sidecar.actives[0].ticker == "SOL"

    def test_empty_actives_allowed(self):
        data = {**PLAYS_SIDECAR_FIXTURE, "actives": []}
        sidecar = PlaysSidecar.model_validate(data)
        assert sidecar.actives == []
