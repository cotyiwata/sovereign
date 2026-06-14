#!/usr/bin/env python3
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""
ignition_generator.py — Sovereign Intelligence System
Creative Ignition Engine v1.0

Generates daily creative bites:
  - ANCHORED: drawn from active universes via RAG
  - WILD CARD: inspired by Coty's taste palette or fully original

Output: ~/sovereign/05-Ignition/Ignition_YYYY-MM-DD_HHMM.md + .html
Alias:  ignite
Cron:   runs inside sovereign_launch.sh after Node 4

Usage:
  python ignition_generator.py           # full run (anchored + wild card)
  python ignition_generator.py --wild    # wild card only
  python ignition_generator.py --anchor  # anchored only
  python ignition_generator.py --universe "the_lost_net"  # force a specific universe
"""

import json
import re
import os
import sys
import random
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
from core.config import VAULT_ROOT as SOVEREIGN, SCRIPTS_DIR as SCRIPTS, load_config
from core.lore  import load_universe_context
IGNITION_DIR = SOVEREIGN / "05-Ignition"
INBOX     = SOVEREIGN / "00-Inbox"
CONFIG    = load_config()

# ── Models ─────────────────────────────────────────────────────────────────────
CREATIVE_MODEL = "gemma3:12b"

# ── Coty's taste palette ───────────────────────────────────────────────────────
TASTE_PALETTE = [
    "Ready Player One",
    "Jujutsu Kaisen",
    "Bleach",
    "Solo Leveling",
    "John Wick",
    "The Matrix",
    "Star Wars",
    "Dune",
    "Warhammer 40K",
    "Lord of the Rings",
    "Kaiju No. 8",
    "Black Clover",
    "James Bond",
    "Hellboy",
    "Harry Potter",
    "He-Man",
    "Teenage Mutant Ninja Turtles",
    "Batman",
    "Cyberpunk 2077",
    "Blade Runner",
    "Terminator",
]

# ── Mode wheel — weighted random ───────────────────────────────────────────────
MODES = [
    ("MICRO-SCENE",        0.35),
    ("WORLD CONCEPT",      0.25),
    ("CHARACTER FRAGMENT", 0.20),
    ("PHILOSOPHICAL BITE", 0.20),
]

MODE_DESCRIPTIONS = {
    "MICRO-SCENE": (
        "Write a visceral micro-scene of 200-300 words. Pure immersion. "
        "Show don't tell. No exposition dumps. Drop the reader mid-moment."
    ),
    "WORLD CONCEPT": (
        "Describe a single world rule, technology, power system, or social dynamic "
        "in 150-250 words. Reveal it through implication and texture, not lecture. "
        "Make the reader feel the weight of how this thing shapes life."
    ),
    "CHARACTER FRAGMENT": (
        "Write 150-250 words from one person's perspective — one moment, no setup given. "
        "The world is implied through their thoughts and reactions. "
        "The reader infers everything. No character name required."
    ),
    "PHILOSOPHICAL BITE": (
        "Write 150-200 words on an idea that bleeds from fiction into real life — "
        "about power, sacrifice, identity, systems, or becoming. "
        "Anchor it in a specific fictional texture but let the truth land universally."
    ),
}

# ── Anchored mode wheel — 6-mode weighted (separate from wild card MODES) ────
ANCHORED_MODES = [
    ("FIRETEAM OP",       0.30),
    ("RAID",              0.28),
    ("LARGE ENGAGEMENT",  0.25),
    ("CHARACTER MOMENT",  0.12),
    ("LORE FRAGMENT",     0.05),
]

ANCHORED_MODE_DESCRIPTIONS = {
    "FIRETEAM OP": (
        "Write a single continuous action scene of at least 600 words. "
        "A small Vigil unit — two to four operators — executes one objective inside a named zone. "
        "Each character's class role must be visible through what they do under fire, not what they're called. "
        "The scene has a beginning, a complication, and a hard end — the objective is reached, lost, or abandoned. "
        "Stay in one location. Show the seams between people working under pressure: "
        "what they say over comms, what they don't say, what breaks and what holds. "
        "The enemy is present and dangerous. Contact happens. Write through it."
    ),
    "RAID": (
        "Write a single continuous action scene of at least 600 words. "
        "A Vigil strike force hits a hardened position — Forsaken-held, Choir-saturated, or structurally hostile. "
        "Show the approach, the breach, and the inside of the fight. "
        "The math is against them and the reader should feel it: ammunition, angles, time, bodies. "
        "Stay in one location. The scene ends when the objective is secured, the extraction is forced, "
        "or the cost becomes the story. Do not summarize the action — write it moment by moment."
    ),
    "LARGE ENGAGEMENT": (
        "Write a single continuous action scene of at least 600 words. "
        "A full-scale engagement — multiple Vigil units, faction-level Forsaken or Choir pressure, "
        "a zone being contested or lost in real time. "
        "Characters are inside something larger than themselves and the reader should feel the scale "
        "through specific ground-level detail: one corridor, one position, one decision inside the chaos. "
        "The battle moves. Show it moving. Stay grounded in what one perspective can see, hear, and lose. "
        "End on a consequence — a position taken, a line broken, a name that won't file a report."
    ),
    "CHARACTER MOMENT": (
        "Write a single continuous scene of at least 600 words. "
        "One Vigil operator, one situation — not necessarily combat, but something is at stake and pressure is real. "
        "A mission going wrong, a hold position stretching past its limits, a decision that can't be walked back. "
        "The world is built through what they notice, what they reach for, what they refuse. "
        "Ground it in the zone — the environment pushes back. "
        "End on something that has changed, even if nothing around them shows it yet."
    ),
    "LORE FRAGMENT": (
        "Write a single continuous scene of at least 600 words. "
        "A location, ruin, artifact, or system is the subject — but a Vigil operator is present and moving through it. "
        "The thing reveals itself through what it does to the person inside it: "
        "what it costs to enter, what it changes about how they move, what it makes them remember or refuse to say. "
        "No exposition. The environment is alive and the reader should feel it. "
        "End on something the operator will not put in their report."
    ),
}

# ── Active universes ───────────────────────────────────────────────────────────
ACTIVE_UNIVERSES = {
    "age_of_aether": {
        "display": "AGE OF AETHER",
        "weight": 0.0,
        "lore_doc_type": "lore_aether",
        "genre": "dark sci-fi action, gritty heroism, cinematic combat",
        "logline": "In the ruins of a collapsed golden age, six fighters — unknown to each other — are already inside a problem the factions above them are too slow to see.",
        "arc": "The Merchant Wars — Wound Zones expanding, Zhal'Thar Horrors probing — Tension RISING",
        "characters": "Kaelric (Acolyte, unknown Titan bloodline), Ignar (House Pyros fire-Aether, first command), Nyxara (Void Aether, hunted, unclassified), Korrven (Beast Clan, volatile transformation), Varek Solen (Enforcer Ronin, hunted by House Vorn, Seer's Lens relic), Toran Vael (Acolyte Vanguard, young, fighting for sponsorship at Karath Station)",
        "next_beat": "Varek grips the Seer's Lens in combat — visions implicate House Vorn in the Golden Age collapse. Toran's Aether surges uncontrolled — House Pyros scout and a rival faction both witness it.",
        "lore_state": SOVEREIGN / "Data" / "lore_state_ageofaether.json",
    },
    "the_veil_ascendancy": {
        "display": "VEIL ASCENDANCY",
        "weight": 0.0,
        "lore_doc_type": "lore_veil",
        "genre": "near-future cosmic-horror military sci-fi, ensemble teams, mystery escalation",
        "logline": "Three teams hunt fragments of a planet-killing Weapon scattered across the system — but each fragment they recover wakes something older that wanted to stay buried, and High Command's compartmentalization is cracking from the inside.",
        "arc": "Season 1 — Factions converging on Mars Shard excavation; Swarm probing; High Command compartmentalization fracturing — Tension RISING",
        "characters": "Aria Vehn (High Command, military formal, 'we' language, compartmentalization philosophy), Reeves (intellectual synthesist, 'data suggests'), Kael (Team A leader — quiet, direct, breaks protocol when it matters), Maris (Team B leader — cool, calculating, strategic, slightly amoral), Zephyr (Team 3 leader Season 2 — questions broken authority)",
        "next_beat": "Mars Shard excavation triggers all-faction convergence; Reeves and Team B begin alliance against High Command compartmentalization; Archive discovery on Io seeds Season 2 mystery escalation.",
        "lore_state": SOVEREIGN / "Data" / "veil_ascendancy" / "lore_state_veil_ascendancy.json",
    },
    "the_vigil": {
        "display": "THE VIGIL",
        "weight": 1.0,
        "lore_doc_type": "lore_vigil",
        "genre": "post-apocalyptic military sci-fi, found family, grinding attrition, light vs corruption",
        "logline": "Fireteam Dusk races to reactivate dark Anchor Points while the Forsaken network accelerates the Golden Age’s unfinished work. Casualty rate equals replacement rate. Humanity is staying flat.",
        "arc": "The Reconnection — Forsaken network accelerating, Choir pressure intensifying, First Avatar not yet manifested — Tension RISING",
        "characters": "Bohr (Sentinel/Conqueror, covering two positions since Tallus drowned, hasn’t named the gap), Sable (Phantom/Cutthroat, staying in copy-state longer than missions require), Rienne (Sage/Curator, filing no reports on what she’s read about the Golden Age’s edge)",
        "next_beat": "Bohr holds a chokepoint alone while Sable runs a Converted extraction under live fire. Rienne maintains battlefield control from a collapsing Anchor Point position as the Choir pressure escalates.",
        "lore_state": SOVEREIGN / "Data" / "vigil" / "vigil_state.json",
        "mode_overrides": {
            "MICRO-SCENE": (
                "Write a visceral combat or operational micro-scene of 200-300 words. "
                "Default to kinetic action: a Sentinel holding a chokepoint alone, a Phantom running "
                "a Converted extraction under fire, a Sage maintaining battlefield control as lines collapse, "
                "a fireteam pushing into a corrupted Anchor Point with Choir pressure rising. "
                "Pure immersion. No exposition. Drop the reader mid-action. "
                "Show the cost - physical, tactical, human."
            ),
            "CHARACTER FRAGMENT": (
                "Write 150-250 words from inside an operational moment - not reflection, but action under pressure. "
                "A Sentinel covering ground and calculating who he can lose. "
                "A Phantom in copy-state past the safe threshold, still moving. "
                "A Sage holding a battlefield shape while the situation degrades around her. "
                "Thoughts are tactical. Observations are threat assessments. "
                "The world is implied through decisions and physical consequence, not feeling."
            ),
            "FIRETEAM OP": (
                "Write a single continuous action scene of at least 600 words. "
                "Fireteam Dusk executes one objective inside a named Vigil zone. "
                "All three classes visible through action: Sentinel holds or breaches, Phantom flanks or extracts, "
                "Sage maintains battlefield shape or comms under Choir pressure or Forsaken contact. "
                "The scene has a beginning, a complication, and a hard end — the objective is reached, lost, or abandoned. "
                "Stay in one location. Show what they say over comms, what they don't say, what breaks and what holds. "
                "The enemy is present and dangerous. Contact happens. Write through it. Show the cost."
            ),
            "RAID": (
                "Write a single continuous action scene of at least 600 words. "
                "Fireteam Dusk hits a hardened position — Forsaken-held, Choir-saturated, or structurally hostile. "
                "Show the approach, the breach, and the inside of the fight. "
                "The math is against them: three against many, Choir pressure or Converted presence closing the odds further. "
                "Show what that gap costs in movement, ammunition, and decision economy. "
                "Stay in one location. The scene ends when the objective is secured, the extraction is forced, "
                "or the cost becomes the story. Do not summarize the action — write it moment by moment."
            ),
            "LARGE ENGAGEMENT": (
                "Write a single continuous action scene of at least 600 words. "
                "An Anchor Point activation, a multi-fireteam push, or a Forsaken advance at scale. "
                "Fireteam Dusk is present — witnesses, participants, or last line standing. "
                "The reader feels the scale through specific ground-level detail: one corridor, one chokepoint, "
                "one decision inside something larger than any single operator can see. "
                "The battle moves — show it moving. Stay grounded in what Fireteam Dusk can see, hear, and lose. "
                "End on a consequence: a position taken, a line broken, a name that won't file a report."
            ),
            "LORE FRAGMENT": (
                "Write a single continuous scene of at least 600 words. "
                "An Anchor Point, Forsaken artifact, Choir relic, or pre-collapse ruin is the subject — "
                "but a Vigil operator is present and moving through it. "
                "The thing reveals itself through what it does to the person inside it: "
                "what it costs to enter, what it changes about how they move, what it makes them remember or refuse to say. "
                "Reveal its nature through what it does to the air, equipment, and body. No exposition. "
                "The environment is alive and the reader should feel it. "
                "End on something the operator will not put in their report."
            ),
        },
    },
    # "the_lost_net": { ... },  # paused — reactivate when ready
}


# ── Utilities ──────────────────────────────────────────────────────────────────

def weighted_choice(options):
    """Pick from list of (value, weight) tuples."""
    values, weights = zip(*options)
    return random.choices(values, weights=weights, k=1)[0]


def ollama(prompt: str, model: str = CREATIVE_MODEL) -> str:
    """Call Ollama via core.llm.generate."""
    from core.llm import generate
    try:
        return generate(prompt, system="", model=model)
    except Exception as e:
        return f"[IGNITION ERROR: {e}]"


def load_lore_state(path: Path) -> dict:
    """Load lore state JSON if it exists."""
    if path and path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def rag_retrieve(query: str, doc_type: str = "lore", n: int = 2) -> str:
    """Pull RAG context via core.rag.retriever."""
    try:
        from core.rag.retriever import retrieve
        results = retrieve(query, n=n, doc_type=doc_type)
        return "\n".join(r["text"][:300] for r in results if r["distance"] < 0.85)
    except Exception:
        return ""


def rag_index(filepath: Path):  # noqa
    """Index the Ignition file into ChromaDB."""
    try:
        from core.rag.indexer import run_index
        run_index(rebuild=False)
    except Exception:
        pass


# ── Classification handler ─────────────────────────────────────────────────────

def _append_ignition_beat(mode: str, classification: str, full_text: str, state_path: Path):
    """Append a LEGEND or CANON beat to ignition_beats in the active universe state file."""
    if not state_path or not state_path.exists():
        return
    try:
        import json as _json
        with open(state_path, encoding="utf-8") as f:
            state = _json.load(f)
        summary = (full_text.split('.')[0].strip())[:120]
        beat = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "mode": mode,
            "classification": classification,
            "summary": summary,
            "full_text": full_text,
        }
        if not isinstance(state.get("ignition_beats"), list):
            state["ignition_beats"] = []
        state["ignition_beats"].append(beat)
        with open(state_path, "w", encoding="utf-8") as f:
            _json.dump(state, f, indent=2, ensure_ascii=False)
        print(f"[IGNITION] Beat classified {classification} — appended to ignition_beats")
    except Exception as e:
        print(f"[IGNITION] Warning: failed to append ignition beat: {e}")


def parse_and_route_classification(content: str, mode: str, state_path: Path) -> tuple:
    """
    Parse CLASSIFICATION line from model output.
    Strips the label from display content.
    Routes LEGEND/CANON to state file ignition_beats.
    Returns (clean_content, classification_label).
    """
    classification = "TEXTURE"
    match = re.search(r'CLASSIFICATION:\s*(TEXTURE|LEGEND|CANON)', content, re.IGNORECASE)
    if match:
        classification = match.group(1).upper()
        clean_content = content[:match.start()].rstrip()
    else:
        clean_content = content
    if classification in ("LEGEND", "CANON"):
        _append_ignition_beat(mode, classification, clean_content, state_path)
    return clean_content, classification


# ── Anchored generation ────────────────────────────────────────────────────────

def generate_anchored(universe_key: str = None) -> dict:
    """Generate one anchored bite from an active universe."""
    if not ACTIVE_UNIVERSES:
        return {}

    # Pick universe — weighted by per-universe "weight" field (default uniform)
    if universe_key and universe_key in ACTIVE_UNIVERSES:
        key = universe_key
    else:
        u_items = [(k, u.get("weight", 1.0)) for k, u in ACTIVE_UNIVERSES.items()]
        u_keys, u_weights = zip(*u_items)
        key = random.choices(u_keys, weights=u_weights, k=1)[0]
    universe = ACTIVE_UNIVERSES[key]
    mode = weighted_choice(ANCHORED_MODES)
    mode_overrides = universe.get("mode_overrides", {})
    mode_instruction = mode_overrides.get(mode, ANCHORED_MODE_DESCRIPTIONS[mode])

    # Load lore state via core.lore — single access point for all universes
    ctx = load_universe_context(CONFIG)
    tension          = ctx["tension"]        or universe["arc"]
    last_beat        = ctx["last_beat"]      or "Unknown"
    next_beat        = ctx["next_beat"]      or universe["next_beat"]
    open_threads     = ctx["open_threads"]
    protected_beats  = ctx.get("protected_beats", [])
    named_cast       = ctx.get("characters", [])
    secondary_roster = ctx.get("secondary_roster", [])
    contested_zones  = ctx.get("contested_zones", [])
    legendary_ops     = ctx.get("legendary_ops", [])
    combat_vocabulary = ctx.get("combat_vocabulary", [])

    # Fallback: read secondary_roster directly from state file if ctx doesn't surface it
    if not secondary_roster:
        lore_path = universe.get("lore_state")
        if lore_path and Path(lore_path).exists():
            try:
                import json as _json
                _state = _json.loads(Path(lore_path).read_text())
                secondary_roster = _state.get("secondary_roster", [])
            except Exception:
                pass

    # Zone anchor — pick one contested zone, instruct model to invent sub-location within it
    if contested_zones:
        zone_entry = random.choice(contested_zones)
        zone_name = zone_entry.get("name", str(zone_entry)) if isinstance(zone_entry, dict) else str(zone_entry)
    else:
        zone_name = "the Threshold"

    # Character tier — 60% named cast / 25% secondary / 15% anonymous
    tier_roll = random.random()
    if tier_roll < 0.60 and named_cast:
        char = random.choice(named_cast) if isinstance(named_cast, list) else named_cast
        char_name = char.get("name", str(char)) if isinstance(char, dict) else str(char)
        char_tier = "NAMED CAST"
        char_instruction = (
            f"Feature {char_name} or make them unmistakable through their class, relic, "
            "or operational role. Respect all protected beats."
        )
    elif tier_roll < 0.85 and secondary_roster:
        char = random.choice(secondary_roster)
        char_name = char.get("name", str(char)) if isinstance(char, dict) else str(char)
        char_tier = "SECONDARY"
        char_instruction = (
            f"Feature {char_name} — a secondary figure in this world. "
            "Named cast may appear but stay peripheral."
        )
    else:
        char_tier = "ANONYMOUS"
        char_instruction = (
            "No named cast required. Use an unnamed Vigil operative, garrison soldier, "
            "Forsaken, or Forge worker. Let role and situation carry the scene."
        )

    # RAG context — universe-specific canon first, lore_audio inspiration second
    canon_doc_type = universe.get("lore_doc_type", "lore_aether")
    rag_canon = rag_retrieve(f"{universe['genre']} {next_beat}", doc_type=canon_doc_type)
    rag_inspiration = rag_retrieve(f"{universe['genre']} power system world building tone", doc_type="lore_audio")
    combined = "\n".join(filter(None, [rag_canon, rag_inspiration]))
    rag_block = f"\n\nRELEVANT ARCHIVE CONTEXT:\n{combined}" if combined else ""

    threads_block = ""
    if open_threads:
        threads_block = f"\nOpen threads in the world: {', '.join(str(t) for t in open_threads)}"

    constraints_block = ""
    if protected_beats:
        beats_text = "\n".join(f"- {b}" for b in protected_beats)
        constraints_block = f"\nHARD CONSTRAINTS — do not contradict or resolve these:\n{beats_text}\n"

    ops_block = ""
    if legendary_ops:
        op_names = [op.get("name", str(op)) if isinstance(op, dict) else str(op) for op in legendary_ops[:3]]
        ops_block = f"\nNamed operations this world carries: {', '.join(op_names)}.\n"

    vocab_block = ""
    if combat_vocabulary:
        terms = [str(t) for t in combat_vocabulary[:8]] if isinstance(combat_vocabulary, list) else []
        if terms:
            vocab_block = f"\nWorld vocabulary — use naturally, do not explain or define: {', '.join(terms)}.\n"

    prompt = (
        "You are a master fiction writer generating a creative bite for a writer's daily inspiration system.\n\n"
        f"UNIVERSE: {universe['display']}\n"
        f"GENRE: {universe['genre']}\n"
        f"LOGLINE: {universe['logline']}\n"
        f"ARC STATE: {tension}\n"
        f"LAST BEAT: {last_beat}\n"
        f"NEXT BEAT: {next_beat}"
        f"{threads_block}{ops_block}\n\n"
        f"LOCATION: {zone_name} — invent one specific sub-location within it "
        "(a corridor, threshold, approach vector, observation post, or contested chokepoint).\n\n"
        f"CHARACTER TIER: {char_tier}\n"
        f"{char_instruction}\n\n"
        f"{constraints_block}"
        f"{rag_block}\n\n"
        f"MODE: {mode}\n"
        f"INSTRUCTION: {mode_instruction}\n\n"
        f"{vocab_block}"
        "Rules:\n"
        "- Stay true to the world's texture and tone\n"
        "- Do NOT summarize plot or explain the world directly\n"
        "- Name the sub-location. Ground the scene physically — the reader should feel the space.\n"
        "- End on something that lingers — an image, a decision, an unanswered weight\n"
        "- No preamble. No commentary before the content.\n"
        "- After the creative content, on its own line, append exactly one of:\n"
        "  CLASSIFICATION: TEXTURE  (atmosphere or world-building — no arc advancement)\n"
        "  CLASSIFICATION: LEGEND   (an event or moment a fireteam would reference in shorthand)\n"
        "  CLASSIFICATION: CANON    (a character beat or revelation that changes the story state)\n\n"
        f"Write the {mode} now:"
    )

    raw_content = ollama(prompt)
    lore_state_path = universe.get("lore_state")
    if lore_state_path:
        lore_state_path = Path(lore_state_path)
    content, classification = parse_and_route_classification(
        raw_content, mode, lore_state_path
    )
    return {
        "type": "ANCHORED",
        "universe": universe["display"],
        "mode": mode,
        "tier": char_tier,
        "zone": zone_name,
        "content": content,
        "classification": classification,
    }


# ── Wild Card generation ───────────────────────────────────────────────────────

def generate_wild_card() -> dict:
    """Generate one wild card bite — inspired by taste palette or fully original."""
    mode = weighted_choice(MODES)
    mode_instruction = MODE_DESCRIPTIONS[mode]

    # Decide influence
    rag_block = ""  # default — overwritten if lore_audio hit found
    influence_roll = random.random()
    if influence_roll < 0.55:
        # Single influence
        influence = random.choice(TASTE_PALETTE)
        influence_tag = f"Inspired by: {influence}"
        influence_instruction = (
            f"Draw creative inspiration from the world-building, power systems, "
            f"aesthetic, or emotional tone of {influence}. "
            f"Do NOT copy characters, plot, or IP directly. "
            f"Invent something original that carries that spirit."
        )
        rag_ctx = rag_retrieve(f"{influence} world building power system tone aesthetic", doc_type="lore_audio")
        rag_block = f"\n\nARCHIVE CONTEXT:\n{rag_ctx}" if rag_ctx else ""
    elif influence_roll < 0.80:
        # Fusion of two
        two = random.sample(TASTE_PALETTE, 2)
        influence_tag = f"Inspired by: {two[0]} × {two[1]} fusion"
        influence_instruction = (
            f"Fuse the aesthetic and thematic DNA of {two[0]} and {two[1]}. "
            f"Find what they share beneath the surface — tone, power, consequence, style. "
            f"Invent something that couldn't exist without both, but is entirely original."
        )
    else:
        # Fully original
        genres = [
            "grimdark sci-fi", "solarpunk", "mythpunk", "cosmic horror western",
            "progression fantasy", "biopunk thriller", "post-collapse eastern fantasy",
            "dieselpunk noir", "military sci-fi", "arcane heist",
        ]
        genre = random.choice(genres)
        influence_tag = f"Original — {genre}"
        influence_instruction = (
            f"Invent something in the {genre} space. "
            f"No direct influences. Build from first principles. "
            f"Make the world feel lived-in and earned."
        )

    prompt = f"""You are a master fiction writer generating a creative bite for a writer's daily inspiration system.

This is a WILD CARD — completely outside the writer's current projects. 
The goal is to expand their imagination, expose them to ideas they'd never write themselves, 
and spark creative thinking through pure quality.

INFLUENCE: {influence_tag}
{influence_instruction}{rag_block}

MODE: {mode}
INSTRUCTION: {mode_instruction}

The writer loves: chosen ones who didn't ask for it, power systems with real cost, 
lone operators in broken worlds, aesthetic maximalism, worlds with weight and history.

Rules:
- Invent freely — no existing IP, no copied characters
- Let the world be implied, not explained
- Make it visceral, specific, and surprising
- End on something that lingers
- No preamble. No commentary. Output only the creative content.

Write the {mode} now:"""

    content = ollama(prompt)
    return {
        "type": "WILD CARD",
        "influence": influence_tag,
        "mode": mode,
        "content": content,
    }


# ── Markdown builder ───────────────────────────────────────────────────────────

def build_markdown(anchored: dict, wild: dict, now: datetime) -> str:
    date_str = now.strftime("%B %-d, %Y")
    time_str = now.strftime("%-I:%M %p")

    lines = [
        "---",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"time: {time_str}",
        "type: ignition",
        f"model: {CREATIVE_MODEL}",
    ]
    if anchored:
        lines.append(f"universe: {anchored.get('universe', '—')}")
    if wild:
        lines.append(f"wild_influence: {wild.get('influence', '—')}")
    lines += ["tags: [ignition, creative]", "---", ""]

    lines += [f"# IGNITION — {date_str}", ""]

    if anchored:
        cls_tag = f"  ·  {anchored['classification']}" if anchored.get('classification') else ""
        lines += [
            "---",
            "",
            f"## ── ANCHORED ── {anchored['universe']}",
            f"**Mode:** {anchored['mode']}{cls_tag}",
            "",
            anchored["content"],
            "",
        ]

    if wild:
        lines += [
            "---",
            "",
            f"## ── WILD CARD ──",
            f"**{wild['influence']}**  ",
            f"**Mode:** {wild['mode']}",
            "",
            wild["content"],
            "",
        ]

    return "\n".join(lines)


# ── HTML builder ───────────────────────────────────────────────────────────────

def build_html(anchored: dict, wild: dict, now: datetime) -> str:
    date_str = now.strftime("%B %-d, %Y")

    def card(label: str, sublabel: str, mode: str, content: str, accent: str) -> str:
        paragraphs = "".join(f"<p>{p.strip()}</p>" for p in content.split("\n\n") if p.strip())
        return f"""
        <div class="card">
            <div class="card-header" style="border-left: 4px solid {accent};">
                <div class="card-type">{label}</div>
                <div class="card-sub">{sublabel}</div>
                <div class="card-mode">{mode}</div>
            </div>
            <div class="card-body">{paragraphs}</div>
        </div>"""

    anchored_html = ""
    if anchored:
        cls_tag = f" · {anchored['classification']}" if anchored.get('classification') else ""
        anchored_html = card(
            "ANCHORED",
            anchored["universe"],
            f"{anchored['mode']}{cls_tag}",
            anchored["content"],
            "#7c6af7"
        )

    wild_html = ""
    if wild:
        wild_html = card(
            "WILD CARD",
            wild["influence"],
            wild["mode"],
            wild["content"],
            "#f7a647"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IGNITION — {date_str}</title>
<style>
  :root {{
    --bg: #0d0f14;
    --surface: #13161e;
    --border: #1e2330;
    --text: #e2e8f0;
    --muted: #64748b;
    --accent-purple: #7c6af7;
    --accent-orange: #f7a647;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Georgia', serif;
    padding: 2rem 1rem;
    max-width: 780px;
    margin: 0 auto;
    line-height: 1.75;
  }}
  header {{
    margin-bottom: 2.5rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 1rem;
  }}
  header h1 {{
    font-size: 0.7rem;
    letter-spacing: 0.25em;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 0.25rem;
  }}
  header h2 {{
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--text);
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 2rem;
    overflow: hidden;
  }}
  .card-header {{
    padding: 1rem 1.25rem;
    background: #0f1117;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }}
  .card-type {{
    font-size: 0.6rem;
    letter-spacing: 0.2em;
    color: var(--muted);
    text-transform: uppercase;
  }}
  .card-sub {{
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--text);
    letter-spacing: 0.05em;
  }}
  .card-mode {{
    font-size: 0.65rem;
    letter-spacing: 0.15em;
    color: var(--muted);
    text-transform: uppercase;
    margin-top: 0.1rem;
  }}
  .card-body {{
    padding: 1.5rem 1.25rem;
    font-size: 1rem;
    color: #cbd5e1;
  }}
  .card-body p {{
    margin-bottom: 1rem;
  }}
  .card-body p:last-child {{
    margin-bottom: 0;
  }}
  footer {{
    margin-top: 2rem;
    font-size: 0.7rem;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-align: center;
    text-transform: uppercase;
  }}
</style>
</head>
<body>
<header>
  <h1>Sovereign Intelligence System — Ignition</h1>
  <h2>{date_str}</h2>
</header>

{anchored_html}
{wild_html}

<footer>Generated {now.strftime("%-I:%M %p")} · {CREATIVE_MODEL} · sovereign ignition v1.0</footer>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sovereign Ignition Generator")
    parser.add_argument("--wild",      action="store_true", help="Wild card only")
    parser.add_argument("--anchor",    action="store_true", help="Anchored only")
    parser.add_argument("--universe",  type=str, default=None, help="Force a specific universe key")
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")

    INBOX.mkdir(parents=True, exist_ok=True)

    anchored = {}
    wild = {}

    if args.wild:
        print("[IGNITION] Generating wild card...")
        wild = generate_wild_card()
    elif args.anchor:
        print("[IGNITION] Generating anchored bite...")
        anchored = generate_anchored(universe_key=args.universe)
    else:
        print("[IGNITION] Generating anchored bite...")
        anchored = generate_anchored(universe_key=args.universe)
        print("[IGNITION] Generating wild card...")
        wild = generate_wild_card()

    IGNITION_DIR.mkdir(exist_ok=True)
    md_path   = IGNITION_DIR / f"Ignition_{timestamp}.md"
    html_path = IGNITION_DIR / f"Ignition_{timestamp}.html"

    md_content   = build_markdown(anchored, wild, now)
    html_content = build_html(anchored, wild, now)

    md_path.write_text(md_content, encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")

    print(f"[IGNITION] ✅ {md_path.name}")
    print(f"[IGNITION] ✅ {html_path.name}")

    # RAG index
    print("[IGNITION] Indexing to RAG...")
    rag_index(md_path)
    print("[IGNITION] ✅ RAG indexed")


if __name__ == "__main__":
    main()
