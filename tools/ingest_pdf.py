#!/usr/bin/env python3
"""
Node 1c — PDF Ingest
Converts PDFs → Research_*.md in 04-Intelligence/ for RAG indexing.

Usage:
    python3 node1c_pdf_ingest.py                    # process all PDFs in Data/pdf_inbox/
    python3 node1c_pdf_ingest.py path/to/file.pdf   # single file
    python3 node1c_pdf_ingest.py --list             # show processed PDFs
"""

import sys
import os
import re
import argparse
from pathlib import Path
from datetime import datetime

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber not installed. Run: pip install pdfplumber --break-system-packages")
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────────────
SOVEREIGN = Path.home() / "sovereign"
PDF_INBOX  = SOVEREIGN / "Data" / "pdf_inbox"
PDF_ARCHIVE = SOVEREIGN / "Data" / "pdf_archive"
OUTPUT_DIR = SOVEREIGN / "04-Intelligence" / "Research-Library"
LOG_FILE   = SOVEREIGN / "logs" / "pdf_ingest.log"

PDF_INBOX.mkdir(parents=True, exist_ok=True)
PDF_ARCHIVE.mkdir(parents=True, exist_ok=True)

# ── Helpers ──────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def sanitize_filename(name: str) -> str:
    """Convert a title or filename to a safe slug."""
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:80]  # cap length


def extract_text(pdf_path: Path) -> tuple[str, dict]:
    """
    Extract full text from PDF using pdfplumber.
    Returns (text, metadata_dict).
    Falls back page-by-page on extraction errors.
    """
    meta = {}
    pages_text = []

    with pdfplumber.open(pdf_path) as pdf:
        # Pull PDF metadata if available
        if pdf.metadata:
            meta["title"]  = pdf.metadata.get("Title", "")
            meta["author"] = pdf.metadata.get("Author", "")
            meta["pages"]  = len(pdf.pages)

        for i, page in enumerate(pdf.pages):
            try:
                text = page.extract_text()
                if text:
                    pages_text.append(f"<!-- Page {i+1} -->\n{text}")
            except Exception as e:
                log(f"  ⚠ Page {i+1} extraction error: {e}")
                continue

    full_text = "\n\n".join(pages_text)
    return full_text, meta


def clean_text(text: str) -> str:
    """Basic cleanup — remove excessive whitespace, fix common PDF artifacts."""
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove form-feed characters
    text = text.replace("\x0c", "\n")
    # Strip leading/trailing whitespace per line
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def derive_title(pdf_path: Path, meta: dict) -> str:
    """Best-effort title: PDF metadata → filename stem."""
    if meta.get("title") and len(meta["title"]) > 3:
        return meta["title"].strip()
    return pdf_path.stem.replace("_", " ").replace("-", " ").title()


def build_frontmatter(title: str, source_file: str, meta: dict, page_count: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    author = meta.get("author", "Unknown").strip() or "Unknown"
    return f"""---
title: "{title}"
date: {today}
type: foundational_research
source_file: "{source_file}"
author: "{author}"
pages: {page_count}
tags: [research, foundational]
---

"""


def output_path(title: str) -> Path:
    slug = sanitize_filename(title)
    return OUTPUT_DIR / f"Research_{slug}.md"


def already_processed(pdf_path: Path) -> bool:
    """Check if a matching Research_ file already exists for this PDF."""
    stem = sanitize_filename(pdf_path.stem)
    expected = OUTPUT_DIR / f"Research_{stem}.md"
    return expected.exists()


def ingest_pdf(pdf_path: Path, force: bool = False) -> bool:
    """
    Full ingest pipeline for one PDF.
    Returns True on success.
    """
    log(f"Processing: {pdf_path.name}")

    if not pdf_path.exists():
        log(f"  ✗ File not found: {pdf_path}")
        return False

    if already_processed(pdf_path) and not force:
        log(f"  ⏭ Already ingested (use --force to re-run): {pdf_path.name}")
        return False

    # Extract
    try:
        raw_text, meta = extract_text(pdf_path)
    except Exception as e:
        log(f"  ✗ Extraction failed: {e}")
        return False

    if not raw_text.strip():
        log(f"  ⚠ No text layer — attempting OCR: {pdf_path.name}")
        try:
            from pdf2image import convert_from_path
            import pytesseract
            images = convert_from_path(str(pdf_path), dpi=200)
            ocr_pages = []
            for i, img in enumerate(images):
                text = pytesseract.image_to_string(img)
                if text.strip():
                    ocr_pages.append(f"<!-- Page {i+1} -->\n{text}")
                if (i + 1) % 10 == 0:
                    log(f"     OCR progress: {i+1}/{len(images)} pages")
            raw_text = "\n\n".join(ocr_pages)
            if not raw_text.strip():
                log(f"  ✗ OCR also returned no text: {pdf_path.name}")
                return False
            meta["pages"] = len(images)
            log(f"  ✓ OCR complete: {len(images)} pages processed")
        except Exception as e:
            log(f"  ✗ OCR failed: {e}")
            return False

    # Clean + compose
    clean = clean_text(raw_text)
    title = derive_title(pdf_path, meta)
    page_count = meta.get("pages", raw_text.count("<!-- Page"))
    frontmatter = build_frontmatter(title, pdf_path.name, meta, page_count)
    final = frontmatter + clean

    # Write
    dest = output_path(title)
    dest.write_text(final, encoding="utf-8")

    word_count = len(clean.split())
    log(f"  ✓ Written: {dest.name} ({page_count} pages, ~{word_count:,} words)")
    archive_dest = PDF_ARCHIVE / pdf_path.name
    pdf_path.rename(archive_dest)
    log(f"  📦 Archived: {pdf_path.name} → Data/pdf_archive/")
    return True


def process_inbox(force: bool = False):
    """Process all PDFs in Data/pdf_inbox/."""
    pdfs = sorted(PDF_INBOX.glob("*.pdf"))
    if not pdfs:
        log(f"No PDFs found in {PDF_INBOX}")
        log(f"Drop PDFs into: ~/sovereign/Data/pdf_inbox/")
        return

    log(f"Found {len(pdfs)} PDF(s) in inbox")
    success = 0
    for pdf in pdfs:
        if ingest_pdf(pdf, force=force):
            success += 1

    log(f"\nDone — {success}/{len(pdfs)} ingested successfully")
    if success > 0:
        log("Running reindex to push to ChromaDB...")
        import subprocess, sys
        venv_python = Path(sys.executable)
        indexer = Path.home() / "sovereign/Scripts/rag_indexer.py"
        result = subprocess.run([str(venv_python), str(indexer)], capture_output=True, text=True)
        if result.returncode == 0:
            log("✓ Reindex complete")
        else:
            log(f"⚠ Reindex error: {result.stderr[-200:]}")
            log("  → Run 'reindex' manually")


def list_processed():
    """Show all Research_ files in 04-Intelligence/."""
    files = sorted(OUTPUT_DIR.glob("Research_*.md"))
    if not files:
        print("No Research_ files found in 04-Intelligence/")
        return
    print(f"\n{'File':<60} {'Size':>8}")
    print("-" * 70)
    for f in files:
        size_kb = f.stat().st_size // 1024
        print(f"{f.name:<60} {size_kb:>6}KB")
    print(f"\nTotal: {len(files)} file(s)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Node 1c — PDF to Research Markdown")
    parser.add_argument("pdf", nargs="?", help="Path to a specific PDF file")
    parser.add_argument("--list",  action="store_true", help="List processed Research files")
    parser.add_argument("--force", action="store_true", help="Re-ingest even if already processed")
    args = parser.parse_args()

    if args.list:
        list_processed()
        return

    if args.pdf:
        pdf_path = Path(args.pdf).expanduser().resolve()
        ingest_pdf(pdf_path, force=args.force)
    else:
        process_inbox(force=args.force)


if __name__ == "__main__":
    main()
