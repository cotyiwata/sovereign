#!/usr/bin/env python3
"""
plays_html_renderer.py v2.8
Patch fixes on top of v2.7:
  Fix 1: ATR14 computed in compute_levels; enrich_actives uses ATR-based forward
          target when price is at/above 20d resistance (prevents R/R collapse on
          breakout plays). Card shows "ATR TARGET" badge when active.
  Fix 2: Counter-trend SHORT gate — when SHORT is generated on MA9↑ MA20↑ MACD
          bullish stack, conviction downgraded HIGH→MED, leverage capped 1x, and
          "⚠ COUNTER-TREND" badge rendered on card.
  Fix 3: SYSTEM_POSITION_WATCH hard rules — zone width 5-10% of current price max;
          posture MUST be ACCUMULATING when price is inside zone + MA20 up.
  Fix 4: enrich_position_watch code-level backstops — zone width auto-capped at 10%
          of current price; posture corrected WATCHING→ACCUMULATING when price is
          in-zone and MA20 is trending up (unless user posture_override set).
"""

import json, os, sys, re, requests, yaml
import yfinance as yf
import pandas as pd
from datetime import datetime
from analysis.regime import get_regime
from analysis.setup import get_setup
from core.market_data import compute_all_levels as _core_compute_all_levels
from core.enrichment import enrich_actives as _core_enrich_actives
from core.enrichment import enrich_position_watch as _core_enrich_position_watch
from core.llm import generate as _core_generate, parse_json_array as _core_parse_json_array
from core.schema import load_context as _schema_load_context
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
VAULT_ROOT          = Path(os.path.expanduser("~/sovereign"))
CONTEXT_PATH        = VAULT_ROOT / "Output" / "context.json"
WATCHED_LEVELS_PATH = VAULT_ROOT / "Data" / "watched_levels.yaml"
OUT_DIR             = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
OLLAMA_URL          = "http://localhost:11434"
MODEL               = "gemma3:12b"

# ── Universe — tight 8-ticker focus ────────────────────────────────────────
ACTIVES = ["BTC", "SOL", "ETH", "TSLA"]            # ETH is conditional
POSITION_WATCH = {
    "semis":  ["NVDA"],
    "energy": ["VST", "CEG", "VRT"],
}
EXPOSURE_ASSETS = ["BTC", "SOL", "ETH", "TSLA", "NVDA", "VST"]

# Display -> yfinance ticker
YF_TICKER = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "TSLA": "TSLA", "NVDA": "NVDA",
    "VST": "VST", "CEG": "CEG", "VRT": "VRT",
}

# ── Sector theses ──────────────────────────────────────────────────────────
SEMIS_THESIS = (
    "AI capex cycle is in durable expansion — hyperscaler spend compounding through 2026. "
    "NVDA remains the cleanest pure play on training + inference compute; customer concentration "
    "is the known risk, but the order book is visible and pricing power intact. "
    "Position framing: size up on drawdowns to major MAs, don't chase rips."
)

ENERGY_THESIS = (
    "AI compute demand is outpacing grid capacity. Nuclear is being repriced as baseload "
    "for hyperscaler PPAs. Uranium supply is constrained. The market is in early innings "
    "of treating energy infrastructure as AI infrastructure — multi-year structural trade "
    "with asymmetric upside in SMRs and grid buildout."
)

SECTOR_LABEL = {
    "semis":  "🖥 Semis — AI Compute",
    "energy": "⚡ AI Energy Nexus",
}
SECTOR_THESIS = {"semis": SEMIS_THESIS, "energy": ENERGY_THESIS}


# ── Loaders ────────────────────────────────────────────────────────────────
def load_json(path):
    """Generic JSON loader. Context path validated via schema; others raw."""
    from pathlib import Path as _Path
    if _Path(path).name.startswith("context"):
        return _schema_load_context(path).model_dump()
    with open(path) as f:
        return json.load(f)

def load_watched_levels() -> dict:
    """Load user-defined S/R + posture overrides. Returns {} on failure."""
    if not WATCHED_LEVELS_PATH.exists():
        print(f"  ⚠️  watched_levels.yaml not found at {WATCHED_LEVELS_PATH}")
        return {}
    try:
        with open(WATCHED_LEVELS_PATH) as f:
            data = yaml.safe_load(f) or {}
        # Strip non-ticker keys
        return {k: v for k, v in data.items()
                if isinstance(v, dict) and ("support" in v or "resistance" in v)}
    except Exception as e:
        print(f"  ⚠️  watched_levels.yaml parse failed: {e}")
        return {}

def ollama_generate_legacy(prompt: str, system: str, max_tokens: int = 1400) -> str:
    payload = {
        "model": MODEL,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.5, "num_predict": max_tokens},
        "keep_alive": "0"
    }
    r = requests.post(OLLAMA_URL + "/api/generate", json=payload, timeout=180)
    r.raise_for_status()
    return r.json().get("response", "").strip()


# ── Level computation (preserved from v2.6) ────────────────────────────────
def compute_levels(yf_ticker: str) -> dict:
    """
    Compute support/resistance + TA from 1y OHLC.
    Stop levels: 5-day swing low (LONG) / swing high (SHORT) ± 1.5%.
    """
    try:
        hist = yf.Ticker(yf_ticker).history(period="1y")
        if hist.empty or len(hist) < 10:
            return {}
        current      = float(hist["Close"].iloc[-1])
        support      = float(hist["Low"].rolling(20).min().dropna().iloc[-1])
        resistance   = float(hist["High"].rolling(20).max().dropna().iloc[-1])
        swing_low_5d = float(hist["Low"].rolling(5).min().dropna().iloc[-1])
        swing_hi_5d  = float(hist["High"].rolling(5).max().dropna().iloc[-1])

        ma9   = float(hist["Close"].rolling(9).mean().dropna().iloc[-1])   if len(hist) >= 9   else None
        ma20  = float(hist["Close"].rolling(20).mean().dropna().iloc[-1])  if len(hist) >= 20  else None
        ma200 = float(hist["Close"].rolling(200).mean().dropna().iloc[-1]) if len(hist) >= 200 else None

        # RSI 14
        delta = hist["Close"].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi_s = 100 - (100 / (1 + rs))
        rsi   = round(float(rsi_s.dropna().iloc[-1]), 1) if not rsi_s.dropna().empty else None

        # MACD 12/26/9
        ema12       = hist["Close"].ewm(span=12, adjust=False).mean()
        ema26       = hist["Close"].ewm(span=26, adjust=False).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_bull   = float(macd_line.iloc[-1]) > float(signal_line.iloc[-1])

        # Relative volume
        avg_vol  = float(hist["Volume"].rolling(20).mean().dropna().iloc[-1])
        last_vol = float(hist["Volume"].iloc[-1])
        rel_vol  = round(last_vol / avg_vol, 1) if avg_vol > 0 else None

        # ATR 14 — average true range
        high_low   = hist["High"] - hist["Low"]
        high_close = (hist["High"] - hist["Close"].shift()).abs()
        low_close  = (hist["Low"]  - hist["Close"].shift()).abs()
        tr         = high_low.combine(high_close, max).combine(low_close, max)
        atr14      = round(float(tr.rolling(14).mean().dropna().iloc[-1]), 2) if len(hist) >= 14 else None

        long_stop    = round(swing_low_5d * 0.985, 2)
        short_stop   = round(swing_hi_5d  * 1.015, 2)
        target_long  = round(resistance * 1.015, 2)
        target_short = round(support    * 0.985, 2)
        risk_l = current - long_stop
        rr_l   = round((target_long - current) / risk_l, 1) if risk_l > 0.01 else 0

        # Session 41: normalized DataFrame for regime_detector + setup_detector
        _df = pd.DataFrame({
            "close": hist["Close"].values,
            "high":  hist["High"].values,
            "low":   hist["Low"].values,
            "ma9":   hist["Close"].rolling(9).mean().values,
            "ma20":  hist["Close"].rolling(20).mean().values,
            "ma200": hist["Close"].rolling(200).mean().values,
            "atr":   tr.rolling(14).mean().values,
        }, index=hist.index)

        return {
            "_df":          _df,
            "current":      round(current, 2),
            "support":      round(support, 2),
            "resistance":   round(resistance, 2),
            "long_stop":    long_stop,
            "short_stop":   short_stop,
            "target_long":  target_long,
            "target_short": target_short,
            "atr14":        atr14,
            "stop":         long_stop,
            "target":       target_long,
            "rr":           f"1:{rr_l}" if rr_l > 0 else "N/A",
            "ma9":          round(ma9, 2)   if ma9   else None,
            "ma20":         round(ma20, 2)  if ma20  else None,
            "ma200":        round(ma200, 2) if ma200 else None,
            "rsi":          rsi,
            "macd_bull":    macd_bull,
            "rel_vol":      rel_vol,
        }
    except Exception as e:
        print(f"    ⚠️  levels({yf_ticker}): {e}")
        return {}

def compute_all_levels_legacy(display_tickers: list) -> dict:
    """Compute levels for a list of display names. Returns dict keyed by display."""
    results = {}
    for display in display_tickers:
        yft = YF_TICKER.get(display, display)
        lvl = compute_levels(yft)
        if lvl:
            results[display] = lvl
    return results

def levels_table(levels: dict) -> str:
    if not levels:
        return "  (levels unavailable)"
    lines = []
    for ticker, d in levels.items():
        lines.append(
            f"  {ticker}: price=${d['current']} support=${d['support']} "
            f"resistance=${d['resistance']} long_stop=${d['long_stop']} "
            f"short_stop=${d['short_stop']} "
            f"target_long=${d['target_long']} target_short=${d['target_short']}"
        )
    return "\n".join(lines)

def ta_snapshot_block(levels: dict) -> str:
    """Compact per-ticker TA summary for prompt injection."""
    lines = []
    for t, d in levels.items():
        if not d:
            continue
        rsi   = d.get("rsi", "?")
        macd  = "bull" if d.get("macd_bull") else "bear"
        vol   = d.get("rel_vol", "?")
        ma9_p = "above" if d.get("ma9")   and d.get("current", 0) > d["ma9"]   else "below"
        ma20_p= "above" if d.get("ma20")  and d.get("current", 0) > d["ma20"]  else "below"
        ma200_p="above" if d.get("ma200") and d.get("current", 0) > d["ma200"] else "below"
        lines.append(
            f"  {t}: RSI={rsi} MACD={macd} MA9={ma9_p} MA20={ma20_p} MA200={ma200_p} Vol={vol}x"
        )
    return "\n".join(lines) or "  (no TA data)"


# ── Market data extraction (preserved) ─────────────────────────────────────
def extract_pulse(ctx: dict) -> dict:
    m      = ctx.get("market", {})
    crypto = m.get("crypto", {})
    core   = m.get("core", {})
    fg     = m.get("fear_greed", {})
    macro  = m.get("macro_regime", {})
    return {
        "btc":  crypto.get("BTC", {}), "eth": crypto.get("ETH", {}),
        "sol":  crypto.get("SOL", {}), "spy": core.get("SPY", {}),
        "tsla": core.get("TSLA", {}),  "gold":core.get("Gold", {}),
        "dxy":  core.get("DXY", {}),   "tlt": core.get("TLT", {}),
        "oil":  core.get("Oil", {}),
        "fear_greed": fg, "macro": macro,
        "posture": ctx.get("daily_posture", ""),
    }

def compute_macro_risk_block(pulse: dict) -> dict:
    """Cross-section macro signal context. Preserved verbatim from v2.6."""
    def pct(key):
        try: return float(pulse.get(key, {}).get("change_pct", 0))
        except: return 0.0
    def trend(key):
        return pulse.get(key, {}).get("trend", "neutral")

    oil_pct  = pct("oil")
    dxy_up   = trend("dxy") == "rising"
    tlt_down = trend("tlt") == "falling"
    gold_up  = trend("gold") == "rising"

    if oil_pct > 1.0:
        oil_line = f"Oil surging ({oil_pct:+.1f}%) — energy input costs rising, watch margin pressure on semis/hyperscalers."
    elif oil_pct < -1.0:
        oil_line = f"Oil falling ({oil_pct:+.1f}%) — inflation relief, reduces energy cost headwind for growth names."
    else:
        oil_line = f"Oil flat ({oil_pct:+.1f}%) — no directional energy signal."

    dxy_line  = "DXY rising — dollar strength is a crypto headwind and suppresses commodity-linked names." if dxy_up \
                else "DXY falling or neutral — dollar weakness supports crypto and commodity upside."
    tlt_line  = "TLT falling — rates bid, growth multiple compression in play. High-multiple semis and tech at risk." if tlt_down \
                else "TLT stable or rising — rates not a headwind, growth multiples supported."
    gold_line = "Gold surging — risk-off confirmed. Cautious posture. Macro hedges favored over aggressive longs." if gold_up \
                else "Gold flat or falling — no risk-off signal from metals."

    return {
        "crypto":  f"MACRO CONTEXT: {dxy_line} {gold_line}",
        "semis":   f"MACRO CONTEXT: {tlt_line} {oil_line}",
        "energy":  f"MACRO CONTEXT: {oil_line} {tlt_line}",
        "macro":   f"MACRO CONTEXT: {gold_line} {dxy_line} {tlt_line}",
        "actives": f"MACRO CONTEXT: {dxy_line} {tlt_line} {gold_line}",
    }

def extract_energy_headlines(ctx: dict) -> list:
    headlines = []
    energy_kw = ["nuclear","smr","solar","grid","power","uranium","renewable",
                 "energy","electricity","ppa","nne","constellation","vistra",
                 "nextera","enphase","nuscale"]
    for cat, items in ctx.get("headlines", {}).items():
        if cat.lower() in ("energy", "macro_policy"):
            for h in items:
                t = h.get("title", "")
                if any(k in t.lower() for k in energy_kw):
                    headlines.append(t)
    return headlines[:6]


# ── System prompts ─────────────────────────────────────────────────────────
SYSTEM_ACTIVES = """You are a sharp market analyst writing day-trade-leaning setups for active positions.

For each play, output a JSON object. Return ONLY a valid JSON array — no prose, no markdown fences, no explanation outside the array.

Each object must have these exact keys:
  ticker      — one of: BTC, SOL, ETH, TSLA
  conviction  — "HIGH" or "MED" only
  direction   — "LONG" or "SHORT"
  why_now     — 1 sentence: what is happening right now that creates this setup
  setup       — 1 sentence: technical pattern or condition
  watch       — 1 sentence: what confirms or invalidates the trade
  timeframe   — default "DAY TRADE · <24hr". Use "SWING · 3-7 days" or "SWING · 1-2 weeks" only if Daily structure clearly calls for it.
  narrative   — 2-3 sentence trade story. MUST include: (a) current TA state with 1 specific indicator value (e.g. RSI 38, MACD cross), (b) exact entry condition with price level from the data, (c) target with reasoning (resistance, ATR extension, etc), (d) stop with structural reference. Use exact dollar prices from the levels provided. Example: "SOL oversold on RSI 38 with fresh MACD bull cross — entry on hold of $81.51, targeting $90.67 resistance on recovery momentum. Stop $76.82 if structure breaks." Never use vague phrases like 'near the swing stop' or 'approaching resistance' — use the actual numbers.

Rules:
- HIGH or MED conviction only
- BTC, SOL, TSLA are REQUIRED — must appear in output
- ETH is CONDITIONAL — include ONLY if a genuine setup is present. If no clean setup, omit entirely.
- DAY TRADE is the default lean. Only upgrade to SWING when the Daily chart clearly calls for it.
- SHORT on TSLA requires HIGH conviction
- Reference TA indicators (MAs, RSI, MACD, volume) in why_now and setup
- Output valid JSON array only"""

SYSTEM_POSITION_WATCH = """You are an analyst framing accumulation setups for long-term position trades.

For each ticker, output a JSON object. Return ONLY a valid JSON array — no prose, no markdown fences.

Each object has exactly:
  ticker          — string
  accumulate_zone — [low, high] numeric array, the price range where you would actively add
  timeframe_bias  — one of: "1-3 months" | "3-6 months" | "6+ months"
  outlook         — 2-3 sentences: why accumulate now, thesis state, what the next catalyst is
  posture         — one of: "ACCUMULATING" | "WATCHING" | "PAUSED" | "INVALIDATED"

Posture rules:
- ACCUMULATING: price is in or near the accumulate zone, TA supports adds, thesis intact
- WATCHING: price above accumulate zone, waiting for pullback, thesis intact
- PAUSED: near-term setup invalidated (broke MA200, rotation, earnings risk) but long-term thesis intact — no new adds now
- INVALIDATED: TA structurally broken AND/OR thesis under material threat — stop accumulating, re-evaluate

HARD RULES — these override everything:
- accumulate_zone width must be 5–10% of current price maximum. A 35% wide zone is useless — tighten to the highest-conviction sub-range.
  Example: if current=$300, zone must be at most $15–30 wide. Good: [$278,$295]. Bad: [$200,$300].
- ZONE TIGHTENING BY POSTURE:
  - ACCUMULATING: zone must be 3-5% wide, centered on the recent swing low or MA20. This is an active add zone — be precise.
  - WATCHING: zone can be 7-10% wide, spanning MA20 to nearest resistance. This is a re-entry planning range — wider is acceptable.
  - PAUSED/INVALIDATED: zone width does not matter — posture overrides action.
- POSTURE RULE: if current price is BETWEEN accumulate_zone[0] and accumulate_zone[1] AND MA20 is above price (or MA20 is trending up), posture MUST be ACCUMULATING. Do not use WATCHING when price is inside the zone.
- WATCHING is only valid when current price is ABOVE accumulate_zone[1].
- If USER-SET POSTURE OVERRIDE appears in the input, use that posture regardless.
- Output valid JSON array only"""

SYSTEM_EXPOSURE = """You are a portfolio exposure analyst generating a simple signal table.

Output a JSON array, one object per asset provided. Return ONLY the array — no prose, no markdown fences.

Each object has exactly:
  ticker — string (one of the provided tickers)
  signal — "INCREASE" | "HOLD" | "REDUCE"
  reason — one-line rationale, max 18 words

Signal rules:
- INCREASE: TA and macro aligned in favor of adding exposure
- HOLD: neutral, no clear edge either direction, or waiting for setup
- REDUCE: weakening technicals, macro headwind, or overextended
- One entry per ticker provided. No duplicates. No omissions.

POSTURE CONSISTENCY — if a ticker has a position watch posture, signal MUST align:
- ACCUMULATING → INCREASE
- WATCHING → HOLD
- PAUSED → HOLD or REDUCE
- INVALIDATED → REDUCE
- Output valid JSON array only"""


# ── Prompt builders ────────────────────────────────────────────────────────

SYSTEM_DAY_TRADE = """You are a day-trade analyst writing intraday setups. Execution is same-session; close before 3:45 PM PT.

For each play, output a JSON object. Return ONLY a valid JSON array — no prose, no markdown fences.

Each object must have exactly:
  ticker     — one of: BTC, SOL, ETH, TSLA
  conviction — "HIGH" or "MED" only
  direction  — "LONG" or "SHORT"
  why_now    — 1 sentence: what is happening on the 1H chart right now (VWAP or PDH/PDL reference required)
  setup      — 1 sentence: 1H pattern (VWAP reclaim, PDH break, compression, flag, etc)
  narrative  — 2-3 sentences: intraday trade story. MUST include VWAP level, PDH or PDL level, and direction rationale.
  watch      — 1 sentence: what confirms the setup on the 15m chart

Rules:
- BTC, SOL, TSLA REQUIRED. ETH CONDITIONAL — include only if clean 1H setup exists.
- HIGH conviction: confirmed break or VWAP reclaim with volume. MED: setup forming, not yet confirmed.
- Never use vague phrases. Use exact prices from the data provided.
- Output valid JSON array only"""

def build_actives_prompt(pulse: dict, levels: dict) -> str:
    actives_levels = {t: levels[t] for t in ACTIVES if t in levels}
    btc = pulse["btc"]; eth = pulse["eth"]; sol = pulse["sol"]; tsla = pulse["tsla"]
    fg = pulse["fear_greed"]
    macro_ctx = compute_macro_risk_block(pulse)["actives"]

    def fmt(v):
        try: return f"{float(v):,.2f}"
        except: return str(v)

    return f"""ACTIVES SNAPSHOT:
BTC:  ${fmt(btc.get('price','?'))}  | 24h: {btc.get('change_pct','?')}%
SOL:  ${fmt(sol.get('price','?'))}  | 24h: {sol.get('change_pct','?')}%
ETH:  ${fmt(eth.get('price','?'))}  | 24h: {eth.get('change_pct','?')}%
TSLA: ${fmt(tsla.get('price','?'))} | {tsla.get('change_pct','?')}%
Fear & Greed: {fg.get('value','?')} — {fg.get('classification','?')}
{macro_ctx}

KEY LEVELS (60-day rolling, 5-day swing stops):
{levels_table(actives_levels)}

TA SNAPSHOT:
{ta_snapshot_block(actives_levels)}

Day-trade lean default. BTC, SOL, TSLA are REQUIRED — include all three.
ETH is CONDITIONAL — include only if a clean setup is present, otherwise omit.
Use SWING timeframes only when the Daily chart clearly calls for it.
Return JSON array only."""



def build_day_trade_prompt(pulse: dict, intraday_data: dict) -> str:
    """Prompt for DAY TRADE tier — uses 1H intraday data from context.json."""
    btc  = pulse["btc"]; eth = pulse["eth"]
    sol  = pulse["sol"]; tsla = pulse["tsla"]
    fg   = pulse["fear_greed"]
    macro_ctx = compute_macro_risk_block(pulse)["actives"]

    def fmt(v):
        try: return f"{float(v):,.2f}"
        except: return str(v)

    def intraday_block(display, daily, yft):
        d = intraday_data.get(yft, {})
        lines = [f"{display}: price=${fmt(daily.get('price','?'))}  24h:{daily.get('change_pct','?')}%"]
        if d.get("vwap"):     lines.append(f"  VWAP=${fmt(d['vwap'])}")
        if d.get("pdh"):      lines.append(f"  PDH=${fmt(d['pdh'])}  PDL=${fmt(d.get('pdl',0))}")
        if d.get("atr14"):    lines.append(f"  ATR14(1H)=${fmt(d['atr14'])}")
        if d.get("support"):  lines.append(f"  1H S=${fmt(d['support'])}  R=${fmt(d.get('resistance',0))}")
        return "\n".join(lines)

    return f"""INTRADAY SNAPSHOT (1H data):
{intraday_block("BTC",  btc,  "BTC-USD")}
{intraday_block("SOL",  sol,  "SOL-USD")}
{intraday_block("ETH",  eth,  "ETH-USD")}
{intraday_block("TSLA", tsla, "TSLA")}

{macro_ctx}
Fear & Greed: {fg.get('value','?')} — {fg.get('classification','?')}

BTC, SOL, TSLA REQUIRED. ETH CONDITIONAL.
Same-session hold only. Return JSON array only."""

def build_position_watch_prompt(pulse: dict, levels: dict, watched: dict, sector: str) -> str:
    tickers = POSITION_WATCH[sector]
    sector_levels = {t: levels[t] for t in tickers if t in levels}
    thesis = SECTOR_THESIS[sector]
    macro_ctx = compute_macro_risk_block(pulse)[sector]

    # User-defined S/R injection
    user_levels_block = []
    override_block    = []
    for t in tickers:
        w = watched.get(t, {})
        sup = w.get("support") or []
        res = w.get("resistance") or []
        notes = w.get("notes", "")
        if sup or res or notes:
            user_levels_block.append(
                f"  {t}: support={sup or '[]'} resistance={res or '[]'}"
                + (f" notes=\"{notes}\"" if notes else "")
            )
        po = w.get("posture_override")
        if po and po in ("ACCUMULATING", "WATCHING", "PAUSED", "INVALIDATED"):
            override_block.append(f"  {t}: USER-SET POSTURE OVERRIDE = {po}")

    user_levels_str = "\n".join(user_levels_block) if user_levels_block else "  (none set)"
    override_str    = "\n".join(override_block) if override_block else ""

    override_section = f"""
POSTURE OVERRIDES (respect these — frame outlook accordingly):
{override_str}
""" if override_str else ""

    return f"""POSITION WATCH — {sector.upper()}

SECTOR THESIS:
{thesis}

{macro_ctx}

TICKERS: {', '.join(tickers)}

COMPUTED LEVELS (60-day rolling):
{levels_table(sector_levels)}

TA SNAPSHOT:
{ta_snapshot_block(sector_levels)}

USER-DEFINED S/R (from watched_levels.yaml — prioritize these over computed):
{user_levels_str}
{override_section}
For each ticker, return accumulate_zone, timeframe_bias (1-3m / 3-6m / 6+m), outlook (2-3 sentences), and posture.
Reference TA and user-defined levels when framing accumulate_zone.
Return JSON array only."""


def build_exposure_signals_prompt(pulse: dict, levels: dict, position_watch_by_sector: dict = None) -> str:
    assets_levels = {t: levels[t] for t in EXPOSURE_ASSETS if t in levels}
    macro_ctx = compute_macro_risk_block(pulse)
    fg = pulse["fear_greed"]

    # Build posture context from position watch
    posture_lines = []
    if position_watch_by_sector:
        for sector_plays in position_watch_by_sector.values():
            for p in sector_plays:
                t = p.get("ticker", "")
                posture = p.get("posture", "")
                if t and posture:
                    posture_lines.append(f"  {t}: {posture}")
    posture_block = "\n".join(posture_lines) if posture_lines else "  (none)"

    return f"""EXPOSURE SIGNALS — generate one row per asset.

ASSETS: {', '.join(EXPOSURE_ASSETS)}

MACRO CONDITIONS:
{macro_ctx['crypto']}
{macro_ctx['semis']}
{macro_ctx['energy']}
Fear & Greed: {fg.get('value','?')} — {fg.get('classification','?')}

POSITION WATCH POSTURES (signal must align — ACCUMULATING→INCREASE, WATCHING→HOLD, PAUSED/INVALIDATED→REDUCE):
{posture_block}

TA SNAPSHOT:
{ta_snapshot_block(assets_levels)}

KEY LEVELS:
{levels_table(assets_levels)}

For each asset, output {{ticker, signal, reason}}.
One entry per asset. No duplicates. No omissions. Reason max 18 words.
Return JSON array only."""


# ── Generation ─────────────────────────────────────────────────────────────
def parse_json_array_legacy(raw: str, label: str) -> list:
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        print(f"    ⚠️  {label}: no JSON array in response")
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        print(f"    ⚠️  {label}: JSON parse failed — {e}")
        return []

def generate_actives(prompt: str) -> list:
    print("    Actives...")
    try:
        raw = _core_generate(prompt, SYSTEM_ACTIVES)
        plays = _core_parse_json_array(raw, "Actives")
        filtered = [p for p in plays
                    if isinstance(p, dict)
                    and p.get("conviction", "").upper() in ("HIGH", "MED")
                    and p.get("ticker", "").upper() in ACTIVES]
        return filtered
    except Exception as e:
        print(f"    ⚠️  Actives failed: {e}")
        return []


def generate_day_trades(prompt: str) -> list:
    print("    Day Trades...")
    try:
        raw   = _core_generate(prompt, SYSTEM_DAY_TRADE)
        plays = _core_parse_json_array(raw, "DayTrades")
        return [p for p in plays
                if isinstance(p, dict)
                and p.get("conviction", "").upper() in ("HIGH", "MED")
                and p.get("ticker", "").upper() in ("BTC", "SOL", "ETH", "TSLA")]
    except Exception as e:
        print(f"    ⚠️  Day Trades failed: {e}")
        return []

def generate_position_watch(prompt: str, sector: str) -> list:
    print(f"    Position Watch — {sector}...")
    try:
        raw = _core_generate(prompt, SYSTEM_POSITION_WATCH)
        return _core_parse_json_array(raw, f"PositionWatch-{sector}")
    except Exception as e:
        print(f"    ⚠️  Position Watch {sector} failed: {e}")
        return []

def generate_exposure_signals(prompt: str) -> list:
    print("    Exposure Signals...")
    try:
        raw = _core_generate(prompt, SYSTEM_EXPOSURE, max_tokens=600)
        return _core_parse_json_array(raw, "Exposure")
    except Exception as e:
        print(f"    ⚠️  Exposure failed: {e}")
        return []


# ── Enrichment ─────────────────────────────────────────────────────────────
def enrich_actives_legacy(plays: list, levels: dict) -> list:
    """Full active-trade enrichment: stop/target/R-R/leverage + TA row."""
    TICKER_SECTION = {
        "BTC": "CRYPTO", "SOL": "CRYPTO", "ETH": "CRYPTO",
        "TSLA": "AI & SEMIS",
    }
    for p in plays:
        p["_kind"] = "active"
        ticker = p.get("ticker", "").upper()
        p["section"] = TICKER_SECTION.get(ticker, "UNKNOWN")
        lvl = levels.get(ticker, {})
        p["support"]    = lvl.get("support")
        p["resistance"] = lvl.get("resistance")
        p["current"]    = lvl.get("current")
        curr_v_ta = lvl.get("current") or 0
        p["ma9"]       = lvl.get("ma9")
        p["ma20"]      = lvl.get("ma20")
        p["ma200"]     = lvl.get("ma200")
        p["rsi"]       = lvl.get("rsi")
        p["macd_bull"] = lvl.get("macd_bull")
        p["rel_vol"]   = lvl.get("rel_vol")
        p["ma9_above"]   = (curr_v_ta > lvl["ma9"])   if lvl.get("ma9")   else None
        p["ma20_above"]  = (curr_v_ta > lvl["ma20"])  if lvl.get("ma20")  else None
        p["ma200_above"] = (curr_v_ta > lvl["ma200"]) if lvl.get("ma200") else None

        # Session 41: regime + setup classification
        _df = lvl.get("_df")
        if _df is not None and len(_df) > 0:
            try:
                p["regime"] = get_regime(ticker, _df)
            except Exception as e:
                print(f"    ⚠️  regime({ticker}): {e}")
                p["regime"] = "UNKNOWN"
            try:
                _direction_pre = p.get("direction", "LONG").upper()
                _setup_result  = get_setup(ticker, _df, lvl, direction=_direction_pre)
                p["setup"]       = _setup_result["type"]
                p["setup_score"] = _setup_result["score"]
            except Exception as e:
                print(f"    ⚠️  setup({ticker}): {e}")
                p["setup"]       = "NO_SETUP"
                p["setup_score"] = 0
        else:
            p["regime"]      = "UNKNOWN"
            p["setup"]       = "NO_SETUP"
            p["setup_score"] = 0

        direction = p.get("direction", "LONG").upper()
        if direction == "SHORT":
            p["stop"]   = lvl.get("short_stop")
            p["target"] = lvl.get("target_short")
        else:
            # ATR-based forward target when price is at/above 20d resistance
            # Prevents R/R collapse on breakout plays where target is already hit
            base_target = lvl.get("target_long")
            curr_v_check = lvl.get("current") or 0
            resistance_v = lvl.get("resistance") or 0
            atr14 = lvl.get("atr14")
            if (atr14 and resistance_v and curr_v_check >= resistance_v * 0.98
                    and base_target and curr_v_check >= base_target * 0.97):
                # Price at or through resistance — use ATR projection instead
                # Crypto gets 3x ATR (wider swings), equity gets 2x
                is_crypto_ticker = ticker in ("BTC", "SOL", "ETH")
                atr_mult = 3.0 if is_crypto_ticker else 2.0
                atr_target = round(curr_v_check + (atr14 * atr_mult), 2)
                p["target"] = atr_target
                p["_atr_target"] = True
            else:
                p["target"] = base_target
            p["stop"] = lvl.get("long_stop")

        # R/R recalc per direction
        try:
            curr_v = float(p.get("current") or 0)
            stop_v = float(p.get("stop")    or 0)
            tgt_v  = float(p.get("target")  or 0)
            if direction == "SHORT":
                risk   = stop_v - curr_v
                reward = curr_v - tgt_v
            else:
                risk   = curr_v - stop_v
                reward = tgt_v  - curr_v
            rr_calc = round(reward / risk, 1) if risk > 0.01 else 0
            p["rr"] = f"1:{rr_calc}" if rr_calc > 0 else "N/A"
        except Exception:
            p["rr"] = lvl.get("rr", "N/A")

        # Quality gate
        rr_str = p.get("rr", "N/A")
        try:
            rr_val = float(str(rr_str).replace("1:", ""))
            if rr_val > 10:
                p["rr"] = "VERIFY ⚠"
                p["rr_flagged"] = True
            elif p.get("conviction", "").upper() == "HIGH" and rr_val < 1.5:
                p["rr_flagged"] = True
            elif p.get("conviction", "").upper() == "MED" and rr_val < 1.0:
                p["rr_flagged"] = True
        except Exception:
            pass

        # Leverage
        is_crypto   = ticker in ("BTC", "SOL", "ETH")
        rr_verify   = str(p.get("rr", "")).startswith("VERIFY")
        rr_adjusted = p.get("rr_flagged", False) and not rr_verify
        conv        = p.get("conviction", "MED").upper()
        try:
            rr_num = float(str(p.get("rr", "1")).replace("1:", ""))
        except Exception:
            rr_num = 1.0
        if rr_verify:
            p["leverage"] = "NO LEVERAGE"
        elif rr_adjusted:
            p["leverage"] = "1x"
        elif conv == "HIGH" and rr_num >= 2.0:
            p["leverage"] = "10x" if is_crypto else "5x"
        elif conv == "HIGH":
            p["leverage"] = "5x" if is_crypto else "3x"
        elif conv == "MED":
            p["leverage"] = "3x" if is_crypto else "2x"
        else:
            p["leverage"] = "1x"

        # P1: Deterministic setup score gates — override leverage from conviction table
        # Score tiers: 0-2 DROP | 3-4 DEVELOPING | 5-6 1x cap | 7+ full table
        _score = p.get("setup_score", 0)
        if _score <= 2:
            p["_drop"] = True           # filtered before render in main()
        elif _score <= 4:
            p["_developing"] = True
            p["leverage"] = "NO SIZE — DEVELOPING"
        elif _score <= 6:
            # Cap at 1x regardless of conviction — setup not confirmed
            if p.get("leverage", "1x") not in ("NO LEVERAGE", "1x",
                                                "NO SIZE — DEVELOPING"):
                p["leverage"] = "1x"
                p["_score_capped"] = True
        # score >= 7: full leverage from conviction table (no override)

        # Counter-trend SHORT gate
        # Flag when model generates SHORT on fully bullish TA stack
        if direction == "SHORT":
            bullish_stack = (
                p.get("ma9_above")  is True and
                p.get("ma20_above") is True and
                p.get("macd_bull")  is True
            )
            if bullish_stack:
                p["_counter_trend"] = True
                # Downgrade HIGH → MED, cap leverage at 1x
                if conv == "HIGH":
                    p["conviction"] = "MED"
                p["leverage"] = "1x"
    return plays


def enrich_position_watch_legacy(items: list, levels: dict, watched: dict, sector: str) -> list:
    """Light enrichment: TA row, user-defined S/R overlay, posture override enforcement."""
    valid_postures = {"ACCUMULATING", "WATCHING", "PAUSED", "INVALIDATED"}
    for p in items:
        p["_kind"] = "position_watch"
        p["_sector"] = sector
        ticker = p.get("ticker", "").upper()
        lvl = levels.get(ticker, {})

        # TA data
        p["current"]   = lvl.get("current")
        p["ma9"]       = lvl.get("ma9")
        p["ma20"]      = lvl.get("ma20")
        p["ma200"]     = lvl.get("ma200")
        p["rsi"]       = lvl.get("rsi")
        p["macd_bull"] = lvl.get("macd_bull")
        p["rel_vol"]   = lvl.get("rel_vol")
        curr_v_ta = lvl.get("current") or 0
        p["ma9_above"]   = (curr_v_ta > lvl["ma9"])   if lvl.get("ma9")   else None
        p["ma20_above"]  = (curr_v_ta > lvl["ma20"])  if lvl.get("ma20")  else None
        p["ma200_above"] = (curr_v_ta > lvl["ma200"]) if lvl.get("ma200") else None

        # User-defined S/R (prefer over computed)
        w = watched.get(ticker, {})
        _sup = w.get("support"); user_sup = [_sup] if isinstance(_sup, (int, float)) else (_sup or [])
        _res = w.get("resistance"); user_res = [_res] if isinstance(_res, (int, float)) else (_res or [])
        p["user_support"]    = user_sup
        p["user_resistance"] = user_res
        p["notes"]           = w.get("notes", "")
        # Computed fallback
        p["support"]    = lvl.get("support")
        p["resistance"] = lvl.get("resistance")

        # Validate accumulate_zone — enforce 5-10% width cap
        az = p.get("accumulate_zone")
        if isinstance(az, list) and len(az) == 2:
            try:
                zone_lo, zone_hi = float(az[0]), float(az[1])
                curr_v_check = float(lvl.get("current") or 0)
                if curr_v_check > 0:
                    max_width = curr_v_check * 0.10
                    actual_width = zone_hi - zone_lo
                    if actual_width > max_width and zone_lo > 0:
                        # Tighten: center the zone around zone midpoint, cap at 10%
                        midpoint = (zone_lo + zone_hi) / 2
                        half = max_width / 2
                        zone_lo = round(midpoint - half, 2)
                        zone_hi = round(midpoint + half, 2)
                        p["_zone_capped"] = True
                p["accumulate_zone"] = [round(zone_lo, 2), round(zone_hi, 2)]
            except Exception:
                p["accumulate_zone"] = None
        else:
            p["accumulate_zone"] = None

        # Posture override enforcement — user yaml takes priority
        posture = p.get("posture", "WATCHING").upper()
        if posture not in valid_postures:
            posture = "WATCHING"
        po = w.get("posture_override")
        if po and po in valid_postures:
            posture = po
            p["_posture_overridden"] = True
        else:
            # Code-level posture correction: price inside zone + MA20 trending up → ACCUMULATING
            az_final = p.get("accumulate_zone")
            if (az_final and isinstance(az_final, list) and len(az_final) == 2
                    and lvl.get("current") is not None):
                curr_check = float(lvl["current"])
                in_zone = az_final[0] <= curr_check <= az_final[1]
                ma20_up = p.get("ma20_above") is True
                if in_zone and ma20_up and posture == "WATCHING":
                    posture = "ACCUMULATING"
                    p["_posture_corrected"] = True
        p["posture"] = posture

    return items


# ── CSS ────────────────────────────────────────────────────────────────────

# ── Watch For / Wary Of helpers (Session C) ──────────────────────────────────
def _build_swing_watchfor_waryof(play: dict) -> tuple:
    """Deterministic Watch For + Wary Of for SWING (daily-bar) cards."""
    direction  = play.get("direction", "LONG").upper()
    current    = float(play.get("current") or 0)
    ma20       = float(play.get("ma20") or 0)
    resistance = float(play.get("resistance") or 0)
    support    = float(play.get("support") or 0)

    if direction == "LONG":
        if ma20 and current < ma20:
            wf = f"Daily close above MA20 ${ma20:,.2f}"
            wo = f"Close below support ${support:,.2f}"
        elif resistance and current < resistance:
            wf = f"Daily close above resistance ${resistance:,.2f}"
            wo = (f"Daily close below MA20 ${ma20:,.2f}"
                  if ma20 else f"Close below support ${support:,.2f}")
        else:
            wf = f"Hold above ${resistance:,.2f} and expand on volume"
            wo = f"Fail back below ${resistance:,.2f}"
    else:
        if ma20 and current > ma20:
            wf = f"Daily close below MA20 ${ma20:,.2f}"
            wo = f"Close above resistance ${resistance:,.2f}"
        else:
            wf = f"Daily close below support ${support:,.2f}"
            wo = (f"Close above MA20 ${ma20:,.2f}"
                  if ma20 else f"Close above resistance ${resistance:,.2f}")
    return wf, wo


def _build_dt_watchfor_waryof(play: dict, intraday: dict) -> tuple:
    """Deterministic Watch For + Wary Of for DAY TRADE (1H) cards."""
    direction = play.get("direction", "LONG").upper()
    current   = float(play.get("current") or 0)
    vwap      = float(intraday.get("vwap") or 0)
    pdh       = float(intraday.get("pdh") or 0)
    pdl       = float(intraday.get("pdl") or 0)
    res_1h    = float(intraday.get("resistance") or 0)
    sup_1h    = float(intraday.get("support") or 0)

    if not (vwap and pdh and pdl):
        return _build_swing_watchfor_waryof(play)

    if direction == "LONG":
        if current < vwap:
            wf = f"Reclaim VWAP ${vwap:,.2f} + hold 2 candles on 15m"
            wo = f"Rejection at VWAP ${vwap:,.2f} — exit"
        elif current < pdh:
            wf = f"Break above PDH ${pdh:,.2f} on 15m close"
            wo = f"Loss of VWAP ${vwap:,.2f}"
        else:
            r  = res_1h if res_1h > current else pdh * 1.005
            wf = f"Break above 1H resistance ${r:,.2f}"
            wo = f"Loss of PDH ${pdh:,.2f}"
    else:
        if current > vwap:
            wf = f"Rejection at VWAP ${vwap:,.2f} on 15m"
            wo = f"Close above VWAP ${vwap:,.2f} — invalidated"
        elif current > pdl:
            wf = f"Break below PDL ${pdl:,.2f} on 15m close"
            wo = f"Reclaim VWAP ${vwap:,.2f}"
        else:
            s  = sup_1h if sup_1h and sup_1h < current else pdl * 0.995
            wf = f"Break below 1H support ${s:,.2f}"
            wo = f"Reclaim PDL ${pdl:,.2f}"
    return wf, wo


def _apply_swing_watchfor_waryof(plays: list) -> list:
    """Post-enrichment pass: stamp Watch For / Wary Of + tier onto SWING cards."""
    for p in plays:
        if not p.get("watch_for"):
            wf, wo = _build_swing_watchfor_waryof(p)
            p["watch_for"] = wf
            p["wary_of"]   = wo
        p["tier"] = "SWING"
    return plays


def enrich_day_trades(plays: list, levels: dict, intraday_data: dict) -> list:
    """Enrich DAY TRADE cards: 1H ATR stops/targets + deterministic Watch For/Wary Of."""
    from core.constants import DAY_TRADE_RR_MIN, CRYPTO_TICKERS
    _DT_YF  = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "TSLA": "TSLA"}
    _DT_SEC = {"BTC": "CRYPTO", "SOL": "CRYPTO", "ETH": "CRYPTO", "TSLA": "AI & SEMIS"}

    for p in plays:
        ticker    = p.get("ticker", "").upper()
        yft       = _DT_YF.get(ticker, ticker)
        intraday  = intraday_data.get(yft, {})
        lvl       = levels.get(ticker, {})

        p["tier"]      = "DAY_TRADE"
        p["_kind"]     = "day_trade"
        p["section"]   = _DT_SEC.get(ticker, "UNKNOWN")
        p["timeframe"] = "DAY TRADE · <24hr"
        p["time_gate"] = "DAY_TRADE"

        current = float(lvl.get("current") or 0)
        p["current"]    = current
        p["support"]    = lvl.get("support")
        p["resistance"] = lvl.get("resistance")
        for k in ("ma9", "ma20", "ma200", "rsi", "macd_bull", "rel_vol"):
            p[k] = lvl.get(k)
        p["ma9_above"]   = (current > lvl["ma9"])   if lvl.get("ma9")   else None
        p["ma20_above"]  = (current > lvl["ma20"])  if lvl.get("ma20")  else None
        p["ma200_above"] = (current > lvl["ma200"]) if lvl.get("ma200") else None

        direction = p.get("direction", "LONG").upper()
        atr14_1h  = float(intraday.get("atr14") or 0)
        pdh       = float(intraday.get("pdh") or 0)
        pdl       = float(intraday.get("pdl") or 0)

        if atr14_1h and current:
            if direction == "LONG":
                stop   = round(current - 0.75 * atr14_1h, 2)
                target = (round(min(pdh * 1.002, current + 2.0 * atr14_1h), 2)
                          if pdh > current * 1.005
                          else round(current + 1.5 * atr14_1h, 2))
            else:
                stop   = round(current + 0.75 * atr14_1h, 2)
                target = (round(max(pdl * 0.998, current - 2.0 * atr14_1h), 2)
                          if pdl and pdl < current * 0.995
                          else round(current - 1.5 * atr14_1h, 2))
        else:
            if direction == "LONG":
                stop   = lvl.get("long_stop")   or round(current * 0.985, 2)
                target = lvl.get("target_long") or round(current * 1.015, 2)
            else:
                stop   = lvl.get("short_stop")   or round(current * 1.015, 2)
                target = lvl.get("target_short") or round(current * 0.985, 2)

        p["stop"]   = stop
        p["target"] = target

        try:
            risk   = (current - stop)   if direction == "LONG" else (stop - current)
            reward = (target - current) if direction == "LONG" else (current - target)
            rr_val = round(reward / risk, 1) if risk > 0.01 else 0.0
            p["rr"] = f"1:{rr_val}" if rr_val > 0 else "N/A"
            p["rr_flagged"] = rr_val < DAY_TRADE_RR_MIN
        except Exception:
            p["rr"] = "N/A"; p["rr_flagged"] = True; rr_val = 0.0

        if rr_val < DAY_TRADE_RR_MIN:
            p["_drop"] = True

        is_crypto = ticker in CRYPTO_TICKERS
        conv = p.get("conviction", "MED").upper()
        try:
            rr_num = float(str(p.get("rr", "1")).replace("1:", ""))
        except Exception:
            rr_num = 1.0
        if rr_num >= 2.0 and conv == "HIGH":
            p["leverage"] = "5x" if is_crypto else "2x"
        elif conv == "HIGH":
            p["leverage"] = "3x" if is_crypto else "2x"
        else:
            p["leverage"] = "2x" if is_crypto else "1x"

        _df = lvl.get("_df")
        if _df is not None and len(_df) > 0:
            try:
                p["regime"]      = get_regime(ticker, _df)
                sr               = get_setup(ticker, _df, lvl, direction=direction)
                p["setup_score"] = sr["score"]
                p["setup_type"]  = sr["type"]
            except Exception:
                p["regime"] = "UNKNOWN"; p["setup_score"] = 0; p["setup_type"] = "NO_SETUP"
        else:
            p["regime"] = "UNKNOWN"; p["setup_score"] = 0; p["setup_type"] = "NO_SETUP"

        p["watch_for"], p["wary_of"] = _build_dt_watchfor_waryof(p, intraday)

    return plays

CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #0e0c08; color: #d4cdc0;
    font-family: 'IBM Plex Sans', -apple-system, 'Helvetica Neue', sans-serif;
    font-size: 15px; line-height: 1.85; padding: 0;
}
.page { max-width: 780px; margin: 0 auto; padding: 40px 32px 56px; }
.header { border-bottom: 1px solid #2a2520; padding-bottom: 20px; margin-bottom: 32px; }
.header-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4px; }
.header-title { font-family: 'IBM Plex Mono', monospace; font-size: 10px; letter-spacing: 0.2em; color: #f5a623; text-transform: uppercase; font-weight: 600; }
.header-stamp { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #5a5248; letter-spacing: 0.08em; }
.header-sub { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: #3a3430; letter-spacing: 0.06em; margin-top: 4px; }

.pulse-bar {
    background: #141210; border: 1px solid #1e1c18; border-radius: 6px;
    padding: 14px 20px; margin-bottom: 36px;
    display: flex; gap: 16px; flex-wrap: wrap; align-items: center;
    font-family: 'IBM Plex Mono', monospace; font-size: 12px;
}
.pulse-item { color: #6b5f42; }
.pulse-item span { font-weight: 600; color: #e8e0d0; }
.pulse-up { color: #4ade80; } .pulse-down { color: #f87171; } .pulse-neu { color: #a89068; }
.pulse-divider { width: 1px; height: 18px; background: #2a2520; flex-shrink: 0; }

.section { background: #141210; border: 1px solid #1e1c18; border-radius: 8px; margin-bottom: 24px; overflow: hidden; }
.section-header { padding: 14px 20px; border-bottom: 1px solid #1e1c18; display: flex; align-items: center; gap: 10px; }
.section-label { font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; font-weight: 600; }
.section-body { padding: 20px 24px; }

.actives .section-label { color: #f5a623; }
.actives { border-left: 3px solid #f5a623; }
.position-watch .section-label { color: #60a5fa; }
.position-watch { border-left: 3px solid #60a5fa; }
.exposure .section-label { color: #a89068; }
.exposure { border-left: 3px solid #a89068; }
.macro .section-label { color: #f59e0b; }
.macro { border-left: 3px solid #f59e0b; }
.day-trade .section-label { color: #22d3ee; }
.day-trade { border-left: 3px solid #22d3ee; }

.play-card { padding: 20px 0; border-bottom: 1px solid #1e1c18; }
.play-card:last-child { border-bottom: none; padding-bottom: 0; }
.play-card:first-child { padding-top: 0; }
.play-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.play-ticker { font-family: 'IBM Plex Mono', monospace; font-size: 16px; font-weight: 600; color: #e8e0d0; letter-spacing: 0.04em; }
.conv-high { font-family: 'IBM Plex Mono', monospace; color: #4ade80; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(74,222,128,0.3); background: rgba(74,222,128,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.conv-med  { font-family: 'IBM Plex Mono', monospace; color: #f5a623; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(245,166,35,0.3); background: rgba(245,166,35,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.play-rows { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 28px; margin-bottom: 16px; }
.play-row-label { font-family: 'IBM Plex Mono', monospace; color: #5a5248; font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 3px; }
.play-row-value { color: #c8c0b4; font-size: 13px; line-height: 1.6; }
.levels-bar {
    display: flex; gap: 16px; flex-wrap: wrap;
    padding: 10px 14px; background: #0e0c08;
    border: 1px solid #1e1c18; border-radius: 6px;
    font-family: 'IBM Plex Mono', monospace; font-size: 11px;
    margin-bottom: 10px;
}
.level-item { color: #5a5248; }
.level-item span { color: #a89068; font-weight: 600; }
.level-stop span  { color: #f87171; }
.level-target span { color: #4ade80; }
.level-rr span    { color: #60a5fa; }
.no-plays { color: #3a3430; font-size: 13px; padding: 10px 0; }
.dir-long  { font-family: 'IBM Plex Mono', monospace; color: #4ade80; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(74,222,128,0.3); background: rgba(74,222,128,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.dir-short { font-family: 'IBM Plex Mono', monospace; color: #f87171; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(248,113,113,0.3); background: rgba(248,113,113,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.lev-badge { font-family: 'IBM Plex Mono', monospace; color: #60a5fa; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(96,165,250,0.3); background: rgba(96,165,250,0.08); border-radius: 3px; letter-spacing: 0.06em; }
.lev-none  { font-family: 'IBM Plex Mono', monospace; color: #f87171; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(248,113,113,0.3); background: rgba(248,113,113,0.08); border-radius: 3px; letter-spacing: 0.06em; }

.sector-block { margin-bottom: 28px; }
.sector-block:last-child { margin-bottom: 0; }
.sector-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: #1a1814; border: 1px solid #2a2520;
    border-radius: 6px; margin-bottom: 14px;
    font-family: 'IBM Plex Mono', monospace; font-size: 11px;
    letter-spacing: 0.12em; text-transform: uppercase; color: #a89068;
}
.sector-thesis {
    background: #0e0c08; border: 1px solid #1e1c18; border-radius: 6px;
    padding: 14px 16px; margin-bottom: 14px;
    font-size: 13px; color: #8a7e6e; line-height: 1.75;
}
.sector-thesis-title {
    font-family: 'IBM Plex Mono', monospace; color: #60a5fa;
    font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; margin-bottom: 6px;
}

.pw-card {
    padding: 18px 18px; margin-bottom: 14px;
    background: #0e0c08; border: 1px solid #1e1c18; border-radius: 8px;
}
.pw-card:last-child { margin-bottom: 0; }
.pw-card.invalidated { opacity: 0.5; border-color: #3a1818; }
.pw-header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.pw-ticker { font-family: 'IBM Plex Mono', monospace; font-size: 16px; font-weight: 600; color: #e8e0d0; letter-spacing: 0.04em; }
.pw-notes { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #5a5248; letter-spacing: 0.06em; }
.pw-timeframe { font-family: 'IBM Plex Mono', monospace; color: #5a5248; font-size: 10px; margin-left: auto; letter-spacing: 0.06em; }

.posture-accumulating { font-family: 'IBM Plex Mono', monospace; color: #4ade80; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(74,222,128,0.3); background: rgba(74,222,128,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.posture-watching     { font-family: 'IBM Plex Mono', monospace; color: #f5a623; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(245,166,35,0.3); background: rgba(245,166,35,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.posture-paused       { font-family: 'IBM Plex Mono', monospace; color: #a89068; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(168,144,104,0.3); background: rgba(168,144,104,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.posture-invalidated  { font-family: 'IBM Plex Mono', monospace; color: #f87171; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(248,113,113,0.3); background: rgba(248,113,113,0.08); border-radius: 3px; letter-spacing: 0.08em; }
.posture-override-tag { font-family: 'IBM Plex Mono', monospace; color: #60a5fa; font-size: 9px; letter-spacing: 0.1em; margin-left: 2px; }

.pw-outlook { font-size: 13.5px; color: #b8b0a0; line-height: 1.8; margin-bottom: 14px; padding: 12px 16px; background: #141210; border-left: 2px solid #2a2520; border-radius: 0 6px 6px 0; }

.exposure-table { width: 100%; border-collapse: collapse; font-family: 'IBM Plex Sans', sans-serif; }
.exposure-table th { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #5a5248; letter-spacing: 0.12em; text-transform: uppercase; text-align: left; padding: 8px 12px; border-bottom: 1px solid #1e1c18; font-weight: 600; }
.exposure-table td { padding: 10px 12px; border-bottom: 1px solid #1e1c18; font-size: 13px; vertical-align: middle; }
.exposure-table tr:last-child td { border-bottom: none; }
.exposure-ticker { font-family: 'IBM Plex Mono', monospace; font-weight: 600; color: #e8e0d0; letter-spacing: 0.04em; }
.exposure-reason { color: #8a7e6e; font-size: 12.5px; line-height: 1.5; }
.sig-increase { font-family: 'IBM Plex Mono', monospace; color: #4ade80; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(74,222,128,0.3); background: rgba(74,222,128,0.08); border-radius: 3px; letter-spacing: 0.1em; }
.sig-hold     { font-family: 'IBM Plex Mono', monospace; color: #f5a623; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(245,166,35,0.3); background: rgba(245,166,35,0.08); border-radius: 3px; letter-spacing: 0.1em; }
.sig-reduce   { font-family: 'IBM Plex Mono', monospace; color: #f87171; font-size: 10px; font-weight: 600; padding: 3px 10px; border: 1px solid rgba(248,113,113,0.3); background: rgba(248,113,113,0.08); border-radius: 3px; letter-spacing: 0.1em; }

.footer {
    margin-top: 48px; padding-top: 16px; border-top: 1px solid #1e1c18;
    font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #3a3430; letter-spacing: 0.06em;
    display: flex; justify-content: space-between;
}
.heat-section .section-label { color: #e8e0d0; }
.heat-section { border-left: 3px solid #e8e0d0; }
.heat-table { width: 100%; border-collapse: collapse; font-family: 'IBM Plex Sans', sans-serif; }
.heat-table th { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #5a5248; letter-spacing: 0.12em; text-transform: uppercase; text-align: left; padding: 8px 12px; border-bottom: 1px solid #1e1c18; font-weight: 600; }
.heat-table td { padding: 10px 12px; border-bottom: 1px solid #1e1c18; font-size: 12px; vertical-align: middle; }
.heat-table tr:last-child td { border-bottom: none; }
.ht-ticker { font-family: 'IBM Plex Mono', monospace; font-weight: 600; color: #e8e0d0; letter-spacing: 0.04em; }
.ht-cell { font-family: 'IBM Plex Mono', monospace; color: #a89068; letter-spacing: 0.04em; }
.the-trade-header { font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 0.18em; color: #f5a623; text-transform: uppercase; font-weight: 700; padding: 6px 0 14px; }
.on-watch-section { background: #0e0c08; border: 1px solid #1e1c18; border-radius: 8px; padding: 14px 20px; margin-bottom: 16px; }
.on-watch-header { font-family: 'IBM Plex Mono', monospace; font-size: 10px; letter-spacing: 0.14em; color: #5a5248; text-transform: uppercase; margin-bottom: 10px; }
.on-watch-row { display: flex; gap: 16px; align-items: baseline; padding: 7px 0; border-bottom: 1px solid #1a1814; font-family: 'IBM Plex Mono', monospace; font-size: 11px; }
.on-watch-row:last-child { border-bottom: none; }
.ow-ticker { color: #e8e0d0; font-weight: 600; min-width: 60px; }
.ow-type { color: #6b5f42; font-size: 10px; min-width: 170px; }
.ow-score { color: #f5a623; font-size: 10px; min-width: 55px; }
.ow-condition { color: #8a7e6e; font-size: 11px; flex: 1; }
.decision-table { width: 100%; border-collapse: collapse; margin-bottom: 14px; }
.decision-table td { padding: 7px 10px; border: 1px solid #1e1c18; font-size: 12px; vertical-align: top; background: #0e0c08; }
.dt-label { font-family: 'IBM Plex Mono', monospace; font-size: 9px; color: #5a5248; letter-spacing: 0.12em; text-transform: uppercase; display: block; margin-bottom: 3px; }
.dt-value { color: #c8c0b4; font-size: 12px; line-height: 1.5; }
.earnings-warn { font-family: 'IBM Plex Mono', monospace; color: #f87171; font-size: 9px; font-weight: 600; padding: 2px 8px; border: 1px solid rgba(248,113,113,0.3); background: rgba(248,113,113,0.08); border-radius: 3px; letter-spacing: 0.1em; margin-left: 4px; }
"""



# -- Render: Session Heat table ------------------------------------------------
def render_heat_table() -> str:
    """Load open positions from trade_log.json -- pure arithmetic, no LLM."""
    import yaml as _yaml
    try:
        with open(VAULT_ROOT / "config.yaml") as _f:
            _cfg = _yaml.safe_load(_f)
        account_size     = float(_cfg.get("account_size", 10000))
        crypto_lev_limit = int(_cfg.get("crypto_corr_leverage_limit", 4))
    except Exception:
        account_size, crypto_lev_limit = 10000, 4

    try:
        with open(VAULT_ROOT / "01-Trading" / "trade_log.json") as _f:
            trades = json.load(_f)
    except Exception:
        trades = []

    open_trades = [t for t in trades if t.get('taken') and not t.get('close_date')]

    EMPTY = (
        '<div class="section heat-section">'
        '<div class="section-header"><span class="section-label">SESSION HEAT</span></div>'
        '<div class="section-body"><div class="no-plays">-- no open positions --</div></div>'
        '</div>'
    )
    if not open_trades:
        return EMPTY

    def _corr_group(trade):
        s = trade.get("section", "").upper()
        if "CRYPTO" in s: return "CRYPTO"
        if "MACRO"  in s: return "MACRO"
        return "EQUITY"

    def _parse_lev_int(lev_str):
        try:
            return int(str(lev_str).replace("x", "").split()[0])
        except Exception:
            return 1

    rows_html = ""
    total_risk = 0.0
    crypto_combined_lev = 0

    for t in open_trades:
        ticker       = t.get("ticker", "?")
        conviction   = t.get("conviction", "--")
        leverage_str = t.get("leverage", "1x") or "1x"
        entry        = t.get("entry_price")
        stop_val     = t.get("stop")
        lev_int      = _parse_lev_int(leverage_str)
        cg           = _corr_group(t)

        notional_risk = None
        if entry is not None and stop_val is not None:
            try:
                notional_risk = abs(float(entry) - float(stop_val)) * lev_int / account_size
            except Exception:
                pass

        if notional_risk is not None:
            total_risk += notional_risk
        if cg == "CRYPTO":
            crypto_combined_lev += lev_int

        risk_str = f"{notional_risk*100:.1f}%" if notional_risk is not None else "--"
        risk_color = (
            "#f87171" if notional_risk is not None and notional_risk > 0.08
            else "#f5a623" if notional_risk is not None and notional_risk > 0.04
            else "#4ade80" if notional_risk is not None
            else "#6b5f42"
        )
        cg_color = {"CRYPTO": "#f5a623", "EQUITY": "#60a5fa", "MACRO": "#a89068"}.get(cg, "#a89068")
        conv_color = "#4ade80" if conviction == "HIGH" else "#f5a623" if conviction == "MED" else "#a89068"

        rows_html += (
            "<tr>"
            + f'<td class="ht-ticker">{ticker}</td>'
            + f'<td class="ht-cell" style="color:{conv_color}">{conviction}</td>'
            + f'<td class="ht-cell">{leverage_str}</td>'
            + f'<td class="ht-cell">{fmt_level(entry)}</td>'
            + f'<td class="ht-cell">{fmt_level(stop_val)}</td>'
            + f'<td class="ht-cell" style="color:{risk_color};font-weight:600">{risk_str}</td>'
            + f'<td class="ht-cell"><span style="color:{cg_color};font-size:9px;letter-spacing:0.1em">{cg}</span></td>'
            + "</tr>"
        )

    heat_color, heat_label = (
        ("#f87171", "RED") if total_risk > 0.08
        else ("#f5a623", "AMBER") if total_risk > 0.04
        else ("#4ade80", "GREEN")
    )

    crypto_warn = ""
    if crypto_combined_lev > crypto_lev_limit:
        crypto_warn = (
            '<div style="margin-top:10px;padding:8px 12px;background:rgba(248,113,113,0.08);'
            'border:1px solid rgba(248,113,113,0.3);border-radius:6px;'
            "font-family:'IBM Plex Mono',monospace;font-size:10px;color:#f87171;letter-spacing:0.08em>"
            + f'WARNING: CRYPTO LEVERAGE {crypto_combined_lev}x -- EXCEEDS {crypto_lev_limit}x LIMIT'
            + '</div>'
        )

    total_str = f"{total_risk*100:.1f}%"
    header_right = (
        f'<span style="margin-left:auto;font-family:\'IBM Plex Mono\',monospace;'
        f'font-size:10px;color:{heat_color};font-weight:600;letter-spacing:0.1em">'
        f'{heat_label} &middot; {total_str} TOTAL RISK</span>'
    )

    return (
        '<div class="section heat-section">'
        '<div class="section-header">'
        '<span class="section-label">SESSION HEAT</span>'
        + header_right
        + '</div>'
        '<div class="section-body">'
        '<table class="heat-table"><thead><tr>'
        '<th>TICKER</th><th>CONVICTION</th><th>LEV</th>'
        '<th>ENTRY</th><th>STOP</th><th>RISK%</th><th>GROUP</th>'
        '</tr></thead>'
        + f'<tbody>{rows_html}</tbody>'
        + '</table>'
        + crypto_warn
        + '</div></div>'
    )

# ── Formatting helpers ─────────────────────────────────────────────────────
def fmt_price(val) -> str:
    try:
        return f"${float(str(val).replace('$','').replace(',','')):,.2f}"
    except Exception:
        return str(val)

def fmt_change(val) -> str:
    try:
        v = float(val)
        cls = "pulse-up" if v > 0 else "pulse-down" if v < 0 else "pulse-neu"
        arrow = "▲" if v > 0 else "▼" if v < 0 else "—"
        return f'<span class="{cls}">{arrow} {v:+.2f}%</span>'
    except Exception:
        return f'<span class="pulse-neu">{val}</span>'

def fmt_level(val) -> str:
    if val is None: return "—"
    try: return f"${float(val):,.2f}"
    except: return str(val)


# ── Render: Active trade card (preserved shape from v2.6) ──────────────────
def render_play_card(play: dict) -> str:
    ticker     = play.get("ticker", "?")
    conviction = play.get("conviction", "MED").upper()
    direction  = play.get("direction", "LONG").upper()
    leverage   = play.get("leverage", "1x")
    why_now    = play.get("why_now", "—")
    watch            = play.get("watch", "—")
    watch_for_display = play.get("watch_for") or watch
    wary_of_display   = play.get("wary_of", "—")
    _tier             = play.get("tier", "SWING")
    tier_badge        = (
        ' <span style="color:#22d3ee;font-size:9px;font-weight:600;'
        'letter-spacing:0.1em;padding:2px 6px;border:1px solid rgba(34,211,238,0.3);'
        'background:rgba(34,211,238,0.08);border-radius:3px;">DAY TRADE</span>'
        if _tier == "DAY_TRADE" else "")
    timeframe  = play.get("timeframe", "—")
    conv_class = "conv-high" if conviction == "HIGH" else "conv-med"
    conv_label = "HIGH CONVICTION" if conviction == "HIGH" else "MED CONVICTION"
    dir_class  = "dir-long" if direction == "LONG" else "dir-short"
    dir_label  = f"{direction} · {leverage}"
    is_short   = (direction == "SHORT")

    narrative  = play.get("narrative", "")
    rr_flagged = play.get("rr_flagged", False)
    rr_is_verify = str(play.get("rr", "")).startswith("VERIFY")
    if rr_is_verify:
        flagged_note = ' <span style="color:#f59e0b;font-size:9px;letter-spacing:0.1em">⚠ VERIFY R/R</span>'
    elif rr_flagged:
        flagged_note = ' <span style="color:#f87171;font-size:9px;letter-spacing:0.1em">R/R ADJUSTED</span>'
    else:
        flagged_note = ""

    counter_trend_badge = ""
    if play.get("_counter_trend"):
        counter_trend_badge = ' <span style="color:#f59e0b;font-size:9px;font-weight:600;letter-spacing:0.1em;padding:2px 6px;border:1px solid rgba(245,158,11,0.3);background:rgba(245,158,11,0.08);border-radius:3px;">⚠ COUNTER-TREND</span>'

    atr_target_badge = ""
    if play.get("_atr_target"):
        atr_target_badge = ' <span style="color:#60a5fa;font-size:9px;letter-spacing:0.08em">ATR TARGET</span>'

    flags_html = ""
    _flag_labels = {
        "SAME_DAY_REENTRY": "⚠ SAME-DAY REENTRY",
        "CONTRARIAN_FEAR":  "⚠ CONTRARIAN FEAR",
        "SHORTING_GREED":   "⚠ SHORTING GREED",
        "POSTURE_DRIFT":    "⚠ POSTURE DRIFT",
        "RATE_HEADWIND":    "⚠ RATE HEADWIND",
        "DOLLAR_STRENGTH":  "⚠ DXY HEADWIND",
    }
    for _flag in play.get("flags", []):
        _label = _flag_labels.get(_flag, f"⚠ {_flag}")
        flags_html += f' <span style="color:#f5a623;font-size:9px;font-weight:600;letter-spacing:0.1em;padding:2px 6px;border:1px solid rgba(245,166,35,0.3);background:rgba(245,166,35,0.08);border-radius:3px;">{_label}</span>'

    # ── Session 41: regime badge ──────────────────────────────
    _regime = play.get("regime", "UNKNOWN")
    _regime_colors = {
        "STRONG_UPTREND":   "#4ade80",
        "STRONG_DOWNTREND": "#f87171",
        "CHOPPY":           "#f5a623",
        "CONSOLIDATING":    "#60a5fa",
    }
    _regime_labels = {
        "STRONG_UPTREND":   "UPTREND",
        "STRONG_DOWNTREND": "DOWNTREND",
        "CHOPPY":           "CHOPPY",
        "CONSOLIDATING":    "COILED",
    }
    if _regime in _regime_colors:
        _rc = _regime_colors[_regime]
        _rl = _regime_labels[_regime]
        regime_badge = f' <span style="color:{_rc};font-size:9px;font-weight:600;letter-spacing:0.1em;padding:2px 6px;border:1px solid {_rc}4D;background:{_rc}15;border-radius:3px;">{_rl}</span>'
    else:
        regime_badge = ""

    # ── Session 41: setup quality score (0-10) ────────────────
    _score = 0
    _setup = play.get("setup", "NO_SETUP")

    # Regime alignment (+3)
    if (_regime == "STRONG_UPTREND"   and direction == "LONG")  or \
       (_regime == "STRONG_DOWNTREND" and direction == "SHORT"):
        _score += 3

    # Setup present (+3)
    if _setup in ("BREAK_AND_RETEST", "COMPRESSION_COIL"):
        _score += 3

    # R/R >= 2:1 (+2)
    try:
        _rr_str = str(play.get("rr", ""))
        if ":" in _rr_str and not _rr_str.startswith("VERIFY"):
            _rr_num = float(_rr_str.split(":")[1])
            if _rr_num >= 2.0:
                _score += 2
    except (ValueError, IndexError):
        pass

    # Conviction HIGH (+1)
    if conviction == "HIGH":
        _score += 1

    # MA stack aligned (+1)
    try:
        _ma9   = float(play.get("ma9")   or 0)
        _ma20  = float(play.get("ma20")  or 0)
        _ma200 = float(play.get("ma200") or 0)
        if _ma9 > 0 and _ma20 > 0 and _ma200 > 0:
            if direction == "LONG" and _ma9 > _ma20 > _ma200:
                _score += 1
            elif direction == "SHORT" and _ma9 < _ma20 < _ma200:
                _score += 1
    except (ValueError, TypeError):
        pass

    if _score >= 7:
        _sc = "#4ade80"
    elif _score >= 5:
        _sc = "#6b5f42"
    else:
        _sc = "#f5a623"
    setup_score_badge = f' <span style="color:{_sc};font-size:9px;font-weight:600;letter-spacing:0.1em;padding:2px 6px;border:1px solid {_sc}4D;background:{_sc}15;border-radius:3px;">SETUP {_score}/10</span>'

    # Position bar
    pos_pct = 0
    try:
        stop_v   = float(play.get("stop") or 0)
        target_v = float(play.get("target") or 0)
        curr_v   = float(play.get("current") or 0)
        if is_short and stop_v > target_v:
            pos_pct = max(0, min(100, int((stop_v - curr_v) / (stop_v - target_v) * 100)))
        elif not is_short and target_v > stop_v:
            pos_pct = max(0, min(100, int((curr_v - stop_v) / (target_v - stop_v) * 100)))
    except Exception:
        pos_pct = 0

    if is_short:
        bar_gradient = "linear-gradient(90deg,#4ade80,#f87171)"
        bar_labels = f'''<span>TARGET {fmt_level(play.get("target"))}</span>
        <span style="color:#a89068">CURRENT {fmt_level(play.get("current"))}</span>
        <span style="color:#f87171">STOP {fmt_level(play.get("stop"))}</span>'''
    else:
        bar_gradient = "linear-gradient(90deg,#f87171,#4ade80)"
        bar_labels = f'''<span>STOP {fmt_level(play.get("stop"))}</span>
        <span style="color:#a89068">CURRENT {fmt_level(play.get("current"))}</span>
        <span>TARGET {fmt_level(play.get("target"))}</span>'''

    pos_bar = f"""<div style="margin:10px 0 6px;">
      <div style="display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#5a5248;margin-bottom:4px;letter-spacing:0.06em;">
        {bar_labels}
      </div>
      <div style="height:4px;background:#1e1c18;border-radius:2px;position:relative;">
        <div style="position:absolute;left:0;top:0;height:100%;width:{pos_pct}%;background:{bar_gradient};border-radius:2px;"></div>
        <div style="position:absolute;left:{pos_pct}%;top:-3px;transform:translateX(-50%);width:10px;height:10px;background:#e8e0d0;border-radius:50%;box-shadow:0 0 0 2px #0e0c08;"></div>
      </div>
    </div>"""

    narrative_block = f'<div style="font-size:14px;color:#b8b0a0;line-height:1.8;margin-bottom:16px;padding:12px 16px;background:#0e0c08;border-left:2px solid #2a2520;border-radius:0 6px 6px 0;">{narrative}</div>' if narrative else ""

    ta_row = render_ta_row(play)

    # -- Decision table fields --
    _setup_label  = play.get('setup_type', 'NO_SETUP').replace('_', ' ')
    _entry_cond   = play.get('entry_condition', '') or '—'
    _upgrade_cond = play.get('upgrade_condition', '') or '—'
    _time_gate    = play.get('time_gate', 'SWING')
    _earn_warn    = play.get('earnings_warning', False)
    _time_str = (
        'No entry before 9:45 AM · Dead by 11:00 AM · Hard exit 3:45 PM'
        if _time_gate == 'DAY_TRADE' else 'Entry on daily close only'
    )
    _earn_badge = ' <span class="earnings-warn">EARNINGS WARNING</span>' if _earn_warn else ''
    _score_val  = play.get('setup_score', 0)
    decision_table_html = (
        '<table class="decision-table"><tr>'
        f'<td><span class="dt-label">Setup</span><span class="dt-value">{_setup_label}</span></td>'
        f'<td><span class="dt-label">Score</span><span class="dt-value">{_score_val}/10</span></td>'
        f'<td><span class="dt-label">R/R</span><span class="dt-value">{play.get("rr") or "—"}</span></td>'
        f'<td><span class="dt-label">Leverage</span><span class="dt-value">{leverage}{_earn_badge}</span></td>'
        '</tr><tr>'
        f'<td colspan="2"><span class="dt-label">Entry Condition</span><span class="dt-value">{_entry_cond}</span></td>'
        f'<td><span class="dt-label">Stop</span><span class="dt-value">{fmt_level(play.get("stop"))}</span></td>'
        f'<td><span class="dt-label">Target</span><span class="dt-value">{fmt_level(play.get("target"))}</span></td>'
        '</tr><tr>'
        f'<td colspan="3"><span class="dt-label">Time Gate</span><span class="dt-value">{_time_str}</span></td>'
        f'<td><span class="dt-label">Upgrade</span><span class="dt-value">{_upgrade_cond}</span></td>'
        '</tr></table>'
    )

    return f"""<div class="play-card">
  <div class="play-header">
    <span class="play-ticker">{ticker}</span>
    <span class="{conv_class}">{conv_label}</span>
    <span class="{dir_class}">{dir_label}</span>{flagged_note}{counter_trend_badge}{atr_target_badge}{flags_html}{regime_badge}{setup_score_badge}{tier_badge}
    <span style="font-family:'IBM Plex Mono',monospace;color:#5a5248;font-size:10px;margin-left:auto">{timeframe}</span>
  </div>
  {decision_table_html}
  {narrative_block}
  <div class="play-rows">
    <div><div class="play-row-label">Why Now</div><div class="play-row-value">{why_now}</div></div>
    <div><div class="play-row-label">Watch For</div><div class="play-row-value">{watch_for_display}</div></div>
    <div style="grid-column:1/-1"><div class="play-row-label">Wary Of</div><div class="play-row-value" style="color:#f87171">{wary_of_display}</div></div>
  </div>
  {pos_bar}
  <div class="levels-bar">
    <div class="level-item">Support <span>{fmt_level(play.get('support'))}</span></div>
    <div class="level-item">Resistance <span>{fmt_level(play.get('resistance'))}</span></div>
    <div class="level-item level-rr">R/R <span style="color:{'#f59e0b' if str(play.get('rr','')).startswith('VERIFY') else 'inherit'};font-weight:{'700' if str(play.get('rr','')).startswith('VERIFY') else '400'}">{play.get('rr') or '—'}</span></div>
  </div>
  {ta_row}
</div>"""


def render_ta_row(p: dict) -> str:
    """Compact TA indicator row — shared by active card and position watch card."""
    def ma_flag(above, label):
        if above is None: return f'<span style="color:#2a2520">{label}</span>'
        color = "#4ade80" if above else "#f87171"
        arrow = "↑" if above else "↓"
        return f'<span style="color:{color}">{label}{arrow}</span>'
    rsi_v   = p.get("rsi")
    macd_b  = p.get("macd_bull")
    rel_vol = p.get("rel_vol")
    rsi_color = "#4ade80" if rsi_v and rsi_v < 30 else "#f87171" if rsi_v and rsi_v > 70 else "#a89068"
    rsi_str  = f'<span style="color:{rsi_color}">RSI {rsi_v}</span>' if rsi_v else '<span style="color:#2a2520">RSI —</span>'
    if macd_b is None:
        macd_str = '<span style="color:#2a2520">MACD —</span>'
    elif macd_b:
        macd_str = '<span style="color:#4ade80">MACD ▲</span>'
    else:
        macd_str = '<span style="color:#f87171">MACD ▼</span>'
    vol_color = "#f5a623" if rel_vol and rel_vol >= 1.5 else "#6b5f42"
    vol_str  = f'<span style="color:{vol_color}">Vol {rel_vol}x</span>' if rel_vol else '<span style="color:#2a2520">Vol —</span>'
    return (
        '<div style="display:flex;gap:10px;flex-wrap:wrap;padding:8px 14px;'
        'background:#0e0c08;border:1px solid #1e1c18;border-radius:6px;'
        "font-family:'IBM Plex Mono',monospace;font-size:10px;margin-top:10px;letter-spacing:0.04em;\">"
        + ma_flag(p.get("ma9_above"),  "9MA")  + " "
        + ma_flag(p.get("ma20_above"), "20MA") + " "
        + ma_flag(p.get("ma200_above"),"200MA")+ ' <span style="color:#1e1c18">·</span> '
        + rsi_str + ' <span style="color:#1e1c18">·</span> '
        + macd_str + ' <span style="color:#1e1c18">·</span> '
        + vol_str + '</div>'
    )


# ── Render: Position Watch card ────────────────────────────────────────────
def render_position_watch_card(p: dict) -> str:
    ticker    = p.get("ticker", "?")
    posture   = p.get("posture", "WATCHING")
    tf_bias   = p.get("timeframe_bias", "—")
    outlook   = p.get("outlook", "—")
    notes     = p.get("notes", "")
    is_invalid = (posture == "INVALIDATED")
    card_class = "pw-card invalidated" if is_invalid else "pw-card"

    posture_class = f"posture-{posture.lower()}"
    override_tag = ' <span class="posture-override-tag">(user override)</span>' if p.get("_posture_overridden") else ''
    notes_html = f'<span class="pw-notes">{notes}</span>' if notes else ''

    # Accumulate zone bar
    az = p.get("accumulate_zone")
    curr = p.get("current")
    az_bar = ""
    if az and isinstance(az, list) and len(az) == 2 and curr is not None:
        try:
            zone_lo, zone_hi = float(az[0]), float(az[1])
            curr_v = float(curr)
            # Frame: 20% padding each side of zone for visualization
            span = zone_hi - zone_lo
            pad  = max(span * 1.0, zone_hi * 0.03)  # at least 3% of zone_hi
            view_lo = zone_lo - pad
            view_hi = zone_hi + pad
            view_span = view_hi - view_lo if view_hi > view_lo else 1.0

            zone_lo_pct = max(0, min(100, (zone_lo - view_lo) / view_span * 100))
            zone_hi_pct = max(0, min(100, (zone_hi - view_lo) / view_span * 100))
            curr_pct    = max(0, min(100, (curr_v  - view_lo) / view_span * 100))

            zone_width = zone_hi_pct - zone_lo_pct

            # In/above/below zone
            if zone_lo <= curr_v <= zone_hi:
                curr_color = "#4ade80"
            elif curr_v > zone_hi:
                curr_color = "#f5a623"
            else:
                curr_color = "#f87171"

            az_bar = f"""<div style="margin:12px 0 10px;">
      <div style="display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#5a5248;margin-bottom:4px;letter-spacing:0.06em;">
        <span>ACCUMULATE ZONE {fmt_level(zone_lo)} – {fmt_level(zone_hi)}</span>
        <span style="color:{curr_color}">CURRENT {fmt_level(curr_v)}</span>
      </div>
      <div style="height:6px;background:#1e1c18;border-radius:3px;position:relative;">
        <div style="position:absolute;left:{zone_lo_pct}%;top:0;height:100%;width:{zone_width}%;background:rgba(74,222,128,0.25);border-left:1px solid #4ade80;border-right:1px solid #4ade80;"></div>
        <div style="position:absolute;left:{curr_pct}%;top:-3px;transform:translateX(-50%);width:10px;height:12px;background:{curr_color};border-radius:2px;box-shadow:0 0 0 2px #0e0c08;"></div>
      </div>
    </div>"""
        except Exception:
            az_bar = ""

    # User-defined S/R row (if any set)
    user_sup = p.get("user_support") or []
    user_res = p.get("user_resistance") or []
    sr_row = ""
    if user_sup or user_res:
        sup_str = ", ".join(fmt_level(s) for s in user_sup) if user_sup else "—"
        res_str = ", ".join(fmt_level(r) for r in user_res) if user_res else "—"
        sr_row = f"""<div class="levels-bar">
    <div class="level-item">User Support <span>{sup_str}</span></div>
    <div class="level-item">User Resistance <span>{res_str}</span></div>
  </div>"""

    outlook_block = f'<div class="pw-outlook">{outlook}</div>' if outlook else ""
    ta_row = render_ta_row(p)

    return f"""<div class="{card_class}">
  <div class="pw-header">
    <span class="pw-ticker">{ticker}</span>
    <span class="{posture_class}">{posture}</span>{override_tag}
    {notes_html}
    <span class="pw-timeframe">{tf_bias}</span>
  </div>
  {outlook_block}
  {az_bar}
  {sr_row}
  {ta_row}
</div>"""


def render_position_watch_section(items_by_sector: dict) -> str:
    blocks = []
    for sector in ("semis", "energy"):
        items = items_by_sector.get(sector, [])
        label = SECTOR_LABEL[sector]
        thesis = SECTOR_THESIS[sector]
        thesis_html = (
            f'<div class="sector-thesis">'
            f'<div class="sector-thesis-title">Sector Thesis</div>'
            f'{thesis}</div>'
        )
        if not items:
            cards = '<div class="no-plays">— no position watch data —</div>'
        else:
            cards = "\n".join(render_position_watch_card(p) for p in items)
        blocks.append(f"""<div class="sector-block">
  <div class="sector-header"><span>{label}</span></div>
  {thesis_html}
  {cards}
</div>""")
    return "\n".join(blocks)


# ── Render: Exposure Signals table ─────────────────────────────────────────
def render_exposure_signals(signals: list) -> str:
    if not signals:
        return '<div class="no-plays">— no exposure signals generated —</div>'

    # Enforce EXPOSURE_ASSETS ordering; insert HOLD placeholder for any missing
    by_ticker = {s.get("ticker", "").upper(): s for s in signals if isinstance(s, dict)}
    rows = []
    for t in EXPOSURE_ASSETS:
        s = by_ticker.get(t)
        if not s:
            signal = "HOLD"
            reason = "(no signal returned — defaulted to HOLD)"
        else:
            signal = s.get("signal", "HOLD").upper()
            if signal not in ("INCREASE", "HOLD", "REDUCE"):
                signal = "HOLD"
            reason = s.get("reason", "—")
        sig_class = f"sig-{signal.lower()}"
        rows.append(
            f"<tr><td class='exposure-ticker'>{t}</td>"
            f"<td><span class='{sig_class}'>{signal}</span></td>"
            f"<td class='exposure-reason'>{reason}</td></tr>"
        )
    return f"""<table class="exposure-table">
  <tr><th style="width:90px">Asset</th><th style="width:120px">Signal</th><th>Reason</th></tr>
  {''.join(rows)}
</table>"""


# ── Render: Macro context (preserved from v2.6) ────────────────────────────
def render_macro_context(pulse: dict) -> str:
    fg_val = int(pulse["fear_greed"].get("value", 50))
    fg_cls = pulse["fear_greed"].get("classification", "")

    def sig_row(label, signal_str, color):
        return (f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
                f'padding:10px 0;border-bottom:1px solid #1e1c18;">'
                f'<span style="color:#6b5f42;font-size:10px;letter-spacing:0.1em;'
                f'text-transform:uppercase;min-width:60px">{label}</span>'
                f'<span style="color:{color};font-size:13px;line-height:1.6;'
                f'text-align:right;flex:1;padding-left:16px">{signal_str}</span></div>')

    dxy = pulse["dxy"]; tlt = pulse["tlt"]; gold = pulse["gold"]; oil = pulse["oil"]
    dxy_trend = dxy.get("trend", "neutral")
    tlt_trend = tlt.get("trend", "neutral")
    gold_trend = gold.get("trend", "neutral")
    try: oil_pct = float(oil.get("change_pct", 0))
    except: oil_pct = 0.0

    dxy_color  = "#f87171" if dxy_trend == "rising" else "#4ade80" if dxy_trend == "falling" else "#a89068"
    tlt_color  = "#f87171" if tlt_trend == "falling" else "#4ade80" if tlt_trend == "rising" else "#a89068"
    gold_color = "#f5a623" if gold_trend == "rising" else "#a89068"
    oil_color  = "#f87171" if oil_pct > 1.0 else "#4ade80" if oil_pct < -1.0 else "#a89068"
    fg_color   = "#f87171" if fg_val < 30 else "#f5a623" if fg_val < 50 else "#4ade80"

    dxy_impl = ("Dollar rising — crypto headwind, commodity pressure" if dxy_trend == "rising"
                else "Dollar falling — crypto tailwind, commodity support" if dxy_trend == "falling"
                else "Dollar neutral — no directional signal")
    tlt_impl = ("Rates bid — growth multiple compression, semis at risk" if tlt_trend == "falling"
                else "Rates easing — growth multiples supported" if tlt_trend == "rising"
                else "Rates neutral — no multiple compression signal")
    gold_impl = ("Gold rising — risk-off confirmed, defensive posture" if gold_trend == "rising"
                 else f"Gold flat ({gold.get('signal','—')}) — no risk-off signal")
    oil_impl = (f"Oil {oil_pct:+.1f}% — energy input costs rising, semi/hyperscaler margin watch" if oil_pct > 1.0
                else f"Oil {oil_pct:+.1f}% — inflation relief, energy cost headwind easing" if oil_pct < -1.0
                else f"Oil flat — no directional energy signal")
    fg_impl = f"{fg_val} {fg_cls} — historically precedes sharp reversals, watch for capitulation signal"

    rows = "".join([
        sig_row("DXY", dxy_impl, dxy_color),
        sig_row("TLT", tlt_impl, tlt_color),
        sig_row("GOLD", gold_impl, gold_color),
        sig_row("OIL", oil_impl, oil_color),
        sig_row("F&G", fg_impl, fg_color),
    ])

    return (f'<div style="padding:4px 0">'
            f'<div style="font-size:10px;letter-spacing:0.12em;text-transform:uppercase;'
            f'color:#6b5f42;margin-bottom:12px">Macro Conditions → Cross-Section Implications</div>'
            f'{rows}'
            f'</div>')


# ── Render: Full HTML ──────────────────────────────────────────────────────
def render_section_plays(plays: list) -> str:
    if not plays:
        return '<div class="no-plays">— no active trades generated —</div>'

    # Sort by setup_score DESC; filter _drop and _developing
    viable = [p for p in plays if not p.get('_drop') and not p.get('_developing')]
    viable.sort(key=lambda p: p.get('setup_score', 0), reverse=True)

    if not viable:
        # All dropped — show developing notice if any
        developing = [p for p in plays if p.get('_developing')]
        if developing:
            tickers = ', '.join(p.get('ticker','?') for p in developing)
            return f'<div class="no-plays">NO SIZE — DEVELOPING: {tickers}</div>'
        return '<div class="no-plays">— no qualifying setups today —</div>'

    the_trade = viable[0]
    on_watch  = [p for p in viable[1:] if p.get('setup_score', 0) >= 5]

    html = '<div class="the-trade-header">THE TRADE</div>'
    html += render_play_card(the_trade)

    if on_watch:
        rows = ''
        for p in on_watch:
            stype   = p.get('setup_type', 'NO_SETUP').replace('_', ' ')
            score   = p.get('setup_score', 0)
            upgrade = p.get('upgrade_condition', '') or p.get('watch', '—')
            rows += (
                '<div class="on-watch-row">'
                f'<span class="ow-ticker">{p.get("ticker","?")}</span>'
                f'<span class="ow-type">{stype}</span>'
                f'<span class="ow-score">{score}/10</span>'
                f'<span class="ow-condition">{upgrade}</span>'
                '</div>'
            )
        html += (
            '<div class="on-watch-section">'
            '<div class="on-watch-header">On Watch</div>'
            + rows + '</div>'
        )

    return html


def render_html(pulse, actives, day_trades, position_watch_by_sector, exposure_signals, timestamp) -> str:
    btc = pulse["btc"]; eth = pulse["eth"]; sol = pulse["sol"]
    spy = pulse["spy"]; gold = pulse["gold"]; dxy = pulse["dxy"]
    fg  = pulse["fear_greed"]

    def btc_fmt(v):
        try: return f"${float(str(v).replace('$','').replace(',','')):,.0f}"
        except: return str(v)

    pulse_bar = f"""<div class="pulse-bar">
        <div class="pulse-item">BTC <span>{btc_fmt(btc.get('price','?'))}</span> {fmt_change(btc.get('change_pct','?'))}</div>
        <div class="pulse-divider"></div>
        <div class="pulse-item">ETH <span>{fmt_price(eth.get('price','?'))}</span> {fmt_change(eth.get('change_pct','?'))}</div>
        <div class="pulse-divider"></div>
        <div class="pulse-item">SOL <span>{fmt_price(sol.get('price','?'))}</span> {fmt_change(sol.get('change_pct','?'))}</div>
        <div class="pulse-divider"></div>
        <div class="pulse-item">SPY <span>{fmt_price(spy.get('price','?'))}</span> {fmt_change(spy.get('change_pct','?'))}</div>
        <div class="pulse-divider"></div>
        <div class="pulse-item">Gold <span>{gold.get('signal','—')}</span></div>
        <div class="pulse-divider"></div>
        <div class="pulse-item">DXY <span>{dxy.get('signal','—')}</span></div>
        <div class="pulse-divider"></div>
        <div class="pulse-item">F&amp;G <span>{fg.get('value','?')}</span> <span style="color:#6b5f42">{fg.get('classification','?')}</span></div>
    </div>"""

    heat_table_html = render_heat_table()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Potential Plays — {timestamp}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="header-top">
      <div class="header-title">⚡ Potential Plays</div>
      <div class="header-stamp">{timestamp}</div>
    </div>
    <div class="header-sub">sovereign intelligence system · v2.8 · model: {MODEL}</div>
  </div>
  {pulse_bar}
  {heat_table_html}
  <div class="section day-trade">
    <div class="section-header"><span class="section-label">⚡ Day Trades</span></div>
    <div class="section-body">{render_section_plays(day_trades)}</div>
  </div>
  <div class="section actives">
    <div class="section-header"><span class="section-label">↗ Swing Trades</span></div>
    <div class="section-body">{render_section_plays(actives)}</div>
  </div>
  <div class="section position-watch">
    <div class="section-header"><span class="section-label">🎯 Position Watch</span></div>
    <div class="section-body">{render_position_watch_section(position_watch_by_sector)}</div>
  </div>
  <div class="section exposure">
    <div class="section-header"><span class="section-label">📊 Exposure Signals</span></div>
    <div class="section-body">{render_exposure_signals(exposure_signals)}</div>
  </div>
  <div class="section macro">
    <div class="section-header"><span class="section-label">🌐 Macro Conditions</span></div>
    <div class="section-body">{render_macro_context(pulse)}</div>
  </div>
  <div class="footer">
    <span>sovereign intelligence system · v2.8</span>
    <span>{MODEL} · {timestamp}</span>
  </div>
</div>
</body>
</html>"""


# ── JSON sidecar ───────────────────────────────────────────────────────────
def write_json_sidecar(actives, day_trades, position_watch_by_sector, exposure_signals, file_ts, timestamp):
    """
    v2.7 schema:
    {
      generated, model, version,
      actives: [...],
      position_watch: {semis: [...], energy: [...]},
      exposure_signals: [...]
    }
    Node 8 reads 'actives' (same shape as old sections.crypto/semis/energy).
    """
    # Strip internal fields from JSON output
    def clean(p):
        return {k: v for k, v in p.items() if not k.startswith("_")}

    out = {
        "generated": timestamp,
        "model": MODEL,
        "version": "2.7.1",
        "actives": [clean(p) for p in actives],
        "day_trades": [clean(p) for p in day_trades],
        "position_watch": {
            s: [clean(p) for p in items]
            for s, items in position_watch_by_sector.items()
        },
        "exposure_signals": exposure_signals,
    }
    out_path = OUT_DIR / f"Plays_{file_ts}.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"  ✅ Sidecar: Plays_{file_ts}.json")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("\n──────────────────────────────────────────────────")
    print("  SOVEREIGN — POTENTIAL PLAYS v2.7")
    print(f"  {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    print("──────────────────────────────────────────────────")

    timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    file_ts   = datetime.now().strftime("%Y-%m-%d_%H%M")

    if not CONTEXT_PATH.exists():
        print("❌ context.json not found — run fetch_news.py first")
        sys.exit(1)

    ctx     = load_json(CONTEXT_PATH)
    pulse   = extract_pulse(ctx)
    watched = load_watched_levels()
    intraday_data = ctx.get("market", {}).get("intraday", {})
    if not intraday_data:
        print("  ⚠️  market.intraday empty — DAY TRADE tier will be skipped")

    # Compute levels for all 8 tickers once
    all_tickers = list(set(
        ACTIVES + [t for sector in POSITION_WATCH.values() for t in sector] + EXPOSURE_ASSETS
    ))
    print("\n  📐 Computing levels...")
    for t in all_tickers:
        print(f"    {t}...")
    all_levels = _core_compute_all_levels(all_tickers)

    # ── ACTIVE TRADES ──
    print("\n  🧠 Generating active trades...")
    actives_raw = generate_actives(build_actives_prompt(pulse, all_levels))
    actives = _core_enrich_actives(actives_raw, all_levels)
 
    # --- Session 40 gates ---
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from validators.same_day_reentry import check_same_day_reentry
    from validators.macro_gates import run_all_gates
    posture = pulse.get("posture", "")
    actives = check_same_day_reentry(actives)
    actives = run_all_gates(actives, pulse, posture)
    actives = _apply_swing_watchfor_waryof(actives)

    # ── DAY TRADES ──
    print("\n  ⚡ Generating day trades...")
    day_trades = []
    if intraday_data:
        dt_raw    = generate_day_trades(build_day_trade_prompt(pulse, intraday_data))
        day_trades = enrich_day_trades(dt_raw, all_levels, intraday_data)
        day_trades = run_all_gates(day_trades, pulse, posture)
    else:
        print("  ⚠️  Skipping DAY TRADE tier — no intraday data")

    # ── POSITION WATCH ──
    print("\n  🎯 Generating position watch...")
    position_watch_by_sector = {}
    for sector in ("semis", "energy"):
        raw = generate_position_watch(
            build_position_watch_prompt(pulse, all_levels, watched, sector),
            sector
        )
        position_watch_by_sector[sector] = _core_enrich_position_watch(
            raw, all_levels, watched, sector
        )

    # ── EXPOSURE SIGNALS ──
    print("\n  📊 Generating exposure signals...")
    exposure = generate_exposure_signals(build_exposure_signals_prompt(pulse, all_levels, position_watch_by_sector))

    # ── Write outputs ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = render_html(pulse, actives, day_trades, position_watch_by_sector, exposure, timestamp)
    out_html = OUT_DIR / f"Plays_{file_ts}.html"
    with open(out_html, "w") as f:
        f.write(html)
    print(f"\n  ✅ HTML: Plays_{file_ts}.html")

    write_json_sidecar(actives, day_trades, position_watch_by_sector, exposure, file_ts, timestamp)
    print("──────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
