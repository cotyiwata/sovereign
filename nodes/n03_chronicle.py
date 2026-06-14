# chronicle.py — Node 02: The Archivist v2.0
# Sovereign Intelligence System
# Reads last 7 daily briefs + active universe lore_state.json
# Injects CHRONICLE and LORE_CONTINUITY blocks into context.json

import os
import sys
import json
import re
import yaml
from datetime import datetime
from pathlib import Path

# Bootstrap: ensure Scripts/ is on the path regardless of invocation context
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.schema import load_context
try:
    from core.rag.retriever import retrieve_for_node
    RAG_ENABLED = True
except ImportError:
    RAG_ENABLED = False

from core.config import VAULT_ROOT, CONTEXT_FILE, CONFIG_PATH, BRIEFS_DIR

# Load config
with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f)

# Active universe lore state — dynamic, reads from config
_UNIVERSE       = CONFIG.get("active_universe", "The-Lost-Net")
_LORE_PATH_OVERRIDES = {
    "The-Vigil": VAULT_ROOT / "Data" / "vigil" / "vigil_state.json",
}
_LORE_FILENAME  = f"lore_state_{_UNIVERSE.lower().replace('-', '')}.json"
LORE_STATE_FILE = _LORE_PATH_OVERRIDES.get(_UNIVERSE, VAULT_ROOT / "Data" / _LORE_FILENAME)

# Section labels from config
_LABELS  = CONFIG.get("section_names", {}).get("stream_a", {})
S_PULSE  = _LABELS.get("pulse",     "PULSE")
S_REGIME = _LABELS.get("regime",    "REGIME")
S_SCAN   = _LABELS.get("scan",      "SCAN")
S_SYNTH  = _LABELS.get("synthesis", "SYNTHESIS")


# ─────────────────────────────────────────────
# BRIEF PARSER
# ─────────────────────────────────────────────

def extract_brief_signals(filepath: str) -> dict:
    """
    Extract key signals from a daily brief .md file.
    Reads frontmatter fields and section content.
    """
    signals = {
        "date":               "",
        "btc_price":          "",
        "spy_price":          "",
        "fear_greed":         "",
        "macro_regime":       "",
        "dominant_narrative": "",
        "hidden_risk":        "",
        "volatility_pattern": "",
        "sector_alerts":      "",
        "equity_movers":      "",
        "posture":            "",
    }

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # --- Frontmatter fields ---
        for field in ["btc", "spy", "fear_greed", "dominant_narrative", "macro_regime"]:
            m = re.search(rf"^{field}:\s*(.+)$", content, re.MULTILINE)
            if m:
                val = m.group(1).strip().strip('"')
                key_map = {"btc": "btc_price", "spy": "spy_price"}
                signals[key_map.get(field, field)] = val[:200]

        # --- BTC price fallback — scan body if not in frontmatter ---
        if not signals["btc_price"]:
            m = re.search(r"BTC[:\s]+\$([0-9,]+)", content)
            if m:
                signals["btc_price"] = f"${m.group(1)}"

        # --- Fear & Greed fallback ---
        if not signals["fear_greed"]:
            m = re.search(r"Fear\s*&\s*Greed[:\s]+(.+?)(?:\n|$)", content)
            if m:
                signals["fear_greed"] = m.group(1).strip()[:50]

        # --- Named section content extraction ---
        headers      = [S_PULSE, S_REGIME, S_SCAN, S_SYNTH]
        header_pat   = "|".join(re.escape(h) for h in headers)

        for section_label, signal_key, max_chars in [
            (S_REGIME, "macro_regime",       150),
            (S_SCAN,   "sector_alerts",      300),
            (S_SCAN,   "equity_movers",      300),
        ]:
            # Extract section body
            pat = rf"^##?\s*{re.escape(section_label)}[^\n]*\n(.*?)(?=^##?\s*(?:{header_pat})[^\n]*\n|$)"
            m   = re.search(pat, content, re.DOTALL | re.MULTILINE)
            if m:
                body = m.group(1).strip()
                # For macro_regime pull the one-liner
                if signal_key == "macro_regime" and not signals["macro_regime"]:
                    signals["macro_regime"] = body[:max_chars]
                # For scan pull sector and equity lines separately
                elif signal_key == "sector_alerts":
                    sector_lines = [l.strip() for l in body.split("\n")
                                    if "SOXX" in l or "XLK" in l or "XLE" in l
                                    or "XLU" in l or "XLF" in l or "sector" in l.lower()]
                    signals["sector_alerts"] = " | ".join(sector_lines)[:300]
                elif signal_key == "equity_movers":
                    mover_lines = [l.strip() for l in body.split("\n")
                                   if any(t in l for t in [
                                       "MU", "NVDA", "AVGO", "AMD", "AMAT",
                                       "VST", "CEG", "VRT", "PWR",
                                       "MSFT", "SONY", "TTWO"
                                   ])]
                    signals["equity_movers"] = " | ".join(mover_lines)[:300]

        # --- Legacy patterns for older briefs ---
        for field, pattern in [
            ("dominant_narrative", r"DOMINANT NARRATIVE:\s*(.+?)(?=\n\n|\nHIDDEN|\Z)"),
            ("hidden_risk",        r"HIDDEN RISK:\s*(.+?)(?=\n\n|\nVOLATILITY|\Z)"),
            ("volatility_pattern", r"VOLATILITY PATTERN:\s*(.+?)(?=\n\n|\n---|\Z)"),
        ]:
            if not signals.get(field):
                m = re.search(pattern, content, re.DOTALL)
                if m:
                    signals[field] = m.group(1).strip()[:200]

        # --- Posture word — lone Hold/Watch/Opportunity at end of SYNTHESIS ---
        last_lines = [l.strip() for l in content.strip().splitlines() if l.strip()]
        for line in reversed(last_lines):
            if line in ("Hold", "Watch", "Opportunity"):
                signals["posture"] = line
                break

    except Exception as e:
        print(f"  ⚠️  [ARCHIVIST] Failed to parse {filepath}: {e}")

    return signals


# ─────────────────────────────────────────────
# CHRONICLE BUILDER
# ─────────────────────────────────────────────

def build_chronicle() -> dict:
    """
    Scan last 7 days of briefs. One entry per calendar date, newest file wins.
    Returns structured chronicle block for context injection.
    """
    print("  📚 [ARCHIVIST] Scanning last 7 days of briefs...")
    today = datetime.now()

    try:
        files = sorted(
            [f for f in os.listdir(BRIEFS_DIR)
             if (f.startswith("Brief_") or f.startswith("Sovereign_Brief_"))
             and f.endswith(".md")],
            reverse=True
        )
    except Exception as e:
        print(f"  ⚠️  [ARCHIVIST] Could not read briefs directory: {e}")
        return {}

    def date_from_filename(filename):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
        return m.group(1) if m else None

    # Deduplicate — one entry per calendar date, newest file wins
    seen_dates = {}
    for filename in files:
        date_key = date_from_filename(filename)
        if not date_key or date_key in seen_dates:
            continue
        filepath = os.path.join(BRIEFS_DIR, filename)
        signals  = extract_brief_signals(filepath)
        signals["date"] = date_key  # authoritative date from filename
        seen_dates[date_key] = signals
        if len(seen_dates) >= 7:
            break

    entries = list(seen_dates.values())
    if not entries:
        return {}

    btc_prices      = [e["btc_price"]    for e in entries if e["btc_price"]]
    spy_prices      = [e["spy_price"]    for e in entries if e["spy_price"]]
    fg_readings     = [e["fear_greed"]   for e in entries if e["fear_greed"]]
    macro_states    = [f"[{e['date']}] {e['macro_regime']}"
                       for e in entries if e.get("macro_regime")]
    narratives      = [f"[{e['date']}] {e['dominant_narrative']}"
                       for e in entries if e["dominant_narrative"]]
    risks           = [f"[{e['date']}] {e['hidden_risk']}"
                       for e in entries if e.get("hidden_risk")]
    sector_thread   = [f"[{e['date']}] {e['sector_alerts']}"
                       for e in entries if e.get("sector_alerts")]
    equity_thread   = [f"[{e['date']}] {e['equity_movers']}"
                       for e in entries if e.get("equity_movers")]

    result = {
        "days_analyzed":      len(entries),
        "btc_price_series":   btc_prices,
        "spy_price_series":   spy_prices,
        "fear_greed_series":  fg_readings,
        "macro_state_thread": macro_states,
        "narrative_thread":   narratives,
        "risk_thread":        risks,
        "sector_thread":      sector_thread,
        "equity_thread":      equity_thread,
        "generated_at":       today.strftime("%Y-%m-%d %H:%M")
    }

    # Yesterday delta — entries[0] is most recent brief (today's hasn't run yet)
    if entries:
        yesterday = entries[0]
        result["yesterday_posture"]   = yesterday.get("posture", "")
        result["yesterday_narrative"] = yesterday.get("dominant_narrative", "")

    print(f"  ✅ [ARCHIVIST] Chronicle built — {len(entries)} days analyzed.")
    return result


# ─────────────────────────────────────────────
# LORE STATE
# ─────────────────────────────────────────────

def load_lore_state() -> dict:
    """Load persistent lore state for active universe."""
    try:
        if os.path.exists(LORE_STATE_FILE):
            with open(LORE_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            arc = state.get("current_arc", state.get("arc", "Unknown"))
            print(f"  🏛️  [ARCHIVIST] Lore state loaded — Universe: {_UNIVERSE} | Arc: {arc[:50]}...")
            return state
        else:
            print(f"  ⚠️  [ARCHIVIST] {_LORE_FILENAME} not found — starting fresh.")
            return {}
    except Exception as e:
        print(f"  ⚠️  [ARCHIVIST] Lore state load failed: {e}")
        return {}


def format_lore_context(lore: dict) -> str:
    """
    Format lore state into a prompt-ready string.
    Universe-agnostic — reads whatever fields are present.
    Handles both Age of Aether schema and The Lost Net schema.
    """
    if not lore:
        return "No persistent lore state available."

    universe = lore.get("universe", _UNIVERSE)

    # active_arc — may be object or string
    arc_raw  = lore.get("active_arc", lore.get("current_arc", lore.get("arc", {})))
    if isinstance(arc_raw, dict):
        arc       = arc_raw.get("name", "Unknown")
        next_beat = arc_raw.get("next_beat", "")
    else:
        arc       = str(arc_raw)
        next_beat = lore.get("next_beat", "")

    # world_state — may be object or string; AoA uses aether_state + aether_intensity
    ws_raw = lore.get("world_state", {})
    if isinstance(ws_raw, dict):
        world_stat = ws_raw.get("status", lore.get("aether_state", "Unknown"))
        tension    = ws_raw.get("tension", lore.get("aether_intensity", lore.get("tension_level", "Unknown")))
    else:
        world_stat = str(ws_raw) if ws_raw else lore.get("aether_state", "Unknown")
        tension    = lore.get("aether_intensity", lore.get("tension_level", lore.get("static_intensity", "Unknown")))

    lines = [
        f"UNIVERSE: {universe}",
        f"CURRENT ARC: {arc}",
        f"WORLD STATUS: {world_stat} | Tension: {tension}",
    ]

    # Characters — dict keyed by name (AoA/Veil) or list with name field (Lost Net)
    characters_raw = lore.get("characters", [])
    if isinstance(characters_raw, dict):
        characters = [(k, v) for k, v in characters_raw.items() if isinstance(v, dict)]
    else:
        characters = [(c.get("name", "Unknown"), c) for c in characters_raw if isinstance(c, dict)]
    if characters:
        lines.append("\nCHARACTERS:")
        for name, c in characters:
            status = c.get("arc_status", c.get("status", ""))
            note   = c.get("arc", c.get("notes", c.get("note", "")))[:80]
            lines.append(f"  - {name.replace("_", " ").title()}: {status} | {note}".strip())

    # Factions / Houses (Age of Aether schema)
    houses = lore.get("active_houses", [])
    if houses:
        lines.append("\nACTIVE HOUSES:")
        for h in houses:
            lines.append(f"  - {h.get('name','?')}: {h.get('status','')}")

    # Active conflicts
    conflicts = lore.get("active_conflicts", [])
    if conflicts:
        lines.append("\nACTIVE CONFLICTS:")
        for c in conflicts:
            lines.append(
                f"  - [{c.get('status','?').upper()}] {c.get('name','?')}: "
                f"{c.get('description','')[:100]}"
            )

    # World zones
    zones = lore.get("world_zones", [])
    if zones:
        lines.append("\nWORLD ZONES:")
        for z in zones:
            lines.append(f"  - {z.get('name','?')}: {z.get('status','')}")

    # Recent codex entries
    codex = lore.get("codex_entries", [])
    if codex:
        lines.append("\nRECENT CODEX ENTRIES:")
        for e in codex[-3:]:
            source  = e.get("source", e.get("title", "?"))
            date    = e.get("date", "?")
            summary = e.get("entry", e.get("summary", ""))[:100]
            lines.append(f"  - [{date}] {source}: {summary}")

    if next_beat:
        lines.append(f"\nNEXT BEAT: {next_beat}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# MAIN INJECTION
# ─────────────────────────────────────────────

def inject_context():
    """Load context.json, inject chronicle + lore state, save."""
    chronicle    = build_chronicle()
    lore         = load_lore_state()
    lore_context = format_lore_context(lore)

    try:
        with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
            context = load_context(CONTEXT_FILE).model_dump()

        if chronicle:
            context["chronicle"] = chronicle

        context["lore_continuity"] = lore_context
        context["lore_state_raw"]  = lore
        context["active_universe"] = _UNIVERSE

        # RAG memory injection
        if RAG_ENABLED:
            try:
                market_memory = retrieve_for_node("chronicle", context)
                lore_memory   = retrieve_for_node("lore", context)
                context["rag_market_memory"] = market_memory
                context["rag_lore_memory"]   = lore_memory
                print(f"  🧠 [ARCHIVIST] RAG memory injected.")
            except Exception as e:
                print(f"  ⚠️  [ARCHIVIST] RAG skipped: {e}")
                context["rag_market_memory"] = ""
                context["rag_lore_memory"]   = ""
        else:
            context["rag_market_memory"] = ""
            context["rag_lore_memory"]   = ""

        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)

        print(f"  ✅ [ARCHIVIST] Context updated — chronicle + lore + RAG injected.")

    except Exception as e:
        print(f"  ❌ [ARCHIVIST] Injection failed: {e}")


if __name__ == "__main__":
    print(f"\n📚 [NODE: ARCHIVIST v2.1] Chronicle cycle initiated — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    inject_context()
    print("🔁 [ARCHIVIST → STRATEGIST] Chronicle + Lore ready.\n")
    