"""
core/llm.py — Single Ollama client with retry, timeout, and JSON-mode support.

Replaces the per-node ad-hoc requests.post calls. Adds:
  - format='json' for guaranteed JSON output (Ollama native feature)
  - Retry on transient failures (3 attempts with backoff)
  - Single helper for parsing JSON arrays out of LLM output
"""
import json
import re
import time
from typing import Optional

import requests

from .constants import OLLAMA_URL, MODEL, OLLAMA_TIMEOUT, OLLAMA_TEMP_DEFAULT


def generate(prompt: str, system: str,
             max_tokens: int = 1400,
             temperature: float = OLLAMA_TEMP_DEFAULT,
             json_mode: bool = False,
             model: str = MODEL,
             retries: int = 2) -> str:
    """Call Ollama /api/generate. Returns the raw response string.

    json_mode=True passes format='json' to Ollama, which constrains output
    to valid JSON. Eliminates the regex-based array extraction in most cases —
    the response will already parse cleanly.
    """
    payload = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "keep_alive": "0",
    }
    if json_mode:
        payload["format"] = "json"

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=OLLAMA_TIMEOUT)
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(1.5 ** attempt)  # 1s, 1.5s, 2.25s
            continue
    raise RuntimeError(f"Ollama generate failed after {retries + 1} attempts: {last_exc}")


# ── JSON array extraction ────────────────────────────────────────────────
_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)
_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def parse_json_array(raw: str, label: str = "") -> list:
    """Extract a JSON array from LLM output. Tolerates markdown fences,
    leading/trailing prose, and trailing backticks. Returns [] on failure."""
    cleaned = _FENCE_RE.sub("", raw).strip().rstrip("`").strip()

    # Try direct parse first (works when json_mode=True or LLM already clean)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        # Some models wrap the array in {"items": [...]} when in json_mode
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass

    # Fallback: regex-extract the first [...] block
    m = _ARRAY_RE.search(cleaned)
    if not m:
        if label:
            print(f"    ⚠️  {label}: no JSON array in response")
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        if label:
            print(f"    ⚠️  {label}: JSON parse failed — {e}")
        return []


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    """
    Embed a single text string using nomic-embed-text via Ollama.
    Single authoritative copy — replaces 4x duplicates across the codebase.
    Raises on HTTP or timeout errors — callers should handle.
    """
    from .constants import OLLAMA_URL, EMBED_MODEL
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["embedding"]


# ── Fallback generation ───────────────────────────────────────────────────────

def query_with_fallback(
    prompt: str,
    system: str = "",
    label: str = "NODE",
    temperature: float = OLLAMA_TEMP_DEFAULT,
    max_tokens: int = 2000,
    timeout: int = 300,
) -> tuple[str, str]:
    """
    Query with gemma3:12b primary, mistral:7b fallback.
    Returns (output_text, model_used).
    Raises RuntimeError if both models fail.
    Uses existing generate() with retry logic.
    """
    from .constants import MODEL, MODEL_FALLBACK
    for model in (MODEL, MODEL_FALLBACK):
        try:
            print(f"🧠 [{label}] Engaging {model}...")
            result = generate(prompt, system=system,
                              max_tokens=max_tokens,
                              temperature=temperature,
                              model=model)
            print(f"✅ [{label}] Complete via {model}")
            return result, model
        except Exception as e:
            print(f"⚠️  [{label}] {model} failed: {e}")
    raise RuntimeError(f"[{label}] Both models failed.")

# Compatibility wrapper — matches old query_ollama(prompt, model, system, ...) signature
def query_ollama(
    prompt: str,
    model: str = None,
    system: str = "",
    temperature: float = OLLAMA_TEMP_DEFAULT,
    max_tokens: int = 2000,
    timeout: int = 300,
) -> str:
    """Backward-compatible wrapper around generate().
    Old signature: query_ollama(prompt, model, system, temperature, max_tokens, timeout)
    """
    from .constants import MODEL as _MODEL
    return generate(
        prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model or _MODEL,
    )
