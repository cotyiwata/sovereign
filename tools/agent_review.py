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

INTEL_DIR     = VAULT_ROOT / "04-Intelligence"
AUDIT_DIR     = INTEL_DIR / "System-Audits"
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


def main():
    p = argparse.ArgumentParser(description="Sovereign Agent")
    p.add_argument("--mode", required=True, choices=["audit", "quality", "workorder"])
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
        print("[agent_review] quality mode — Session B build, not yet implemented")
        sys.exit(0)

    elif args.mode == "workorder":
        print("[agent_review] workorder mode — Session C build, not yet implemented")
        sys.exit(0)


if __name__ == "__main__":
    main()
