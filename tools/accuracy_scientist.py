#!/usr/bin/env python3
"""
tools/accuracy_scientist.py — AccuracyScientist worker
Reads .issues.json from System-Reviews/, System-Audits/, Macro-Reviews/
Deduplicates, triages by impact, writes consolidated file for patch_writer.

CONTRACT: Proposes only — never applies patches.
After running, invoke `patchwrite` manually to generate patch scripts.

Usage:
    research                   # via research_agent.py orchestrator
    python3 tools/accuracy_scientist.py   # standalone

Import rules: tools/* → core/*, analysis/* only. No node imports.
"""

import sys
import os
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime

# sys.path bootstrap
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import VAULT_ROOT

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Directories that contain .issues.json files
ISSUES_SOURCES = [
    VAULT_ROOT / "04-Intelligence" / "System-Reviews",
    VAULT_ROOT / "04-Intelligence" / "System-Audits",
    VAULT_ROOT / "04-Intelligence" / "Macro-Reviews",
]

# Output: consolidated issues for patch_writer
OUTPUT_DIR = VAULT_ROOT / "Data"
OUTPUT_FILENAME_PREFIX = "consolidated"

# Severity priority for triage (lower index = higher priority)
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# Source priority (lower index = higher priority)
SOURCE_ORDER = {
    "agent_review": 0,    # rule-based audit — most reliable
    "system_review": 1,   # LLM system critique
    "macro_analyst": 2,   # macro coherence critique
}

# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_all_issues() -> list[dict]:
    """
    Glob latest .issues.json from each source directory.
    Returns flat list of all issue dicts.
    """
    all_issues = []

    for source_dir in ISSUES_SOURCES:
        if not source_dir.exists():
            log.debug(f"Directory not found, skipping: {source_dir}")
            continue

        issue_files = sorted(source_dir.glob("*.issues.json"))
        if not issue_files:
            log.debug(f"No .issues.json files in {source_dir.name}")
            continue

        # Load only the most recent file from each directory
        latest = issue_files[-1]
        log.info(f"Loading: {latest.relative_to(VAULT_ROOT)}")

        try:
            raw = json.loads(latest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to load {latest.name}: {e}")
            continue

        if not isinstance(raw, list):
            if isinstance(raw, dict) and "issues" in raw:
                raw = raw["issues"]
                log.debug(f"Unwrapped dict wrapper in {latest.name}")
            else:
                log.warning(f"Expected list in {latest.name}, got {type(raw).__name__} — skipping")
                continue

        # Normalize: ensure required fields are present
        for item in raw:
            if not isinstance(item, dict):
                continue
            # SystemReview uses different field names — normalize to shared schema
            if "issue" in item and "description" not in item:
                item["description"] = item["issue"]
            if "node" in item and "affected_file" not in item:
                item["affected_file"] = item["node"]
            if "category" in item and "issue_type" not in item:
                item["issue_type"] = item["category"]
            raw_sev = item.get("severity", "LOW")
            if raw_sev not in ("HIGH", "MEDIUM", "LOW"):
                sev_map = {"systemic": "HIGH", "recurring_3": "HIGH", "recurring_2": "MEDIUM", "single_instance": "LOW"}
                item["severity"] = sev_map.get(raw_sev, "MEDIUM")
            # Fill missing fields with defaults so downstream logic is safe
            item.setdefault("source", source_dir.name.lower().replace("-", "_"))
            item.setdefault("severity", "LOW")
            item.setdefault("issue_type", "unknown")
            item.setdefault("description", "")
            item.setdefault("affected_file", "")
            item.setdefault("suggested_change", "")
            item.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
            item.setdefault("issue_id", "")
            all_issues.append(item)

    log.info(f"Total issues loaded: {len(all_issues)}")
    return all_issues


# ---------------------------------------------------------------------------
# Deduplicate
# ---------------------------------------------------------------------------

def _issue_fingerprint(issue: dict) -> str:
    """
    Fingerprint by (affected_file, description).
    Strips whitespace and lowercases for fuzzy dedup.
    """
    key = f"{issue.get('affected_file', '').strip().lower()}::{issue.get('description', '').strip().lower()[:120]}"
    return hashlib.md5(key.encode()).hexdigest()


def deduplicate(issues: list[dict]) -> list[dict]:
    """
    Deduplicate by (affected_file, description) fingerprint.
    When duplicates exist, keep the highest-severity version.
    """
    seen: dict[str, dict] = {}

    for issue in issues:
        fp = _issue_fingerprint(issue)
        if fp not in seen:
            seen[fp] = issue
        else:
            # Keep higher severity
            existing_sev = SEVERITY_ORDER.get(seen[fp].get("severity", "LOW"), 99)
            new_sev = SEVERITY_ORDER.get(issue.get("severity", "LOW"), 99)
            if new_sev < existing_sev:
                seen[fp] = issue

    deduped = list(seen.values())
    removed = len(issues) - len(deduped)
    if removed:
        log.info(f"Deduplication: removed {removed} duplicate(s), {len(deduped)} remain")
    return deduped


# ---------------------------------------------------------------------------
# Triage + sort
# ---------------------------------------------------------------------------

def triage(issues: list[dict]) -> list[dict]:
    """
    Sort by: severity (HIGH first) → source reliability → affected_file alphabetically.
    Brief/plays issues are higher priority than social/lore.
    """
    def sort_key(issue: dict):
        sev = SEVERITY_ORDER.get(issue.get("severity", "LOW"), 99)
        src = SOURCE_ORDER.get(issue.get("source", ""), 99)
        file_priority = _file_priority(issue.get("affected_file", ""))
        return (sev, src, file_priority, issue.get("affected_file", ""))

    return sorted(issues, key=sort_key)


def _file_priority(filepath: str) -> int:
    """Lower = higher priority. Brief and plays nodes first."""
    if "n04_strategist" in filepath or "n03_chronicle" in filepath:
        return 0
    if "n08_plays" in filepath:
        return 1
    if "n05_brief" in filepath:
        return 2
    if filepath.startswith("nodes/"):
        return 3
    if filepath.startswith("tools/"):
        return 4
    if filepath.startswith("core/"):
        return 5
    return 9


# ---------------------------------------------------------------------------
# Re-ID and write
# ---------------------------------------------------------------------------

def reassign_ids(issues: list[dict]) -> list[dict]:
    """Assign clean sequential IDs: AS-001, AS-002, etc."""
    for i, issue in enumerate(issues, start=1):
        issue["issue_id"] = f"AS-{i:03d}"
    return issues


def write_consolidated(issues: list[dict]) -> Path:
    """Write consolidated .issues.json to Data/ with datestamp."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    output_path = OUTPUT_DIR / f"{OUTPUT_FILENAME_PREFIX}_{date_str}.issues.json"
    output_path.write_text(json.dumps(issues, indent=2), encoding="utf-8")
    log.info(f"✓ Consolidated issues: {output_path} ({len(issues)} items)")
    return output_path


def write_summary(issues: list[dict]):
    """Print a readable triage summary to stdout."""
    if not issues:
        print("\n  No issues to report.\n")
        return

    print("\n" + "=" * 60)
    print(f"  ACCURACY SCIENTIST — TRIAGE SUMMARY")
    print(f"  {len(issues)} issues | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    high = [i for i in issues if i.get("severity") == "HIGH"]
    med = [i for i in issues if i.get("severity") == "MEDIUM"]
    low = [i for i in issues if i.get("severity") == "LOW"]

    print(f"\n  HIGH:   {len(high)}")
    print(f"  MEDIUM: {len(med)}")
    print(f"  LOW:    {len(low)}")

    if high:
        print("\n  — HIGH PRIORITY —")
        for issue in high:
            print(f"\n  [{issue['issue_id']}] {issue.get('affected_file', '?')}")
            print(f"  {issue.get('description', '')[:120]}")
            print(f"  → {issue.get('suggested_change', '')[:120]}")

    if med:
        print("\n  — MEDIUM PRIORITY —")
        for issue in med:
            print(f"  [{issue['issue_id']}] {issue.get('affected_file', '?')} — {issue.get('description', '')[:80]}")

    print("\n  Run `patchwrite` to generate heredoc patch scripts.")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> Path | None:
    """
    Full AccuracyScientist cycle.
    Returns path to consolidated .issues.json, or None if no issues found.
    """
    log.info("=== AccuracyScientist starting ===")

    issues = load_all_issues()
    if not issues:
        log.info("No issues found across all sources — nothing to consolidate")
        return None

    issues = deduplicate(issues)
    issues = triage(issues)
    issues = reassign_ids(issues)

    output_path = write_consolidated(issues)
    write_summary(issues)

    log.info("=== AccuracyScientist complete ===")
    log.info(f"Next step: run `patchwrite` to generate patch scripts from {output_path.name}")

    return output_path


if __name__ == "__main__":
    run()
