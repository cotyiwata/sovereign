#!/usr/bin/env python3
"""
pipeline.py — Sovereign Intelligence System Orchestrator
Replaces: run_all_daily.py

Node scripts now live in nodes/ instead of Scripts root.
Behavior is unchanged — this file only knows pipeline order and timing.

Usage:
    python pipeline.py
    python pipeline.py --skip-lore
    python pipeline.py --only Plays
    alias: daily
"""
import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import SCRIPTS_DIR, LOGS_DIR, TRADING_DIR
from core.rag.indexer import run_index

PYTHON    = sys.executable
TRADE_LOG = TRADING_DIR / "trade_log.json"
PIPE_LOG  = LOGS_DIR / "sovereign_daily.log"

# ── Node registry ─────────────────────────────────────────────────────────────
# Order is meaningful — downstream nodes consume upstream outputs.
# critical=False means failure logs but does not halt the pipeline.

@dataclass
class Node:
    name:           str
    script:         str                 # relative to SCRIPTS_DIR
    critical:       bool  = True
    optional_group: str   = ""          # e.g. "lore" → skipped by --skip-lore


NODES = [
    Node("Inbox Processor", "nodes/n00_inbox.py",         critical=False),
    Node("Scout",           "nodes/n01_scout.py",         critical=True),
    Node("Levels",          "nodes/n02_levels.py",        critical=True),
    Node("Archivist",       "nodes/n03_chronicle.py",     critical=True),
    Node("Strategist",      "nodes/n04_strategist.py",    critical=True),
    Node("Lore Updater",    "nodes/n06_lore_updater.py",  critical=False, optional_group="lore"),
    Node("HTML Renderer",   "nodes/n05_brief.py",         critical=True),
    Node("Lore Renderer",   "nodes/n07_lore_renderer.py", critical=False, optional_group="lore"),
    Node("Plays",           "nodes/n08_plays.py",         critical=True),
    Node("Trade Log",       "nodes/n09_trade_log.py",     critical=True),
    Node("Dashboard",       "nodes/n10_dashboard.py",     critical=False),
    # Node("Ignition",        "nodes/n11_ignition.py",      critical=False, optional_group="lore"),  # on-demand only — use: ignite
]


# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class NodeResult:
    name:        str
    duration_s:  float = 0.0
    exit_code:   int   = 0
    skipped:     bool  = False
    skip_reason: str   = ""

    @property
    def status(self) -> str:
        if self.skipped:   return "SKIP"
        if self.exit_code: return "FAIL"
        return "OK"


# ── Logging ───────────────────────────────────────────────────────────────────

class Tee:
    """Mirror stdout to both terminal and pipeline log."""
    def __init__(self, log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log  = open(log_path, "a")
        self.term = sys.__stdout__
        self.log.write(
            f"\n{'='*60}\n"
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}] PIPELINE START\n"
            f"{'='*60}\n"
        )
        self.log.flush()

    def write(self, s: str) -> None:
        self.term.write(s)
        self.log.write(s)
        self.log.flush()

    def flush(self) -> None:
        self.term.flush()
        self.log.flush()


# ── Execution ─────────────────────────────────────────────────────────────────

def run_node(node: Node) -> NodeResult:
    path = SCRIPTS_DIR / node.script
    print(f"\n⚡ [{node.name.upper()}] Engaging {node.script}...")
    t0 = time.perf_counter()
    try:
        env = {**os.environ, 'PYTHONPATH': str(SCRIPTS_DIR)}
        result = subprocess.run([PYTHON, str(path)], cwd=str(SCRIPTS_DIR), env=env)
        return NodeResult(
            name=node.name,
            duration_s=time.perf_counter() - t0,
            exit_code=result.returncode,
        )
    except Exception as e:
        print(f"❌ [{node.name.upper()}] subprocess error: {e}")
        return NodeResult(
            name=node.name,
            duration_s=time.perf_counter() - t0,
            exit_code=99,
        )


# ── Trade status footer ───────────────────────────────────────────────────────

def print_trade_status() -> None:
    if not TRADE_LOG.exists():
        print("\n📊 [TRADE STATUS] trade_log.json not found.")
        return
    try:
        trades = json.loads(TRADE_LOG.read_text())
    except json.JSONDecodeError as e:
        print(f"\n📊 [TRADE STATUS] trade_log.json malformed: {e}")
        return

    open_trades = [t for t in trades if not t.get("close_date") and t.get("taken")]
    if not open_trades:
        print("\n📊 [TRADE STATUS] No open positions.")
        return

    durations = []
    for t in open_trades:
        try:
            durations.append((datetime.now() - datetime.fromisoformat(t["date"])).days)
        except (KeyError, ValueError):
            pass

    tickers = ", ".join(t.get("ticker", "?") for t in open_trades)
    print(f"\n📊 [TRADE STATUS] {len(open_trades)} open: {tickers}")
    if durations:
        print(f"   Duration: {min(durations)}–{max(durations)} days  |  Log trades: tradelog status")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: list[NodeResult]) -> None:
    print("\n" + "─" * 60)
    print("  PIPELINE SUMMARY")
    print("─" * 60)
    total      = sum(r.duration_s for r in results)
    name_width = max((len(r.name) for r in results), default=10)
    for r in results:
        marker = {"OK": "✅", "FAIL": "❌", "SKIP": "⏭ "}[r.status]
        if r.skipped:
            print(f"  {marker} {r.name:<{name_width}}    skipped ({r.skip_reason})")
        else:
            print(f"  {marker} {r.name:<{name_width}}    {r.duration_s:>6.1f}s  exit={r.exit_code}")
    print("─" * 60)
    print(f"  Total: {total:.1f}s")
    print("─" * 60)


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-lore", action="store_true", help="Skip the lore/ignition optional group")
    ap.add_argument("--only", metavar="NAME", help="Run only the named node (e.g. 'Plays')")
    return ap.parse_args()


def main() -> int:
    args       = parse_args()
    sys.stdout = Tee(PIPE_LOG)

    nodes_to_run = NODES
    if args.only:
        nodes_to_run = [n for n in NODES if n.name.lower() == args.only.lower()]
        if not nodes_to_run:
            print(f"❌ Unknown node: {args.only}")
            print(f"   Available: {', '.join(n.name for n in NODES)}")
            return 1

    results: list[NodeResult] = []
    pipeline_failed = False

    for node in nodes_to_run:
        if args.skip_lore and node.optional_group == "lore":
            results.append(NodeResult(name=node.name, skipped=True, skip_reason="--skip-lore"))
            print(f"\n⏭  [{node.name.upper()}] skipped (--skip-lore)")
            continue

        r = run_node(node)
        results.append(r)

        if r.exit_code != 0:
            if node.critical:
                print(f"❌ [{node.name.upper()}] CRITICAL — pipeline halted.")
                pipeline_failed = True
                break
            else:
                print(f"⚠️  [{node.name.upper()}] failed (non-critical) — continuing.")

    print_trade_status()
    print_summary(results)

    if not pipeline_failed:
        print("\n🔄 [REINDEX] Updating RAG with new vault content...")
        try:
            run_index(rebuild=False)
            print("✅ [REINDEX] Complete.")
        except Exception as e:
            print(f"⚠️  [REINDEX] Failed: {e}")

    if pipeline_failed:
        print("\n❌ [PIPELINE] Halted on critical failure.\n")
        return 1

    print("\n✅ [PIPELINE] Sovereign cycle complete.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
