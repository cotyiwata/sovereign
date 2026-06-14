"""
core/rag/indexer.py — Sovereign Intelligence System
RAG vault indexer: pure indexing functions with no CLI entry point.

The CLI entry point lives in tools/reindex.py.
The __main__ block has been removed from this module intentionally.

Usage:
    from core.rag.indexer import run_index
    run_index(rebuild=False)

Or as a callable from pipeline nodes that need to trigger reindexing.
"""

import hashlib
import json
import re
from pathlib import Path

from core.config import (
    VAULT_ROOT, RAG_DIR, CHROMA_DB,
    RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP,
)
from core.llm import embed
from core.rag.client import get_client, get_collection

# ── Index paths ───────────────────────────────────────────────────────────────
INDEX_PATHS = [
    VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs",
    VAULT_ROOT / "02-Market-Intel" / "Intraday",
    VAULT_ROOT / "02-Market-Intel" / "Weekly-Reviews",
    VAULT_ROOT / "02-Market-Intel" / "inbox_observations",
    VAULT_ROOT / "01-Trading" / "inbox_trades",
    VAULT_ROOT / "03-Universes" / "The-Lost-Net" / "Daily-Expansions",
    VAULT_ROOT / "03-Universes" / "The-Lost-Net" / "inbox_expansions",
    VAULT_ROOT / "03-Universes" / "Age-of-Aether" / "Daily-Expansions",
    VAULT_ROOT / "03-Universes" / "Age-of-Aether" / "inbox_expansions",
    VAULT_ROOT / "03-Universes" / "Veil-Ascendancy" / "Daily-Expansions",
    VAULT_ROOT / "03-Universes" / "Veil-Ascendancy" / "inbox_expansions",
    VAULT_ROOT / "03-Universes" / "Age-of-Aether" / "Lore-Bible.md",
    VAULT_ROOT / "03-Universes" / "Veil-Ascendancy" / "VEIL_ASCENDANCY_LORE_BIBLE.md",
    VAULT_ROOT / "03-Universes" / "The-Vigil" / "Daily-Expansions",
    VAULT_ROOT / "03-Universes" / "The-Vigil" / "inbox_expansions",
    VAULT_ROOT / "03-Universes" / "The-Vigil" / "The-Vigil-Bible.md",
    VAULT_ROOT / "03-Universes" / "The-Lost-Net" / "LoreBible_TheLostNet.md",
    VAULT_ROOT / "Data" / "lore_general",
    VAULT_ROOT / "Data" / "veil_ascendancy",
    VAULT_ROOT / "00-Inbox",
    VAULT_ROOT / "04-Intelligence",
    VAULT_ROOT / "05-Ignition",
    VAULT_ROOT / "Data" / "transcripts",
]

HASH_FILE = RAG_DIR / "index_hashes.json"

# System/noise files — never index these
SKIP_FILES = {
    "sovereign_context_brief.md",
    "sovereign_context_brief.md.md",
    "weekly_thesis.md",
    "dashboard.md",
    "SOVERIGN_DASHBOARD.md.md",
    "dashboard.html",
    "context.json",
    "lore_state_thelostnet.json",
}

SKIP_PATTERNS = [
    re.compile(r"Rich Text\.md$"),
    re.compile(r"^SocialContent_"),
]


# ── Text processing ───────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    size: int = RAG_CHUNK_SIZE,
    overlap: int = RAG_CHUNK_OVERLAP,
) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return [c.strip() for c in chunks if len(c.strip()) > 60]


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            return text[end + 3 :].strip()
    return text


# ── Metadata ──────────────────────────────────────────────────────────────────

def extract_meta(path: Path) -> dict | None:
    """
    Derive doc_type and date from file path and name.
    Returns None for files that should be excluded from RAG (e.g. social drafts).
    """
    meta = {"source": str(path), "filename": path.name, "doc_type": "unknown"}
    name = path.name

    if name.startswith("SocialContent_"):
        return None   # social drafts are never indexed

    if name.startswith(("Brief_", "Sovereign_Brief_", "Daily-Brief-", "daily_brief_")):
        meta["doc_type"] = "market_brief"
    elif name.startswith("Intraday_"):
        meta["doc_type"] = "intraday"
    elif name.startswith("Weekly_"):
        meta["doc_type"] = "weekly"
    elif "Age-of-Aether" in str(path):
        meta["doc_type"] = "lore_aether"
    elif "Veil-Ascendancy" in str(path) or "veil_ascendancy" in str(path):
        meta["doc_type"] = "lore_veil"
    elif "The-Vigil" in str(path) or "vigil" in str(path).lower():
        meta["doc_type"] = "lore_vigil"
    elif name.startswith("Expansion_"):
        meta["doc_type"] = "lore_aether"
    elif "lore_general" in str(path):
        meta["doc_type"] = "lore_general"
    elif name.startswith("Research_"):
        meta["doc_type"] = "foundational_research"
    elif name.startswith("Research-Digest_"):
        meta["doc_type"] = "research_digest"
    elif name.startswith("SystemReview_"):
        meta["doc_type"] = "system_review"
    elif name.startswith("SystemAudit_"):
        meta["doc_type"] = "system_audit"
    elif name.startswith("Ignition_"):
        meta["doc_type"] = "ignition"
    elif name == "trading-rules.md":
        meta["doc_type"] = "trading_rules"
    elif name.startswith("Note_"):
        meta["doc_type"] = "inbox"
    elif path.parent.name == "00-Inbox":
        meta["doc_type"] = "inbox"
    elif "Data/transcripts" in str(path):
        _LORE_PREFIXES = (
            "LikeStories", "Luetin", "MoreLore", "ImperialIterator",
            "WatchmanGaming", "TheLorebrarians", "ArcaveliStudios",
        )
        meta["doc_type"] = "lore_audio" if any(name.startswith(p) for p in _LORE_PREFIXES) else "analyst_audio"

    # Pull date from filename if present (YYYY-MM-DD segment)
    for part in name.replace(".md", "").split("_"):
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            meta["date"] = part
            break

    return meta


# ── Hash tracking ─────────────────────────────────────────────────────────────

def _load_hashes() -> dict:
    if HASH_FILE.exists():
        return json.loads(HASH_FILE.read_text())
    return {}


def _save_hashes(hashes: dict) -> None:
    HASH_FILE.write_text(json.dumps(hashes, indent=2))


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


# ── Main index function ────────────────────────────────────────────────────────

def run_index(rebuild: bool = False) -> dict:
    """
    Index vault markdown files into ChromaDB.

    Parameters
    ----------
    rebuild : if True, wipes the collection and re-indexes everything.
              if False (default), only processes new/changed files.

    Returns
    -------
    dict with keys: added, skipped, errors, total_chunks
    """
    RAG_DIR.mkdir(parents=True, exist_ok=True)

    client = get_client()
    if rebuild:
        print("🔥 Rebuild mode — wiping existing collection...")
        try:
            client.delete_collection("sovereign_vault")
        except Exception:
            pass
        hashes = {}
    else:
        hashes = _load_hashes()

    collection = get_collection(client, create_if_missing=True)

    md_files = []
    for base in INDEX_PATHS:
        if not base.exists():
            continue
        if base.is_file() and base.suffix == ".md":
            md_files.append(base)
        elif base.is_dir():
            md_files.extend(base.rglob("*.md"))
            if "transcripts" in str(base):
                md_files.extend(base.rglob("*.txt"))

    print(f"📂 Found {len(md_files)} markdown files across vault")

    added = skipped = errors = 0

    for path in sorted(md_files):
        if not path.is_file():
            continue
        if path.name in SKIP_FILES:
            skipped += 1
            continue
        if any(p.search(path.name) for p in SKIP_PATTERNS):
            skipped += 1
            continue

        fhash = _file_hash(path)
        if not rebuild and hashes.get(str(path)) == fhash:
            skipped += 1
            continue

        try:
            text = strip_frontmatter(
                path.read_text(encoding="utf-8", errors="replace")
            )
            chunks = chunk_text(text)
            if not chunks:
                continue

            meta = extract_meta(path)
            if meta is None:
                skipped += 1
                continue

            for i, chunk in enumerate(chunks):
                collection.upsert(
                    ids=[f"{path.stem}_chunk{i}"],
                    embeddings=[embed(chunk)],
                    documents=[chunk],
                    metadatas=[{**meta, "chunk_index": i}],
                )

            hashes[str(path)] = fhash
            added += 1
            print(f"  ✅ {path.name} → {len(chunks)} chunks")

        except Exception as e:
            errors += 1
            print(f"  ⚠️  {path.name}: {e}")

    _save_hashes(hashes)
    total = collection.count()

    return {"added": added, "skipped": skipped, "errors": errors, "total_chunks": total}
