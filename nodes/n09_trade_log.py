#!/usr/bin/env python3
"""
trade_log.py — Node 8 | Sovereign Intelligence System
Auto-ingests HIGH/MED plays from today's Plays_*.json into trade_log.json
CLI: tradelog open | tradelog close TICKER
"""

import json
import os
import sys
import glob
from datetime import datetime, date

# ── yfinance optional import ──────────────────────────────────────────────────
try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

# ── PATHS ─────────────────────────────────────────────────────────────────────
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT
BRIEFS_DIR = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
TRADING_DIR = VAULT_ROOT / "01-Trading"
LOG_JSON = TRADING_DIR / "trade_log.json"
LOG_MD = TRADING_DIR / "trade_log.md"

# ── SECTION LABEL MAP ─────────────────────────────────────────────────────────
SECTION_LABELS = {
    "crypto":     "CRYPTO",
    "semis":      "AI & SEMIS",
    "ai_semis":   "AI & SEMIS",
    "energy":     "AI ENERGY NEXUS",
    "ai_energy":  "AI ENERGY NEXUS",
    "macro":      "MACRO",
}

# ── CRYPTO TICKERS — append -USD for yfinance ─────────────────────────────────
CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "AVAX", "MATIC", "LINK", "DOT", "ADA", "XRP"}


def yf_ticker(ticker: str) -> str:
    """Return yfinance-compatible ticker string."""
    if ticker.upper() in CRYPTO_TICKERS:
        return f"{ticker.upper()}-USD"
    return ticker.upper()


def fetch_price(ticker: str, fallback: float) -> float:
    """Fetch live price via yfinance. Falls back to provided price on failure."""
    if not YF_AVAILABLE:
        print(f"  [warn] yfinance not installed — using plays price for {ticker}")
        return fallback
    try:
        t = yf.Ticker(yf_ticker(ticker))
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            # Try fast_info as secondary
            price = t.fast_info.get("lastPrice") or t.fast_info.get("last_price")
            if price:
                return round(float(price), 4)
            print(f"  [warn] No price data for {ticker} — using plays price")
            return fallback
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as e:
        print(f"  [warn] yfinance error for {ticker}: {e} — using plays price")
        return fallback


def load_log() -> list:
    """Load existing trade log or return empty list."""
    os.makedirs(TRADING_DIR, exist_ok=True)
    if os.path.exists(LOG_JSON):
        with open(LOG_JSON, "r") as f:
            return json.load(f)
    return []


def save_log(entries: list):
    """Save trade log to JSON."""
    with open(LOG_JSON, "w") as f:
        json.dump(entries, f, indent=2)


def find_todays_plays() -> str | None:
    """Find today's Plays_*.json sidecar."""
    today = date.today().strftime("%Y-%m-%d")
    pattern = os.path.join(BRIEFS_DIR, f"Plays_{today}*.json")
    matches = glob.glob(pattern)
    if matches:
        return sorted(matches)[-1]  # most recent if multiple
    # Fallback — any Plays json from today
    pattern2 = os.path.join(BRIEFS_DIR, "Plays_*.json")
    all_plays = sorted(glob.glob(pattern2))
    for p in reversed(all_plays):
        if today in p:
            return p
    return None


def ingest_plays():
    """Node 8 main — ingest today's plays into trade_log.json."""
    print("── Node 8 | Trade Log Ingest ─────────────────────────────")

    plays_file = find_todays_plays()
    if not plays_file:
        print("  [skip] No Plays_*.json found for today. Run plays_html_renderer.py first.")
        return

    print(f"  [source] {os.path.basename(plays_file)}")

    with open(plays_file, "r") as f:
        plays_data = json.load(f)

    today_str = date.today().strftime("%Y-%m-%d")
    existing = load_log()
    existing_ids = {e["id"] for e in existing}

    new_entries = []
    raw_actives = plays_data.get("actives", plays_data.get("sections", {}))

    # v2.7.1+: actives is a flat list with section field per play
    # v2.7 and earlier: actives is a dict keyed by section
    if isinstance(raw_actives, list):
        all_plays = [(p.get("section", "UNKNOWN"), p) for p in raw_actives]
    else:
        all_plays = [
            (SECTION_LABELS.get(k.lower(), k.upper()), p)
            for k, plays_list in raw_actives.items()
            if isinstance(plays_list, list)
            for p in plays_list
        ]

    for section_label, play in all_plays:
            ticker = play.get("ticker", "").upper()
            conviction = play.get("conviction", "").upper()

            # Only HIGH and MED
            if conviction not in ("HIGH", "MED"):
                continue

            trade_id = f"{today_str}-{ticker}"

            # Skip if any open or pending entry exists for this ticker (any date)
            already_open = any(
                e.get("ticker", "").upper() == ticker
                and e.get("outcome_pct") is None
                for e in existing
            )
            if already_open:
                print(f"  [skip]  {ticker} already has an open/pending position")
                continue

            if trade_id in existing_ids:
                print(f"  [skip]  {trade_id} already exists today")
                continue

            fallback_price = play.get("current", 0.0)
            print(f"  [fetch] {ticker} price...", end=" ", flush=True)
            entry_price = fetch_price(ticker, fallback_price)
            print(f"${entry_price}")

            entry = {
                "id": trade_id,
                "date": today_str,
                "ticker": ticker,
                "section": section_label,
                "conviction": conviction,
                "entry_price": entry_price,
                "support": play.get("support"),
                "resistance": play.get("resistance"),
                "stop": play.get("stop"),
                "target": play.get("target"),
                "rr": play.get("rr"),
                "timeframe": play.get("timeframe"),
                "why_now": play.get("why_now", ""),
                "setup": play.get("setup", ""),
                "taken": None,
                "taken_date": None,
                "close_date": None,
                "outcome_pct": None,
                "notes": ""
            }

            existing.append(entry)
            existing_ids.add(trade_id)
            new_entries.append(trade_id)

    if new_entries:
        save_log(existing)
        render_markdown(existing)
        print(f"\n  [done]  {len(new_entries)} new entries staged → {LOG_JSON}")
    else:
        print("  [done]  No new entries to add.")

    print("─────────────────────────────────────────────────────────\n")


# ── CLI COMMANDS ──────────────────────────────────────────────────────────────

def cmd_open():
    """Print all pending (taken: null) entries and prompt y/n."""
    entries = load_log()
    pending = [e for e in entries if e.get("taken") is None]

    if not pending:
        print("No pending trades.")
        return

    print(f"\n── Pending Trades ({len(pending)}) ──────────────────────────────\n")
    for e in pending:
        print(f"  {e['id']}")
        print(f"    Section:    {e['section']} | Conviction: {e['conviction']}")
        print(f"    Entry:      ${e['entry_price']} | Stop: ${e['stop']} | Target: ${e['target']}")
        print(f"    R/R:        {e['rr']} | Timeframe: {e['timeframe']}")
        print(f"    Setup:      {e['setup'][:120]}")
        print()

        answer = input(f"  Did you take {e['ticker']}? (y/n/skip): ").strip().lower()
        if answer == "skip":
            continue
        elif answer == "y":
            e["taken"] = True
            e["taken_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            print(f"  ✓ {e['ticker']} marked as TAKEN\n")
        elif answer == "n":
            e["taken"] = False
            e["taken_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            print(f"  ✗ {e['ticker']} marked as PASSED\n")

    save_log(entries)
    render_markdown(entries)
    print("Trade log updated.")


def cmd_close(ticker: str):
    """Close an open trade — record outcome % and notes."""
    entries = load_log()
    ticker = ticker.upper()

    # Find taken=True, outcome_pct=None entries for this ticker
    candidates = [
        e for e in entries
        if e.get("ticker") == ticker
        and e.get("taken") is True
        and e.get("outcome_pct") is None
    ]

    if not candidates:
        print(f"No open taken trades found for {ticker}.")
        # Show if exists at all
        all_ticker = [e for e in entries if e.get("ticker") == ticker]
        if all_ticker:
            print(f"Existing entries for {ticker}:")
            for e in all_ticker:
                print(f"  {e['id']} | taken={e['taken']} | outcome={e['outcome_pct']}")
        return

    # Use most recent if multiple
    entry = sorted(candidates, key=lambda x: x["date"])[-1]

    print(f"\n── Close Trade: {entry['id']} ──────────────────────────────")
    print(f"  Entry price: ${entry['entry_price']} | Target: ${entry['target']} | Stop: ${entry['stop']}")
    print()

    outcome_input = input("  Outcome % (e.g. +4.2 or -2.1): ").strip()
    try:
        outcome_pct = round(float(outcome_input.replace("%", "").replace("+", "")), 2)
    except ValueError:
        print("  Invalid input. Use format: +4.2 or -2.1")
        return

    notes_input = input("  Notes (optional, press Enter to skip): ").strip()

    entry["outcome_pct"] = outcome_pct
    entry["close_date"] = date.today().strftime("%Y-%m-%d")
    if notes_input:
        entry["notes"] = notes_input

    save_log(entries)
    render_markdown(entries)

    result = "WIN ✓" if outcome_pct > 0 else "LOSS ✗"
    print(f"\n  [{result}] {ticker} closed at {outcome_pct:+.2f}%")
    print(f"  Trade log updated → {LOG_JSON}\n")


# ── MARKDOWN RENDERER ─────────────────────────────────────────────────────────

def render_markdown(entries: list):
    """Render trade_log.md — open trades first, closed below, date desc."""
    open_trades = [e for e in entries if e.get("taken") is True and e.get("outcome_pct") is None]
    closed_trades = [e for e in entries if e.get("outcome_pct") is not None]
    passed_trades = [e for e in entries if e.get("taken") is False]

    open_trades.sort(key=lambda x: x["date"], reverse=True)
    closed_trades.sort(key=lambda x: x.get("close_date") or x["date"], reverse=True)

    lines = []
    lines.append("# Trade Log\n")
    lines.append(f"_Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n")

    # ── Summary stats ──
    if closed_trades:
        wins = [e for e in closed_trades if e["outcome_pct"] > 0]
        losses = [e for e in closed_trades if e["outcome_pct"] <= 0]
        hit_rate = len(wins) / len(closed_trades) * 100
        avg_win = sum(e["outcome_pct"] for e in wins) / len(wins) if wins else 0
        avg_loss = sum(e["outcome_pct"] for e in losses) / len(losses) if losses else 0
        expectancy = (hit_rate / 100 * avg_win) + ((1 - hit_rate / 100) * avg_loss)
        lines.append("## Summary\n")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Closed Trades | {len(closed_trades)} |")
        lines.append(f"| Hit Rate | {hit_rate:.1f}% |")
        lines.append(f"| Avg Win | {avg_win:+.2f}% |")
        lines.append(f"| Avg Loss | {avg_loss:+.2f}% |")
        lines.append(f"| Expectancy | {expectancy:+.2f}% |")
        lines.append("")

    # ── Open trades ──
    if open_trades:
        lines.append("## Open Trades\n")
        lines.append("| Date | Ticker | Section | Conviction | Entry | Stop | Target | R/R | Timeframe |")
        lines.append("|------|--------|---------|------------|-------|------|--------|-----|-----------|")
        for e in open_trades:
            lines.append(
                f"| {e['date']} | {e['ticker']} | {e['section']} | {e['conviction']} "
                f"| ${e['entry_price']} | ${e['stop']} | ${e['target']} | {e['rr']} | {e['timeframe']} |"
            )
        lines.append("")

    # ── Closed trades ──
    if closed_trades:
        lines.append("## Closed Trades\n")
        lines.append("| Date | Ticker | Section | Conviction | Entry | Close Date | Outcome % | Notes |")
        lines.append("|------|--------|---------|------------|-------|------------|-----------|-------|")
        for e in closed_trades:
            outcome_str = f"{e['outcome_pct']:+.2f}%" if e['outcome_pct'] is not None else "—"
            lines.append(
                f"| {e['date']} | {e['ticker']} | {e['section']} | {e['conviction']} "
                f"| ${e['entry_price']} | {e.get('close_date', '—')} | {outcome_str} | {e.get('notes', '')} |"
            )
        lines.append("")

    # ── Passed trades ──
    if passed_trades:
        lines.append("## Passed\n")
        lines.append("| Date | Ticker | Section | Conviction | Entry |")
        lines.append("|------|--------|---------|------------|-------|")
        for e in sorted(passed_trades, key=lambda x: x["date"], reverse=True):
            lines.append(
                f"| {e['date']} | {e['ticker']} | {e['section']} | {e['conviction']} | ${e['entry_price']} |"
            )
        lines.append("")

    with open(LOG_MD, "w") as f:
        f.write("\n".join(lines))


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        # Called as Node 8 in pipeline — ingest today's plays
        ingest_plays()
        return

    cmd = args[0].lower()

    if cmd == "open":
        cmd_open()
    elif cmd == "close":
        if len(args) < 2:
            print("Usage: tradelog close TICKER")
            sys.exit(1)
        cmd_close(args[1])
    elif cmd == "status":
        entries = load_log()
        open_t = [e for e in entries if e.get("taken") is True and e.get("outcome_pct") is None]
        closed_t = [e for e in entries if e.get("outcome_pct") is not None]
        pending_t = [e for e in entries if e.get("taken") is None]
        print(f"\nTrade Log Status")
        print(f"  Pending review : {len(pending_t)}")
        print(f"  Open (taken)   : {len(open_t)}")
        print(f"  Closed         : {len(closed_t)}")
        print(f"  Total entries  : {len(entries)}\n")
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: tradelog [open | close TICKER | status]")
        sys.exit(1)


if __name__ == "__main__":
    main()
