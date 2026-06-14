# ingest_processor.py — Inbox Ingestion Node
# Sovereign Intelligence System
# v2.0 — Batch processor, 5-category classification, Apple Notes bridge ready

import os
import re
import json
import shutil
import tempfile
import requests
from datetime import datetime
from pathlib import Path

# --- CONFIG ---
BASE_DIR = "/Users/cotyiwata/Library/Mobile Documents/com~apple~CloudDocs/INTELLIGENCE-SYSTEM"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma3:12b"
FALLBACK_MODEL = "mistral:7b"

INBOX = os.path.join(BASE_DIR, "00-Inbox")

DESTINATIONS = {
    "market":    os.path.join(BASE_DIR, "02-Market-Intel"),
    "trading":   os.path.join(BASE_DIR, "01-Trading"),
    "creative":  os.path.join(BASE_DIR, "03-Universes/Age-of-Aether"),
    "profile":   os.path.join(BASE_DIR, "04-Intelligence"),
    "ideas":     os.path.join(BASE_DIR, "05-Ideas"),
    "unclassified": os.path.join(BASE_DIR, "00-Inbox/Unclassified"),
}

# Context file — classified notes inject summaries here for next brief
CONTEXT_FILE = os.path.join(BASE_DIR, "Output/context.json")

CLASSIFIER_PROMPT = """You are classifying a note into exactly one category for an intelligence system.

Categories:
- market: Market research, economic analysis, macro trends, sector insights, research papers on finance or investing
- trading: Trading rules, trade ideas, stock/crypto setups, position sizing, risk management, watchlist additions
- creative: Worldbuilding ideas, character concepts, fictional universe lore, story ideas, fantasy/sci-fi concepts
- profile: Personal frameworks, quotes, mental models, life philosophy, productivity insights, personal goals
- ideas: Business ideas, income streams, side projects, product concepts, monetization angles, entrepreneurial insights

Respond with ONLY the category word. Nothing else. No explanation.

Note to classify:
"""

FORMATTER_PROMPT = """You are formatting a raw captured note into a clean Obsidian markdown note.

Rules:
- Add a concise title as H1
- Add date as YYYY-MM-DD
- Add a "Category" field
- Clean up grammar and formatting
- Preserve ALL original ideas, data points, and insights — do not summarize or cut content
- Add a "Key Takeaways" section at the bottom with 2-4 bullet points
- Keep it tight — no filler

Raw note:
"""


# ─────────────────────────────────────────────
# OLLAMA
# ─────────────────────────────────────────────

def query_ollama(prompt: str, model: str, max_tokens: int = 1000, temperature: float = 0.3) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
        "keep_alive": "0"
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    return response.json()["response"].strip()


def query_with_fallback(prompt: str, label: str, max_tokens: int = 1000) -> str:
    try:
        return query_ollama(prompt, MODEL, max_tokens)
    except Exception as e:
        print(f"  ⚠️  [{label}] {MODEL} failed: {e} — trying {FALLBACK_MODEL}")
        return query_ollama(prompt, FALLBACK_MODEL, max_tokens)


# ─────────────────────────────────────────────
# CLASSIFICATION
# ─────────────────────────────────────────────

def classify_note(content: str) -> str:
    """Use Ollama to classify note into one of 5 categories."""
    try:
        raw = query_with_fallback(CLASSIFIER_PROMPT + content[:2000], "CLASSIFIER", max_tokens=10)
        category = raw.lower().strip().split()[0]
        if category in DESTINATIONS:
            return category
        # Fallback keyword classification if model output is unexpected
        return keyword_classify(content)
    except Exception as e:
        print(f"  ⚠️  [CLASSIFIER] Failed: {e} — using keyword fallback")
        return keyword_classify(content)


def keyword_classify(content: str) -> str:
    """Keyword fallback classifier."""
    t = content.lower()

    market_words = ['market', 'economy', 'inflation', 'fed', 'gdp', 'macro', 'sector',
                    'research paper', 'analysis', 'recession', 'interest rate', 'yield']
    trading_words = ['btc', 'bitcoin', 'eth', 'crypto', 'stock', 'trade', 'position',
                     'watchlist', 'setup', 'entry', 'exit', 'risk', 'nvda', 'smr', 'oklo',
                     'bwxt', 'chart', 'technical', 'fundamental', 'buy', 'sell', 'short']
    creative_words = ['world', 'universe', 'character', 'lore', 'magic', 'faction', 'house',
                      'aether', 'titan', 'fantasy', 'sci-fi', 'story', 'myth', 'fiction',
                      'realm', 'species', 'power system', 'villain', 'hero']
    ideas_words = ['business', 'income', 'revenue', 'product', 'startup', 'monetize',
                   'idea', 'side project', 'opportunity', 'build', 'launch', 'service',
                   'market fit', 'customers', 'profit', 'passive', 'saas', 'app']

    scores = {
        'trading': sum(1 for w in trading_words if w in t),
        'creative': sum(1 for w in creative_words if w in t),
        'ideas': sum(1 for w in ideas_words if w in t),
        'market': sum(1 for w in market_words if w in t),
    }

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    return 'profile'  # Default: goes to SOVEREIGN_PROFILE context


# ─────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────

def format_note(content: str, category: str) -> str:
    """Format raw note into clean markdown."""
    try:
        formatted = query_with_fallback(
            FORMATTER_PROMPT + f"Category: {category}\n\n" + content[:3000],
            "FORMATTER",
            max_tokens=1500
        )
        return formatted
    except Exception as e:
        print(f"  ⚠️  [FORMATTER] Failed: {e} — using raw content")
        today = datetime.now().strftime('%Y-%m-%d')
        return f"# Captured Note\nDate: {today}\nCategory: {category}\n\n{content}"


# ─────────────────────────────────────────────
# CONTEXT INJECTION
# ─────────────────────────────────────────────

def inject_into_context(category: str, summary: str, filename: str) -> None:
    """Add processed note summary to context.json so next brief is aware of it."""
    if not os.path.exists(CONTEXT_FILE):
        return

    try:
        with open(CONTEXT_FILE, 'r', encoding='utf-8') as f:
            context = json.load(f)

        if "inbox_intel" not in context:
            context["inbox_intel"] = []

        context["inbox_intel"].append({
            "timestamp": datetime.now().isoformat(),
            "category": category,
            "summary": summary[:300],
            "source_file": filename
        })

        # Keep only last 20 inbox items in context
        context["inbox_intel"] = context["inbox_intel"][-20:]

        with open(CONTEXT_FILE, 'w', encoding='utf-8') as f:
            json.dump(context, f, indent=2, ensure_ascii=False)

        print(f"  💉 [CONTEXT] Injected into context.json")

    except Exception as e:
        print(f"  ⚠️  [CONTEXT] Injection failed: {e}")


# ─────────────────────────────────────────────
# FILE I/O
# ─────────────────────────────────────────────

def atomic_write(content: str, target_path: str) -> None:
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode='w',
        dir=target_dir,
        suffix='.tmp',
        delete=False,
        encoding='utf-8'
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    shutil.move(tmp_path, target_path)


def get_inbox_files() -> list:
    """Get all processable files from inbox, excluding system files."""
    if not os.path.exists(INBOX):
        return []

    files = []
    for f in os.listdir(INBOX):
        path = os.path.join(INBOX, f)
        if os.path.isfile(path) and not f.startswith('.') and f != '.DS_Store':
            if f.endswith(('.md', '.txt')):
                files.append(path)
    return files


def destination_path(category: str, filename: str) -> str:
    """Build the full destination path for a classified note."""
    dest_dir = DESTINATIONS.get(category, DESTINATIONS["unclassified"])
    os.makedirs(dest_dir, exist_ok=True)

    # Add timestamp prefix to avoid collisions
    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
    stem = Path(filename).stem
    return os.path.join(dest_dir, f"{timestamp}_{stem}.md")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n📥 [NODE: INGEST] Inbox scan initiated...")

    files = get_inbox_files()

    if not files:
        print(f"  ✅ [INGEST] Inbox empty — nothing to process.")
        return

    print(f"  📋 [INGEST] Found {len(files)} file(s) to process.")

    processed = 0
    failed = 0

    for filepath in files:
        filename = os.path.basename(filepath)
        print(f"\n  📄 Processing: {filename}")

        # Skip sovereign system outputs — not user notes
        if filename.startswith(("Ignition_", "Brief_", "Intraday_", "Plays_", "SocialContent_", "Weekly_")):
            print(f"  ⏭️  System file — skipping.")
            continue

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                raw_content = f.read().strip()

            if not raw_content:
                print(f"  ⚠️  Empty file — skipping.")
                os.remove(filepath)
                continue

            # Classify
            category = classify_note(raw_content)
            print(f"  🏷️  Category: {category.upper()}")

            # Format
            formatted = format_note(raw_content, category)

            # Write to destination
            dest = destination_path(category, filename)
            atomic_write(formatted, dest)
            print(f"  ✅ Routed → {os.path.relpath(dest, BASE_DIR)}")

            # Inject summary into context for next brief
            inject_into_context(category, raw_content[:300], filename)

            # Remove from inbox
            os.remove(filepath)
            processed += 1

        except Exception as e:
            print(f"  ❌ Failed to process {filename}: {e}")
            # Move to unclassified rather than deleting
            unclassified_dir = DESTINATIONS["unclassified"]
            os.makedirs(unclassified_dir, exist_ok=True)
            shutil.move(filepath, os.path.join(unclassified_dir, filename))
            failed += 1

    print(f"\n  {'✅' if failed == 0 else '⚠️ '} [INGEST] Complete — "
          f"{processed} processed, {failed} failed.")


if __name__ == "__main__":
    main()