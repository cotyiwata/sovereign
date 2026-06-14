#!/bin/bash
# bootstrap.sh — Sovereign System Setup
# Run once on any new machine to stand up the full stack

set -e

SYSTEM_DIR="/Users/$(whoami)/Library/Mobile Documents/com~apple~CloudDocs/INTELLIGENCE-SYSTEM"
SCRIPTS_DIR="$SYSTEM_DIR/Scripts"

echo "⚡ [BOOTSTRAP] Sovereign System initialization..."

# 1. Create .venv
cd "$SCRIPTS_DIR"
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install --upgrade pip
pip install requests feedparser yfinance anthropic ollama

# 3. Write daily alias to shell profile
ALIAS_LINE="alias daily='cd \"$SCRIPTS_DIR\" && source .venv/bin/activate && python3 run_all_daily.py'"

if ! grep -q "alias daily=" ~/.zshrc; then
    echo "$ALIAS_LINE" >> ~/.zshrc
    echo "✅ [BOOTSTRAP] Alias written to .zshrc"
else
    echo "ℹ️  [BOOTSTRAP] Alias already exists in .zshrc"
fi

# 4. Verify Ollama is running
if ollama list &>/dev/null; then
    echo "✅ [BOOTSTRAP] Ollama detected"
else
    echo "⚠️  [BOOTSTRAP] Ollama not running — start it manually"
fi

echo "✅ [BOOTSTRAP] Complete. Restart terminal or run: source ~/.zshrc"
