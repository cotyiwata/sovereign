"""
core/rag/retriever.py — Sovereign Intelligence System
RAG retrieval layer: pure retrieval functions with no CLI entry point.

The CLI entry point lives in tools/vault_query.py.

Usage:
    from core.rag.retriever import retrieve, retrieve_for_node
"""
from typing import Optional

from core.config import RAG_DISTANCE_THRESHOLD
from core.llm import embed
from core.rag.client import get_client, get_collection

DEFAULT_N = 5


# ── Core retrieval ────────────────────────────────────────────────────────────

def retrieve(
    query: str,
    n: int = DEFAULT_N,
    doc_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """
    Query the vault for semantically similar chunks.

    Returns list of {text, source, type, date, distance} dicts, best match first.
    Returns a single-item error list on connection failure — callers always get
    a list back and can check distance == 1.0 to detect unavailability.
    """
    try:
        collection = get_collection()
    except Exception as e:
        return [{"text": f"[RAG unavailable: {e}]", "source": "", "type": "", "date": "", "distance": 1.0}]

    where: dict = {}
    if doc_type:
        where["doc_type"] = doc_type
    if date_from and date_to:
        where["$and"] = [{"date": {"$gte": date_from}}, {"date": {"$lte": date_to}}]
    elif date_from:
        where["date"] = {"$gte": date_from}
    elif date_to:
        where["date"] = {"$lte": date_to}

    kwargs = dict(
        query_embeddings=[embed(query)],
        n_results=min(n, collection.count() or 1),
        include=["documents", "metadatas", "distances"],
    )
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    return [
        {
            "text":     doc,
            "source":   meta.get("filename", ""),
            "type":     meta.get("type", ""),
            "date":     meta.get("date", ""),
            "distance": round(dist, 4),
        }
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ]


# ── Node-specific helpers ─────────────────────────────────────────────────────

def retrieve_for_node(node: str, context: dict) -> str:
    """
    Return a pre-formatted RAG context block for injection into a node's prompt.

    node    : "chronicle" | "lore" | "ignition" | "plays" | "weekly" | "intraday"
    context : pipeline context dict (market data, fear_greed, etc.)
    """
    if node == "chronicle":
        btc_change = context.get("market", {}).get("crypto", {}).get("BTC", {}).get("change_pct", "")
        fg = context.get("market", {}).get("fear_greed", {}).get("classification", "")
        query = f"market regime BTC {btc_change} fear greed {fg} narrative signal"
        results  = retrieve(query, n=4, doc_type="market_brief")
        r2       = retrieve(query, n=2, doc_type="research_digest")
        r3       = retrieve(query, n=2, doc_type="analyst_audio")
        r4       = retrieve(query, n=2, doc_type="foundational_research")
        r5       = retrieve(query, n=2, doc_type="feedback")
        r6       = retrieve(query, n=2, doc_type="inbox")
        return _format_block("MARKET MEMORY", results + r2 + r3 + r4 + r5 + r6, max_chars=1200)

    if node == "lore":
        universe_key = context.get("active_universe", "age_of_aether")
        doc_type_map = {
            "age_of_aether":       "lore_aether",
            "the_veil_ascendancy": "lore_veil",
            "the_vigil":           "lore_vigil",
            "the_lost_net":        "lore_general",
        }
        doc_type = doc_type_map.get(universe_key, "lore_aether")
        universe_label = universe_key.replace("_", " ").title()
        query = f"{universe_label} arc tension characters wound zone conflict progression"
        results = retrieve(query, n=4, doc_type=doc_type)
        r2      = retrieve(query, n=2, doc_type="lore_audio")
        return _format_block("LORE MEMORY", results + r2, max_chars=800)

    if node == "ignition":
        universe_key = context.get("active_universe", "age_of_aether")
        doc_type_map = {
            "age_of_aether":       "lore_aether",
            "the_veil_ascendancy": "lore_veil",
            "the_vigil":           "lore_vigil",
            "the_lost_net":        "lore_general",
        }
        doc_type = doc_type_map.get(universe_key, "lore_aether")
        universe_label = universe_key.replace("_", " ").title()
        query = f"{universe_label} arc tension characters wound zone conflict progression"
        results = retrieve(query, n=4, doc_type=doc_type)
        r2      = retrieve(query, n=2, doc_type="lore_audio")
        r3      = retrieve(query, n=2, doc_type="ignition")
        return _format_block("LORE MEMORY", results + r2 + r3, max_chars=1000)

    if node == "plays":
        query = "high conviction equity plays AI energy nuclear uranium semiconductor signal"
        results = retrieve(query, n=3, doc_type="market_brief")
        r2      = retrieve(query, n=2, doc_type="research_digest")
        r3      = retrieve(query, n=3, doc_type="analyst_audio")
        r4      = retrieve(query, n=2, doc_type="trading_rules")
        r5      = retrieve(query, n=2, doc_type="foundational_research")
        r6      = retrieve(query, n=2, doc_type="feedback")
        r7      = retrieve(query, n=2, doc_type="inbox")
        return _format_block("PLAYS HISTORY", results + r2 + r3 + r4 + r5 + r6 + r7, max_chars=1400)

    if node == "weekly":
        query = "week regime signal dominant narrative BTC trend"
        results = retrieve(query, n=5, doc_type="weekly")
        r2      = retrieve(query, n=3, doc_type="market_brief")
        r3      = retrieve(query, n=2, doc_type="research_digest")
        r4      = retrieve(query, n=2, doc_type="feedback")
        r5      = retrieve(query, n=2, doc_type="inbox")
        return _format_block("WEEKLY MEMORY", results + r2 + r3 + r4 + r5, max_chars=1200)

    if node == "intraday":
        query = "intraday signal sector rotation momentum today"
        results = retrieve(query, n=3, doc_type="intraday")
        r2      = retrieve(query, n=2, doc_type="inbox")
        return _format_block("INTRADAY MEMORY", results + r2, max_chars=500)

    return ""


# ── Formatting ────────────────────────────────────────────────────────────────

def _format_block(label: str, results: list[dict], max_chars: int = 600) -> str:
    if not results:
        return ""

    lines = [f"=== {label} (from vault) ==="]
    total = 0

    for r in results:
        if r["distance"] > RAG_DISTANCE_THRESHOLD:
            continue
        header = f"[{r['date'] or 'undated'} | {r['type']}]"
        snippet = r["text"][:300].replace("\n", " ").strip()
        entry = f"{header} {snippet}"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)

    if len(lines) == 1:
        return ""

    lines.append("=== END MEMORY ===")
    return "\n".join(lines)
