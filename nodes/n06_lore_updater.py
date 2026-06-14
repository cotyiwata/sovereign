import re
import json
import os
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT as VAULT, load_config
from core.lore import load_universe_context

import random

# ── Universe registry ─────────────────────────────────────────────────────────
# characters + wound_zones removed — sourced live from core.lore at runtime
UNIVERSE_REGISTRY = {
    "age_of_aether": {
        "expansions_dir": VAULT / "03-Universes" / "Age-of-Aether" / "Daily-Expansions",
        "lore_state":     VAULT / "Data" / "lore_state_ageofaether.json",
        "weight":         0.0,
    },
    "the_veil_ascendancy": {
        "expansions_dir": VAULT / "03-Universes" / "Veil-Ascendancy" / "Daily-Expansions",
        "lore_state":     VAULT / "Data" / "veil_ascendancy" / "lore_state_veil_ascendancy.json",
        "weight":         0.0,
    },
    "the_vigil": {
        "expansions_dir": VAULT / "03-Universes" / "The-Vigil" / "Daily-Expansions",
        "lore_state":     VAULT / "Data" / "vigil" / "vigil_state.json",
        "weight":         1.0,
    },
}

# Module-level vars — set at runtime by run() via pick_universe()
EXPANSIONS_DIR  = UNIVERSE_REGISTRY["age_of_aether"]["expansions_dir"]
LORE_STATE_PATH = UNIVERSE_REGISTRY["age_of_aether"]["lore_state"]
CHARACTERS:  list[str] = []   # populated from core.lore at runtime
WOUND_ZONES: list[str] = []   # populated from core.lore at runtime

# Maps n06 snake_case registry keys → core.lore universe name format
_LORE_KEY: dict[str, str] = {
    "age_of_aether":       "Age-of-Aether",
    "the_veil_ascendancy": "Veil-Ascendancy",
    "the_vigil":           "The-Vigil",
}

def pick_universe() -> str:
    keys    = list(UNIVERSE_REGISTRY)
    weights = [UNIVERSE_REGISTRY[k]["weight"] for k in keys]
    return random.choices(keys, weights=weights, k=1)[0]


def get_latest_expansion():
    files = sorted(EXPANSIONS_DIR.glob("Expansion_*.md"), reverse=True)
    if not files:
        print("[lore_updater] No expansion files found.")
        return None, None
    return files[0], files[0].read_text(encoding="utf-8")


def extract_frontmatter(text):
    """Pull arc and aether_state from YAML frontmatter."""
    data = {}
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for field in ["arc", "aether_state"]:
            m = re.search(rf"^{field}:\s*(.+)$", fm, re.MULTILINE)
            if m:
                data[field] = m.group(1).strip().strip('"')
    return data


def extract_character_activity(text):
    """Extract last sentence/fragment mentioning each character from Section IV."""
    activity = {}
    # Grab Section IV block
    section = re.search(
        r"SECTION IV.*?(?:AGE OF AETHER|THE LOST NET|THE VIGIL|VEIL ASCENDANCY)(.*?)(?=SECTION V|CODEX ENTRY|---|\Z)",
        text, re.DOTALL | re.IGNORECASE
    )
    block = section.group(1) if section else text

    for char in CHARACTERS:
        # Find all sentences mentioning the character
        sentences = re.findall(
            rf"[^.!?\n]*\b{char}\b[^.!?\n]*[.!?]",
            block, re.IGNORECASE
        )
        if sentences:
            activity[char] = sentences[-1].strip()  # most recent mention

    return activity


def extract_wound_zone_updates(text):
    """Extract status lines for each Wound Zone."""
    updates = {}
    for zone_id in WOUND_ZONES:
        # Match lines containing the zone ID and grab the rest of that line
        m = re.search(
            rf"{re.escape(zone_id)}[^\n]*",
            text, re.IGNORECASE
        )
        if m:
            updates[zone_id] = m.group(0).strip()
    return updates


def extract_codex_entry(text):
    """Pull Codex Entry block if present."""
    m = re.search(
        r"CODEX ENTRY[:\s\-]*(.*?)(?=SECTION|---|\Z)",
        text, re.DOTALL | re.IGNORECASE
    )
    if m:
        entry = m.group(1).strip()
        # Only return if non-trivial
        return entry if len(entry) > 20 else None
    return None


def extract_sovereign_spark(text):
    """Pull the atmosphere/mechanic note from Section V."""
    m = re.search(
        r"SOVEREIGN SPARK(.*?)(?=CODEX ENTRY|SECTION|---|\Z)",
        text, re.DOTALL | re.IGNORECASE
    )
    if m:
        spark = m.group(1).strip()
        # First non-empty line
        lines = [l.strip() for l in spark.splitlines() if l.strip()]
        return lines[0] if lines else None
    return None


def load_lore_state():
    if LORE_STATE_PATH.exists():
        with open(LORE_STATE_PATH, "r") as f:
            return json.load(f)
    return {}


def save_lore_state(state):
    tmp = LORE_STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, LORE_STATE_PATH)
    print(f"[lore_updater] Lore state written → {LORE_STATE_PATH}")


def run():
    global EXPANSIONS_DIR, LORE_STATE_PATH, CHARACTERS, WOUND_ZONES
    u_key = pick_universe()
    u = UNIVERSE_REGISTRY[u_key]
    EXPANSIONS_DIR  = u["expansions_dir"]
    LORE_STATE_PATH = u["lore_state"]
    _cfg = load_config()
    _cfg["active_universe"] = _LORE_KEY[u_key]
    _ctx = load_universe_context(_cfg)
    CHARACTERS  = [c["name"] for c in _ctx["characters"]]
    WOUND_ZONES = [
        z if isinstance(z, str) else z.get("name") or z.get("zone") or z.get("location") or ""
        for z in _ctx["contested_zones"]
    ]
    WOUND_ZONES = [z for z in WOUND_ZONES if z]  # drop any empty fallbacks
    EXPANSIONS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[lore_updater] Universe: {u_key}")

    filepath, text = get_latest_expansion()
    if not text:
        print("[lore_updater] No prior expansion — skipping state update. Run n04 first.")
        return

    print(f"[lore_updater] Reading: {filepath.name}")

    state = load_lore_state()

    # Frontmatter fields
    fm = extract_frontmatter(text)
    if fm.get("arc"):
        state["current_arc"] = fm["arc"]
    if fm.get("aether_state"):
        state["aether_state"] = fm["aether_state"]

    # Character activity
    activity = extract_character_activity(text)
    if activity:
        state.setdefault("character_last_seen", {}).update(activity)
        print(f"[lore_updater] Character updates: {list(activity.keys())}")

    # Wound zone status
    wound_updates = extract_wound_zone_updates(text)
    if wound_updates:
        state.setdefault("wound_zones", {}).update(wound_updates)
        print(f"[lore_updater] Wound zone updates: {list(wound_updates.keys())}")

    # Codex entry
    codex = extract_codex_entry(text)
    if codex:
        state.setdefault("codex_entries", []).append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "source": filepath.name,
            "entry": codex
        })
        print(f"[lore_updater] Codex entry captured.")

    # Sovereign Spark
    spark = extract_sovereign_spark(text)
    if spark:
        state["last_sovereign_spark"] = spark

    # Timestamp
    state["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    state["last_expansion_file"] = filepath.name

    save_lore_state(state)


if __name__ == "__main__":
    run()
