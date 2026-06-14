#!/usr/bin/env python3
"""
tools/macro_analyst.py — MacroAnalyst worker
Reads latest Brief_*.md + Plays_*.json, evaluates coherence against analyst rubric,
outputs MacroReview_YYYY-MM-DD.md + .html + .issues.json to 04-Intelligence/Macro-Reviews/

Usage:
    macro-review               # run on latest brief + plays
    python3 tools/macro_analyst.py

Import rules: tools/* → core/*, analysis/* only. No node imports.
"""

import sys
import os
import json
import glob
import logging
import re
from pathlib import Path
from datetime import datetime

# sys.path bootstrap — required for tools/ context
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import VAULT_ROOT
from core.llm import generate
from core.style import SOVEREIGN_CSS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BRIEFS_DIR = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
OUTPUT_DIR = VAULT_ROOT / "04-Intelligence" / "Macro-Reviews"
RUBRIC_PATH = VAULT_ROOT / "Data" / "analyst_rubric.md"

# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_latest_brief() -> tuple[str, str]:
    """Return (filename, content) of most recent Brief_*.md."""
    files = sorted(BRIEFS_DIR.glob("Brief_*.md"))
    if not files:
        raise FileNotFoundError(f"No Brief_*.md files found in {BRIEFS_DIR}")
    path = files[-1]
    log.info(f"Brief: {path.name}")
    return path.name, path.read_text(encoding="utf-8")


def load_latest_plays() -> tuple[str, dict]:
    """Return (filename, data) of most recent Plays_*.json sidecar."""
    files = sorted(BRIEFS_DIR.glob("Plays_*.json"))
    if not files:
        log.warning("No Plays_*.json found — proceeding without plays data")
        return "", {}
    path = files[-1]
    log.info(f"Plays: {path.name}")
    try:
        return path.name, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning(f"Plays JSON parse error: {e}")
        return path.name, {}


def load_rubric() -> str:
    """Load analyst_rubric.md or fall back to inline defaults."""
    if RUBRIC_PATH.exists():
        return RUBRIC_PATH.read_text(encoding="utf-8")
    log.warning("analyst_rubric.md not found — using inline defaults")
    return ""  # build_prompt handles the fallback inline


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def format_plays_summary(plays_data: dict) -> str:
    actives = plays_data.get("actives", [])
    if not actives:
        return "(no plays data available)"
    lines = []
    for p in actives:
        ticker = p.get("ticker", "?")
        direction = p.get("direction", "?")
        conviction = p.get("conviction", "?")
        section = p.get("section", "?")
        rr = p.get("rr", "?")
        score = p.get("setup_score", p.get("score", "?"))
        entry = p.get("entry", "?")
        stop = p.get("stop", "?")
        target = p.get("target", "?")
        confluence = p.get("confluence", p.get("rationale", ""))
        invalidation = p.get("invalidation", "")
        lines.append(
            f"  {ticker} [{section}] {direction} {conviction} | R/R {rr} | Score {score}/10 | "
            f"Entry {entry} / Stop {stop} / Target {target}\n"
            f"    Confluence: {confluence}\n"
            f"    Invalidation: {invalidation}"
        )
    return "\n".join(lines)


def build_prompt(
    brief_name: str,
    brief_text: str,
    plays_name: str,
    plays_data: dict,
    rubric: str,
    date_str: str,
) -> str:
    plays_summary = format_plays_summary(plays_data)

    # Trim brief to ~5000 chars to stay within context — keep frontmatter + body
    brief_trimmed = brief_text[:5500]
    if len(brief_text) > 5500:
        brief_trimmed += "\n... [truncated]"

    rubric_block = rubric if rubric else """
BRIEF COHERENCE CHECKS:
- Does the macro read (CPI, DXY, TLT, Fed posture) logically connect to crypto/equity plays?
- Is the dominant narrative supported by PULSE and REGIME sections, or drifting from them?
- Is Fear & Greed used meaningfully or just cited as a number?
- Are crypto plays consistent with BTC dominance direction?
- Is FORWARD 72H internally consistent — does the BULL case follow from current conditions?
- Is the SYNTHESIS posture word consistent with everything above it?

PLAYS COHERENCE CHECKS:
- Does each HIGH conviction play have explicit confluence (not just RSI/MACD listed)?
- Does each play have a clear invalidation condition?
- Does plays posture match brief macro posture?
- Are any plays contradicting the stated macro regime?
- Is R/R logic internally consistent with entry/stop/target stated?
- Is conviction level consistent with setup score?
"""

    return f"""You are a senior market analyst evaluating the coherence and quality of a daily intelligence brief and trade plays package.

EVALUATION RUBRIC:
{rubric_block}

---
BRIEF FILE: {brief_name}
{brief_trimmed}

---
PLAYS FILE: {plays_name or "none"}
{plays_summary}

---
INSTRUCTIONS:
Produce a structured macro review using EXACTLY this format. Be specific — cite actual content from the brief and plays when making observations. Do not be vague.

# Macro Review — {date_str}

## OVERALL VERDICT
[1-2 sentences: is the brief/plays package coherent and useful today?]

## BRIEF QUALITY
[Section-by-section critique. What is strong, what is weak, what is missing. Cite actual text.]

## PLAYS QUALITY
[Per-play critique where issues exist. Flag contradictions, weak confluence, missing invalidation. If plays are clean, say so briefly.]

## MACRO COHERENCE
[Does the macro narrative connect end-to-end: CPI → Fed posture → TLT → equity multiples → crypto? Where does the chain break down?]

## ISSUES FLAGGED
[Numbered list. Each item should be a specific, actionable observation that could be addressed by changing a prompt or adding logic. If none, write "No significant issues identified."]

## WHAT TO WATCH TOMORROW
[1-3 forward-looking observations based on today's brief. Concrete, not generic.]

---
After the review, output a JSON block using EXACTLY this delimiter format (no markdown code fences, raw JSON only):

===ISSUES_JSON_START===
[
  {{
    "issue_id": "MA-001",
    "source": "macro_analyst",
    "date": "{date_str}",
    "issue_type": "brief_coherence",
    "severity": "HIGH",
    "description": "Plain language description of the issue",
    "affected_file": "nodes/n04_strategist.py",
    "suggested_change": "Specific prompt or logic change to address this"
  }}
]
===ISSUES_JSON_END===

Rules:
- Only include real issues you identified above
- If no issues: output empty array []
- issue_type must be one of: brief_coherence, plays_coherence, macro_drift, missing_field
- severity must be one of: HIGH, MEDIUM, LOW
- affected_file: the most likely script responsible (nodes/n04_strategist.py, nodes/n08_plays.py, etc.)
- Maximum 8 issues — prioritize by impact on decision quality
"""


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def parse_issues_json(raw: str) -> list:
    """Extract and parse the JSON issues block from LLM output."""
    match = re.search(
        r"===ISSUES_JSON_START===\s*(.*?)\s*===ISSUES_JSON_END===",
        raw,
        re.DOTALL,
    )
    if not match:
        log.warning("No ISSUES_JSON block found in LLM output — returning empty list")
        return []
    raw_json = match.group(1).strip()
    # Strip accidental markdown fences if model adds them anyway
    raw_json = re.sub(r"^```[a-z]*\n?", "", raw_json)
    raw_json = re.sub(r"\n?```$", "", raw_json)
    try:
        result = json.loads(raw_json)
        if not isinstance(result, list):
            log.warning("Issues JSON is not a list — returning empty list")
            return []
        return result
    except json.JSONDecodeError as e:
        log.warning(f"Issues JSON parse error: {e}")
        log.debug(f"Raw JSON attempted: {raw_json[:500]}")
        return []


def parse_review_text(raw: str) -> str:
    """Strip the JSON block from LLM output, return clean markdown."""
    cleaned = re.sub(
        r"\n*===ISSUES_JSON_START===.*?===ISSUES_JSON_END===\n*",
        "",
        raw,
        flags=re.DOTALL,
    )
    return cleaned.strip()


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def md_to_html_body(md: str) -> str:
    """Minimal markdown → HTML conversion. No external deps."""
    lines = md.split("\n")
    html_parts = []
    in_list = False

    for line in lines:
        stripped = line.rstrip()

        if stripped.startswith("# "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f'<h1 class="review-title">{stripped[2:]}</h1>')

        elif stripped.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f'<h2 class="review-section">{stripped[3:]}</h2>')

        elif stripped.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f'<h3>{stripped[4:]}</h3>')

        elif stripped.startswith("- ") or (stripped and stripped[0].isdigit() and ". " in stripped[:4]):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            content = stripped[2:] if stripped.startswith("- ") else stripped.split(". ", 1)[-1]
            html_parts.append(f"  <li>{content}</li>")

        elif stripped == "" or stripped == "---":
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if stripped == "---":
                html_parts.append("<hr>")
            else:
                html_parts.append("")

        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{stripped}</p>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def render_html(review_md: str, date_str: str) -> str:
    body = md_to_html_body(review_md)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Macro Review — {date_str}</title>
  <style>
    {SOVEREIGN_CSS}

    .review-wrap {{
      max-width: 860px;
      margin: 0 auto;
      padding: 2.5rem 2rem;
    }}
    .review-title {{
      color: var(--accent);
      font-size: 1.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.5rem;
      margin-bottom: 1.5rem;
    }}
    .review-section {{
      color: var(--text-primary);
      font-size: 1.05rem;
      margin-top: 2rem;
      margin-bottom: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    p {{
      color: var(--text-secondary);
      line-height: 1.7;
      margin: 0.5rem 0;
    }}
    ul {{
      padding-left: 1.5rem;
      margin: 0.5rem 0;
    }}
    li {{
      color: var(--text-secondary);
      line-height: 1.6;
      margin: 0.3rem 0;
    }}
    hr {{
      border: none;
      border-top: 1px solid var(--border);
      margin: 1.5rem 0;
    }}
    h3 {{
      color: var(--text-muted, var(--text-secondary));
      font-size: 0.95rem;
      margin-top: 1rem;
    }}
  </style>
</head>
<body>
  <div class="review-wrap">
    {body}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    log.info("=== MacroAnalyst starting ===")

    brief_name, brief_text = load_latest_brief()
    plays_name, plays_data = load_latest_plays()
    rubric = load_rubric()

    prompt = build_prompt(brief_name, brief_text, plays_name, plays_data, rubric, date_str)

    log.info("Running gemma3:12b evaluation (this takes ~60-90s)...")

    # NOTE: Verify generate() signature in core/llm.py before deploying.
    # Expected: generate(prompt: str, model: str = "gemma3:12b") -> str
    # If it takes system + user separately, split prompt at the first "---\nBRIEF FILE" line.
    system_prompt = (
        "You are a senior market analyst evaluating daily intelligence briefs and trade plays packages. "
        "You are methodical, specific, and cite actual content when making observations. "
        "You never give vague feedback — every critique names a specific section, number, or field."
    )
    raw_output = generate(prompt, system_prompt, model="gemma3:12b", max_tokens=3200)

    if not raw_output or not raw_output.strip():
        log.error("LLM returned empty output — aborting")
        sys.exit(1)

    review_md = parse_review_text(raw_output)
    issues = parse_issues_json(raw_output)

    # Write outputs
    md_path = OUTPUT_DIR / f"MacroReview_{date_str}.md"
    html_path = OUTPUT_DIR / f"MacroReview_{date_str}.html"
    issues_path = OUTPUT_DIR / f"MacroReview_{date_str}.issues.json"

    md_path.write_text(review_md, encoding="utf-8")
    log.info(f"✓ Review MD:    {md_path}")

    html_path.write_text(render_html(review_md, date_str), encoding="utf-8")
    log.info(f"✓ Review HTML:  {html_path}")

    issues_path.write_text(json.dumps(issues, indent=2), encoding="utf-8")
    log.info(f"✓ Issues JSON:  {issues_path} ({len(issues)} items)")

    if issues:
        high = sum(1 for i in issues if i.get("severity") == "HIGH")
        med = sum(1 for i in issues if i.get("severity") == "MEDIUM")
        log.info(f"Issue summary: {len(issues)} total — {high} HIGH / {med} MEDIUM")
    else:
        log.info("No issues flagged")

    log.info("=== MacroAnalyst complete ===")


if __name__ == "__main__":
    run()
