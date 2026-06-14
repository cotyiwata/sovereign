"""
core/schema.py — Pydantic v2 contracts for all Sovereign state files.

Validates on load; raises ValidationError immediately on contract break
instead of letting bad data corrupt downstream nodes silently.

Loaders:
    load_context(path)      -> ContextJSON
    load_trade_log(path)    -> List[TradeEntry]
    load_plays_sidecar(path) -> PlaysSidecar
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# context.json
# ---------------------------------------------------------------------------

class CryptoAsset(BaseModel):
    price: str
    change_24h: str
    change_pct: float
    market_cap: str
    display_mode: Literal["price"]


class PriceAsset(BaseModel):
    """Equity / ETF asset with live price (SPY, TSLA)."""
    price: str
    change_24h: str
    change_pct: float
    flagged: bool = False
    display_mode: Literal["price"]


class SignalAsset(BaseModel):
    """Macro signal without a raw price (Gold, DXY, TLT, Oil)."""
    trend: str
    direction: str
    change_pct: float
    signal: str
    display_mode: Literal["signal"]
    price: Optional[float] = None  # ETF price for validation only — not rendered


class CryptoMarket(BaseModel):
    """crypto block — three named assets + provenance fields."""
    BTC: CryptoAsset
    ETH: CryptoAsset
    SOL: CryptoAsset
    source: str
    status: str


class CoreMarket(BaseModel):
    """core block — two price assets + four signal assets."""
    SPY: PriceAsset
    TSLA: PriceAsset
    Gold: SignalAsset
    DXY: SignalAsset
    TLT: SignalAsset
    Oil: SignalAsset


class FearGreed(BaseModel):
    value: int = Field(..., ge=0, le=100)
    classification: str
    delta_24h: str
    reading: str
    source: str
    status: str


class GlobalMeta(BaseModel):
    total_market_cap: str
    total_volume_24h: str
    btc_dominance: str
    eth_dominance: str
    market_cap_change_24h: str
    status: str


class MacroRegime(BaseModel):
    fed: str
    inflation: str
    labor: str
    display: str
    fed_headline: str = ""
    source: str
    status: str


class Truflation(BaseModel):
    rate: Optional[float] = None
    label: Optional[str] = None
    status: str


class MarketBlock(BaseModel):
    crypto: CryptoMarket
    core: CoreMarket
    fear_greed: FearGreed
    global_meta: GlobalMeta
    macro_regime: MacroRegime
    truflation: Truflation
    intraday: Dict[str, Any] = {}


class EquityAsset(BaseModel):
    price: str
    change_24h: str
    change_pct: float
    status: str
    flagged: bool = False


class ContextJSON(BaseModel):
    """Top-level contract for Output/context.json."""
    timestamp: str
    harvest_duration_s: float
    market: MarketBlock
    # equities is Dict[section_name, Dict[ticker, EquityAsset]]
    equities: Dict[str, Dict[str, EquityAsset]]
    # Written by daily_ideas.py after Node 3 runs; absent on first daily run
    daily_posture: Optional[str] = None
    # P0 enrichment — per-ticker extended data keyed by display ticker name
    enriched: Dict[str, Any] = {}
    # P4 calendar alerts — populated by n01_scout from config weekly_calendar
    calendar_alerts: List[Any] = []
    # P2 session heat — computed by n08_plays from open trade_log positions
    session_heat: Optional[Any] = None


# ---------------------------------------------------------------------------
# trade_log.json
# ---------------------------------------------------------------------------

_CONVICTION = Literal["HIGH", "MED"]
_SECTION    = Literal["CRYPTO", "AI & SEMIS", "AI ENERGY NEXUS", "MACRO"]
_TIMEFRAME  = Literal[
    "DAY TRADE · <24hr",
    "SWING · 3-7 days",
    "SWING · 1-2 weeks",
    "POSITION · 1-3 months",
]


class TradeEntry(BaseModel):
    """One entry in trade_log.json."""
    id: str                              # "YYYY-MM-DD-TICKER"
    date: str                            # "YYYY-MM-DD"
    ticker: str
    section: str                         # loose — new sections get added
    conviction: _CONVICTION
    entry_price: float
    support: Optional[float] = None
    resistance: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    rr: Optional[str] = None            # string ratio e.g. "1:2.1"
    timeframe: Optional[str] = None
    why_now: Optional[str] = None
    setup: Optional[str] = None
    taken: Optional[bool] = None
    taken_date: Optional[str] = None
    close_date: Optional[str] = None
    outcome_pct: Optional[float] = None
    notes: str = ""

    @field_validator("rr")
    @classmethod
    def rr_format(cls, v: Optional[str]) -> Optional[str]:
        """Catch accidental float coercion — rr must stay a string ratio."""
        if v is not None and ":" not in v:
            raise ValueError(f"rr must be a ratio string like '1:2.1', got: {v!r}")
        return v

    @field_validator("date")
    @classmethod
    def date_format(cls, v: str) -> str:
        from datetime import date as _date
        try:
            _date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"date must be YYYY-MM-DD, got: {v!r}")
        return v


# ---------------------------------------------------------------------------
# plays sidecar  (Plays_*.json)
# ---------------------------------------------------------------------------

_DIRECTION  = Literal["LONG", "SHORT"]
_PLAY_CONV  = Literal["HIGH", "MED", "R/R ADJUSTED", "VERIFY ⚠"]
_REGIME     = Literal["CHOPPY", "STRONG_UPTREND", "STRONG_DOWNTREND", "CONSOLIDATING"]

# All known validation gate names — new gates must be added here
_GATE_NAMES = {
    "SAME_DAY_REENTRY",
    "CONTRARIAN_FEAR",
    "SHORTING_GREED",
    "POSTURE_DRIFT",
    "RATE_HEADWIND",
    "DOLLAR_STRENGTH",
    "COUNTER_TREND_SHORT",
}


class PlayCard(BaseModel):
    """One active play card in the plays sidecar."""
    ticker: str
    conviction: _PLAY_CONV
    direction: _DIRECTION
    why_now: str
    setup: str
    watch: str
    timeframe: str
    narrative: str
    section: str
    current: float
    support: float
    resistance: float
    ma9: float
    ma20: float
    ma200: float
    rsi: float = Field(..., ge=0, le=100)
    macd_bull: bool
    rel_vol: float = Field(..., ge=0)
    ma9_above: bool
    ma20_above: bool
    ma200_above: bool
    regime: str
    target: float
    stop: float
    rr: str
    rr_flagged: bool = False
    leverage: str
    flags: List[str] = []
    # Optional fields added by specific gates / posture logic
    posture: Optional[str] = None
    accumulate_zone: Optional[List[float]] = None
    watch_zone: Optional[List[float]] = None
    # P1 — deterministic setup scoring
    setup_score: int = Field(0, ge=0, le=10)
    setup_type: str = "NO_SETUP"
    # P3 — decision table fields
    entry_condition: str = ""
    upgrade_condition: str = ""
    time_gate: str = "SWING"          # DAY_TRADE | SWING | POSITION
    earnings_warning: bool = False
    heat_contribution: float = 0.0
    # Session C — two-tier execution fields
    watch_for: str = ""           # deterministic handoff trigger
    wary_of: str = ""             # deterministic invalidation condition
    tier: str = "SWING"           # SWING | DAY_TRADE

    @field_validator("flags")
    @classmethod
    def flags_are_known_gates(cls, v: List[str]) -> List[str]:
        unknown = set(v) - _GATE_NAMES
        if unknown:
            raise ValueError(
                f"Unknown gate flag(s): {unknown}. "
                f"Add to _GATE_NAMES in schema.py if intentional."
            )
        return v

    @field_validator("rr")
    @classmethod
    def rr_format(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError(f"rr must be a ratio string like '1:2.1', got: {v!r}")
        return v

    @model_validator(mode="after")
    def stop_direction_consistency(self) -> "PlayCard":
        """Stop must be below entry for LONG, above for SHORT."""
        if self.direction == "LONG" and self.stop >= self.current:
            raise ValueError(
                f"LONG stop {self.stop} must be below current price {self.current}"
            )
        if self.direction == "SHORT" and self.stop <= self.current:
            raise ValueError(
                f"SHORT stop {self.stop} must be above current price {self.current}"
            )
        return self


class PlaysSidecar(BaseModel):
    """Top-level contract for Plays_*.json."""
    generated: str
    model: str
    version: str
    actives: List[PlayCard]
    day_trades: List[PlayCard] = []




# ---------------------------------------------------------------------------
# P0/P1/P2/P4 new models — Session 66
# ---------------------------------------------------------------------------

class EnrichedTicker(BaseModel):
    """Extended per-ticker enrichment from market_data.py P0 layer."""
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    pdc: Optional[float] = None
    pm_high: Optional[float] = None
    pm_low: Optional[float] = None
    vwap: Optional[float] = None
    weekly_ma20: Optional[float] = None
    weekly_ma50: Optional[float] = None
    bbw: Optional[float] = None
    bbw_20p_low: Optional[bool] = None
    rs_vs_spy: Optional[float] = None
    earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    high_52w: Optional[float] = None
    dist_52w_high_pct: Optional[float] = None
    setup_type: Optional[str] = None
    setup_score: Optional[int] = Field(None, ge=0, le=10)


class CalendarEvent(BaseModel):
    """Economic calendar event. Owner maintains in config.yaml weekly_calendar."""
    date: str            # "YYYY-MM-DD"
    time: str            # "HH:MM ET"
    name: str
    prior: Optional[str] = None
    consensus: Optional[str] = None
    reaction_chain: Optional[str] = None  # plain-text chain, e.g. "DXY↑ TLT↓ BTC↓ if hot"


class SessionHeat(BaseModel):
    """Portfolio heat summary. Computed from open trade_log positions."""
    total_risk_pct: float = 0.0
    heat_rating: Literal["GREEN", "AMBER", "RED"] = "GREEN"
    corr_flags: List[str] = []


# ---------------------------------------------------------------------------
# Loaders — the public API
# ---------------------------------------------------------------------------

def load_context(path: Union[str, Path]) -> ContextJSON:
    """Load and validate Output/context.json. Raises ValidationError on breach."""
    with open(path) as f:
        data = json.load(f)
    return ContextJSON.model_validate(data)


def load_trade_log(path: Union[str, Path]) -> List[TradeEntry]:
    """Load and validate trade_log.json. Raises ValidationError on breach."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"trade_log.json must be a JSON array, got {type(data)}")
    return [TradeEntry.model_validate(entry) for entry in data]


def load_plays_sidecar(path: Union[str, Path]) -> PlaysSidecar:
    """Load and validate the most recent Plays_*.json sidecar."""
    with open(path) as f:
        data = json.load(f)
    return PlaysSidecar.model_validate(data)


def latest_plays_sidecar(briefs_dir: Union[str, Path]) -> Optional[PlaysSidecar]:
    """Convenience: find and load the most recent sidecar in Daily-Briefs/."""
    briefs_dir = Path(briefs_dir)
    sidecars = sorted(briefs_dir.glob("Plays_*.json"), reverse=True)
    if not sidecars:
        return None
    return load_plays_sidecar(sidecars[0])
