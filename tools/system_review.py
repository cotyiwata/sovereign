#!/usr/bin/env python3
"""
tools/system_review.py — Sovereign System Self-Review

Sliding window (default 14d) analysis of brief quality, plays accuracy,
feedback themes, and anomalies. Outputs structured .md + .html report.

Usage:
    python3 tools/system_review.py
    python3 tools/system_review.py --days 7
    python3 tools/system_review.py --auto        # flag as cron-scheduled
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── import layer: tools/* → core/*, analysis/* only ──────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import VAULT_ROOT
from core.llm import generate
from core.style import SOVEREIGN_CSS

# ── paths ─────────────────────────────────────────────────────────────────────
BRIEFS_DIR   = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
INTEL_DIR    = VAULT_ROOT / "04-Intelligence"
REVIEW_DIR   = INTEL_DIR / "System-Reviews"
TRADE_LOG    = VAULT_ROOT / "01-Trading" / "trade_log.json"
FEEDBACK_LOG = VAULT_ROOT / "Data" / "feedback_log.json"

# ── limits — keep prompt within gemma3:12b context window ────────────────────
MAX_BRIEF_CHARS   = 1200   # per brief (truncated — headline + synthesis)
MAX_PLAYS_PER_DAY = 6
MAX_FEEDBACK      = 20
MAX_WINDOW        = 14     # hard cap regardless of --days value


# ── data loaders ──────────────────────────────────────────────────────────────

def load_recent_briefs(days: int) -> list[dict]:
    """Brief_YYYY-MM-DD_HHMM.md — return last N within window."""
    cutoff = datetime.now() - timedelta(days=days)
    found = []
    for f in sorted(BRIEFS_DIR.glob("Brief_*.md")):
        try:
            date_str = f.stem.split("_")[1]           # Brief_2026-05-04_0700
            if datetime.strptime(date_str, "%Y-%m-%d") >= cutoff:
                found.append({
                    "date": date_str,
                    "text": f.read_text(encoding="utf-8")[:MAX_BRIEF_CHARS],
                })
        except (IndexError, ValueError, OSError):
            continue
    return found[-MAX_WINDOW:]


def load_recent_plays(days: int) -> list[dict]:
    """Plays_YYYY-MM-DD_HHMM.json — return actives summary within window."""
    cutoff = datetime.now() - timedelta(days=days)
    found = []
    for f in sorted(BRIEFS_DIR.glob("Plays_*.json")):
        try:
            date_str = f.stem.split("_")[1]
            if datetime.strptime(date_str, "%Y-%m-%d") >= cutoff:
                raw = json.loads(f.read_text(encoding="utf-8"))
                # v2.8 sidecar: flat actives list
                actives = raw.get("actives", raw) if isinstance(raw, dict) else raw
                found.append({"date": date_str, "actives": actives})
        except (IndexError, ValueError, json.JSONDecodeError, OSError):
            continue
    return found[-MAX_WINDOW:]


def load_trade_outcomes(days: int) -> dict:
    """trade_log.json — closed trades within window, open count."""
    if not TRADE_LOG.exists():
        return {"closed": [], "open_count": 0, "note": "trade_log.json not found"}
    try:
        raw = json.loads(TRADE_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"closed": [], "open_count": 0, "note": "trade_log.json unreadable"}

    entries = raw if isinstance(raw, list) else raw.get("entries", [])
    cutoff  = datetime.now() - timedelta(days=days)
    closed, open_count = [], 0

    for e in entries:
        try:
            entry_date = datetime.strptime(e.get("date", ""), "%Y-%m-%d")
        except ValueError:
            continue
        if entry_date < cutoff:
            continue
        if e.get("outcome_pct") is not None:
            closed.append({
                "ticker":      e.get("ticker", "?"),
                "section":     e.get("section", "?"),
                "conviction":  e.get("conviction", "?"),
                "outcome_pct": e.get("outcome_pct"),
            })
        elif e.get("taken"):
            open_count += 1

    return {"closed": closed, "open_count": open_count}


def load_recent_feedback(days: int) -> list[dict]:
    """feedback_log.json — {date, text} dicts within window."""
    if not FEEDBACK_LOG.exists():
        return []
    try:
        raw = json.loads(FEEDBACK_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    entries = raw if isinstance(raw, list) else raw.get("entries", [])
    cutoff  = datetime.now() - timedelta(days=days)
    result  = []

    for e in entries:
        try:
            ts = e.get("timestamp", e.get("date", ""))[:10]
            if datetime.strptime(ts, "%Y-%m-%d") >= cutoff:
                text = e.get("text", e.get("content", ""))
                if text:
                    result.append({"date": ts, "text": text})
        except (ValueError, AttributeError):
            continue

    return result[-MAX_FEEDBACK:]


# ── prompt builder ─────────────────────────────────────────────────────────────

def build_prompt(
    briefs:     list[dict],
    plays_data: list[dict],
    trades:     dict,
    feedback:   list[str],
    days:       int,
    mode:       str,
) -> str:

    # --- briefs block ---
    brief_block = ""
    for b in briefs:
        brief_block += f"\n--- {b['date']} ---\n{b['text']}\n"
    if not brief_block:
        brief_block = "None found in window."

    # --- plays block ---
    plays_block = ""
    for p in plays_data:
        plays_block += f"\n--- {p['date']} ---\n"
        actives = p.get("actives", [])
        if isinstance(actives, list):
            for play in actives[:MAX_PLAYS_PER_DAY]:
                flags = play.get("flags", [])
                plays_block += (
                    f"  {play.get('ticker','?'):6s} | "
                    f"{play.get('direction','?'):5s} | "
                    f"{play.get('conviction','?'):4s} | "
                    f"section: {play.get('section','?'):14s} | "
                    f"flags: {flags if flags else 'none'}\n"
                )
    if not plays_block:
        plays_block = "None found in window."

    # --- trades block ---
    closed = trades.get("closed", [])
    trade_block = f"Open/pending positions: {trades.get('open_count', 0)}\n"
    if trades.get("note"):
        trade_block += f"Note: {trades['note']}\n"
    if closed:
        wins   = [t for t in closed if (t.get("outcome_pct") or 0) > 0]
        losses = [t for t in closed if (t.get("outcome_pct") or 0) <= 0]
        trade_block += (
            f"Closed this window: {len(closed)} trades — "
            f"{len(wins)} wins / {len(losses)} losses\n"
        )
        for t in closed:
            result_str = f"{t['outcome_pct']:+.1f}%" if t.get("outcome_pct") is not None else "?"
            trade_block += (
                f"  {t['ticker']:6s} | {t['section']:16s} | "
                f"{t['conviction']:4s} | outcome: {result_str}\n"
            )
    else:
        trade_block += "No closed trades in window (paper trading or no exits yet).\n"

    # --- feedback block ---
    feedback_block = (
        "\n".join(f"- {f['date']}: {f['text']}" for f in feedback)
        if feedback else "No feedback entries in window."
    )

    data_section = f"""WINDOW: last {days} days | MODE: {mode}
BRIEFS FOUND: {len(briefs)} | PLAYS DAYS: {len(plays_data)} | FEEDBACK ENTRIES: {len(feedback)}

=== RECENT DAILY BRIEFS (truncated) ===
{brief_block}

=== RECENT PLAYS ===
{plays_block}

=== TRADE OUTCOMES ===
{trade_block}
=== FEEDBACK LOG ===
{feedback_block}"""

    return f"""You are performing a structured self-review of the Sovereign Intelligence System — a personal AI-powered trading and intelligence pipeline built by one operator.

Analyze the data below and produce a direct, specific critique. Reference actual observations — do not generalize or fabricate patterns. If data is thin (few briefs, no trades yet), acknowledge it and assess what IS available.

{data_section}

Produce your review in EXACTLY this format. Use the section headers as shown.

## BRIEF QUALITY
Assess narrative consistency, dominant theme coherence, and forward call quality where verifiable. Flag repetition, vagueness, or model drift patterns.

## PLAYS ACCURACY
Assess conviction vs outcome alignment. Note directional bias, section over-representation, leverage appropriateness. If no closed trades exist, assess plays quality on construction merit instead.

## FEEDBACK THEMES
Group feedback by theme. What is the operator most consistently flagging? Any contradiction between feedback and system behavior?

## ANOMALIES
Structural observations: date gaps in output, unusual flag frequency, missing sections, any sign the pipeline skipped a node or produced empty output.

## SUGGESTED CHANGES
Numbered list. Each item must cite a specific observation from above. No generic advice. Maximum 5 items — prioritize by impact.

## STRUCTURED ISSUES
After the prose review, output a machine-readable issue list. Each issue is a block separated by a line containing only "---". Use the EXACT field names below.

GROUNDING RULES — violations make the issue useless:
- EVIDENCE must cite ONLY specific dates, tickers, or counts that appear in the data above. Do not invent dates or events.
- NODE must be selected from the REAL FILE LIST below. Do not guess names. If unsure, use "unknown".
- Only output an issue if you can point to concrete evidence in the data section.
- ISSUE_TYPE must be exactly one of: code_patch (bug or logic error in a function), prompt_patch (LLM instruction needs rewording or stronger constraint), config_change (threshold, parameter, or constant value), operator_decision (trading judgment call — not a code fix).
- SUGGESTED_CHANGE must name the specific function name or prompt section to modify and describe the change in one sentence. No generic advice.

REAL FILE LIST (pick NODE from this list only):
nodes/n00_inbox.py | nodes/n01_scout.py | nodes/n02_levels.py | nodes/n03_chronicle.py | nodes/n04_strategist.py | nodes/n05_brief.py | nodes/n06_lore_updater.py | nodes/n07_lore_renderer.py | nodes/n08_plays.py | nodes/n09_trade_log.py | nodes/n10_dashboard.py | nodes/n11_ignition.py | tools/feedback.py | tools/research_digest.py | tools/ingest_audio.py | tools/ingest_pdf.py | analysis/regime.py | analysis/setup.py | core/badges.py | unknown

ISSUE FORMAT:

ISSUE: <one-line title>
CATEGORY: brief_quality | plays_discipline | rag_retrieval | validator_logic | data_freshness | feedback_pattern | other
SEVERITY: single_instance | recurring_3 | recurring_5_plus | systemic
FREQUENCY: <integer count of observations in window>
NODE: <pick ONE filename from REAL FILE LIST above>
SECTION: <prompt section name, function name, or "unknown">
EVIDENCE: <one sentence citing specific dates or counts from the data>
ISSUE_TYPE: code_patch | prompt_patch | config_change | operator_decision
SUGGESTED_CHANGE: <one sentence: specific function or prompt section to modify and what to change>
---

Output 0-8 issues. If nothing material is wrong, output the literal line: NO_ISSUES

Final line (standalone, no header):
SYSTEM HEALTH: [STRONG / STABLE / NEEDS ATTENTION / DEGRADED]"""


# ── html renderer ─────────────────────────────────────────────────────────────

def _md_to_html_body(text: str) -> str:
    """Minimal markdown → HTML. Handles ## headers, - bullets, blank lines."""
    lines = text.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f'<h2>{stripped[3:]}</h2>')
        elif stripped.startswith("- ") or (stripped and stripped[0].isdigit() and ". " in stripped[:4]):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{stripped.lstrip('-0123456789. ')}</li>")
        elif "SYSTEM HEALTH:" in stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            health_val = stripped.split("SYSTEM HEALTH:")[-1].strip()
            color_map = {
                "STRONG":          "#4caf50",
                "STABLE":          "#8bc34a",
                "NEEDS ATTENTION": "#ff9800",
                "DEGRADED":        "#f44336",
            }
            color = color_map.get(health_val, "#aaaaaa")
            html_lines.append(
                f'<p class="health-line">SYSTEM HEALTH: '
                f'<span style="color:{color};font-weight:bold">{health_val}</span></p>'
            )
        elif stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{stripped}</p>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False

    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def render_html(md_content: str, date_str: str, time_str: str, mode: str) -> str:
    mode_badge_color = "#3a3a5c" if mode == "auto" else "#2a3a2a"
    mode_label = "AUTO" if mode == "auto" else "MANUAL"
    body = _md_to_html_body(md_content)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>System Review {date_str}</title>
<style>
{SOVEREIGN_CSS}
body {{
    max-width: 860px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
    font-family: var(--font-mono, monospace);
}}
h1 {{
    font-size: 1.4rem;
    margin-bottom: 0.25rem;
    border-bottom: 1px solid var(--border, #333);
    padding-bottom: 0.5rem;
}}
h2 {{
    color: var(--accent, #7eb8f7);
    font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid var(--border, #333);
    padding-bottom: 3px;
    margin-top: 2rem;
}}
p, li {{ font-size: 0.92rem; line-height: 1.6; }}
ul {{ padding-left: 1.2rem; }}
li {{ margin-bottom: 0.3rem; }}
.mode-badge {{
    display: inline-block;
    background: {mode_badge_color};
    color: #ccc;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    margin-left: 10px;
    vertical-align: middle;
}}
.health-line {{
    margin-top: 2rem;
    font-size: 1rem;
    font-weight: bold;
    border-top: 1px solid var(--border, #333);
    padding-top: 0.75rem;
}}
</style>
</head>
<body>
<h1>⚙ System Review — {date_str} {time_str}<span class="mode-badge">{mode_label}</span></h1>
{body}
</body>
</html>"""



# -- structured issues parser ----------------------------------------------

def parse_structured_issues(review_text: str) -> list[dict]:
    """Extract structured issue blocks. Tolerant of missing header — finds ISSUE: blocks
    anywhere after SUGGESTED CHANGES, terminating at SYSTEM HEALTH or end-of-text."""
    text = review_text
    # Prefer explicit header if present, otherwise start after SUGGESTED CHANGES
    if "## STRUCTURED ISSUES" in text:
        text = text.split("## STRUCTURED ISSUES", 1)[1]
    elif "## SUGGESTED CHANGES" in text:
        text = text.split("## SUGGESTED CHANGES", 1)[1]
    # Strip trailing health line and any subsequent sections
    for terminator in ["SYSTEM HEALTH:", "\n## "]:
        if terminator in text:
            text = text.split(terminator, 1)[0]
    if "NO_ISSUES" in text.upper():
        return []
    valid_keys = {"issue", "category", "severity", "frequency", "node", "section", "evidence", "issue_type", "suggested_change"}
    issues = []
    for block in [b.strip() for b in text.split("---") if b.strip()]:
        # Skip blocks that don\'t look like an issue (prose paragraphs)
        if not block.lower().lstrip().startswith("issue:"):
            continue
        issue = {}
        for line in block.split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key in valid_keys:
                issue[key] = val.strip()
        if issue.get("issue") and issue.get("category"):
            try:
                issue["frequency"] = int(issue.get("frequency", 0))
            except (ValueError, TypeError):
                issue["frequency"] = 0
            issues.append(issue)
    return issues


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sovereign System Self-Review")
    parser.add_argument(
        "--days", type=int, default=14,
        help="Sliding window in days (default: 14, max: 14)"
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Flag output as auto-scheduled run (vs manual)"
    )
    args   = parser.parse_args()
    days   = min(args.days, MAX_WINDOW)
    mode   = "auto" if args.auto else "manual"
    now    = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")

    print(f"[system_review] window={days}d | mode={mode}")
    print(f"[system_review] loading data...")

    briefs     = load_recent_briefs(days)
    plays_data = load_recent_plays(days)
    trades     = load_trade_outcomes(days)
    feedback   = load_recent_feedback(days)

    print(f"  briefs={len(briefs)} | plays_days={len(plays_data)} | "
          f"closed_trades={len(trades.get('closed',[]))} | feedback={len(feedback)}")

    if len(briefs) == 0 and len(plays_data) == 0:
        print("[system_review] ⚠ no pipeline output found in window — run `daily` first")
        sys.exit(0)

    print("[system_review] generating review (gemma3:12b)...")
    prompt      = build_prompt(briefs, plays_data, trades, feedback, days, mode)
    review_text = generate(
        prompt,
        system="You are a senior systems analyst reviewing an AI-powered trading pipeline. Be direct, specific, and critical. Reference only what you can observe in the data provided.",
        model="gemma3:12b",
    )

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    stem      = f"SystemReview_{date_str}_{time_str}"
    md_path   = REVIEW_DIR / f"{stem}.md"
    html_path = REVIEW_DIR / f"{stem}.html"

    frontmatter = (
        f"---\n"
        f"date: {date_str}\n"
        f"time: {time_str}\n"
        f"type: system_review\n"
        f"mode: {mode}\n"
        f"window_days: {days}\n"
        f"briefs_analyzed: {len(briefs)}\n"
        f"plays_days: {len(plays_data)}\n"
        f"closed_trades: {len(trades.get('closed', []))}\n"
        f"---\n\n"
    )

    md_path.write_text(frontmatter + review_text, encoding="utf-8")
    html_path.write_text(
        render_html(review_text, date_str, time_str, mode),
        encoding="utf-8"
    )

    issues = parse_structured_issues(review_text)
    issues_path = REVIEW_DIR / f"{stem}.issues.json"
    issues_payload = {
        "date": date_str, "time": time_str, "mode": mode, "window_days": days,
        "briefs_analyzed": len(briefs), "plays_days": len(plays_data),
        "closed_trades": len(trades.get("closed", [])), "issues": issues,
    }
    issues_path.write_text(json.dumps(issues_payload, indent=2), encoding="utf-8")

    print(f"[system_review] ✓ {md_path}")
    print(f"[system_review] ✓ {html_path}")
    print(f"[system_review] ✓ {issues_path} ({len(issues)} issues)")

    for line in review_text.split("\n"):
        if "SYSTEM HEALTH:" in line:
            print(f"\n  {line.strip()}\n")
            break


if __name__ == "__main__":
    main()
