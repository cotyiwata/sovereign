#!/usr/bin/env python3
"""
tools/agent_review.py — Sovereign Agent

Multi-mode self-review. Session A delivers --mode audit only.

Modes:
    --mode audit      Rule-based health check, no LLM.
    --mode quality    LLM critique → proposals (Session B, stub)
    --mode workorder  Process workorder queue (Session C, stub)

Usage:
    python3 tools/agent_review.py --mode audit
"""
import argparse
import json
import shutil
import subprocess
import sys
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import VAULT_ROOT
from core.style import SOVEREIGN_CSS
from core.constants import MODEL
from core.llm import query_ollama

INTEL_DIR     = VAULT_ROOT / "04-Intelligence"
AUDIT_DIR     = INTEL_DIR / "System-Audits"
REVIEWS_DIR   = INTEL_DIR / "System-Reviews"
BRIEFS_DIR    = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
IGNITION_DIR  = VAULT_ROOT / "05-Ignition"
LOGS_DIR      = VAULT_ROOT / "logs"
DAILY_LOG     = LOGS_DIR / "sovereign_daily.log"
RAG_PATH      = VAULT_ROOT / "Data" / "rag" / "chroma_db"
AUDIT_HISTORY = VAULT_ROOT / "Data" / "audit_history.json"

KEY_DIRS = [
    "00-Inbox", "01-Trading", "02-Market-Intel", "03-Universes",
    "04-Intelligence", "05-Ignition", "Data", "Output", "Scripts", "logs",
]

OK   = "✅"
WARN = "⚠️"
FAIL = "🔴"


def check_pipeline_freshness() -> dict:
    briefs = sorted(BRIEFS_DIR.glob("Brief_*.md"), reverse=True)
    if not briefs:
        return {"status": FAIL, "label": "PIPELINE FRESHNESS",
                "detail": "no briefs found in vault"}
    latest = briefs[0]
    age_h = (datetime.now().timestamp() - latest.stat().st_mtime) / 3600
    status = OK if age_h < 30 else (WARN if age_h < 50 else FAIL)
    return {"status": status, "label": "PIPELINE FRESHNESS",
            "detail": f"last brief: {latest.name} ({age_h:.1f}h ago)",
            "age_hours": round(age_h, 1)}


def check_output_completeness(days: int = 7) -> dict:
    today = datetime.now().date()
    expected = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    missing = []
    for d in expected:
        if not list(BRIEFS_DIR.glob(f"Brief_{d}_*.md")):
            missing.append(f"{d}: brief")
        if not list(BRIEFS_DIR.glob(f"Plays_{d}_*.json")):
            missing.append(f"{d}: plays")
        if not list(IGNITION_DIR.glob(f"Ignition_{d}_*.md")):
            missing.append(f"{d}: ignition")
    if not missing:
        return {"status": OK, "label": "OUTPUT COMPLETENESS",
                "detail": f"all expected outputs present ({days}d window)"}
    sev = WARN if len(missing) <= 3 else FAIL
    return {"status": sev, "label": "OUTPUT COMPLETENESS",
            "detail": f"{len(missing)} missing outputs",
            "missing": missing[:10]}


def check_log_errors() -> dict:
    if not DAILY_LOG.exists():
        return {"status": WARN, "label": "LOG ERRORS",
                "detail": "sovereign_daily.log not found"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent_lines = []
    for line in DAILY_LOG.read_text(errors="ignore").splitlines():
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    recent_lines.append(line)
            except ValueError:
                pass
        elif recent_lines:
            recent_lines.append(line)
    window = chr(10).join(recent_lines)
    err = window.count("Traceback") + window.lower().count("error:")
    if err == 0:
        return {"status": OK, "label": "LOG ERRORS", "detail": "no errors in last 7 days"}
    sev = WARN if err < 5 else FAIL
    return {"status": sev, "label": "LOG ERRORS",
            "detail": f"{err} error indicators (last 7 days)"}


def check_rag_growth() -> dict:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(RAG_PATH))
        current = client.get_or_create_collection("sovereign_vault").count()
    except Exception as e:
        return {"status": FAIL, "label": "RAG GROWTH",
                "detail": f"could not read ChromaDB: {e}"}

    history = []
    if AUDIT_HISTORY.exists():
        try:
            history = json.loads(AUDIT_HISTORY.read_text())
        except (json.JSONDecodeError, OSError):
            history = []
    last = history[-1].get("rag_count") if history else None

    if last is None:
        return {"status": OK, "label": "RAG GROWTH",
                "detail": f"baseline established: {current} chunks", "current": current}
    if current < last:
        return {"status": FAIL, "label": "RAG GROWTH",
                "detail": f"{last} -> {current} (REGRESSED, -{last - current})",
                "current": current}
    return {"status": OK, "label": "RAG GROWTH",
            "detail": f"{last} -> {current} (+{current - last})", "current": current}


def check_cron_validity() -> dict:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return {"status": WARN, "label": "CRON VALIDITY",
                    "detail": "crontab -l failed or empty"}
        lines = result.stdout.strip().split("\n")
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"status": WARN, "label": "CRON VALIDITY",
                "detail": "could not invoke crontab"}

    broken, valid = [], 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for part in line.split():
            if part.endswith(".py"):
                p = Path(part) if Path(part).is_absolute() else VAULT_ROOT / "Scripts" / part
                if p.exists():
                    valid += 1
                else:
                    broken.append(part)
    if not broken:
        return {"status": OK, "label": "CRON VALIDITY",
                "detail": f"{valid} targets, all exist"}
    return {"status": FAIL, "label": "CRON VALIDITY",
            "detail": f"{len(broken)} broken — script not found",
            "broken": broken}


def check_vault_structure() -> dict:
    missing = [d for d in KEY_DIRS if not (VAULT_ROOT / d).exists()]
    if not missing:
        return {"status": OK, "label": "VAULT STRUCTURE",
                "detail": "all key directories present"}
    return {"status": FAIL, "label": "VAULT STRUCTURE",
            "detail": f"missing: {', '.join(missing)}", "missing": missing}


def check_disk_space() -> dict:
    try:
        usage = shutil.disk_usage(str(Path.home()))
        free_gb = usage.free / (1024 ** 3)
    except OSError as e:
        return {"status": WARN, "label": "DISK SPACE", "detail": f"could not read: {e}"}
    status = OK if free_gb > 10 else (WARN if free_gb > 5 else FAIL)
    return {"status": status, "label": "DISK SPACE",
            "detail": f"{free_gb:.1f}GB free", "free_gb": round(free_gb, 1)}


def run_audit() -> dict:
    checks = [
        check_pipeline_freshness(),
        check_output_completeness(),
        check_log_errors(),
        check_rag_growth(),
        check_cron_validity(),
        check_vault_structure(),
        check_disk_space(),
    ]
    statuses = [c["status"] for c in checks]
    if FAIL in statuses:
        verdict = "NEEDS ATTENTION"
    elif WARN in statuses:
        verdict = "STABLE WITH WARNINGS"
    else:
        verdict = "GREEN"
    return {"checks": checks, "verdict": verdict}


def render_md(result: dict, date_str: str, time_str: str) -> str:
    lines = [f"# System Audit — {date_str} {time_str}", ""]
    for c in result["checks"]:
        lines.append(f"## {c['status']} {c['label']}")
        lines.append(c["detail"])
        for key in ("missing", "broken"):
            if key in c and isinstance(c[key], list):
                for item in c[key]:
                    lines.append(f"  - {item}")
        lines.append("")
    lines.append(f"\n**OVERALL: {result['verdict']}**")
    return "\n".join(lines)


def render_html(result: dict, date_str: str, time_str: str) -> str:
    color = {"GREEN": "#4caf50", "STABLE WITH WARNINGS": "#ff9800", "NEEDS ATTENTION": "#f44336"}
    vc = color.get(result["verdict"], "#aaa")
    rows = []
    for c in result["checks"]:
        extra = ""
        for key in ("missing", "broken"):
            if key in c and isinstance(c[key], list):
                extra = "<ul>" + "".join(f"<li>{i}</li>" for i in c[key]) + "</ul>"
        rows.append(
            f'<div class="check"><div class="hdr">{c["status"]} {c["label"]}</div>'
            f'<div class="dtl">{c["detail"]}{extra}</div></div>'
        )
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>System Audit {date_str}</title>
<style>
{SOVEREIGN_CSS}
body {{ max-width: 760px; margin: 0 auto; padding: 2rem 1.5rem; font-family: var(--font-mono, monospace); }}
h1 {{ font-size: 1.3rem; border-bottom: 1px solid var(--border, #333); padding-bottom: 0.5rem; }}
.check {{ margin: 1.2rem 0; padding: 0.8rem 1rem; background: rgba(255,255,255,0.02); border-radius: 6px; }}
.hdr {{ font-weight: bold; font-size: 1rem; margin-bottom: 0.3rem; }}
.dtl {{ font-size: 0.9rem; color: #aaa; }}
.verdict {{ margin-top: 2rem; font-size: 1.1rem; font-weight: bold; padding: 0.8rem; border-radius: 6px; text-align: center; background: {vc}; color: #000; }}
ul {{ margin: 0.4rem 0 0 0; padding-left: 1.2rem; font-size: 0.85rem; }}
</style></head><body>
<h1>⚙ System Audit — {date_str} {time_str}</h1>
{"".join(rows)}
<div class="verdict">OVERALL: {result['verdict']}</div>
</body></html>"""


def update_history(result: dict, date_str: str, time_str: str) -> None:
    history = []
    if AUDIT_HISTORY.exists():
        try:
            history = json.loads(AUDIT_HISTORY.read_text())
        except (json.JSONDecodeError, OSError):
            history = []
    rag = next((c for c in result["checks"] if c["label"] == "RAG GROWTH"), {})
    history.append({
        "date": date_str, "time": time_str,
        "verdict": result["verdict"], "rag_count": rag.get("current"),
    })
    history = history[-52:]
    AUDIT_HISTORY.write_text(json.dumps(history, indent=2))



# ─────────────────────────────────────────────────────────────────────────────
# QUALITY MODE
# ─────────────────────────────────────────────────────────────────────────────

QUALITY_SYSTEM_PROMPT = (
    "You are a senior trading systems analyst reviewing an AI-generated daily market brief. "
    "Your job is to score the brief on 5 dimensions of synthesis quality and identify specific weaknesses. "
    "Respond ONLY in valid JSON. No preamble. No markdown fences."
)

QUALITY_RUBRIC_DIMENSIONS = [
    ("cross_asset_coherence",
     "Do signals across BTC, SPY, Gold, DXY, and equities connect into a unified read, "
     "or do they sit side by side without synthesis?"),
    ("directional_conviction",
     "Does the brief commit to a directional lean with a specific reason, "
     "or hedge in every direction equally?"),
    ("specificity",
     "Are conditions named precisely (specific levels, catalysts, timeframes), "
     "or generic enough to apply on any random trading day?"),
    ("synthesis_depth",
     "Does the PULSE section tell you something headlines don't? "
     "Does it read like genuine analysis or a news summary?"),
    ("forward_72h_quality",
     "Are the FORWARD 72H levels tight and structurally grounded, "
     "or wide hedges with no actionable structure?"),
]


def load_recent_briefs(window: int = 1) -> list:
    """Return list of {date, filename, text} for the last N brief .md files."""
    briefs = sorted(BRIEFS_DIR.glob("Brief_*.md"), reverse=True)[:window]
    results = []
    for b in briefs:
        try:
            text = b.read_text(encoding="utf-8", errors="ignore")
            results.append({"date": b.stem, "filename": b.name, "text": text})
        except OSError as e:
            print(f"[quality] WARNING: could not read {b.name}: {e}")
    return results


def build_quality_prompt(brief: dict) -> str:
    dims = "\n".join(
        f"{i+1}. {d[0].upper().replace('_', ' ')}: {d[1]}"
        for i, d in enumerate(QUALITY_RUBRIC_DIMENSIONS)
    )
    schema = (
        '{\n'
        '  "scores": {\n'
        '    "cross_asset_coherence": <int 1-5>,\n'
        '    "directional_conviction": <int 1-5>,\n'
        '    "specificity": <int 1-5>,\n'
        '    "synthesis_depth": <int 1-5>,\n'
        '    "forward_72h_quality": <int 1-5>\n'
        '  },\n'
        '  "weaknesses": [\n'
        '    {\n'
        '      "dimension": "<dimension_name>",\n'
        '      "score": <int>,\n'
        '      "evidence": "<specific quote or observation, max 30 words>",\n'
        '      "suggestion": "<one concrete improvement, max 20 words>"\n'
        '    }\n'
        '  ],\n'
        '  "overall_grade": <float, mean of scores>,\n'
        '  "headline": "<one sentence summary of brief quality>"\n'
        '}'
    )
    return (
        f"You are reviewing this daily market brief:\n\n"
        f"--- BRIEF START ---\n{brief['text'][:6000]}\n--- BRIEF END ---\n\n"
        f"Score the brief on each of the 5 dimensions below. Use a 1-5 scale:\n"
        f"  5 = excellent, no issues\n"
        f"  4 = good, minor weakness\n"
        f"  3 = acceptable, notable gap\n"
        f"  2 = weak, significant problem\n"
        f"  1 = poor, fails this dimension\n\n"
        f"Dimensions:\n{dims}\n\n"
        f"For each dimension with a score below 4, include it in 'weaknesses' with evidence and suggestion.\n\n"
        f"Respond ONLY with this JSON structure (no markdown, no preamble):\n{schema}"
    )


def score_brief(brief: dict) -> dict:
    """Run LLM quality rubric on a single brief. Returns parsed result dict."""
    prompt = build_quality_prompt(brief)
    print(f"[quality] scoring {brief['filename']} via {MODEL}...")
    raw = query_ollama(
        prompt,
        model=MODEL,
        system=QUALITY_SYSTEM_PROMPT,
        max_tokens=1200,
        timeout=180,
    )
    # Strip markdown fences if model wraps response
    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    clean = re.sub(r"```\s*$", "", clean.strip(), flags=re.MULTILINE)
    try:
        result = json.loads(clean)
        # Compute overall_grade from scores if model omitted it
        if "overall_grade" not in result and "scores" in result:
            vals = [v for v in result["scores"].values() if isinstance(v, (int, float))]
            result["overall_grade"] = round(sum(vals) / len(vals), 2) if vals else 0.0
    except json.JSONDecodeError as e:
        print(f"[quality] WARNING: JSON parse failed ({e}) — storing raw")
        result = {"parse_error": str(e), "raw": raw[:500], "overall_grade": 0.0}
    result["date"] = brief["date"]
    result["filename"] = brief["filename"]
    return result


def render_quality_md(scores: list, date_str: str, time_str: str) -> str:
    lines = [f"# Quality Review — {date_str} {time_str}", ""]
    for s in scores:
        if "parse_error" in s:
            lines += [f"## ⚠️ {s['filename']}", f"Parse error: {s['parse_error']}", ""]
            continue
        grade = s.get("overall_grade", 0)
        flag = "🔴" if grade < 3.0 else ("⚠️" if grade < 4.0 else "✅")
        lines += [
            f"## {flag} {s.get('filename', s['date'])}",
            f"**Overall grade: {grade:.1f}/5.0**",
            f"_{s.get('headline', '')}_",
            "",
            "| Dimension | Score |",
            "|---|---|",
        ]
        for dim, _ in QUALITY_RUBRIC_DIMENSIONS:
            sc = s.get("scores", {}).get(dim, "—")
            lines.append(f"| {dim.replace('_', ' ').title()} | {sc}/5 |")
        lines.append("")
        for w in s.get("weaknesses", []):
            lines += [
                f"**{w.get('dimension','').replace('_',' ').title()} ({w.get('score','?')}/5)**",
                f"- Evidence: {w.get('evidence','')}",
                f"- Fix: {w.get('suggestion','')}",
                "",
            ]
    return "\n".join(lines)


def render_quality_html(scores: list, date_str: str, time_str: str) -> str:
    cards = []
    for s in scores:
        if "parse_error" in s:
            cards.append(
                f'<div class="card warn"><b>⚠️ {s["filename"]}</b>'
                f'<p>Parse error: {s["parse_error"]}</p></div>'
            )
            continue
        grade = s.get("overall_grade", 0)
        color = "#f44336" if grade < 3.0 else ("#ff9800" if grade < 4.0 else "#4caf50")
        rows = "".join(
            f'<tr><td>{d[0].replace("_"," ").title()}</td>'
            f'<td>{s.get("scores",{}).get(d[0],"—")}/5</td></tr>'
            for d in QUALITY_RUBRIC_DIMENSIONS
        )
        weak_html = ""
        for w in s.get("weaknesses", []):
            weak_html += (
                f'<div class="weakness">'
                f'<b>{w.get("dimension","").replace("_"," ").title()} ({w.get("score","?")})/5</b>'
                f'<div class="ev">Evidence: {w.get("evidence","")}</div>'
                f'<div class="fix">Fix: {w.get("suggestion","")}</div>'
                f'</div>'
            )
        cards.append(
            f'<div class="card">'
            f'<div class="card-hdr">{s.get("filename", s["date"])}</div>'
            f'<div class="grade" style="color:{color}">Grade: {grade:.1f}/5.0</div>'
            f'<div class="headline">{s.get("headline","")}</div>'
            f'<table class="scores">{rows}</table>'
            f'{weak_html}'
            f'</div>'
        )
    body = "\n".join(cards)
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Quality Review {date_str}</title>
<style>
{SOVEREIGN_CSS}
body {{ max-width: 800px; margin: 0 auto; padding: 2rem 1.5rem; font-family: var(--font-mono, monospace); }}
h1 {{ font-size: 1.3rem; border-bottom: 1px solid var(--border, #333); padding-bottom: 0.5rem; }}
.card {{ margin: 1.5rem 0; padding: 1rem 1.2rem; background: rgba(255,255,255,0.03); border-radius: 8px; border: 1px solid rgba(255,255,255,0.07); }}
.card-hdr {{ font-weight: bold; font-size: 1rem; margin-bottom: 0.4rem; }}
.grade {{ font-size: 1.3rem; font-weight: bold; margin-bottom: 0.3rem; }}
.headline {{ font-size: 0.9rem; color: #aaa; margin-bottom: 0.8rem; font-style: italic; }}
.scores {{ width: 100%; border-collapse: collapse; margin-bottom: 0.8rem; font-size: 0.88rem; }}
.scores td {{ padding: 0.25rem 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.05); }}
.weakness {{ margin: 0.6rem 0; padding: 0.6rem 0.8rem; background: rgba(244,67,54,0.08); border-radius: 4px; font-size: 0.87rem; }}
.ev {{ color: #aaa; margin-top: 0.2rem; }}
.fix {{ color: #80cbc4; margin-top: 0.2rem; }}
</style></head><body>
<h1>⚙ Quality Review — {date_str} {time_str}</h1>
{body}
</body></html>"""


def run_quality(window: int = 1) -> list:
    """Score last N briefs. Returns list of score dicts."""
    briefs = load_recent_briefs(window)
    if not briefs:
        print("[quality] no briefs found — nothing to score")
        return []
    return [score_brief(b) for b in briefs]


def update_history_quality(scores: list, date_str: str, time_str: str) -> None:
    """Append quality entries to audit_history.json."""
    history = []
    if AUDIT_HISTORY.exists():
        try:
            history = json.loads(AUDIT_HISTORY.read_text())
        except (json.JSONDecodeError, OSError):
            history = []
    for s in scores:
        history.append({
            "date": date_str,
            "time": time_str,
            "mode": "quality",
            "brief": s.get("filename", ""),
            "overall_grade": s.get("overall_grade"),
            "scores": s.get("scores", {}),
        })
    history = history[-52:]
    AUDIT_HISTORY.write_text(json.dumps(history, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# WORKORDER MODE
# ─────────────────────────────────────────────────────────────────────────────

# Map quality rubric dimensions to the nodes responsible for them
DIMENSION_TO_NODE = {
    "cross_asset_coherence": "n04_strategist.py",
    "directional_conviction": "n04_strategist.py",
    "specificity":            "n04_strategist.py",
    "synthesis_depth":        "n04_strategist.py",
    "forward_72h_quality":    "n04_strategist.py",
}

DIMENSION_TO_SECTION = {
    "cross_asset_coherence": "PULSE / ASYMMETRIC SETUP",
    "directional_conviction": "ASYMMETRIC SETUP / SYNTHESIS",
    "specificity":            "LEVELS / FORWARD 72H",
    "synthesis_depth":        "PULSE",
    "forward_72h_quality":    "FORWARD 72H",
}

SEVERITY_MAP = {5: "low", 4: "low", 3: "medium", 2: "high", 1: "critical"}


def load_latest_quality_review() -> dict | None:
    """Return the most recent quality scores dict from audit_history.json."""
    if not AUDIT_HISTORY.exists():
        print("[workorder] no audit_history.json found")
        return None
    try:
        history = json.loads(AUDIT_HISTORY.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[workorder] could not read audit_history: {e}")
        return None
    # Find most recent quality entry
    quality_entries = [h for h in reversed(history) if h.get("mode") == "quality"]
    if not quality_entries:
        print("[workorder] no quality runs in audit_history — run --mode quality first")
        return None
    return quality_entries[0]


def load_latest_review_file() -> dict | None:
    """Load the most recent SystemReview .md to get full weakness detail."""
    reviews = sorted(REVIEWS_DIR.glob("SystemReview_*.md"), reverse=True)
    if not reviews:
        return None
    # Parse the md for weakness blocks — but we stored scores in audit_history
    # so we just need the latest scores dict from history + the filename
    return None  # handled via audit_history


def quality_entry_to_issues(entry: dict) -> list:
    """Convert a quality audit_history entry into .issues.json issue dicts."""
    issues = []
    scores = entry.get("scores", {})
    brief = entry.get("brief", "unknown")

    # We need weakness detail — re-score is expensive, so read from SystemReview md
    weaknesses = extract_weaknesses_from_md(entry)

    for w in weaknesses:
        dim = w.get("dimension", "")
        score = w.get("score", 3)
        severity = SEVERITY_MAP.get(score, "medium")
        issues.append({
            "issue":            w.get("evidence", f"{dim} quality degraded"),
            "category":         "prompt_quality",
            "severity":         severity,
            "frequency":        1,
            "node":             DIMENSION_TO_NODE.get(dim, "n04_strategist.py"),
            "section":          DIMENSION_TO_SECTION.get(dim, dim),
            "evidence":         f"Quality score {score}/5 on {brief}: {w.get('evidence','')}",
            "issue_type":       "operator_decision",
            "suggested_change": w.get("suggestion", ""),
        })

    # Also flag any dimension at 3 or below that didn't appear in weaknesses
    weakness_dims = {w.get("dimension") for w in weaknesses}
    for dim, score in scores.items():
        if isinstance(score, (int, float)) and score <= 3 and dim not in weakness_dims:
            severity = SEVERITY_MAP.get(int(score), "medium")
            issues.append({
                "issue":            f"{dim.replace('_',' ').title()} scored {score}/5",
                "category":         "prompt_quality",
                "severity":         severity,
                "frequency":        1,
                "node":             DIMENSION_TO_NODE.get(dim, "n04_strategist.py"),
                "section":          DIMENSION_TO_SECTION.get(dim, dim),
                "evidence":         f"Quality run on {brief}: score {score}/5",
                "issue_type":       "operator_decision",
                "suggested_change": f"Improve {dim.replace('_',' ')} in prompt",
            })

    return issues


def extract_weaknesses_from_md(entry: dict) -> list:
    """
    Pull weakness dicts from the most recent SystemReview .md file.
    Falls back to empty list if file unreadable — issues will still be
    generated from scores alone via the fallback branch in quality_entry_to_issues.
    """
    reviews = sorted(REVIEWS_DIR.glob("SystemReview_*.md"), reverse=True)
    if not reviews:
        return []
    try:
        text = reviews[0].read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    weaknesses = []
    # Parse markdown weakness blocks:
    # **Dimension Name (N/5)**
    # - Evidence: ...
    # - Fix: ...
    current = {}
    for line in text.splitlines():
        line = line.strip()
        # Match weakness header: **Cross Asset Coherence (3/5)**
        m = re.match(r"\*\*(.+?)\s+\((\d)/5\)\*\*", line)
        if m:
            if current.get("dimension"):
                weaknesses.append(current)
            dim_name = m.group(1).lower().replace(" ", "_")
            current = {"dimension": dim_name, "score": int(m.group(2))}
        elif line.startswith("- Evidence:"):
            current["evidence"] = line[len("- Evidence:"):].strip()
        elif line.startswith("- Fix:"):
            current["suggestion"] = line[len("- Fix:"):].strip()
    if current.get("dimension"):
        weaknesses.append(current)
    return weaknesses


def run_workorder() -> dict:
    """Build .issues.json from latest quality run."""
    entry = load_latest_quality_review()
    if not entry:
        return {}

    issues = quality_entry_to_issues(entry)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")

    payload = {
        "date":           date_str,
        "time":           time_str,
        "mode":           "workorder",
        "source":         "quality_review",
        "source_brief":   entry.get("brief", ""),
        "overall_grade":  entry.get("overall_grade"),
        "issues":         issues,
    }
    return payload

def main():
    p = argparse.ArgumentParser(description="Sovereign Agent")
    p.add_argument("--mode", required=True, choices=["audit", "quality", "workorder"])
    p.add_argument("--window", type=int, default=1, help="quality mode: number of recent briefs to score (default 1, max 3)")
    args = p.parse_args()

    if args.mode == "audit":
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")

        print(f"[agent_review] mode=audit | running 7 checks...")
        result = run_audit()

        for c in result["checks"]:
            print(f"  {c['status']} {c['label']}: {c['detail']}")
        print(f"\n  OVERALL: {result['verdict']}")

        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"SystemAudit_{date_str}_{time_str}"
        md_path   = AUDIT_DIR / f"{stem}.md"
        html_path = AUDIT_DIR / f"{stem}.html"

        md_path.write_text(render_md(result, date_str, time_str), encoding="utf-8")
        html_path.write_text(render_html(result, date_str, time_str), encoding="utf-8")
        update_history(result, date_str, time_str)

        print(f"\n[agent_review] ✓ {md_path}")
        print(f"[agent_review] ✓ {html_path}")

    elif args.mode == "quality":
        window = getattr(args, "window", 1)
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")

        scores = run_quality(window=window)
        if not scores:
            sys.exit(1)

        valid = [s for s in scores if "parse_error" not in s]
        avg_grade = sum(s.get("overall_grade", 0) for s in valid) / len(valid) if valid else 0
        print(f"\n[quality] average grade: {avg_grade:.1f}/5.0 across {len(valid)} brief(s)")

        REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"SystemReview_{date_str}_{time_str}"
        md_path   = REVIEWS_DIR / f"{stem}.md"
        html_path = REVIEWS_DIR / f"{stem}.html"

        md_path.write_text(render_quality_md(scores, date_str, time_str), encoding="utf-8")
        html_path.write_text(render_quality_html(scores, date_str, time_str), encoding="utf-8")
        update_history_quality(scores, date_str, time_str)

        print(f"[quality] ✓ {md_path}")
        print(f"[quality] ✓ {html_path}")

    elif args.mode == "workorder":
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")

        print("[workorder] reading latest quality review...")
        payload = run_workorder()
        if not payload:
            sys.exit(1)

        issues = payload.get("issues", [])
        print(f"[workorder] {len(issues)} issue(s) from quality run on {payload.get('source_brief','')} "
              f"(grade: {payload.get('overall_grade','?')}/5.0)")

        REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"SystemReview_{date_str}_{time_str}"
        issues_path = REVIEWS_DIR / f"{stem}.issues.json"
        issues_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        for iss in issues:
            sev = iss.get("severity","?").upper()
            print(f"  [{sev}] {iss['node']} / {iss['section']}: {iss['issue'][:80]}")

        print(f"\n[workorder] ✓ {issues_path}")
        print(f"[workorder] run: patchwrite to generate patch scripts from these issues")


if __name__ == "__main__":
    main()