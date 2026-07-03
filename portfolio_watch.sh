#!/bin/bash
# Pins the sovereign venv (has yfinance/requests) regardless of Hermes's
# default interpreter. exec replaces the shell so exit codes/signals pass through.
exec /Users/cotyiwata/sovereign/Scripts/.venv/bin/python3 \
     /Users/cotyiwata/sovereign/Scripts/portfolio_watch.py
