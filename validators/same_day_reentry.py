"""
SAME_DAY_REENTRY gate — v1.0
Flags play cards where the ticker was already closed today.
Non-veto. Amber badge. Coty decides.
"""

from datetime import date
import json
from pathlib import Path

TRADE_LOG = Path.home() / "sovereign/01-Trading/trade_log.json"


def check_same_day_reentry(plays: list) -> list:
    today = date.today().isoformat()

    try:
        entries = json.loads(TRADE_LOG.read_text())
    except Exception as e:
        print(f"[same_day_reentry] Could not load trade log: {e}")
        return plays

    # Handle both flat list (v2.7.1+) and legacy dict schema
    if isinstance(entries, dict):
        flat = []
        for v in entries.values():
            flat.extend(v if isinstance(v, list) else [v])
        entries = flat

    closed_today = {
        e["ticker"].upper()
        for e in entries
        if isinstance(e, dict) and e.get("close_date") == today
    }

    if closed_today:
        print(f"[same_day_reentry] Closed today: {closed_today}")

    for play in plays:
        if "flags" not in play:
            play["flags"] = []
        if play.get("ticker", "").upper() in closed_today:
            play["flags"].append("SAME_DAY_REENTRY")
            print(f"[same_day_reentry] ⚠ Flagged: {play.get('ticker')}")

    return plays
