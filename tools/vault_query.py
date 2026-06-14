#!/usr/bin/env python3
"""
tools/vault_query.py — Sovereign Intelligence System
CLI wrapper for querying the RAG vault.

Usage:
    python tools/vault_query.py "what was the market regime last week"
    python tools/vault_query.py "cael bloodfire arc" --type lore --n 3
    alias: vault-query
"""
import argparse
import sys
from pathlib import Path

# ── import layer: tools/* → core/*, analysis/* only ──────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rag.retriever import retrieve


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the Sovereign vault via RAG")
    parser.add_argument("query", help="Natural language query string")
    parser.add_argument("--type",  dest="doc_type", help="Filter: market_brief | intraday | weekly | lore | inbox")
    parser.add_argument("--n",     type=int, default=5, help="Number of results (default 5)")
    parser.add_argument("--from",  dest="date_from", help="Start date YYYY-MM-DD")
    parser.add_argument("--to",    dest="date_to",   help="End date YYYY-MM-DD")
    args = parser.parse_args()

    print(f"\n🔍 Query: {args.query}")
    if args.doc_type:
        print(f"   Filter: type={args.doc_type}")
    print()

    results = retrieve(
        args.query,
        n=args.n,
        doc_type=args.doc_type,
        date_from=args.date_from,
        date_to=args.date_to,
    )

    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        relevance = (
            "relevant" if r["distance"] < 0.50 else
            "moderate" if r["distance"] < 0.75 else
            "weak"
        )
        print(f"─── Result {i} ─────────────────────────────────────────")
        print(f"  Source  : {r['source']}")
        print(f"  Type    : {r['type']}")
        print(f"  Date    : {r['date']}")
        print(f"  Distance: {r['distance']} ({relevance})")
        print(f"  Text    : {r['text'][:400]}{'...' if len(r['text']) > 400 else ''}")
        print()


if __name__ == "__main__":
    main()
