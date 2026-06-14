#!/usr/bin/env python3
"""
NODE 1b — ANALYST AUDIO INGEST
yt-dlp + Whisper → ChromaDB RAG
Usage:
  python3 node1b_audio_ingest.py "https://youtube.com/watch?v=..."
  python3 node1b_audio_ingest.py --batch
  python3 node1b_audio_ingest.py --list         (show all indexed sources)
  python3 node1b_audio_ingest.py --lore "URL"   (index as lore, not market)
  python3 node1b_audio_ingest.py --lore --batch (batch process lore queue)
"""

import os
import sys
import requests
import json
import hashlib
import argparse
import textwrap
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SOVEREIGN = Path.home() / "sovereign"
AUDIO_DIR = SOVEREIGN / "Data" / "audio"
TRANSCRIPT_DIR = SOVEREIGN / "Data" / "transcripts"
QUEUE_FILE = SOVEREIGN / "Data" / "audio_queue.txt"
LORE_QUEUE_FILE = SOVEREIGN / "Data" / "lore_queue.txt"
HASH_FILE = SOVEREIGN / "Data" / "rag" / "index_hashes.json"
CHROMA_DIR = SOVEREIGN / "Data" / "rag" / "chroma_db"

AUDIO_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

# ── Whisper model (medium = best balance for financial speech) ─────────────────
WHISPER_MODEL = "medium"

# ── Chunk settings ─────────────────────────────────────────────────────────────
CHUNK_WORDS = 120      # ~500 tokens
CHUNK_OVERLAP = 20     # word overlap between chunks


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def load_hashes() -> dict:
    if HASH_FILE.exists():
        with open(HASH_FILE) as f:
            return json.load(f)
    return {}


def save_hashes(hashes: dict):
    with open(HASH_FILE, "w") as f:
        json.dump(hashes, f, indent=2)


def url_hash(url: str) -> str:
    return hashlib.md5(url.strip().encode()).hexdigest()


def already_indexed(url: str, hashes: dict) -> bool:
    key = f"audio:{url_hash(url)}"
    return key in hashes


def mark_indexed(url: str, hashes: dict, metadata: dict):
    key = f"audio:{url_hash(url)}"
    hashes[key] = {
        "url": url,
        "indexed_at": datetime.now().isoformat(),
        **metadata
    }


def load_queue() -> list[str]:
    if not QUEUE_FILE.exists():
        return []
    with open(QUEUE_FILE) as f:
        lines = [l.strip() for l in f.readlines()]
    return [l for l in lines if l and not l.startswith("#")]


def clear_queue():
    if QUEUE_FILE.exists():
        QUEUE_FILE.write_text("")
    print("  ✓ Queue cleared")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — DOWNLOAD AUDIO
# ══════════════════════════════════════════════════════════════════════════════

def download_audio(url: str) -> tuple[Path, dict]:
    """Download audio via yt-dlp. Returns (audio_path, video_metadata)."""
    import yt_dlp

    print(f"\n[1/4] Downloading audio...")
    print(f"      {url}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_template = str(AUDIO_DIR / f"%(uploader)s_%(title)s_{ts}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    meta = {}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        meta = {
            "title": info.get("title", "Unknown"),
            "channel": info.get("uploader", "Unknown"),
            "channel_id": info.get("uploader_id", ""),
            "upload_date": info.get("upload_date", ""),
            "duration_seconds": info.get("duration", 0),
            "url": url,
        }
        # Find the downloaded file
        prepared = ydl.prepare_filename(info)
        base = Path(prepared).stem
        audio_path = next(AUDIO_DIR.glob(f"{base}*.mp3"), None)

    # Fallback: grab newest mp3 in audio dir
    if not audio_path or not audio_path.exists():
        mp3s = sorted(AUDIO_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        if mp3s:
            audio_path = mp3s[0]

    if not audio_path or not audio_path.exists():
        raise FileNotFoundError("Audio download failed — no mp3 found in audio dir")

    duration_min = meta.get("duration_seconds", 0) // 60
    print(f"  ✓ {meta['title']}")
    print(f"  ✓ Channel: {meta['channel']} | Duration: {duration_min}m")
    print(f"  ✓ Saved: {audio_path.name}")

    return audio_path, meta


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — TRANSCRIBE
# ══════════════════════════════════════════════════════════════════════════════

def transcribe_audio(audio_path: Path, meta: dict) -> Path:
    """Transcribe audio with Whisper. Returns transcript .txt path."""
    import whisper

    print(f"\n[2/4] Transcribing with Whisper ({WHISPER_MODEL})...")
    print(f"      This takes ~3-5 min for a 30min video on M3 Pro")

    model = whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(str(audio_path), language="en", verbose=False)

    transcript_text = result["text"].strip()

    # Save transcript
    safe_channel = "".join(c for c in meta["channel"] if c.isalnum() or c in "-_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    transcript_path = TRANSCRIPT_DIR / f"{safe_channel}_{ts}.txt"

    with open(transcript_path, "w") as f:
        f.write(f"# Transcript: {meta['title']}\n")
        f.write(f"# Channel: {meta['channel']}\n")
        f.write(f"# URL: {meta['url']}\n")
        f.write(f"# Date: {meta.get('upload_date', 'unknown')}\n")
        f.write(f"# Transcribed: {datetime.now().isoformat()}\n\n")
        f.write(transcript_text)

    word_count = len(transcript_text.split())
    print(f"  ✓ Transcript: {word_count:,} words")
    print(f"  ✓ Saved: {transcript_path.name}")

    return transcript_path


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — CHUNK
# ══════════════════════════════════════════════════════════════════════════════

def chunk_transcript(transcript_path: Path, meta: dict, doc_type: str = "analyst_audio") -> list[dict]:
    """Split transcript into overlapping word-window chunks."""
    print(f"\n[3/4] Chunking transcript...")

    with open(transcript_path) as f:
        full_text = f.read()

    # Strip header lines (lines starting with #)
    lines = full_text.split("\n")
    body_lines = [l for l in lines if not l.startswith("#")]
    body = " ".join(body_lines).strip()

    words = body.split()
    chunks = []
    step = CHUNK_WORDS - CHUNK_OVERLAP

    for i, start in enumerate(range(0, len(words), step)):
        end = start + CHUNK_WORDS
        chunk_words = words[start:end]
        if len(chunk_words) < 10:
            continue  # skip tiny trailing chunks
        chunk_text = " ".join(chunk_words)
        chunks.append({
            "text": chunk_text,
            "chunk_index": i,
            "chunk_total": None,  # fill after loop
            "source": meta["title"],
            "channel": meta["channel"],
            "url": meta["url"],
            "upload_date": meta.get("upload_date", ""),
            "doc_type": doc_type,
        })

    for c in chunks:
        c["chunk_total"] = len(chunks)

    print(f"  ✓ {len(chunks)} chunks ({CHUNK_WORDS} words, {CHUNK_OVERLAP} overlap)")
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# ── Module-level embed (shared by index_chunks + reindex_transcripts) ────────
def embed(text: str) -> list[float]:
    resp = requests.post(
        "http://localhost:11434/api/embeddings",
        json={"model": "nomic-embed-text", "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]

# STEP 4 — EMBED + INDEX INTO CHROMADB
# ══════════════════════════════════════════════════════════════════════════════

def index_chunks(chunks: list[dict], meta: dict):
    """Embed chunks with nomic-embed-text via Ollama and store in ChromaDB."""
    import chromadb
    import requests

    print(f"\n[4/4] Embedding + indexing into ChromaDB...")

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(
        name="sovereign_vault",
        metadata={"hnsw:space": "cosine"}
    )

    def embed(text: str) -> list[float]:
        resp = requests.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    indexed = 0
    url_h = url_hash(meta["url"])

    for chunk in chunks:
        doc_id = f"audio_{url_h}_{chunk['chunk_index']:04d}"

        # Skip if already in collection
        existing = collection.get(ids=[doc_id])
        if existing["ids"]:
            continue

        embedding = embed(chunk["text"])

        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[chunk["text"]],
            metadatas=[{
                "doc_type": chunk["doc_type"],
                "type":     chunk["doc_type"],
                "source":   chunk["source"],
                "channel":  chunk["channel"],
                "url":      chunk["url"],
                "upload_date": chunk.get("upload_date", ""),
                "chunk_index": chunk["chunk_index"],
                "chunk_total": chunk["chunk_total"],
            }]
        )
        indexed += 1

    print(f"  ✓ {indexed} chunks indexed (skipped {len(chunks) - indexed} duplicates)")
    print(f"  ✓ Collection size: {collection.count()} total chunks")


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def process_url(url: str, hashes: dict, skip_audio_cleanup: bool = False, doc_type: str = "analyst_audio") -> bool:
    """Full pipeline for one URL. Returns True on success."""
    url = url.strip()
    if not url:
        return False

    is_lore = doc_type == "lore"
    mode_label = "LORE INGEST" if is_lore else "ANALYST AUDIO INGEST"
    hash_prefix = "lore" if is_lore else "audio"

    print(f"\n{'═'*60}")
    print(f"{mode_label}")
    print(f"{'═'*60}")

    if already_indexed(url, hashes):
        print(f"⚡ Already indexed — skipping")
        print(f"   {url}")
        return True

    try:
        # Step 1 — Download
        audio_path, meta = download_audio(url)

        # Step 2 — Transcribe
        transcript_path = transcribe_audio(audio_path, meta)

        # Step 3 — Chunk
        chunks = chunk_transcript(transcript_path, meta, doc_type=doc_type)

        # Step 4 — Index
        index_chunks(chunks, meta)

        # Mark complete — use hash_prefix to keep lore and market separate in list
        key = f"{hash_prefix}:{url_hash(url)}"
        hashes[key] = {
            "url": url,
            "indexed_at": datetime.now().isoformat(),
            "title": meta["title"],
            "channel": meta["channel"],
            "chunks": len(chunks),
            "doc_type": doc_type,
        }
        save_hashes(hashes)

        # Optional: remove mp3 to save disk (transcript kept)
        if not skip_audio_cleanup:
            try:
                audio_path.unlink()
                print(f"\n  ✓ Audio file removed (transcript preserved)")
            except Exception:
                pass

        rag_type = "lore" if is_lore else "analyst_audio"
        print(f"\n{'─'*60}")
        print(f"✅ COMPLETE: {meta['title']}")
        print(f"   Channel  : {meta['channel']}")
        print(f"   Type     : {doc_type}")
        print(f"   Chunks   : {len(chunks)} indexed into RAG")
        print(f"   Queryable: rag \"{rag_type}\" or any topic search")
        print(f"{'─'*60}")
        return True

    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        return False



def reindex_transcripts():
    """Re-index all existing transcripts from Data/transcripts/ into ChromaDB.
    Determines doc_type from filename prefix — no URLs needed."""
    import chromadb

    LORE_PREFIXES = (
        "LikeStories", "Luetin", "MoreLore", "ImperialIterator",
        "WatchmanGaming", "TheLorebrarians", "ArcaveliStudios",
    )

    transcripts_dir = SOVEREIGN / "Data" / "transcripts"
    files = sorted(transcripts_dir.glob("*.txt"))

    if not files:
        print("  No transcripts found in Data/transcripts/")
        return

    client = chromadb.PersistentClient(path=str(SOVEREIGN / "Data" / "rag" / "chroma_db"))
    collection = client.get_or_create_collection("sovereign_vault")

    print(f"  Reindexing {len(files)} transcripts...")
    total_chunks = 0

    for fpath in files:
        doc_type = "lore_audio" if any(fpath.name.startswith(p) for p in LORE_PREFIXES) else "analyst_audio"
        source = fpath.stem
        meta = {"title": source, "source": str(fpath), "doc_type": doc_type, "channel": source, "url": f"file://{fpath}"}

        chunks = chunk_transcript(fpath, meta, doc_type=doc_type)
        if not chunks:
            print(f"  ⚠️  {fpath.name}: no chunks generated")
            continue

        for i, chunk in enumerate(chunks):
            try:
                vec = embed(chunk["text"])
                chunk_id = f"{hashlib.md5(str(fpath).encode()).hexdigest()[:12]}_{i}"
                collection.upsert(
                    ids=[chunk_id],
                    embeddings=[vec],
                    documents=[chunk["text"]],
                    metadatas=[{
                        "source":   chunk["source"],
                        "type":     doc_type,
                        "doc_type": doc_type,
                        "date":     chunk.get("date", ""),
                        "title":    source,
                    }],
                )
            except Exception as e:
                print(f"  ⚠️  {fpath.name} chunk error: {e}")

        total_chunks += len(chunks)
        print(f"  ✅ {fpath.name} → {len(chunks)} chunks [{doc_type}]")

    print(f"\n  Total chunks added: {total_chunks}")
    print(f"  Collection size: {collection.count()}")

def list_indexed(hashes: dict):
    """Print all indexed sources, separated by type."""
    market_entries = {k: v for k, v in hashes.items() if k.startswith("audio:")}
    lore_entries = {k: v for k, v in hashes.items() if k.startswith("lore:")}

    if not market_entries and not lore_entries:
        print("No audio indexed yet.")
        return

    if market_entries:
        print(f"\n{'═'*60}")
        print(f"MARKET INTELLIGENCE — {len(market_entries)} sources")
        print(f"{'═'*60}")
        for key, val in sorted(market_entries.items(), key=lambda x: x[1].get("indexed_at", "")):
            print(f"\n  📈 {val.get('title', 'Unknown')}")
            print(f"     Channel : {val.get('channel', '?')}")
            print(f"     Chunks  : {val.get('chunks', '?')}")
            print(f"     Indexed : {val.get('indexed_at', '?')[:10]}")
            print(f"     URL     : {val.get('url', '?')}")

    if lore_entries:
        print(f"\n{'═'*60}")
        print(f"LORE INTELLIGENCE — {len(lore_entries)} sources")
        print(f"{'═'*60}")
        for key, val in sorted(lore_entries.items(), key=lambda x: x[1].get("indexed_at", "")):
            print(f"\n  📖 {val.get('title', 'Unknown')}")
            print(f"     Channel : {val.get('channel', '?')}")
            print(f"     Chunks  : {val.get('chunks', '?')}")
            print(f"     Indexed : {val.get('indexed_at', '?')[:10]}")
            print(f"     URL     : {val.get('url', '?')}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Node 1b — Audio Ingest (Market + Lore)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              analyst "https://youtube.com/watch?v=..."              (market)
              analyst --lore "https://youtube.com/watch?v=..."       (lore)
              analyst --batch                                         (market queue)
              analyst --lore --batch                                  (lore queue)
              analyst --list
              analyst --keep-audio "https://youtube.com/watch?v=..."

            Queue files:
              Market : ~/sovereign/Data/audio_queue.txt
              Lore   : ~/sovereign/Data/lore_queue.txt
        """)
    )
    parser.add_argument("url", nargs="?", help="YouTube URL to ingest")
    parser.add_argument("--batch", action="store_true", help="Process all URLs in queue file")
    parser.add_argument("--list", action="store_true", help="List all indexed sources")
    parser.add_argument("--keep-audio", action="store_true", help="Keep mp3 after transcription")
    parser.add_argument("--lore", action="store_true", help="Index as lore_audio instead of analyst_audio")
    parser.add_argument("--reindex-transcripts", action="store_true", help="Re-index all existing transcripts from Data/transcripts/")

    args = parser.parse_args()
    hashes = load_hashes()

    if args.reindex_transcripts:
        reindex_transcripts()
        return
        return
    doc_type = "lore_audio" if args.lore else "analyst_audio"
    queue_file = LORE_QUEUE_FILE if args.lore else QUEUE_FILE

    if args.list:
        list_indexed(hashes)
        return

    if args.batch:
        # Load from appropriate queue file
        if not queue_file.exists():
            print(f"Queue file not found: {queue_file}")
            print("Create it with one URL per line.")
            return
        with open(queue_file) as f:
            lines = [l.strip() for l in f.readlines()]
        queue = [l for l in lines if l and not l.startswith("#")]
        if not queue:
            mode = "lore" if args.lore else "market"
            print(f"{mode.capitalize()} queue is empty.")
            print(f"Add URLs to: {queue_file}")
            return
        mode = "lore" if args.lore else "market"
        print(f"Batch mode [{mode}]: {len(queue)} URLs in queue")
        results = []
        for url in queue:
            ok = process_url(url, hashes, skip_audio_cleanup=not args.keep_audio, doc_type=doc_type)
            results.append((url, ok))
        # Clear the queue
        queue_file.write_text("")
        print(f"  ✓ Queue cleared")
        print(f"\n{'═'*60}")
        print(f"BATCH COMPLETE")
        passed = sum(1 for _, ok in results if ok)
        print(f"  {passed}/{len(results)} succeeded")
        return

    if args.url:
        process_url(args.url, hashes, skip_audio_cleanup=not args.keep_audio, doc_type=doc_type)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
