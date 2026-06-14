#!/bin/bash
# Sovereign RAG Setup Script
# Run once to bootstrap RAG on MacBook Pro

set -e

VENV="$HOME/sovereign/Scripts/.venv"
BOLD="\033[1m"
GREEN="\033[32m"
AMBER="\033[33m"
RESET="\033[0m"

echo -e "${BOLD}=== Sovereign RAG Bootstrap ===${RESET}"

# 1. Activate venv
if [ ! -d "$VENV" ]; then
    echo -e "${AMBER}Creating venv at $VENV${RESET}"
    python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"

# 2. Install dependencies
echo -e "${BOLD}Installing Python dependencies...${RESET}"
pip install --upgrade pip -q
pip install chromadb sentence-transformers langchain-community -q
echo -e "${GREEN}✅ chromadb, sentence-transformers, langchain-community installed${RESET}"

# 3. Pull embedding model via Ollama
echo -e "${BOLD}Pulling nomic-embed-text via Ollama...${RESET}"
if ! ollama list | grep -q "nomic-embed-text"; then
    ollama pull nomic-embed-text
    echo -e "${GREEN}✅ nomic-embed-text pulled${RESET}"
else
    echo -e "${GREEN}✅ nomic-embed-text already present${RESET}"
fi

# 4. Create RAG data dir
RAG_DIR="$HOME/sovereign/Data/rag"
mkdir -p "$RAG_DIR/chroma_db"
echo -e "${GREEN}✅ RAG data dir ready: $RAG_DIR${RESET}"

echo ""
echo -e "${BOLD}${GREEN}=== RAG Bootstrap Complete ===${RESET}"
echo -e "Next: python ~/sovereign/Scripts/rag_indexer.py --rebuild"
echo -e "Then: add 'rag_retriever.py' call to pipeline (Node 0.5)"
