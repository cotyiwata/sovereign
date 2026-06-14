# Sovereign Intelligence System

Sovereign is a local-first market intelligence system that ingests financial content, retrieves relevant context via RAG, and generates structured market briefs and trade ideas automatically each morning. Built with Python, ChromaDB, Ollama (gemma3:12b / mistral:7b), nomic-embed-text, and Pydantic v2.

---

## Architecture

Sovereign is organized into four layers — core, nodes, analysis, and tools. Core handles shared configuration, LLM clients, and utilities. Nodes handle individual pipeline steps, each doing one job. Analysis takes node output and produces the final market briefs and trade ideas. Tools handle supporting utilities like scrapers and ingestion helpers. Each layer has a single responsibility, so when something breaks or needs to change, you know exactly where to look — instead of debugging a single monolithic script where everything is tangled together.

---

## Features

- **Automated morning briefs** — pipeline runs on cron, no manual trigger required
- **Trade idea generation** — plays are generated from brief output, keeping analysis grounded in the same context
- **Critic gate validation** — briefs and plays pass through Pydantic v2 schema enforcement before output is accepted
- **RAG memory** — retrieves relevant context from 8,000+ chunks ingested from YouTube transcripts, PDFs, and market sources
- **Multi-model orchestration** — gemma3:12b handles complex synthesis, mistral:7b handles retrieval and validation tasks

---

## Why I Built This

Senior analysts get paid to know what data matters and synthesize it into something actionable. I built Sovereign to automate that — ingest the data, retrieve what's relevant, and generate structured briefs and trade ideas every morning. The goal was to build a system that could help anyone develop an edge in markets using their own sovereign data.

---

## Usage

**Prerequisites:** A machine capable of running a local LLM (Apple Silicon recommended). Install [Python 3.12+](https://python.org) and [Ollama](https://ollama.com), then pull the required models:

```bash
ollama pull gemma3:12b
ollama pull mistral:7b
ollama pull nomic-embed-text
```

**Clone the repo and install dependencies:**

```bash
git clone https://github.com/YOUR_USERNAME/sovereign.git
cd sovereign
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Ingest research into the vector DB:**

```bash
analyst "https://youtube.com/..."    # audio/video → RAG
pdf path/to/report.pdf               # PDF → research library → RAG
reindex                              # incremental index update
reindex --rebuild                    # full wipe + reindex
```

**Query the vault:**

```bash
vault-query "what was the market regime last week"
vault-query "BTC dominance thesis" --type market_brief --n 3
```

**Run the daily pipeline:**

```bash
daily                    # full pipeline — brief + plays + lore + dashboard
daily --skip-lore        # skip lore nodes (faster)
```

**Self-review cycle:**

```bash
audit                    # 7-point system health check
review                   # LLM critique → .issues.json
macro-review             # macro coherence review of latest brief + plays
research                 # AccuracyScientist — deduplicates and triages issues
patchwrite               # generate patch scripts from consolidated issues
```

**Trade log and feedback:**

```bash
tradelog open NVDA long 0.5 135.00
tradelog close NVDA 142.00
feedback "entry was early — need confirmation candle"
feedback --list --category play
```

**Set up cron for automatic morning runs:**

```bash
crontab -e
# Add:
# 0 7 * * * /path/to/sovereign/Scripts/.venv/bin/python3 /path/to/sovereign/Scripts/pipeline.py
```
