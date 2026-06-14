#!/usr/bin/env python3
"""
tools/reindex.py — Sovereign Intelligence System
CLI wrapper for the RAG vault indexer.

Usage:
    python tools/reindex.py              # incremental (new/changed files only)
    python tools/reindex.py --rebuild    # full wipe + re-index
    alias: reindex
"""
import argparse
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
from core.rag.indexer import run_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Sovereign RAG Vault Indexer")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Wipe and re-index everything from scratch",
    )
    args = parser.parse_args()

    stats = run_index(rebuild=args.rebuild)

    print()
    print("=== Index complete ===")
    print(f"  Added/updated      : {stats['added']} files")
    print(f"  Skipped (unchanged): {stats['skipped']} files")
    print(f"  Errors             : {stats['errors']}")
    print(f"  Total chunks in DB : {stats['total_chunks']}")


if __name__ == "__main__":
    main()
