"""
tools/research_digest.py — Weekly Research Digest

Intersects this week's tape themes (from daily briefs) with institutional
research (foundational_research RAG). Renders to 04-Intelligence/.

Run: digest
Cron: 0 6 * * 0 (Sunday 6am, before weekly_review.py)
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Layer-safe imports ─────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT, load_config
from core.llm import generate, query_ollama
from core.style import SOVEREIGN_CSS
from core.constants import (
    MODEL,
    RESEARCH_SOURCE_TAGS,
    DIGEST_EVERGREEN_THEMES,
    DIGEST_CONFIDENCE_SOLID_MIN_SOURCES,
    DIGEST_CONFIDENCE_SOLID_MIN_CHUNKS,
    DIGEST_TENSION_MIN_BRIEF_HITS,
    DIGEST_TENSION_MIN_CHUNKS,
    DIGEST_CONVERGENCE_BRIEF_DAYS,
    DIGEST_CONVERGENCE_WEEKLY_COUNT,
    DIGEST_CONVERGENCE_RESEARCH_TOPK,
    DIGEST_CONVERGENCE_PRIOR_DIGESTS,
)

import chromadb

# ── Paths ──────────────────────────────────────────────────────────────────
INTEL_DIR      = VAULT_ROOT / "04-Intelligence"
DIGEST_DIR     = INTEL_DIR / "Research-Digests"
BRIEFS_DIR     = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
WEEKLY_DIR     = VAULT_ROOT / "02-Market-Intel" / "Weekly-Reviews"
RAG_PATH       = VAULT_ROOT / "Data" / "rag" / "chroma_db"
HISTORY_FILE   = VAULT_ROOT / "Data" / "digest_themes_history.json"
COLLECTION     = "sovereign_vault"

def _extract_section(label: str, content: str) -> str:
    """Extract text under a ## LABEL heading from markdown content."""
    newline = "\n"
    pat = r"##\s+" + re.escape(label) + r"\s*" + newline + r"(.*?)(?=" + newline + r"##\s|\Z)"
    m = re.search(pat, content, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


TODAY     = datetime.now().strftime("%Y-%m-%d")
TIMESTAMP = datetime.now().strftime("%Y-%m-%d %H:%M")


# ══════════════════════════════════════════════════════════════════════════
# STAGE 1 — Theme extraction from last 7 daily briefs
# ══════════════════════════════════════════════════════════════════════════

def load_recent_briefs(days: int = DIGEST_CONVERGENCE_BRIEF_DAYS) -> list[dict]:
    """Return list of {date, pulse, regime, synthesis} from last N days of briefs."""
    cutoff = datetime.now() - timedelta(days=days)
    briefs = []
    for f in sorted(BRIEFS_DIR.glob("Brief_*.md"), reverse=True):
        text = f.read_text(encoding="utf-8")
        # extract date from frontmatter
        date_match = re.search(r"^date:\s*(.+)$", text, re.MULTILINE)
        if not date_match:
            continue
        try:
            brief_date = datetime.strptime(date_match.group(1).strip(), "%Y-%m-%d")
        except ValueError:
            continue
        if brief_date < cutoff:
            continue
        briefs.append({
            "date":      brief_date.strftime("%Y-%m-%d"),
            "filename":  f.name,
            "pulse":     _extract_section("PULSE",     text),
            "regime":    _extract_section("REGIME",    text),
            "synthesis": _extract_section("SYNTHESIS", text),
        })
        if len(briefs) >= days:
            break
    return briefs


def extract_tape_themes(briefs: list[dict]) -> list[str]:
    """LLM call → 2 tape-driven theme strings from brief content."""
    combined = ""
    for b in briefs:
        combined += f"\n\n--- {b['date']} ---\n"
        combined += f"PULSE: {b['pulse']}\n"
        combined += f"REGIME: {b['regime']}\n"
        combined += f"SYNTHESIS: {b['synthesis']}"

    prompt = f"""You are analyzing a week of daily market intelligence briefs.
Extract exactly 2 dominant macro or sector themes the tape has been signaling this week.
Themes should be specific enough to query institutional research against (e.g. "AI capex cycle",
"BTC safe haven narrative", "rate cut expectations", "energy grid constraints").
Respond ONLY with valid JSON: {{"themes": ["theme one", "theme two"]}}
No explanation. No markdown. No extra keys.

BRIEF CONTENT:
{combined[:4000]}"""

    try:
        raw = generate(prompt, system="You are a market intelligence analyst. Respond only with valid JSON.", temperature=0.3)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"```[a-z]*" + "\n" + "?", "", raw).replace("```", "").strip()
        data = json.loads(raw)
        themes = data.get("themes", [])
        return [str(t).strip() for t in themes if t][:2]
    except Exception as e:
        print(f"  ⚠ Theme extraction failed: {e} — using fallback")
        return []


# ══════════════════════════════════════════════════════════════════════════
# STAGE 2 — Evergreen theme selection
# ══════════════════════════════════════════════════════════════════════════

def load_theme_history() -> dict:
    """Load digest_themes_history.json; return {} if missing."""
    if not HISTORY_FILE.exists():
        return {}
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_theme_history(history: dict) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


def pick_evergreen_theme(history: dict) -> str:
    """Pick 1 evergreen not used in last 4 weeks. Resets if all excluded."""
    cutoff = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")
    recent = {theme for theme, date in history.items() if date >= cutoff}
    available = [t for t in DIGEST_EVERGREEN_THEMES if t not in recent]
    if not available:
        print("  ⚠ All evergreen themes used in last 4 weeks — resetting history")
        available = list(DIGEST_EVERGREEN_THEMES)
    # Pick least-recently-used: sort by last use date ascending, take first
    def last_used(t: str) -> str:
        return history.get(t, "1970-01-01")
    return sorted(available, key=last_used)[0]


# ══════════════════════════════════════════════════════════════════════════
# STAGE 3 — Per-theme retrieval from foundational_research
# ══════════════════════════════════════════════════════════════════════════

def get_chroma_collection():
    """Return ChromaDB collection. Queries foundational_research via old 'type' key."""
    client = chromadb.PersistentClient(path=str(RAG_PATH))
    return client.get_or_create_collection(COLLECTION)


def tag_source(source_path: str) -> str:
    """Map full source path → short tag (ARK, Coinbase, etc.) or filename stem."""
    fname = Path(source_path).stem
    return RESEARCH_SOURCE_TAGS.get(fname, fname)


def retrieve_for_theme(col, theme: str, n: int = 6) -> list[dict]:
    """
    Query foundational_research chunks for a theme.
    Returns list of {text, source_tag, source_path}.
    Uses 'type' key (old indexer format).
    """
    try:
        import chromadb.utils.embedding_functions as ef
        embed_fn = ef.OllamaEmbeddingFunction(
            url="http://localhost:11434/api/embeddings",
            model_name="nomic-embed-text",
        )
        query_embedding = embed_fn([theme])[0]
        results = col.query(
            query_embeddings=[query_embedding],
            n_results=min(n, col.count()),
            where={"type": {"$eq": "foundational_research"}},
            include=["documents", "metadatas", "distances"],
        )
        chunks = []
        docs      = results.get("documents", [[]])[0]
        metas     = results.get("metadatas",  [[]])[0]
        distances = results.get("distances",  [[]])[0]
        for doc, meta, dist in zip(docs, metas, distances):
            # distance → similarity (chromadb returns L2 or cosine distance)
            source_path = meta.get("source", "")
            chunks.append({
                "text":        doc,
                "source_tag":  tag_source(source_path),
                "source_path": source_path,
                "distance":    dist,
            })
        return chunks
    except Exception as e:
        print(f"  ⚠ Retrieval failed for '{theme}': {e}")
        return []


def assign_confidence(chunks: list[dict]) -> str:
    """SOLID if ≥2 distinct sources AND ≥3 chunks, else THIN."""
    if len(chunks) < DIGEST_CONFIDENCE_SOLID_MIN_CHUNKS:
        return "THIN"
    distinct_sources = len(set(c["source_tag"] for c in chunks))
    if distinct_sources >= DIGEST_CONFIDENCE_SOLID_MIN_SOURCES:
        return "SOLID"
    return "THIN"


# ══════════════════════════════════════════════════════════════════════════
# STAGE 4 — Per-theme synthesis
# ══════════════════════════════════════════════════════════════════════════

def synthesize_theme(theme: str, chunks: list[dict], brief_hits: list[str],
                     confidence: str) -> dict:
    """
    1 LLM call per theme.
    Returns {institutional_view, agree, diverge, this_week, confidence}.
    """
    research_block = ""
    for c in chunks:
        research_block += "[" + c["source_tag"] + "] " + c["text"][:600] + "\n\n"

    tape_block = ""
    if brief_hits:
        tape_block = "\n".join(brief_hits[:3])
    else:
        tape_block = "No direct mention in this week's briefs."

    thin_warning = ""
    if confidence == "THIN":
        thin_warning = "NOTE: Single-source or sparse retrieval. Flag this synthesis as THIN confidence."

    prompt = f"""You are synthesizing institutional research against live market intelligence.
Theme: {theme}
{thin_warning}

INSTITUTIONAL RESEARCH EXCERPTS:
{research_block[:3000]}

THIS WEEK'S TAPE (from daily brief SYNTHESIS sections where this theme appeared):
{tape_block[:1000]}

Respond ONLY with valid JSON. No markdown. No explanation. No extra keys.
{{
  "institutional_view": "2-3 sentences summarizing what the research says. Attribute by source name where sources diverge.",
  "agree": "1 sentence on where the tape and institutions align.",
  "diverge": "1 sentence on where they diverge, or: Broad alignment across sources.",
  "this_week": "1-2 sentences directly comparing what the tape showed this week versus what institutions project."
}}"""

    try:
        raw = generate(prompt, system="You are a market intelligence analyst. Respond only with valid JSON.", temperature=0.3)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"```[a-z]*", "", raw).replace("```", "").strip()
        data = json.loads(raw)
        return {
            "institutional_view": data.get("institutional_view", ""),
            "agree":              data.get("agree", ""),
            "diverge":            data.get("diverge", ""),
            "this_week":          data.get("this_week", ""),
            "confidence":         confidence,
        }
    except Exception as e:
        print(f"  ⚠ Synthesis failed for '{theme}': {e}")
        return {
            "institutional_view": f"Synthesis unavailable — LLM error: {e}",
            "agree":              "",
            "diverge":            "",
            "this_week":          "",
            "confidence":         confidence,
        }


# ══════════════════════════════════════════════════════════════════════════
# STAGE 5 — Tension map (rule-based, no LLM)
# ══════════════════════════════════════════════════════════════════════════

def build_tension_map(themes: list[str], theme_results: list[dict],
                      brief_counts: dict) -> dict:
    """
    Returns {aligned: [...], in_tension: [...], watching: [...]}.
    Rule-based only — no LLM call.
    """
    aligned    = []
    in_tension = []
    watching   = []

    for tr in theme_results:
        theme      = tr["theme"]
        hits       = brief_counts.get(theme, 0)
        num_chunks = tr.get("chunk_count", 0)

        has_tape        = hits >= 1  # at least 1 brief mention triggers tape signal
        has_research    = num_chunks >= DIGEST_TENSION_MIN_CHUNKS

        if has_tape and has_research:
            # Check for directional conflict: if diverge is not alignment language
            diverge_text = tr.get("diverge", "").lower()
            is_aligned = (
                "broad alignment" in diverge_text
                or "align" in diverge_text
                or diverge_text == ""
            )
            if is_aligned:
                aligned.append(theme)
            else:
                in_tension.append(theme)
        elif has_research and not has_tape:
            watching.append(theme)
        elif has_tape and not has_research:
            # Tape sees it but no institutional coverage
            watching.append(theme)

    return {
        "aligned":    aligned,
        "in_tension": in_tension,
        "watching":   watching,
    }


# ══════════════════════════════════════════════════════════════════════════
# STAGE 6 — Convergence block
# ══════════════════════════════════════════════════════════════════════════

def load_recent_weeklies(n: int = DIGEST_CONVERGENCE_WEEKLY_COUNT) -> list[str]:
    """Return text of the last N weekly review .md files."""
    files = sorted(WEEKLY_DIR.glob("Weekly_*.md"), reverse=True)[:n]
    result = []
    for f in files:
        try:
            result.append(f.read_text(encoding="utf-8")[:2000])
        except Exception:
            pass
    return result


def load_prior_digest() -> str:
    """Load most recent Research-Digest_*.md content, or empty string."""
    files = sorted(DIGEST_DIR.glob("Research-Digest_*.md"), reverse=True)
    if not files:
        return ""
    try:
        return files[0].read_text(encoding="utf-8")[:2000]
    except Exception:
        return ""


def generate_convergence(briefs: list[dict], weeklies: list[str],
                         prior_digest: str, theme_syntheses: list[dict],
                         col) -> str:
    """Single LLM call. Returns 5-7 sentence convergence paragraph."""

    # Build brief block — PULSE + REGIME + SYNTHESIS only
    brief_block = ""
    for b in briefs:
        brief_block += "--- " + b["date"] + " ---\n"
        brief_block += "PULSE: " + b["pulse"][:300] + "\n"
        brief_block += "REGIME: " + b["regime"][:300] + "\n"
        brief_block += "SYNTHESIS: " + b["synthesis"][:400] + "\n\n"

    # Weekly trajectory block
    weekly_block = ""
    if weeklies:
        weekly_block = "\n\n---\n".join(weeklies)
    else:
        weekly_block = "No weekly reviews available."

    # Prior digest block
    prior_block = prior_digest if prior_digest else "No prior digest available."

    # Theme syntheses block
    theme_block = ""
    for ts in theme_syntheses:
        if ts.get("skipped"):
            theme_block += "THEME: " + ts["theme"] + " — Insufficient coverage.\n\n"
        else:
            theme_block += "THEME: " + ts["theme"] + "\n"
            theme_block += "Institutional: " + ts.get("institutional_view","") + "\n"
            theme_block += "Agree: " + ts.get("agree","") + "\n"
            theme_block += "Diverge: " + ts.get("diverge","") + "\n"
            theme_block += "This week: " + ts.get("this_week","") + "\n\n"

    prompt = f"""You are writing the most important paragraph in a weekly intelligence document.
This paragraph runs FIRST. It is what the trader reads before writing their weekly thesis.

YOUR INPUTS:
1. THIS WEEK — last 7 daily briefs (PULSE + REGIME + SYNTHESIS):
{brief_block[:2500]}

2. TRAJECTORY — last 2 weekly reviews:
{weekly_block[:1500]}

3. INSTITUTIONAL RESEARCH — theme syntheses this week:
{theme_block[:1500]}

4. PRIOR DIGEST (last week's convergence for continuity):
{prior_block[:800]}

WRITE a convergence paragraph of exactly 5-7 sentences that answers:
- What is the system pointing toward right now?
- Where is this week consistent with prior trajectory, where is it a break?
- What do institutions confirm or contradict from the tape?

You MUST include at least one of:
- A theme where the tape is ahead of institutional framing
- A theme where institutional research contradicts the tape
- A risk neither source has priced in

If everything aligns unusually well, end with:
"Unusually high alignment this week — verify independently before treating as confirmation."

Write in direct, confident prose. No bullet points. No headers. No JSON. Plain paragraph only."""

    try:
        result = generate(prompt, system="You are a senior market intelligence analyst writing a weekly convergence briefing.", temperature=0.4)
        return result.strip()
    except Exception as e:
        return f"Convergence generation failed: {e}"


# ══════════════════════════════════════════════════════════════════════════
# STAGE 7 — Render
# ══════════════════════════════════════════════════════════════════════════

def render_markdown(convergence: str, themes: list[str],
                    theme_results: list[dict], tension: dict,
                    chunk_count: int, sources_used: set) -> str:
    sources_str = " · ".join(sorted(sources_used))
    week_of = datetime.now().strftime("%Y-%m-%d")
    theme_names = " | ".join(themes)

    lines = []
    lines.append("---")
    lines.append(f"date: {week_of}")
    lines.append(f"type: research_digest")
    lines.append(f"doc_type: research_digest")
    lines.append(f"themes: {theme_names}")
    lines.append(f"sources: {sources_str}")
    lines.append(f"chunks: {chunk_count}")
    lines.append("---")
    lines.append("")
    lines.append("# RESEARCH DIGEST")
    lines.append(f"**Week of {week_of}**")
    lines.append(f"Themes: {theme_names}")
    lines.append(f"Sources: {sources_str} | {chunk_count} chunks")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## CONVERGENCE")
    lines.append("")
    lines.append(convergence)
    lines.append("")

    for tr in theme_results:
        conf_badge = f"[{tr['confidence']}]"
        label_badge = f"[{tr['label']}]"
        lines.append("---")
        lines.append("")
        lines.append(f"## {tr['theme'].upper()}  {label_badge}  {conf_badge}")
        lines.append("")
        if tr.get("skipped"):
            lines.append("> ⚠ Insufficient research coverage on this theme — consider ingesting additional reports.")
        else:
            if tr["confidence"] == "THIN":
                lines.append("> ⚠ THIN — Single-source synthesis. Weight accordingly.")
                lines.append("")
            lines.append("**INSTITUTIONAL VIEW**")
            lines.append(tr.get("institutional_view", ""))
            lines.append("")
            lines.append(f"**AGREE** — {tr.get('agree', '')}")
            lines.append("")
            lines.append(f"**DIVERGE** — {tr.get('diverge', '')}")
            lines.append("")
            lines.append("**THIS WEEK**")
            lines.append(tr.get("this_week", ""))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## TENSION MAP")
    lines.append("")
    aligned_str    = ", ".join(tension.get("aligned", [])) or "None"
    tension_str    = ", ".join(tension.get("in_tension", [])) or "None"
    watching_str   = ", ".join(tension.get("watching", [])) or "None"
    lines.append(f"**ALIGNED** — {aligned_str}")
    lines.append("")
    lines.append(f"**IN TENSION** — {tension_str}")
    lines.append("")
    lines.append(f"**WATCHING** — {watching_str}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*Generated {TIMESTAMP} | {chunk_count} chunks | {sources_str}*")
    lines.append("*Read before writing weekly_thesis.md*")

    return "\n".join(lines)


def render_html(md_content: str, convergence: str, themes: list[str],
                theme_results: list[dict], tension: dict,
                chunk_count: int, sources_used: set) -> str:
    sources_str = " · ".join(sorted(sources_used))
    week_of     = datetime.now().strftime("%Y-%m-%d")
    theme_names = " | ".join(themes)

    def conf_badge_html(conf: str) -> str:
        color = "#2ecc71" if conf == "SOLID" else "#f39c12"
        return f'<span style="background:{color};color:#000;padding:2px 8px;border-radius:4px;font-size:0.75em;font-weight:700;">{conf}</span>'

    def label_badge_html(label: str) -> str:
        color = "#3498db" if label == "tape-driven" else "#8e44ad"
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.75em;font-weight:700;">{label}</span>'

    theme_blocks = ""
    for tr in theme_results:
        if tr.get("skipped"):
            body = '<p class="warn">⚠ Insufficient research coverage — consider ingesting additional reports.</p>'
        else:
            thin = '<p class="warn">⚠ THIN — Single-source synthesis. Weight accordingly.</p>' if tr["confidence"] == "THIN" else ""
            body = f"""{thin}
            <p><strong>INSTITUTIONAL VIEW</strong><br>{tr.get("institutional_view","")}</p>
            <p><strong>AGREE</strong> — {tr.get("agree","")}</p>
            <p><strong>DIVERGE</strong> — {tr.get("diverge","")}</p>
            <p><strong>THIS WEEK</strong><br>{tr.get("this_week","")}</p>"""

        theme_blocks += f"""
        <div class="theme-block">
            <div class="theme-header">
                {tr["theme"].upper()}
                {label_badge_html(tr["label"])}
                {conf_badge_html(tr["confidence"])}
            </div>
            {body}
        </div>"""

    aligned_str  = ", ".join(tension.get("aligned", [])) or "None"
    tension_str  = ", ".join(tension.get("in_tension", [])) or "None"
    watching_str = ", ".join(tension.get("watching", [])) or "None"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Research Digest — {week_of}</title>
<style>
{SOVEREIGN_CSS}
.digest-header {{ background: var(--surface); border: 1px solid var(--border);
    padding: 1.2rem 1.5rem; border-radius: 8px; margin-bottom: 1.5rem; }}
.digest-header h1 {{ margin: 0 0 0.25rem; font-size: 1.4rem; color: var(--accent); }}
.digest-meta {{ font-size: 0.8rem; color: var(--muted); }}
.convergence-block {{ background: var(--surface); border-left: 4px solid var(--accent);
    padding: 1.2rem 1.5rem; border-radius: 0 8px 8px 0; margin-bottom: 2rem;
    font-size: 1.05rem; line-height: 1.7; }}
.theme-block {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.2rem 1.5rem; margin-bottom: 1.2rem; }}
.theme-header {{ font-size: 1rem; font-weight: 700; margin-bottom: 0.8rem;
    display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
.tension-block {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.2rem 1.5rem; margin-bottom: 1.5rem; }}
.tension-block p {{ margin: 0.3rem 0; }}
.warn {{ color: #f39c12; font-weight: 600; }}
.footer {{ font-size: 0.75rem; color: var(--muted); text-align: center;
    padding-top: 1rem; border-top: 1px solid var(--border); }}
</style>
<style>
:root {{
    --surface: #111009;
    --border: #1e1c18;
    --accent: #f5a623;
    --muted: #5a5248;
}}
</style></head><body>
<div class="container">

<div class="digest-header">
    <h1>RESEARCH DIGEST</h1>
    <div class="digest-meta">Week of {week_of} &nbsp;|&nbsp; {theme_names}</div>
    <div class="digest-meta">Sources: {sources_str} &nbsp;|&nbsp; {chunk_count} chunks</div>
</div>

<h2 style="font-size:0.85rem;text-transform:uppercase;letter-spacing:0.1em;
    color:var(--muted);margin-bottom:0.5rem;">Convergence</h2>
<div class="convergence-block">{convergence}</div>

{theme_blocks}

<div class="tension-block">
    <div class="theme-header">TENSION MAP</div>
    <p><strong>ALIGNED</strong> — {aligned_str}</p>
    <p><strong>IN TENSION</strong> — {tension_str}</p>
    <p><strong>WATCHING</strong> — {watching_str}</p>
</div>

<div class="footer">
    Generated {TIMESTAMP} &nbsp;|&nbsp; {chunk_count} chunks &nbsp;|&nbsp; {sources_str}<br>
    Read before writing weekly_thesis.md
</div>

</div></body></html>"""


def index_digest(md_path: Path) -> None:
    """Index the rendered digest back into ChromaDB via incremental run_index."""
    try:
        from core.rag.indexer import run_index
        stats = run_index(rebuild=False)
        added = stats.get("added", 0)
        print(f"[digest] ✓ RAG reindex complete — {added} chunks added")
    except Exception as e:
        print(f"[digest] ⚠ RAG index failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print(f"[digest] Starting Research Digest — {TIMESTAMP}")

    col = get_chroma_collection()

    # Stage 1
    print("[digest] Stage 1 — loading briefs...")
    briefs = load_recent_briefs()
    print(f"  {len(briefs)} briefs loaded")

    # Stage 2
    print("[digest] Stage 2 — theme selection...")
    history = load_theme_history()
    evergreen = pick_evergreen_theme(history)

    if len(briefs) >= 3:
        tape_themes = extract_tape_themes(briefs)
    else:
        print("  ⚠ Fewer than 3 briefs — falling back to 3 evergreen themes")
        tape_themes = []

    if len(tape_themes) >= 2:
        themes = tape_themes[:2] + [evergreen]
        theme_labels = ["tape-driven", "tape-driven", "evergreen"]
    else:
        history2 = load_theme_history()
        themes = [pick_evergreen_theme(history2)]
        used = {themes[0]}
        for t in DIGEST_EVERGREEN_THEMES:
            if t not in used:
                themes.append(t)
            if len(themes) == 3:
                break
        theme_labels = ["evergreen", "evergreen", "evergreen"]

    print(f"  Themes: {themes}")

    # Stage 3
    print("[digest] Stage 3 — retrieval...")
    theme_chunks   = []
    brief_counts   = {}
    sources_used   = set()
    total_chunks   = 0

    for theme in themes:
        chunks = retrieve_for_theme(col, theme)
        confidence = assign_confidence(chunks)
        brief_hits = [b["synthesis"] for b in briefs
                      if theme.lower() in (b.get("pulse","") + b.get("regime","") +
                                           b.get("synthesis","")).lower()]
        brief_counts[theme] = len(brief_hits)
        for c in chunks:
            sources_used.add(c["source_tag"])
        total_chunks += len(chunks)
        theme_chunks.append({
            "theme": theme,
            "label": theme_labels[themes.index(theme)],
            "chunks": chunks,
            "confidence": confidence,
            "brief_hits": brief_hits,
        })
        print(f"  {theme}: {len(chunks)} chunks, {confidence}")

    # Stage 4
    print("[digest] Stage 4 — synthesis...")
    theme_results = []
    for tc in theme_chunks:
        if len(tc["chunks"]) == 0:
            result = {
                "theme": tc["theme"],
                "label": tc["label"],
                "confidence": tc["confidence"],
                "institutional_view": "Insufficient research coverage on this theme — consider ingesting additional reports.",
                "agree": "",
                "diverge": "",
                "this_week": "",
                "skipped": True,
                "chunk_count": 0,
            }
        else:
            synth = synthesize_theme(
                tc["theme"], tc["chunks"], tc["brief_hits"], tc["confidence"]
            )
            synth["theme"]      = tc["theme"]
            synth["label"]      = tc["label"]
            synth["skipped"]    = False
            synth["chunk_count"] = len(tc["chunks"])
            result = synth
        theme_results.append(result)

    # Stage 5
    print("[digest] Stage 5 — tension map...")
    tension = build_tension_map(themes, theme_results, brief_counts)

    # Stage 6
    print("[digest] Stage 6 — convergence block...")
    weeklies     = load_recent_weeklies()
    prior_digest = load_prior_digest()
    convergence  = generate_convergence(briefs, weeklies, prior_digest,
                                        theme_results, col)

    # Stage 7
    print("[digest] Stage 7 — rendering...")
    md_content   = render_markdown(convergence, themes, theme_results,
                                   tension, total_chunks, sources_used)
    html_content = render_html(md_content, convergence, themes, theme_results,
                               tension, total_chunks, sources_used)

    slug     = datetime.now().strftime("%Y-%m-%d_%H%M")
    md_path  = DIGEST_DIR / f"Research-Digest_{slug}.md"
    html_path = DIGEST_DIR / f"Research-Digest_{slug}.html"

    md_path.write_text(md_content,   encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")
    print(f"[digest] ✓ Written: {md_path.name}")
    print(f"[digest] ✓ Written: {html_path.name}")

    # Update evergreen history
    history[evergreen] = TODAY
    save_theme_history(history)

    # Index back to RAG
    index_digest(md_path)

    print(f"[digest] ✓ Complete — {total_chunks} chunks, {len(sources_used)} sources")


if __name__ == "__main__":
    main()
