"""
core/lore.py — Universe context loader and normalizer.

Provides load_universe_context(config) → UniverseContext dict.
Called by n04_strategist.py and n11_ignition.py.
Neither node reads state files directly — this is the single access point.

Import layer: core/ only — stdlib + third-party, no upward imports.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vault root — mirrors core/config.py convention (no import to avoid coupling)
# ---------------------------------------------------------------------------
_VAULT_ROOT = Path.home() / "sovereign"


# ---------------------------------------------------------------------------
# Universe registry — maps config key → relative state file path
# ---------------------------------------------------------------------------
UNIVERSE_REGISTRY: dict[str, str] = {
    "The-Vigil":        "Data/vigil/vigil_state.json",
    "Age-of-Aether":    "Data/lore_state_ageofaether.json",
    "Veil-Ascendancy":  "Data/veil_ascendancy/lore_state_veil_ascendancy.json",
}


# ---------------------------------------------------------------------------
# Output contract — both n04 and n11 consume exactly this shape
# ---------------------------------------------------------------------------
class UniverseContext(TypedDict):
    universe_name:   str
    arc_summary:     str
    arc_name:        str          # short arc title e.g. 'The Merchant Wars'
    arc_status:      str          # e.g. RISING / PEAK / FALLING
    aether_state:    str          # energy / metaphysical status line
    tension:         str          # current dramatic tension descriptor
    last_beat:       str          # most recent story beat
    next_beat:       str          # next beat to generate toward
    open_threads:    list[str]    # unresolved plot threads
    active_threat:   str          # primary antagonist / threat
    active_faction:  str          # dominant faction in play
    characters:      list[dict]   # normalized list; each has at minimum "name"
    contested_zones: list          # locations under active conflict (may be str or dict)
    protected_beats: list          # beats that must not be contradicted (may be str or dict)
    secondary_roster:         list[dict]   # secondary characters beyond the named cast
    ignition_beats:           list[dict]   # classified ignition outputs (LEGEND / CANON)
    legendary_ops:            list[dict]   # named legendary operations registry
    combat_vocabulary:        dict         # world-specific terminology
    legend_naming_convention: str          # how legendary ops get their names


# ---------------------------------------------------------------------------
# Internal: characters normalization
# Vigil stores characters as a JSON array.
# AoA stores characters as a dict-of-dicts keyed by name.
# Veil: treated same as Vigil (array) — falls back gracefully.
# ---------------------------------------------------------------------------
def _normalize_characters(raw_chars: Any) -> list[dict]:
    """Return a flat list of character dicts, each guaranteed to have 'name'."""
    if isinstance(raw_chars, list):
        result = []
        for entry in raw_chars:
            if isinstance(entry, dict):
                result.append(entry)
            else:
                # Bare string name — wrap it
                result.append({"name": str(entry)})
        return result

    if isinstance(raw_chars, dict):
        result = []
        for name, data in raw_chars.items():
            if isinstance(data, dict):
                entry = {"name": name, **data}
            else:
                entry = {"name": name, "notes": str(data)}
            result.append(entry)
        return result

    # Unexpected shape — return empty, log a warning
    logger.warning("core/lore.py: unexpected characters shape: %s", type(raw_chars))
    return []


# ---------------------------------------------------------------------------
# Internal: generic field extraction with fallback
# All three state files share the same top-level keys after Session 73
# standardization. Only characters format differs — handled above.
# ---------------------------------------------------------------------------
def _extract(raw: dict, *keys: str, default: str = "") -> str:
    """Try each key in order; return first non-empty string found."""
    for key in keys:
        val = raw.get(key)
        if val and isinstance(val, str):
            return val.strip()
    return default


def _extract_list(raw: dict, *keys: str) -> list:
    """Try each key in order; return first non-empty list found."""
    for key in keys:
        val = raw.get(key)
        if isinstance(val, list) and val:
            return val
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_universe_context(config: dict) -> UniverseContext:
    """
    Load and normalize the active universe state.

    Args:
        config: dict from core/config.py load_config()

    Returns:
        UniverseContext — normalized dict consumed by n04 and n11.

    Raises:
        ValueError: unknown active_universe key in config
        FileNotFoundError: state file missing from vault
    """
    active = config.get("active_universe", "The-Vigil")

    if active not in UNIVERSE_REGISTRY:
        raise ValueError(
            f"core/lore.py: unknown universe {active!r}. "
            f"Valid keys: {list(UNIVERSE_REGISTRY)}"
        )

    state_path = _VAULT_ROOT / UNIVERSE_REGISTRY[active]

    if not state_path.exists():
        raise FileNotFoundError(
            f"core/lore.py: state file not found: {state_path}\n"
            f"Run reindex or verify Data/ structure."
        )

    with open(state_path, encoding="utf-8") as fh:
        raw = json.load(fh)

    # Vigil nests arc fields under a top-level "arc" key; AoA/Veil are flat.
    # Merge arc sub-keys into raw so _extract() works uniformly across all universes.
    # Top-level keys win on conflict (e.g. universe_name stays untouched).
    if isinstance(raw.get("arc"), dict):
        raw = {**raw, **raw.get("arc")}

    # --- Characters normalization (schema difference handled here) ---
    raw_chars = raw.get("characters", [])
    characters = _normalize_characters(raw_chars)

    # --- Flat field extraction with fallback key aliases ---
    # arc_summary: AoA uses "arc_summary"; Vigil also uses "arc_summary" (Session 73 standard)
    arc_summary = _extract(raw, "arc_summary", "arc_description", "arc")
    arc_name    = _extract(raw, "current_arc", "arc_name", "arc_title")

    # arc_status: stored as "arc_status" in all three after Session 73
    arc_status = _extract(raw, "arc_status", "status")

    # aether_state: AoA uses "aether_state"; Vigil may use "world_state" or "energy_state"
    aether_state = _extract(raw, "aether_state", "world_state", "energy_state", "aether_notes")

    # tension: stored as "tension" in all three after Session 73
    tension = _extract(raw, "tension", "dramatic_tension", "current_tension")

    # last/next beat
    last_beat = _extract(raw, "last_beat", "previous_beat", "last_expansion")
    next_beat  = _extract(raw, "next_beat",  "upcoming_beat", "next_expansion")

    # open_threads
    open_threads = _extract_list(raw, "open_threads", "threads", "unresolved")

    # active_threat
    active_threat = _extract(raw, "active_threat", "primary_threat", "threat")

    # active_faction — added Session 73 to all state files
    active_faction = _extract(raw, "active_faction", "dominant_faction", "faction")

    # contested_zones — added Session 73
    contested_zones = _extract_list(raw, "contested_zones", "wound_zones", "zones")

    # protected_beats — added Session 73
    protected_beats = _extract_list(raw, "protected_beats", "locked_beats", "canon_beats")

    # secondary_roster — Vigil secondary characters (Vigil-specific; graceful empty for others)
    secondary_roster = _extract_list(raw, "secondary_roster")

    # ignition_beats — classified creative outputs accumulated over runs
    ignition_beats = _extract_list(raw, "ignition_beats")

    # legendary_ops — named operations registry
    legendary_ops = _extract_list(raw, "legendary_ops")

    # combat_vocabulary — world-specific terminology dict
    combat_vocabulary = raw.get("combat_vocabulary", {})
    if not isinstance(combat_vocabulary, dict):
        combat_vocabulary = {}

    # legend_naming_convention — how ops get named (prose string)
    legend_naming_convention = _extract(raw, "legend_naming_convention")

    return UniverseContext(
        universe_name   = active,
        arc_summary     = arc_summary,
        arc_name        = arc_name,
        arc_status      = arc_status,
        aether_state    = aether_state,
        tension         = tension,
        last_beat       = last_beat,
        next_beat       = next_beat,
        open_threads    = open_threads,
        active_threat   = active_threat,
        active_faction  = active_faction,
        characters      = characters,
        contested_zones = contested_zones,
        protected_beats          = protected_beats,
        secondary_roster         = secondary_roster,
        ignition_beats           = ignition_beats,
        legendary_ops            = legendary_ops,
        combat_vocabulary        = combat_vocabulary,
        legend_naming_convention = legend_naming_convention,
    )
