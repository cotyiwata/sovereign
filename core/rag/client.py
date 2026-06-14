"""
core/rag/client.py — Sovereign Intelligence System
Single ChromaDB client factory.

Consolidates 3x duplicate chromadb.PersistentClient() calls from:
  rag_indexer.py, rag_retriever.py, feedback_learner.py

All RAG operations (index, retrieve, feedback) open their connection
through get_client() and get_collection() here.

Usage:
    from core.rag.client import get_client, get_collection, COLLECTION_NAME
"""
import chromadb
from core.config import CHROMA_DB

COLLECTION_NAME = "sovereign_vault"


def get_client() -> chromadb.PersistentClient:
    """Return a PersistentClient pointed at the vault's ChromaDB directory."""
    return chromadb.PersistentClient(path=str(CHROMA_DB))


def get_collection(
    client: chromadb.PersistentClient | None = None,
    *,
    create_if_missing: bool = False,
) -> chromadb.Collection:
    """
    Return the sovereign_vault collection.

    Parameters
    ----------
    client : optional pre-existing client (avoids re-opening for callers
             that already hold one)
    create_if_missing : if True, creates the collection when it doesn't
                        exist (used by indexer and feedback writer).
                        If False (default), raises if missing (used by retriever).
    """
    if client is None:
        client = get_client()

    if create_if_missing:
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return client.get_collection(COLLECTION_NAME)
