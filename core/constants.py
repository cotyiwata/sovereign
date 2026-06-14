"""
core/constants.py — Policy constants for the plays renderer.

Every magic number that previously lived inline in plays_html_renderer.py
moved here. When you change a stop buffer or a leverage rule, you change
it once, in one place.

Behavior is unchanged from v2.7.1 — values are copied verbatim.
"""

# ── Stop & target buffers ──────────────────────────────────────────────────
# Multiplied against 5-day swing low/high for stop placement.
LONG_STOP_BUFFER  = 0.985   # swing_low_5d  * 0.985 → 1.5% below swing low
SHORT_STOP_BUFFER = 1.015   # swing_high_5d * 1.015 → 1.5% above swing high

# Multiplied against 20-day rolling S/R for base targets.
LONG_TARGET_BUFFER  = 1.015  # resistance * 1.015
SHORT_TARGET_BUFFER = 0.985  # support    * 0.985

# ── ATR forward-target gate (Fix 1 from v2.7.1) ────────────────────────────
# When price is at/above 20d resistance AND base target is essentially hit,
# project a fresh target from current price using ATR multiples.
ATR_GATE_RESISTANCE_PROXIMITY = 0.98  # current >= resistance * 0.98 triggers
ATR_GATE_TARGET_PROXIMITY     = 0.97  # AND current >= target * 0.97
ATR_MULT_CRYPTO = 3.0
ATR_MULT_EQUITY = 2.0

# ── R/R quality gates ──────────────────────────────────────────────────────
RR_VERIFY_THRESHOLD     = 10.0   # R/R > 10:1 → VERIFY ⚠ (probably model error)
RR_HIGH_CONV_MIN        = 1.5    # HIGH conviction needs at least 1.5:1
RR_MED_CONV_MIN         = 1.0    # MED conviction needs at least 1.0:1
RR_LEVERAGE_BOOST_LEVEL = 2.0    # R/R >= 2 unlocks max leverage tier

# ── Brief R/R validation floors ────────────────────────────────────────────
BRIEF_RR_MIN      = 1.5   # floor for BEST SETUP TODAY in brief
BEST_SETUP_ENTRY_DISTANCE_MAX = 0.03  # entry >3% from current → CONDITIONAL TRIGGER
SWING_RR_MIN      = 2.0   # swing plays (daily candles, 2-5 day hold)
DAY_TRADE_RR_MIN  = 1.5   # day trade plays (1H candles, same session)

# ── Leverage table (conviction × R/R × asset class) ────────────────────────
# Resolved by core.enrichment.assign_leverage()
LEVERAGE_TABLE = {
    # (conviction, rr_tier, is_crypto): leverage_string
    ("HIGH", "boosted", True):  "10x",
    ("HIGH", "boosted", False): "5x",
    ("HIGH", "base",    True):  "5x",
    ("HIGH", "base",    False): "3x",
    ("MED",  "any",     True):  "3x",
    ("MED",  "any",     False): "2x",
}
LEVERAGE_RR_ADJUSTED = "1x"
LEVERAGE_VERIFY      = "NO LEVERAGE"
LEVERAGE_DEFAULT     = "1x"
LEVERAGE_COUNTER_TREND_CAP = "1x"

# ── Position Watch zone enforcement (Fix 3 + Fix 4 from v2.7.1) ────────────
PW_ZONE_MAX_WIDTH_PCT = 0.10  # accumulate_zone width <= 10% of current price

VALID_POSTURES = ("ACCUMULATING", "WATCHING", "PAUSED", "INVALIDATED")

# ── TA computation windows ─────────────────────────────────────────────────
SR_WINDOW       = 20    # rolling support/resistance window
SWING_WINDOW    = 5     # 5-day swing high/low for stops
RSI_WINDOW      = 14
ATR_WINDOW      = 14
MACD_FAST       = 12
MACD_SLOW       = 26
MACD_SIGNAL     = 9
MA_WINDOWS      = (9, 20, 200)
VOLUME_AVG_WIN  = 20
HISTORY_PERIOD  = "1y"

# ── Macro signal thresholds ────────────────────────────────────────────────
OIL_SURGE_PCT = 1.0   # |oil change_pct| > 1.0 → directional signal

# ── Setup score weights (Session 41) ───────────────────────────────────────
SCORE_REGIME_ALIGN  = 3
SCORE_SETUP_PRESENT = 3
SCORE_RR_THRESHOLD  = 2.0
SCORE_RR_BONUS      = 2
SCORE_HIGH_CONV     = 1
SCORE_MA_STACK      = 1

SCORE_BAND_GREEN = 7  # >= 7 → green badge
SCORE_BAND_GREY  = 5  # 5-6  → grey, < 5 → amber

# ── Asset classification ───────────────────────────────────────────────────
CRYPTO_TICKERS = frozenset({"BTC", "ETH", "SOL"})

# ── Price sanity deviation ceilings (PRICE_LEVEL_SANITY gate) ─────────────
# Crypto targets can legitimately exceed 10% — use wider band.
PRICE_SANITY_THRESHOLD_CRYPTO  = 0.40   # 40% — BTC/ETH/SOL can target 2–3x moves
PRICE_SANITY_THRESHOLD_EQUITY  = 0.20   # 20% — equities rarely move >20% in 72H

# ── Trading universe (brief critic gate + SYNTHESIS validator) ─────────────
TRADING_UNIVERSE = frozenset({
    "BTC", "ETH", "SOL",
    "NVDA", "TSLA",
    "VST", "CEG", "VRT", "WATT",
    "NNE", "SMR", "FSLR", "ENPH",
    "SPY", "QQQ", "TLT", "USO", "GLD",
})


TICKER_SECTION = {
    "BTC": "CRYPTO", "SOL": "CRYPTO", "ETH": "CRYPTO",
    "TSLA": "AI & SEMIS",
}

# ── Ollama ─────────────────────────────────────────────────────────────────
OLLAMA_URL          = "http://localhost:11434"
MODEL               = "gemma3:12b"
OLLAMA_TIMEOUT      = 180
OLLAMA_TEMP_DEFAULT = 0.5
OLLAMA_TEMP_STRICT  = 0.3   # for exposure signals — less variation

# ── Additional model names ─────────────────────────────────────────────────
MODEL_FALLBACK = "mistral:7b"        # classification + fallback generation
EMBED_MODEL    = "nomic-embed-text"  # embedding model for RAG

# ── Research Digest ────────────────────────────────────────────────────────
RESEARCH_SOURCE_TAGS = {
    "Research_Arkinvest_Bigideas2026":               "ARK",
    "Research_Cb_Cryptomarketoutlook_2026":          "Coinbase",
    "Research_Grayscale_2026_Digital_Asset_Outlook": "Grayscale",
    "Research_State_Of_Crypto_2025_A16Z_Crypto":     "a16z",
}

DIGEST_EVERGREEN_THEMES = [
    "AI capex cycle",
    "BTC monetary thesis",
    "Fed policy path",
    "Stablecoin adoption",
    "Nuclear and AI energy",
    "L2 scaling and onchain volume",
    "Tokenization of real-world assets",
]

DIGEST_CONFIDENCE_SOLID_MIN_SOURCES = 2
DIGEST_CONFIDENCE_SOLID_MIN_CHUNKS  = 3
DIGEST_TENSION_MIN_BRIEF_HITS       = 3
DIGEST_TENSION_MIN_CHUNKS           = 2

DIGEST_CONVERGENCE_BRIEF_DAYS       = 7
DIGEST_CONVERGENCE_WEEKLY_COUNT     = 2
DIGEST_CONVERGENCE_RESEARCH_TOPK    = 4
DIGEST_CONVERGENCE_PRIOR_DIGESTS    = 1