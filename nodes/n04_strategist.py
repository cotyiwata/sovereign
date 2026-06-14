# daily_ideas.py — Node 03: The Strategist
# Sovereign Intelligence System
# v2.0 — Stream A/B separation layer

import re

import json
import os
import shutil
import tempfile
from datetime import datetime

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT, CONTEXT_FILE, load_config
from core.constants import MODEL_FALLBACK, MODEL
from core.llm  import query_ollama, query_with_fallback
from core.lore import load_universe_context

try:
    from core.rag.retriever import retrieve as _rag_retrieve
    _RAG_AVAILABLE = True
except Exception:
    _rag_retrieve  = None
    _RAG_AVAILABLE = False

_config          = load_config()   # safe — returns {} instead of crashing if missing
_active_universe = _config.get("active_universe", "Age-of-Aether")
_universe_slug   = _active_universe.replace(" ", "-")
_LORE_STATE_MAP = {
    "Age-of-Aether":   VAULT_ROOT / "Data" / "lore_state_ageofaether.json",
    "Veil-Ascendancy": VAULT_ROOT / "Data" / "veil_ascendancy" / "lore_state_veil_ascendancy.json",
    "The-Vigil":       VAULT_ROOT / "Data" / "vigil" / "vigil_state.json",
}

_UNIVERSE_SECTION_IV = {
    "Age-of-Aether": """Focus on one or two characters per dispatch — not all six every day.
Be concrete about their situation, their choices, what they are facing.
Evolve existing threads. Do not reset or ignore active conflicts.
Write at ground level — dusty frontier outposts, crumbling station corridors, open wastes under a wrong-colored sky.
These characters do not have the full picture. Write from inside that limitation.
Include:
- CHARACTER ACTIVITY: One character facing one concrete moment. Name them. Be specific.
- FACTION MOVE: What House Vorn, House Pyros, or a Merchant faction is doing that matters right now.
- WOUND ZONE STATUS: Brief update on one of the three active Wound Zones (Ashveil, Karath, Thresh). Stable, expanding, or breached?
- THREAT SIGNAL: What the Horrors are doing at the edges. Specific and ominous. Not yet an invasion — probing.""",
    "Veil-Ascendancy": """Focus on one or two characters per dispatch — not all six every day.
Be concrete about their situation, their choices, what they are facing.
Evolve existing threads. Do not reset or ignore active conflicts.
Include:
- CHARACTER ACTIVITY: One character facing one concrete moment. Name them. Be specific.
- FACTION MOVE: What a major faction is doing that matters right now.
- WOUND ZONE STATUS: Brief update on one of the active zones. Stable, expanding, or breached?
- THREAT SIGNAL: What the primary threat is doing at the edges. Specific and ominous.""",
    "The-Vigil": """Focus on one or two characters per dispatch — not all six every day.
Be concrete about their situation, their choices, what they are facing.
Evolve existing threads. Do not reset or ignore active conflicts.
Write at ground level — decaying orbital platforms, corrupted Anchor Points, the void between stations.
These characters do not have the full picture. Write from inside that limitation.
Include:
- CHARACTER ACTIVITY: One character in an operational moment — a firefight inside a corrupted zone, a forced extraction under fire, a tactical hold with the line collapsing, or a decision with immediate physical consequence. Name them. Be specific. Avoid purely introspective scenes unless the beat demands it.
- FACTION MOVE: What the Reclamation Directorate or the Forsaken network is doing that matters right now — a strategic move, a resource play, an escalation.
- WOUND ZONE STATUS: Brief update on one of the three active zones (The Pale Anchor, Ember Point, The Foundry). Stable, expanding, or breached?
- THREAT SIGNAL: What the Choir, Remnant Tide, or Eldest is doing at the edges. Specific and ominous. Not yet an invasion — probing.""",
}
_section_iv_instructions = _UNIVERSE_SECTION_IV.get(_universe_slug, _UNIVERSE_SECTION_IV["Age-of-Aether"])

VAULT_MARKET = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
VAULT_LORE   = VAULT_ROOT / "03-Universes" / _universe_slug / "Daily-Expansions"


# ─────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────

SYSTEM_PROMPT_A = """You are an elite market intelligence analyst delivering a daily briefing.

Your voice: precise, direct, zero filler. Every line earns its place or is cut.
No hedging language. No sign-offs. No offers to elaborate. No lore references.
This brief is read by a disciplined crypto and AI/tech equity trader. It must stand alone.

OUTPUT FORMAT — use EXACTLY these seven section headers, in this order:

PULSE
REGIME
NEWS
SCAN
LEVELS
SYNTHESIS
PORTFOLIO WATCH

GLOBAL CONSTRAINTS:
- Never reproduce instruction text or prompt scaffolding in output
- Never output the PULSE data block — it is pre-rendered and prepended automatically
- No filler sign-offs or closing remarks
- The brief ends after PORTFOLIO WATCH. Hard stop.

SIGNAL FEED FORMAT:
For each category you will see headlines followed by "→ SO WHAT:"
Write ONLY the SO WHAT response. Max 2 sentences. Directional. No hedging. No restating headlines.

PULSE: 4-5 sentences. Integrate BTC, SPY, Gold/DXY, Fear & Greed into one coherent market read. Include specific price action observations, what is leading vs lagging, and the net read for positioning.
REGIME: 4-5 sentences. Macro regime, what drives it, what would change it, and how it directly affects your crypto and AI equity universe.
NEWS: 4-5 items from the signal feed. Use exactly this format — no deviations:

FEATURED | [CATEGORY] | [headline, 10 words max]
[What happened, 2 sentences.] [Why it matters for crypto/AI equity traders, 1 sentence.]
SIGNAL: [one sentence — direct relevance to BTC, AI semis, or AI energy thesis]

QUICK | [CATEGORY] | [headline, 8 words max]
[1-2 sentences. Fact + market relevance. No hedging.]

Categories: CRYPTO, MACRO, AI, ENERGY
Pick the single highest-signal item as FEATURED. Write 3-4 QUICK items from remaining signals.
SOURCE REJECT: Never attribute a NEWS item to 'Sovereign Intelligence Brief', 'this system', or any self-reference. If the RSS signal feed contains no external signals, output exactly: NO EXTERNAL SIGNALS — RSS RETURNED EMPTY — and stop. Do not generate synthetic news from your own prior output.
SOURCE RULE:
- SOURCE must name an external publication, data release, named analyst, or RSS feed.
- "Chronicle" is NOT a valid source. Chronicle is an internal synthesis layer, not external intelligence.
- If no external signal exists, write: SOURCE: NO EXTERNAL SIGNAL — [one-line internal observation]
- Do not fabricate causal chains from internal synthesis.
SCAN: 1-2 sentences per signal category. Lead with sharpest insight.
LEVELS: 2-3 structurally significant price levels. Exact prices, one sentence each on why the level matters.
SYNTHESIS: Six labeled fields in this exact order — no prose outside these fields:
CONFLUENCE: [one sentence — what 3+ watched tickers are collectively signaling]
ROTATION: [top mover] leading | [lagging name] lagging | theme: [one phrase]
ASYMMETRIC SETUP: [ticker] — [condition] at [specific $ price level] — [LONG/SHORT bias only — no entry or stop]
POSTURE DERIVATION: [condition A] + [condition B] → [posture word]
POSTURE: [Hold / Watch / Opportunity]
SETUP SIGNAL: Use exactly one of these three formats:
  SETUP SIGNAL: [TICKER] [LONG/SHORT] — see Plays
  SETUP SIGNAL: NO SETUP TODAY
  SETUP SIGNAL: WATCH ONLY — [one-line reason]
This field is REQUIRED. Do not omit it. Do not write a dash. No entry, stop, or target here.
RULE — SYNTHESIS ORIGINALITY: Do not repeat any sentence from PULSE or DOMINANT NARRATIVE verbatim. Derive posture from convergence of REGIME, SCAN, and FORWARD 72H — do not restate earlier observations.
FORWARD 72H
Output EXACTLY three scenario blocks using the structure below. No prose outside the fields. No deviations. All six fields required per block.

LIKELY:
SCENARIO: [one sentence — most probable 72h outcome]
PROBABILITY: [e.g. 60%]
TRIGGER: [exact price level or event that confirms this scenario]
PATH: [one sentence — how price gets there from current levels]
EXPRESSION: [ticker] [LONG/SHORT] entry: [specific price level] | stop: [specific price level] | target: [specific price level] | size: [HIGH/MED/LOW] — or if levels cannot be derived AND SCAN shows no AT S / AT R flag: NO TRADE — INSUFFICIENT SETUP. Watch for [specific condition] at [specific price level].
REJECT: NO TRADE on LIKELY when SCAN shows any ticker flagged AT S or AT R. Derive entry, stop, and target from the flagged level. A LIKELY NO TRADE when structural flags exist is a generation failure.
MOST LIKELY EXPRESSION RULE:
- If a trade is available: entry + stop + target required.
- If NO TRADE: state the specific blocking condition.
  Format: NO TRADE — [REASON]
  Valid reasons: SETUP SCORE BELOW THRESHOLD | R/R BELOW MINIMUM | NO STRUCTURAL LEVEL AVAILABLE | POSTURE CONFLICT
- Bare "NO TRADE — INSUFFICIENT SETUP" with no reason will FAIL the critic.
INVALIDATION: [exact price or event that kills this scenario]

BULL:
SCENARIO: [one sentence — upside case]
PROBABILITY: [e.g. 25%]
TRIGGER: [exact price level or event]
PATH: [one sentence]
EXPRESSION: [ticker] [LONG/SHORT] entry: [specific price level] | stop: [specific price level] | target: [specific price level] | size: [HIGH/MED/LOW] — or NO TRADE — INSUFFICIENT SETUP
INVALIDATION: [exact price or event]

BEAR:
SCENARIO: [one sentence — downside case]
PROBABILITY: [e.g. 15%]
TRIGGER: [exact price level or event]
PATH: [one sentence]
EXPRESSION: [ticker] SHORT entry: [specific price level] | stop: [specific price level] | target: [specific price level] | size: [HIGH/MED/LOW] — or NO TRADE — INSUFFICIENT SETUP — or defensive MACRO expression only. A LONG inside BEAR is structurally invalid.
INVALIDATION: [exact price or event]

Final line: one word only — Hold, Watch, or Opportunity
PORTFOLIO WATCH
3-4 sentences. RAG-backed portfolio thesis validation only — no execution parameters.
1. AI ENERGY NEXUS: Is the thesis (VST, CEG, VRT, NNE, SMR) confirming or cracking?
2. ROTATION: Any sector rotation signal that supports or undermines the thesis?
3. BTC ACCUMULATION: Are accumulation conditions improving or deteriorating?
Name specific tickers and levels. Do not repeat PULSE or REGIME.
QUALITY ENFORCEMENT RULES

1. SELF-CITATION REJECT: NEWS items must trace to external RSS signals only. Any item attributed to 'Sovereign Intelligence Brief', 'this system', or any self-reference is invalid. Replace with the next available external signal or output: NO EXTERNAL SIGNALS — RSS RETURNED EMPTY.

2. COMPLETE SETUPS ONLY: Every EXPRESSION field must include entry, stop, AND target. Entry + stop with no target is an incomplete setup — output NO TRADE — INSUFFICIENT SETUP instead.

3. NO VAGUE GEOPOLITICAL FILLER: NEWS items referencing China/Taiwan/Russia/Ukraine/OPEC must state a direct consequence to a specific watched ticker or price level. Geopolitical color with no market linkage is omitted.

4. LARGE MOVER VERDICT REQUIRED: Any equity flagged >3% in SCAN must receive an explicit LONG, SHORT, or AVOID verdict with one-line rationale. Silent pass-through of large movers is a generation failure.

5. FORWARD-LOOKING TRIGGERS ONLY: TRIGGER fields must describe future conditions that would confirm the scenario. Past-tense or already-observed conditions are invalid as triggers.

6. POSTURE UPGRADE CONDITION: POSTURE may only upgrade (Hold → Watch → Opportunity) when FORWARD 72H LIKELY EXPRESSION contains a fully executable trade with entry, stop, and target. A NO TRADE LIKELY scenario cannot support Watch or Opportunity posture.

7. TRENDING ASSETS ABOVE MA20: Any watched ticker trading above its MA20 with positive SCAN alignment must appear in SYNTHESIS or FORWARD 72H. Omitting a trending aligned ticker is a generation failure."""

# Load lore context via core.lore — single access point for all universes
_lore_ctx     = load_universe_context(_config)
_lore_summary = _lore_ctx["arc_summary"] or "A fictional universe in active development."
_arc_name     = _lore_ctx["arc_name"]    or "Unknown Arc"
_arc_next     = _lore_ctx["next_beat"]   or ""
_ignition_beats = _lore_ctx.get("ignition_beats", [])
_recent_beats   = [b for b in _ignition_beats if isinstance(b, dict) and b.get("classification") in ("LEGEND", "CANON")][-5:]
if _recent_beats:
    _beats_lines = "\n".join(f"- [{b.get('classification','')}] {b.get('beat_text','')[:120]}" for b in _recent_beats)
    _beats_block = f"\nRecent classified beats (do not contradict):\n{_beats_lines}\n"
else:
    _beats_block = ""

SYSTEM_PROMPT_B = f"""You are the Chronicler of {_active_universe} — a living fictional universe.

The universe summary:
{_lore_summary}

Active arc: {_arc_name}
Next story beat: {_arc_next}{_beats_block}

Your role:
- Expand the world through vivid, grounded storytelling
- Stay strictly within established canon — never invent new mechanics
- Focus on characters, tension, and consequence
- Each expansion advances the active arc meaningfully
- Voice: cinematic, precise, no filler

GLOBAL CONSTRAINTS:
- Never reproduce instruction text in output
- No meta-commentary about the universe or your role
- The expansion ends after the final section. Hard stop.



You will receive market signals as inspiration only. You are not explaining markets.
You are writing fiction. The story runs on its own internal logic.
Loose signal mapping: volatility → Wound Zone expansion / Zhal'Thar incursion pressure, momentum → Aether surge / character escalation, macro risk → House faction conflict / Merchant Wars maneuvering, entropy → Aether destabilization, systemic threat → First Avatar stirring.
Never name real assets, tickers, companies, or prices. Never explain the mapping.

Your voice: cinematic, gritty, stylized. Loud and visceral on the surface, philosophical underneath.
These characters are unproven — flashes of potential, not mastery. The world is ending in slow motion and only a few can feel it.

GLOBAL CONSTRAINTS:
- Do not reference real markets, prices, tickers, or company names
- Do not explain the signal-to-story mapping
- Never reproduce the ESTABLISHED LORE CONTEXT block verbatim — use it as reference only
- Do not contradict established lore
- No closing remarks. The dispatch ends after the final element. Hard stop.

SECTION IV — {_active_universe.upper()}:
{_section_iv_instructions}

SECTION V — CREATIVE LAB: THE SOVEREIGN SPARK:
- ATMOSPHERE: Full paragraph, minimum 4 sentences, sensory and specific. Frontier dust, crumbling Aether infrastructure, sky that looks wrong. Put the reader inside the world.
- WORLD MECHANIC: Bold the mechanic name. Full explanation of one Vigil system — Sentinel/Phantom/Sage class distinctions, Forsaken transformation paths (Drowned/Converted/Broken), Anchor Point mechanics, the three threat types (Choir/Remnant Tide/Eldest), light behavior near corrupted Anchor Points, or the transformation protocol’s true purpose. What makes it dangerous, coveted, or load-bearing.

CODEX ENTRY: Only generate if signals include a confirmed security breach, major AI release,
significant geopolitical shock, or major regulatory action.
If threshold not met: complete silence. No placeholder text. No explanation."""

CRITIC_PROMPT = """You are a factual consistency auditor reviewing a market intelligence brief.
You will receive RAW DATA and a GENERATED BRIEF. Evaluate each checklist item as PASS or FAIL.

CRITIC CHECKLIST:
[ ] FORWARD_72H_LEVELS: All 3 EXPRESSION fields contain entry price + stop price + target price — OR the field contains exactly NO TRADE — INSUFFICIENT SETUP. NO TRADE is valid and must be treated as PASS for this check.
[ ] FORWARD_72H_NO_DIRECTION: No EXPRESSION field contains direction language (Long/Short/Buy/Sell) without a specific price level
[ ] NEWS_CAUSAL_CHAIN: At least one NEWS item contains a CAUSAL CHAIN linked to a watched ticker
[ ] SYNTHESIS_CONFLUENCE: CONFLUENCE field names 3 or more tickers by symbol
[ ] SYNTHESIS_SETUP_LEVEL: ASYMMETRIC SETUP contains a specific $ price level
[ ] SYNTHESIS_POSTURE_DERIVATION: POSTURE DERIVATION field is present and non-empty
[ ] DATA_INTEGRITY: No percentage value (e.g. Truflation %, 24h change) is formatted as a $ price level anywhere in the brief
[ ] EXPRESSION_TARGET: All 3 EXPRESSION fields contain a target price level, or explicit NO TRADE — INSUFFICIENT SETUP. An EXPRESSION with entry + stop but no target is INCOMPLETE — treat as FAIL.
[ ] LIKELY_NO_TRADE_GATE: If LIKELY EXPRESSION is NO TRADE — INSUFFICIENT SETUP, check whether the SCAN section contains any ticker flagged AT S or AT R. If AT S or AT R flags are present in SCAN, LIKELY NO TRADE is a FAIL.

CONFIDENCE SCORING — apply strictly:
HIGH   → 0 FAILs
MEDIUM → 1-2 FAILs
LOW    → 3-4 FAILs
FAIL   → 5+ FAILs, OR any FORWARD 72H EXPRESSION contains direction without a price level

Respond in this exact format only:
VERDICT: PASS or FLAG
CHECKLIST:
- FORWARD_72H_LEVELS: [PASS or FAIL] — [one sentence reason if FAIL]
- FORWARD_72H_NO_DIRECTION: [PASS or FAIL] — [one sentence reason if FAIL]
- NEWS_CAUSAL_CHAIN: [PASS or FAIL] — [one sentence reason if FAIL]
- SYNTHESIS_CONFLUENCE: [PASS or FAIL] — [one sentence reason if FAIL]
- SYNTHESIS_SETUP_LEVEL: [PASS or FAIL] — [one sentence reason if FAIL]
- SYNTHESIS_POSTURE_DERIVATION: [PASS or FAIL] — [one sentence reason if FAIL]
- DATA_INTEGRITY: [PASS or FAIL] — [one sentence reason if FAIL]
- EXPRESSION_TARGET: [PASS or FAIL] — [one sentence reason if FAIL]
- LIKELY_NO_TRADE_GATE: [PASS or FAIL] — [one sentence reason if FAIL]
FAIL_COUNT: [integer 0-9]
CONFIDENCE: HIGH or MEDIUM or LOW or FAIL
ISSUES: [for each FAIL item: name the section and the specific criterion that failed. Generic quality notes are not acceptable. Write None if all PASS.]"""


# ─────────────────────────────────────────────
# SECTION RENDERERS (shared)
# ─────────────────────────────────────────────

def render_section_i(context: dict) -> str:
    """Render PULSE block from v4.0 context packet."""
    market = context.get("market", {})
    crypto = market.get("crypto", {})
    core = market.get("core", {})
    fear_greed = market.get("fear_greed", {})
    equities = context.get("equities", {})
    sectors = context.get("sectors", {})

    # BTC — from market.crypto
    btc = crypto.get("BTC", {})
    btc_str = btc.get("price", "N/A")
    btc_chg_raw = btc.get("change_24h", "")
    btc_chg_str = f" ({btc_chg_raw})" if btc_chg_raw else ""

    # SPY — from market.core
    spy = core.get("SPY", {})
    spy_str = spy.get("price", "N/A")
    spy_chg_raw = spy.get("change_24h", "")
    spy_chg_str = f" ({spy_chg_raw})" if spy_chg_raw else ""


    # TSLA — from market.core (fetched by Node 1)
    tsla = core.get("TSLA", {})
    tsla_str = tsla.get("price", "N/A")
    tsla_chg_raw = tsla.get("change_24h", "")
    tsla_chg_str = f" ({tsla_chg_raw})" if tsla_chg_raw else ""



    # Gold + DXY — signal mode from market.core
    gld_sig = core.get("Gold", {}).get("signal", "—")
    dxy_sig = core.get("DXY", {}).get("signal", "—")

    # Fear & Greed
    fg_val = fear_greed.get("value", "N/A")
    fg_cls = fear_greed.get("classification", "")
    fg_str = f"{fg_val} ({fg_cls})" if fg_cls else str(fg_val)

    # Flagged equities
    flagged = []
    for group, tickers in equities.items():
        if isinstance(tickers, dict):
            for ticker, data in tickers.items():
                if isinstance(data, dict) and data.get("flagged"):
                    chg = data.get("change_pct", 0)
                    flagged.append(f"  {ticker}: {chg:+.2f}%")
    equity_block = "\n".join(flagged) if flagged else "  No significant movers"

    # Flagged sectors
    flagged_sectors = []
    for etf, data in sectors.items():
        if isinstance(data, dict) and data.get("flagged"):
            chg = data.get("change_pct", 0)
            flagged_sectors.append(f"  {etf}: {chg:+.2f}%")
    sector_block = "\n".join(flagged_sectors) if flagged_sectors else "  No significant moves"

    # Calendar block — FOMC, CPI, earnings, 72h manual alerts
    cal_lines = []
    for a in context.get("calendar_alerts", []):
        cal_lines.append(f"  ⚠ {a.get('name', a.get('event', '?'))} — 72h alert")
    for e in context.get("catalysts", {}).get("upcoming", []):
        label = "TODAY" if e["days_away"] == 0 else f"in {e['days_away']}d ({e['date']})"
        cal_lines.append(f"  {e['event']}: {label}")
    for e in context.get("earnings", {}).get("day_of", []):
        cal_lines.append(f"  EARNINGS TODAY: {e['ticker']}")
    for e in context.get("earnings", {}).get("upcoming", [])[:5]:
        cal_lines.append(f"  Earnings {e['ticker']}: in {e['days_away']}d ({e['date']})")
    calendar_block = "\n".join(cal_lines) if cal_lines else "  None in window"

    return f"""PULSE

📊 CORE
  BTC: {btc_str}{btc_chg_str}
  SPY: {spy_str}{spy_chg_str}
  TSLA: {tsla_str}{tsla_chg_str}
  Gold: {gld_sig}
  DXY: {dxy_sig}
  Fear & Greed: {fg_str}

📈 EQUITY FLAGS
{equity_block}

🏭 SECTOR FLAGS
{sector_block}

📅 CATALYST CALENDAR
{calendar_block}"""


def render_section_ii_scaffold(context: dict) -> str:
    """Render Section II headlines — model fills SO WHAT lines only."""
    structured = context.get("signals", {}).get("structured", {})

    def headlines(key):
        items = structured.get(key, [])
        if not items:
            return "  • No fresh signals."
        return "\n".join(f"  • {h['headline']}" for h in items[:3])

    return f"""SECTION II — SIGNAL FEED

⚡ AI-ENERGY
{headlines('Energy')}
→ SO WHAT:

💰 CRYPTO
{headlines('Crypto')}
→ SO WHAT:

🌍 MACRO
{headlines('Macro_Policy')}
→ SO WHAT:

🤖 LOCAL AI / TECH
{headlines('AI_Tech')}
→ SO WHAT:"""


def render_chronicle(context: dict) -> str:
    chronicle = context.get("chronicle", {})
    if not chronicle or chronicle.get("days_analyzed", 0) == 0:
        return "CHRONICLE: Insufficient history."

    btc_series = " → ".join(chronicle.get("btc_price_series", []))
    fg_series = " → ".join(chronicle.get("fear_greed_series", []))
    narratives = "\n".join(chronicle.get("narrative_thread", [])[:5])
    risks = "\n".join(chronicle.get("risk_thread", [])[:5])

    yesterday_posture   = chronicle.get("yesterday_posture", "")
    yesterday_narrative = chronicle.get("yesterday_narrative", "")
    delta_line = ""
    if yesterday_posture or yesterday_narrative:
        posture_ref   = yesterday_posture   or "unknown"
        narrative_ref = yesterday_narrative or "unknown"
        delta_line = f"\nYESTERDAY DELTA: Posture → {posture_ref} | Narrative: {narrative_ref}"

    return f"""CHRONICLE — LAST {chronicle['days_analyzed']} DAYS{delta_line}
BTC Trajectory: {btc_series}
Fear & Greed Trajectory: {fg_series}
Narrative Thread:
{narratives}
Risk Thread:
{risks}"""


# ─────────────────────────────────────────────
# PROMPT BUILDERS
# ─────────────────────────────────────────────

def build_prompt_a(context: dict) -> str:
    """Stream A — Market Intel brief. PULSE/REGIME/SCAN/LEVELS/SYNTHESIS format."""
    timestamp = context.get("timestamp", "unknown")
    section_i = render_section_i(context)
    section_ii = render_section_ii_scaffold(context)
    chronicle = render_chronicle(context)
    macro = context.get("market", {}).get("macro_regime", {})
    macro_str = f"Fed: {macro.get('fed','?')} | Inflation: {macro.get('inflation','?')} | Labor: {macro.get('labor','?')}"
    truf = context.get("market", {}).get("truflation", {})
    truf_rate = truf.get("rate")
    if truf_rate is not None:
        if truf_rate > 5.0:
            truf_signal = f"Truflation {truf_rate}% YoY — HOT: rate hike risk live, growth multiples compressed"
        elif truf_rate > 3.5:
            truf_signal = f"Truflation {truf_rate}% YoY — ELEVATED: Fed restrictive, sticky inflation"
        elif truf_rate > 2.0:
            truf_signal = f"Truflation {truf_rate}% YoY — TARGET RANGE: neutral macro backdrop"
        else:
            truf_signal = f"Truflation {truf_rate}% YoY — COOLING: disinflation, rate cut narrative building"
    else:
        truf_signal = f"Truflation unavailable ({truf.get('status','unknown')})"

    # --- Catalyst block: FOMC + CPI + earnings ---
    catalysts = context.get("catalysts", {})
    cat_upcoming = catalysts.get("upcoming", [])
    earnings = context.get("earnings", {})
    earnings_today = earnings.get("day_of", [])
    earnings_upcoming = earnings.get("upcoming", [])

    calendar_alerts = context.get("calendar_alerts", [])
    cat_lines = []
    for a in calendar_alerts:
        cat_lines.append(f"  ⚠ {a.get('name', a.get('event', '?'))} — 72h window")
    for e in cat_upcoming:
        label = "TODAY" if e["days_away"] == 0 else f"in {e['days_away']}d ({e['date']})"
        cat_lines.append(f"  {e['event']}: {label}")
    for e in earnings_today:
        cat_lines.append(f"  EARNINGS TODAY: {e['ticker']}")
    for e in earnings_upcoming[:5]:
        cat_lines.append(f"  Earnings {e['ticker']}: {e['date']} (in {e['days_away']}d)")
    catalyst_block = "\n".join(cat_lines) if cat_lines else "  None in 14-day window"

    # --- Price anchor block (Issue #18: prevents pre-split price confabulation) ---
    _anc_parts  = ["CURRENT PRICE ANCHORS — verified live data:"]
    _anc_crypto = context.get("market", {}).get("crypto", {})
    _anc_core   = context.get("market", {}).get("core",   {})
    for _t in ["BTC", "ETH", "SOL"]:
        _p = _anc_crypto.get(_t, {}).get("price")
        if _p: _anc_parts.append(f"  {_t}: {_p}")
    # Build flat equity price lookup from context["equities"][group][ticker]
    _eq_flat = {}
    for _grp in context.get("equities", {}).values():
        if isinstance(_grp, dict):
            for _et, _ed in _grp.items():
                if isinstance(_ed, dict) and _ed.get("price"):
                    _eq_flat[_et] = _ed["price"]
    for _t in ["SPY", "QQQ", "NVDA", "TSLA", "VST", "CEG", "VRT", "NNE", "SMR", "WATT", "TLT", "GLD"]:
        _asset = _anc_core.get(_t, {})
        _p = _asset.get("price") or _eq_flat.get(_t)
        if _p:
            _anc_parts.append(f"  {_t}: {_p}")
        elif _asset.get("display_mode") == "signal" and _asset.get("price"):
            _anc_parts.append(f"  {_t}: ${_asset['price']:.2f} (ETF price — do not use spot price)")
    _spy_px = str(_anc_core.get("SPY", {}).get("price", "?"))
    _anc_parts.append("PRICE RULE: Every price level in LEVELS, SYNTHESIS, PORTFOLIO WATCH, and FORWARD 72H MUST be within 20% of the anchor for equities and 40% for crypto (BTC/ETH/SOL). Any level outside these ranges is a hallucination — do not write it.")
    _anc_parts.append(f"SPY RULE: SPY trades near {_spy_px}. The S&P 500 index (~5000-6000) is NOT SPY price. Never write SPY levels in the 3000-6000 range.")
    price_anchor = chr(10).join(_anc_parts)

    # --- Synthesis price table (inline anchor for ASYMMETRIC SETUP) ---
    _synth_rows = []
    for _t in ["BTC", "ETH", "SOL"]:
        _p = _anc_crypto.get(_t, {}).get("price")
        if _p: _synth_rows.append(f"  {_t}: {_p}")
    for _t in ["SPY", "NVDA", "TSLA", "VST", "CEG", "VRT", "NNE", "SMR", "WATT"]:
        _asset = _anc_core.get(_t, {})
        _p = _asset.get("price") or _eq_flat.get(_t)
        if _p: _synth_rows.append(f"  {_t}: {_p}")
    synth_price_table = (
        "VALID ASYMMETRIC SETUP PRICES — use ONLY these values:\n"
        + "\n".join(_synth_rows)
        + "\nThe price level you write MUST match the ticker you name. "
        "VST price → VST anchor. CEG price → CEG anchor. "
        "Assigning SPY's price to VST (or any cross-ticker substitution) is a hallucination and will be rejected."
    )

    # --- Portfolio Watch RAG pull (top_k=3, foundational_research) ---
    portfolio_watch_rag = ""
    if _RAG_AVAILABLE:
        try:
            _pw_results = _rag_retrieve(
                "AI energy nuclear bitcoin accumulation institutional thesis sector rotation",
                n=3,
                doc_type="foundational_research"
            )
            _pw_chunks = [r["text"] for r in _pw_results if r.get("distance", 1.0) <= 0.62]
            if _pw_chunks:
                portfolio_watch_rag = (
                    "PORTFOLIO WATCH RESEARCH (institutional analysis):\n"
                    + "\n---\n".join(c[:500] for c in _pw_chunks[:3])
                )
        except Exception as _e:
            print(f"\u26a0 [STRATEGIST] Portfolio Watch RAG unavailable: {_e}")

    return f"""SOVEREIGN INTELLIGENCE BRIEF — {timestamp}

MACRO REGIME: {macro_str}
INFLATION SIGNAL: {truf_signal}
DATA TYPES — the values above are RATES and PERCENTAGES, not price levels.
Never reference Truflation rate, 24h change %, or Fear & Greed score with a $ symbol.
Price levels must come from SCAN or LEVELS data only.

{price_anchor}

CATALYST CALENDAR (next 14 days):
{catalyst_block}

---

{section_i}

---

{section_ii}

---

{chronicle}

---

Using ONLY the data above, write the following five sections.
No filler. No hedging. No sign-offs. Each section earns its place.

PULSE
4-5 sentences. How does the market feel RIGHT NOW as a system? Integrate BTC, SPY, Gold/DXY signals, Fear & Greed, and any flagged equities or sectors into a single coherent read. Cover what is leading, what is lagging, what is diverging, and what the net read means for positioning today. If CATALYST CALENDAR shows any FOMC, CPI, or earnings event within 7 days, name it by event type and days-away count, and state explicitly whether it creates a risk-on or risk-off bias for the current setup. No bullet points.

REGIME
4-5 sentences. What macro regime are we in? What is driving it? What would change it? How does this regime specifically affect BTC, AI semis, and AI energy names? Reference macro signals and chronicle thread. If YESTERDAY DELTA is present, explicitly address whether yesterday's posture still holds or has shifted — and why.

NEWS
4-5 items from the signal feed above. Use exactly this schema — no deviations:

FEATURED | [CATEGORY] | [headline, 10 words max]
SOURCE: [publication or feed name]
EVENT: [one sentence, factual, no editorializing]
CAUSAL CHAIN: [event] → [mechanism] → [watched ticker] [direction]
SIGNAL STRENGTH: [HIGH / MED / NOISE]

QUICK | [CATEGORY] | [headline, 8 words max]
SOURCE: [publication or feed]
EVENT: [one sentence, factual]
CAUSAL CHAIN: [event] → [mechanism] → [ticker] [direction]

Categories: CRYPTO, MACRO, AI, ENERGY. Pick highest-signal item as FEATURED. 3-4 QUICK items.
If no signal-relevant news exists: output NO SIGNAL NEWS TODAY — do not generate filler abstraction.
SOURCE RULE:
- SOURCE must name an external publication, data release, named analyst, or RSS feed.
- "Chronicle" is NOT a valid source. Chronicle is an internal synthesis layer, not external intelligence.
- If no external signal exists, write: SOURCE: NO EXTERNAL SIGNAL — [one-line internal observation]
- Do not fabricate causal chains from internal synthesis.

SCAN
Tight intelligence scan. Each signal category gets 1-2 sentences max. Lead with the sharpest insight. No restating headlines.

LEVELS
2-3 structurally significant price levels for this week. One level per line, this exact format:
[TICKER] [PRICE]: one sentence — what holds or breaks at this level and why it matters structurally.
Use exact prices from the data above. Reference BTC, SPY, and any flagged equity from PULSE.
Do not approximate. Do not invent levels not supported by the data.

SYNTHESIS
Output exactly these five labeled fields in this order — no prose outside them. No exceptions.

CONFLUENCE: [one sentence — what 3+ watched tickers from SCAN are collectively signaling. Must name the tickers.]
ROTATION: [top mover by % today] leading | [lagging name] lagging | theme: [one phrase]
ASYMMETRIC SETUP: [ticker] — [condition] at [specific $ price level from SCAN or LEVELS] — [LONG/SHORT bias only — no entry or stop]
{synth_price_table}
POSTURE DERIVATION: [condition A with a level or %] + [condition B with a level or %] → [posture word]
POSTURE: [Hold / Watch / Opportunity]
SETUP SIGNAL: Use exactly one of these three formats:
  SETUP SIGNAL: [TICKER] [LONG/SHORT] — see Plays
  SETUP SIGNAL: NO SETUP TODAY
  SETUP SIGNAL: WATCH ONLY — [one-line reason]
This field is REQUIRED. Do not omit it. Do not write a dash. No entry, stop, or target here — those live in Plays.
UNIVERSE CONSTRAINT: The only valid tickers for SETUP SIGNAL are: BTC, CEG, ENPH, ETH, FSLR, GLD, NNE, NVDA, QQQ, SMR, SOL, SPY, TLT, TSLA, USO, VRT, VST, WATT. Any ticker outside this list is invalid — write NO SETUP TODAY instead.

RULES:
- CONFLUENCE must name at least 3 tickers from SCAN by ticker symbol
- ASYMMETRIC SETUP must contain a specific $ price level — no level, no setup, write NO SETUP — INSUFFICIENT DATA
- POSTURE must follow directly from POSTURE DERIVATION — POSTURE alone without POSTURE DERIVATION is a FAIL
- PRICE LEVEL RULE: Only use price levels from SCAN or LEVELS sections. Truflation %, 24h change %, and Fear & Greed values are NOT price levels — never format them with a $ symbol"
- SYNTHESIS ORIGINALITY: Do not repeat any sentence from PULSE or DOMINANT NARRATIVE verbatim. SYNTHESIS derives posture from convergence — it does not restate observations already in PULSE or DOMINANT NARRATIVE.
- SETUP SIGNAL: Use exactly one of the three valid formats. No entry, stop, target, R/R, or score.

PRICE ANCHOR REMINDER — use ONLY these levels for all EXPRESSION, TRIGGER, and INVALIDATION fields:
{price_anchor}
Any price level outside 20% of the anchors above (40% for BTC/ETH/SOL) is a hallucination — do not write it.
PORTFOLIO WATCH PRICE RULE: When mentioning VST, CEG, VRT, NNE, SMR, WATT prices in PORTFOLIO WATCH, you MUST use the anchored prices above. Do not invent or recall prices from memory — the anchors are the only valid source. A price not in the anchor list must not appear in PORTFOLIO WATCH.

SYNTHESIS REMINDER — output ALL SIX fields before moving to FORWARD 72H:
CONFLUENCE: [3+ tickers + collective signal]
ROTATION: [top mover] leading | [lagging] lagging | theme: [phrase]
ASYMMETRIC SETUP: [ticker] — [condition] at [$level] — [LONG/SHORT bias]
{synth_price_table}
POSTURE DERIVATION: [condition A] + [condition B] → [posture word]
POSTURE: [Hold / Watch / Opportunity]
SETUP SIGNAL: [TICKER LONG/SHORT — see Plays] OR [NO SETUP TODAY] OR [WATCH ONLY — reason]
All six fields required. Missing any one field is a generation failure.

FORWARD 72H
Write exactly three scenario blocks. Probabilities must sum to 100. Use this exact format for each block — no deviation:

LIKELY:
SCENARIO: [one sentence — what most probably happens]
PROBABILITY: [X%]
TRIGGER: [specific price level or event that confirms this scenario]
PATH: [two sentences — how price gets there and what it means for positioning]
EXPRESSION: [ticker] [LONG/SHORT] entry: [specific price level] | stop: [specific price level] | target: [specific price level] | size: [HIGH/MED/LOW]. If exact levels cannot be derived AND SCAN shows no AT S / AT R flag: NO TRADE — INSUFFICIENT SETUP. Watch for [specific condition] at [specific price level].
REJECT: NO TRADE on LIKELY when SCAN shows any ticker flagged AT S or AT R. Derive entry, stop, and target from that level.
MOST LIKELY EXPRESSION RULE:
- If a trade is available: entry + stop + target required.
- If NO TRADE: state the specific blocking condition.
  Format: NO TRADE — [REASON]
  Valid reasons: SETUP SCORE BELOW THRESHOLD | R/R BELOW MINIMUM | NO STRUCTURAL LEVEL AVAILABLE | POSTURE CONFLICT
- Bare "NO TRADE — INSUFFICIENT SETUP" with no reason will FAIL the critic.
INVALIDATION: [specific price level or event that kills this scenario]

BULL:
SCENARIO: [one sentence]
PROBABILITY: [X%]
TRIGGER: [specific price level or event]
PATH: [two sentences]
EXPRESSION: [ticker] [LONG/SHORT] entry: [specific price level] | stop: [specific price level] | target: [specific price level] | size: [HIGH/MED/LOW]. If exact levels cannot be derived from available data, write: NO TRADE — INSUFFICIENT SETUP
INVALIDATION: [specific price level or event]

BEAR:
SCENARIO: [one sentence]
PROBABILITY: [X%]
TRIGGER: [specific price level or event]
PATH: [two sentences]
EXPRESSION: [ticker] SHORT entry: [specific price level] | stop: [specific price level] | target: [specific price level] | size: [HIGH/MED/LOW]. BEAR block only: SHORT, NO TRADE — INSUFFICIENT SETUP, or defensive MACRO expression. A LONG inside BEAR is structurally invalid and will be rejected.
INVALIDATION: [specific price level or event]

Final line: one word only — Hold, Watch, or Opportunity
QUALITY ENFORCEMENT RULES

1. SELF-CITATION REJECT: NEWS items must trace to external RSS signals only. Any item attributed to 'Sovereign Intelligence Brief', 'this system', or any self-reference is invalid. Replace with the next available external signal or output: NO EXTERNAL SIGNALS — RSS RETURNED EMPTY.

2. COMPLETE SETUPS ONLY: Every EXPRESSION field must include entry, stop, AND target. Entry + stop with no target is an incomplete setup — output NO TRADE — INSUFFICIENT SETUP instead.

3. NO VAGUE GEOPOLITICAL FILLER: NEWS items referencing China/Taiwan/Russia/Ukraine/OPEC must state a direct consequence to a specific watched ticker or price level. Geopolitical color with no market linkage is omitted.

4. LARGE MOVER VERDICT REQUIRED: Any equity flagged >3% in SCAN must receive an explicit LONG, SHORT, or AVOID verdict with one-line rationale. Silent pass-through of large movers is a generation failure.

5. FORWARD-LOOKING TRIGGERS ONLY: TRIGGER fields must describe future conditions that would confirm the scenario. Past-tense or already-observed conditions are invalid as triggers.

6. POSTURE UPGRADE CONDITION: POSTURE may only upgrade (Hold → Watch → Opportunity) when FORWARD 72H LIKELY EXPRESSION contains a fully executable trade with entry, stop, and target. A NO TRADE LIKELY scenario cannot support Watch or Opportunity posture.

7. TRENDING ASSETS ABOVE MA20: Any watched ticker trading above its MA20 with positive SCAN alignment must appear in SYNTHESIS or FORWARD 72H. Omitting a trending aligned ticker is a generation failure.

{portfolio_watch_rag}

PORTFOLIO WATCH
3-4 sentences. RAG-backed portfolio thesis validation — no execution parameters.
If PORTFOLIO WATCH RESEARCH is provided above, ground your analysis in it.
1. AI ENERGY NEXUS: Is the thesis (VST, CEG, VRT, NNE, SMR) confirming or cracking based on today's price action?
2. ROTATION: Any sector rotation signal that supports or undermines the thesis?
3. BTC ACCUMULATION: Are accumulation conditions improving or deteriorating? Reference specific levels.
Name specific tickers and levels. Do not repeat observations from PULSE or REGIME."""


def build_prompt_b(context: dict) -> str:
    """Stream B — Lore Dispatch. Market signals as inspiration only."""
    lore_anchor = context.get("lore_anchor", "Lore state unavailable.")
    lore_continuity = context.get("lore_continuity", "No persistent lore state available.")
    timestamp = context.get("timestamp", "unknown")

    # Extract signal energy as abstract inspiration — no tickers
    structured = context.get("signals", {}).get("structured", {})
    def signal_energy(key):
        items = structured.get(key, [])
        if not items:
            return "  • No signals."
        return "\n".join(f"  • {h['headline']}" for h in items[:2])

    chronicle = render_chronicle(context)

    return f"""LORE DISPATCH — {timestamp}

---

SIGNAL ENERGY (inspiration only — do not reference directly):
AI/TECH: {signal_energy('AI_Tech')}
MACRO: {signal_energy('Macro_Policy')}
CRYPTO: {signal_energy('Crypto')}
ENERGY: {signal_energy('Energy')}

---

{chronicle}

---

LORE ANCHOR: {lore_anchor}

ESTABLISHED LORE CONTEXT — reference only, do not reproduce verbatim:
{lore_continuity}

---

SECTION IV — {_active_universe.upper()}

CHARACTER ACTIVITY:

FACTION MOVE:

WOUND ZONE STATUS:

THREAT SIGNAL:


---

SECTION V — CREATIVE LAB: THE SOVEREIGN SPARK

🎨 CREATIVE LAB: THE SOVEREIGN SPARK

[World or scene name — you choose]

ATMOSPHERE:

SYSTEM:"""


# ─────────────────────────────────────────────
# OLLAMA INTERFACE
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# CRITIC
# ─────────────────────────────────────────────

def critic_review(prompt: str, brief: str) -> dict:
    print(f"🔍 [CRITIC] Engaging {MODEL_FALLBACK} for Stream A review...")
    # Brief-only: checklist is structural; mistral:7b is faster and independent from generation model
    critic_input = f"GENERATED BRIEF:\n{brief}"

    try:
        raw = query_ollama(critic_input, MODEL_FALLBACK, CRITIC_PROMPT, timeout=120)

        verdict = "PASS" if re.search(r'VERDICT:\s*PASS', raw, re.IGNORECASE) else "FLAG"

        # Extract FAIL_COUNT
        fail_match = re.search(r'FAIL_COUNT:\s*(\d+)', raw, re.IGNORECASE)
        fail_count = int(fail_match.group(1)) if fail_match else None

        # Extract CHECKLIST block
        checklist_match = re.search(r'CHECKLIST:\s*(.+?)(?=FAIL_COUNT:|CONFIDENCE:|ISSUES:|$)', raw, re.DOTALL | re.IGNORECASE)
        checklist = checklist_match.group(1).strip() if checklist_match else ""

        # Extract ISSUES
        issues_match = re.search(r'ISSUES:\s*(.+?)$', raw, re.DOTALL | re.IGNORECASE)
        issues = issues_match.group(1).strip() if issues_match else (checklist[:150] if checklist else raw.strip()[:150])

        confidence_match = re.search(r'CONFIDENCE:\s*(HIGH|MEDIUM|LOW|FAIL)', raw, re.IGNORECASE)
        confidence = confidence_match.group(1).upper() if confidence_match else "MEDIUM"

        icon = "✅" if verdict == "PASS" else "⚠️ "
        fail_str = f" | FAILs: {fail_count}" if fail_count is not None else ""
        print(f"  {icon} [CRITIC] Verdict: {verdict} | Confidence: {confidence}{fail_str}")
        if verdict == "FLAG":
            print(f"  📋 [CRITIC] Issues: {issues[:200]}")

        return {"verdict": verdict, "issues": issues, "confidence": confidence, "fail_count": fail_count, "checklist": checklist}

    except Exception as e:
        print(f"  ⚠️  [CRITIC] Review failed: {e} — defaulting to PASS")
        return {"verdict": "PASS", "issues": "Critic unavailable", "confidence": "LOW", "fail_count": None, "checklist": ""}


# ─────────────────────────────────────────────
# GENERATION
# ─────────────────────────────────────────────

def generate_stream_a(context: dict) -> tuple[str, dict, str]:
    """Generate market intel brief. Returns (full_brief, review, model_used)."""
    prompt = build_prompt_a(context)
    result, model_used = query_with_fallback(prompt, SYSTEM_PROMPT_A, "STREAM-A")

    # Prepend pre-rendered sections
    section_i = render_section_i(context)
    result_clean = re.sub(
        r'SOVEREIGN INTELLIGENCE BRIEF —.*?\n---\n', '',
        result, count=1, flags=re.DOTALL
    ).strip()
    full_brief = f"{section_i}\n\n---\n\n{result_clean}"

    review = critic_review(prompt, full_brief)
    return full_brief, review, model_used


def generate_stream_b(context: dict) -> tuple[str, str]:
    """Generate lore dispatch. Returns (dispatch, model_used)."""
    prompt = build_prompt_b(context)
    result, model_used = query_with_fallback(
        prompt, SYSTEM_PROMPT_B, "STREAM-B", timeout=300
    )

    # Strip prompt echo if model repeats the header
    result_clean = re.sub(
        r'LORE DISPATCH —.*?\n---\n', '',
        result, count=1, flags=re.DOTALL
    ).strip()

    return result_clean, model_used


# ─────────────────────────────────────────────
# FILE I/O
# ─────────────────────────────────────────────

def atomic_write(content: str, target_path: str) -> None:
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode='w',
        dir=target_dir,
        suffix='.tmp',
        delete=False,
        encoding='utf-8'
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    shutil.move(tmp_path, target_path)


def write_stream_a(brief: str, review: dict, model_used: str, context: dict, timestamp: datetime) -> str:
    btc_price = context.get("market", {}).get("crypto", {}).get("BTC", {}).get("price", "N/A")
    fg = context.get("market", {}).get("fear_greed", {})
    fg_value = fg.get("value", "N/A")
    fg_class = fg.get("classification", "N/A")

    _issues_safe = (
        review['issues'][:80]
        .replace('\n', ' ')
        .replace('"', "'")
        .replace(':', '-')
        .strip()
    ) if review['verdict'] == "FLAG" else ""
    _critic_val = f"{review['verdict']} | {review['confidence']}" + (
        f" | {_issues_safe}" if _issues_safe else ""
    )
    critic_tag = f'critic: "{_critic_val}"'

    # Extract dominant narrative — grab first sentence of SYNTHESIS section
    dominant_narrative = "N/A"
    lines = brief.splitlines()
    in_synthesis = False
    for line in lines:
        if line.strip() == "SYNTHESIS":
            in_synthesis = True
            continue
        if in_synthesis:
            clean = line.strip().strip('"').strip("*").strip()
            if clean and not clean.startswith("---"):
                dominant_narrative = clean[:120]
                break

    # Extract posture word — final lone word on its own line
    posture_word = ""
    for line in reversed(lines):
        if line.strip() in ("Hold", "Watch", "Opportunity"):
            posture_word = line.strip()
            break

    # Thread posture word back into context.json so gates can read it
    if posture_word:
        try:
            with open(CONTEXT_FILE, "r") as _f:
                _ctx = json.load(_f)
            _ctx["daily_posture"] = posture_word
            with open(CONTEXT_FILE, "w") as _f:
                json.dump(_ctx, _f, indent=2)
        except Exception as _e:
            print(f"⚠ [STRATEGIST] posture write failed: {_e}")

    output = f"""---
date: {timestamp.strftime('%Y-%m-%d')}
time: {timestamp.strftime('%I:%M %p')}
type: market-brief
model: {model_used}
{critic_tag}
btc: "{btc_price}"
fear_greed: "{fg_value} ({fg_class})"
dominant_narrative: "{dominant_narrative}"
tags: [market-intel, daily-brief]
---

SOVEREIGN INTELLIGENCE BRIEF — {timestamp.strftime('%Y-%m-%d %H:%M')}

---

{brief}"""

    filename = f"Brief_{timestamp.strftime('%Y-%m-%d_%H%M')}.md"
    path = os.path.join(VAULT_MARKET, filename)
    atomic_write(output, path)
    return filename


def write_stream_b(dispatch: str, model_used: str, context: dict, timestamp: datetime) -> str:
    lore_state = context.get("lore_state", {})
    arc = lore_state.get("current_arc", _active_universe + " — Active Arc")
    world_status = lore_state.get("aether_state", "UNKNOWN")

    output = f"""---
date: {timestamp.strftime('%Y-%m-%d')}
time: {timestamp.strftime('%I:%M %p')}
type: lore-dispatch
universe: "{_active_universe}"
arc: "{arc}"
world_status: "{world_status}"
model: {model_used}
tags: [{_universe_slug.lower()}, lore, dispatch]
---

{_active_universe.upper()} — {timestamp.strftime('%Y-%m-%d %H:%M')}

---

{dispatch}"""

    filename = f"Expansion_{timestamp.strftime('%Y-%m-%d_%H%M')}.md"
    path = VAULT_LORE / filename
    atomic_write(output, path)
    return filename


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n⚡ [NODE: STRATEGIST] Synthesis initiated — v2.0 dual-stream...")

    if not os.path.exists(CONTEXT_FILE):
        print("❌ [STRATEGIST] context.json missing. Run fetch_news.py first.")
        return

    try:
        with open(CONTEXT_FILE, 'r', encoding='utf-8') as f:
            context = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ [STRATEGIST] context.json malformed: {e}")
        return

    timestamp = datetime.now()
    stream_a_ok = False
    stream_b_ok = False

    # --- Stream A: Market Intel ---
    print(f"\n📊 [STREAM A] Market Intel Brief...")
    try:
        brief, review, model_a = generate_stream_a(context)
        filename_a = write_stream_a(brief, review, model_a, context, timestamp)
        print(f"✅ [STREAM A] Written → {filename_a}")
        if review['verdict'] == "FLAG":
            print(f"⚠️  [STREAM A] Flagged by Critic — review before trading on it.")
        stream_a_ok = True
    except RuntimeError as e:
        print(f"❌ [STREAM A] {e}")
    except Exception as e:
        print(f"❌ [STREAM A] Write error: {e}")
        # Emergency fallback to Desktop
        emergency = os.path.expanduser(f"~/Desktop/EMERGENCY_Brief_{timestamp.strftime('%Y-%m-%d_%H%M')}.md")
        try:
            with open(emergency, 'w', encoding='utf-8') as f:
                f.write(str(e))
            print(f"💾 [EMERGENCY] Saved to Desktop: {emergency}")
        except Exception:
            pass

    # --- Stream B: Lore Dispatch ---
    print(f"\n🌌 [STREAM B] Lore Dispatch...")
    try:
        dispatch, model_b = generate_stream_b(context)
        filename_b = write_stream_b(dispatch, model_b, context, timestamp)
        print(f"✅ [STREAM B] Written → {filename_b}")
        stream_b_ok = True
    except RuntimeError as e:
        print(f"❌ [STREAM B] {e}")
    except Exception as e:
        print(f"❌ [STREAM B] Write error: {e}")

    # --- Summary ---
    print(f"\n{'✅' if stream_a_ok and stream_b_ok else '⚠️ '} [STRATEGIST] Complete — "
          f"Stream A: {'OK' if stream_a_ok else 'FAILED'} | "
          f"Stream B: {'OK' if stream_b_ok else 'FAILED'}")


if __name__ == "__main__":
    main()
