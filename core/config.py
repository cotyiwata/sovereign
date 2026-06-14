"""
core/config.py — Sovereign Intelligence System
Single source of truth for vault paths and RAG settings.

Ollama URLs and model names live in core/constants.py.
Import from here for all file paths and load_config().
"""
from pathlib import Path
import yaml

# ── Vault root ────────────────────────────────────────────────────────────────
VAULT_ROOT   = Path.home() / "sovereign"
SCRIPTS_DIR  = Path(__file__).parent.parent
OUTPUT_DIR   = VAULT_ROOT / "Output"
CONTEXT_FILE = OUTPUT_DIR / "context.json"
CONFIG_PATH  = VAULT_ROOT / "config.yaml"
LOGS_DIR     = VAULT_ROOT / "logs"

# ── Standard vault paths ──────────────────────────────────────────────────────
BRIEFS_DIR   = VAULT_ROOT / "02-Market-Intel" / "Daily-Briefs"
INTRADAY_DIR = VAULT_ROOT / "02-Market-Intel" / "Intraday"
WEEKLY_DIR   = VAULT_ROOT / "02-Market-Intel" / "Weekly-Reviews"
TRADING_DIR  = VAULT_ROOT / "01-Trading"
INBOX_DIR    = VAULT_ROOT / "00-Inbox"
INTEL_DIR    = VAULT_ROOT / "04-Intelligence"
DATA_DIR     = VAULT_ROOT / "Data"

# ── RAG ───────────────────────────────────────────────────────────────────────
RAG_DIR                = DATA_DIR / "rag"
CHROMA_DB              = RAG_DIR / "chroma_db"
RAG_DISTANCE_THRESHOLD = 0.65
RAG_CHUNK_SIZE         = 800
RAG_CHUNK_OVERLAP      = 150

# ── Config loader ─────────────────────────────────────────────────────────────
def load_config() -> dict:
    """Load config.yaml. Returns {} on failure — never crashes the pipeline."""
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except FileNotFoundError:
        print(f"  ⚠️  config.yaml not found at {CONFIG_PATH} — using defaults")
        return {}
    except Exception as e:
        print(f"  ⚠️  config.yaml load failed: {e}")
        return {}
