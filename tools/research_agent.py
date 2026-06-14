#!/usr/bin/env python3
"""
tools/research_agent.py — Research Agent orchestrator
Checks agent_state.txt, runs AccuracyScientist, logs output.

MacroAnalyst runs INDEPENDENTLY via `macro-review` alias.
This orchestrator only drives the AccuracyScientist cycle.

Usage:
    research               # via alias
    python3 tools/research_agent.py

Pause control:
    agent-pause            # echo PAUSED > Data/agent_state.txt
    agent-resume           # echo ACTIVE > Data/agent_state.txt
    agent-status           # cat Data/agent_state.txt

Import rules: tools/* → core/*, analysis/* only. No node imports.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# sys.path bootstrap
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import VAULT_ROOT

# ---------------------------------------------------------------------------
# Logging — file + console
# ---------------------------------------------------------------------------
LOG_PATH = VAULT_ROOT / "logs" / "research_agent.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AGENT_STATE_PATH = VAULT_ROOT / "Data" / "agent_state.txt"

# ---------------------------------------------------------------------------
# Pause check
# ---------------------------------------------------------------------------

def is_paused() -> bool:
    """Return True if agent_state.txt contains PAUSED."""
    if not AGENT_STATE_PATH.exists():
        return False  # No file → default ACTIVE
    state = AGENT_STATE_PATH.read_text(encoding="utf-8").strip().upper()
    return state == "PAUSED"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    log.info("=== Research Agent starting ===")

    # Pause gate
    if is_paused():
        log.info("Agent is PAUSED — set ACTIVE via `agent-resume` to run")
        log.info("=== Research Agent exiting (paused) ===")
        return

    start = datetime.now()

    # --- AccuracyScientist ---
    log.info("Running AccuracyScientist...")
    try:
        from tools.accuracy_scientist import run as run_accuracy_scientist
        output_path = run_accuracy_scientist()
        if output_path:
            log.info(f"AccuracyScientist complete → {output_path.name}")
        else:
            log.info("AccuracyScientist found no issues")
    except Exception as e:
        log.error(f"AccuracyScientist failed: {e}", exc_info=True)

    # --- LORE WORKER — deferred ---
    # lore_worker = LoreWorker()
    # lore_worker.run()

    elapsed = (datetime.now() - start).total_seconds()
    log.info(f"=== Research Agent complete — {elapsed:.1f}s ===")
    log.info("MacroAnalyst runs separately via `macro-review` alias")


if __name__ == "__main__":
    run()
