#!/usr/bin/env python3
"""
queue_monitor.py — Sovereign Intelligence System
Checks audio_queue.txt and lore_queue.txt for starvation.
Cron: 0 8 * * 1  (Monday 8am)
"""

import os
import sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.expanduser("~"), "sovereign", "Data")

def check_queues():
    market_queue = os.path.join(DATA_DIR, "audio_queue.txt")
    lore_queue   = os.path.join(DATA_DIR, "lore_queue.txt")

    def is_empty(path):
        if not os.path.exists(path):
            return True
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip()]
        return len(lines) == 0

    market_empty = is_empty(market_queue)
    lore_empty   = is_empty(lore_queue)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if market_empty and lore_empty:
        print(f"[{ts}] 🚨 BOTH QUEUES EMPTY — RAG not being fed")
        sys.exit(1)
    elif market_empty:
        print(f"[{ts}] ⚠️  Market queue empty — add analyst URLs to audio_queue.txt")
    elif lore_empty:
        print(f"[{ts}] ⚠️  Lore queue empty — add lore URLs to lore_queue.txt")
    else:
        print(f"[{ts}] ✓ Both queues active")

if __name__ == "__main__":
    check_queues()
