#!/usr/bin/env python3
"""
tools/feedback.py — Sovereign Intelligence System | Node 0.6
Log feedback notes and index them into the RAG vault.

Usage:
    feedback "NVDA entry was early — need confirmation candle"
    feedback --list
    feedback --list --category play
    feedback --weekly-block
    alias: feedback
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── import layer: tools/* → core/*, analysis/* only ──────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import DATA_DIR
from core.llm import embed
from core.rag.client import get_collection

FEEDBACK_LOG = DATA_DIR / "feedback_log.json"

# ── Auto-categorization keywords ─────────────────────────────────────────────
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "play": [
        "ticker", "trade", "entry", "stop", "target", "r/r", "conviction",
        "long", "short", "position", "play", "nvda", "btc", "eth", "sol",
        "spy", "qqq", "setup", "breakout", "support", "resistance", "level",
        "leverage", "size", "close", "open", "profit", "loss", "win", "exit",
    ],
    "brief": [
        "brief", "posture", "narrative", "pulse", "regime", "scan",
        "synthesis", "hold", "watch", "opportunity", "fear", "greed",
        "dxy", "cpi", "inflation", "macro", "fed", "rates", "tlt", "spy",
        "market", "signal", "context",
    ],
    "ignition": [
        "ignition", "lore", "scene", "character", "world", "creative",
        "cael", "dax", "voss", "lost net", "bloodfire", "story", "beat",
        "arc", "universe", "age of aether", "wild card", "bite",
    ],
    "system": [
        "node", "script", "pipeline", "alias", "bug", "error", "rag",
        "chroma", "ollama", "whisper", "analyst", "daily", "cron",
        "index", "embed", "vault", "run_all", "fetch", "render", "output",
    ],
}


def auto_categorize(text: str) -> str:
    t = text.lower()
    scores = {cat: sum(1 for kw in kws if kw in t) for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


# ── Storage helpers ───────────────────────────────────────────────────────────

def _load_log() -> list:
    return json.loads(FEEDBACK_LOG.read_text()) if FEEDBACK_LOG.exists() else []


def _save_log(entries: list) -> None:
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_LOG.write_text(json.dumps(entries, indent=2))


def _rag_index(entry: dict) -> None:
    try:
        collection = get_collection(create_if_missing=True)
        embedding  = embed(entry["text"])
        if not embedding:
            print("  ⚠  RAG: empty embedding — skipping")
            return
        collection.upsert(
            ids=[f"feedback_{entry['id']}"],
            embeddings=[embedding],
            documents=[entry["text"]],
            metadatas=[{
                "doc_type": "feedback",
                "type":     "feedback",
                "category": entry["category"],
                "date":     entry["date"],
                "source":   "feedback_learner",
            }],
        )
        print(f"  ✓ RAG indexed — {collection.count()} total chunks")
    except Exception as e:
        print(f"  ⚠  RAG index failed: {e}")


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_log(text: str, category_override: str | None) -> None:
    category = category_override or auto_categorize(text)
    now = datetime.now(timezone.utc)
    entry = {
        "id":        now.strftime("%Y%m%d_%H%M%S"),
        "date":      now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        "category":  category,
        "text":      text,
    }
    entries = _load_log()
    entries.append(entry)
    _save_log(entries)

    print(f"\n  ✓ Feedback logged")
    print(f"  category : {category}")
    print(f"  date     : {entry['date']}")
    print(f"  text     : {text[:100]}{'...' if len(text) > 100 else ''}")
    print(f"\n  Indexing to RAG...")
    _rag_index(entry)
    print()


def cmd_list(n: int, category: str | None) -> None:
    entries = _load_log()
    if not entries:
        print("\n  No feedback logged yet.\n")
        return

    filtered = [e for e in entries if not category or e["category"] == category]
    recent   = filtered[-n:]

    counts: dict[str, int] = {}
    for e in entries:
        counts[e["category"]] = counts.get(e["category"], 0) + 1

    cat_label = f" [{category.upper()}]" if category else ""
    print(f"\n{'─'*64}")
    print(f"  FEEDBACK LOG{cat_label} — {len(entries)} total | showing {len(recent)}")
    print(f"{'─'*64}")
    print("  " + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print(f"{'─'*64}")

    for e in reversed(recent):
        badge = f"[{e['category'].upper():8}]"
        print(f"  {badge}  {e['date']}  {e['text'][:70]}{'...' if len(e['text']) > 70 else ''}")
    print()


def get_weekly_block() -> str:
    """
    Return formatted string of last 7 days of feedback.
    Called by weekly_review.py — import and use directly.
    """
    cutoff  = (date.today() - timedelta(days=7)).isoformat()
    entries = _load_log()
    recent  = [e for e in entries if e["date"] >= cutoff]
    if not recent:
        return ""
    lines = ["FEEDBACK LOG (last 7 days):"]
    lines.extend(f"- [{e['category'].upper()}] {e['date']}: {e['text']}" for e in recent)
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Node 0.6 — Feedback Learner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  feedback "NVDA play thesis was too early — need confirmation candle first"
  feedback --list
  feedback --list --category play
  feedback --weekly-block
        """,
    )
    parser.add_argument("text",          nargs="?", help="Feedback note to log")
    parser.add_argument("--list",        action="store_true", help="Show recent feedback")
    parser.add_argument("--n",           type=int, default=20, help="Entries to show (default 20)")
    parser.add_argument("--category",   choices=["play", "brief", "ignition", "system", "general"],
                        help="Override auto-category or filter --list")
    parser.add_argument("--weekly-block", action="store_true",
                        help="Print 7-day feedback block for weekly synthesis")
    args = parser.parse_args()

    if args.list:
        cmd_list(args.n, args.category)
    elif args.weekly_block:
        block = get_weekly_block()
        print(block if block else "No feedback in last 7 days.")
    elif args.text:
        cmd_log(args.text, args.category)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
