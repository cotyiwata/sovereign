"""
core/prices.py — Canonical price accessor for Sovereign Intelligence System.

Single source of truth for all price lookups from context.json.
Eliminates scattered path-navigation across nodes — every price goes through here.

Lookup chain per ticker:
  1. context["market"]["crypto"][ticker]["price"]   -- BTC/ETH/SOL
  2. context["equities"][group][ticker]["price"]     -- equity groups (primary)
  3. context["market"]["core"][ticker]["price"]      -- legacy fallback
  4. None                                            -- unavailable, log and return

Usage:
    from core.prices import get_price, build_price_map

    px = get_price("BTC", context)          # float or None
    price_map = build_price_map(context)    # {ticker: float} for full universe
"""

from __future__ import annotations
from typing import Optional
from core.constants import CRYPTO_TICKERS


def get_price(ticker: str, context: dict) -> Optional[float]:
    """Return live price for ticker from context. None if unavailable."""
    raw = None

    if ticker in CRYPTO_TICKERS:
        raw = (context.get("market", {})
                      .get("crypto", {})
                      .get(ticker, {})
                      .get("price"))
    else:
        # Primary: walk equity groups
        for group_data in context.get("equities", {}).values():
            if not isinstance(group_data, dict):
                continue
            if ticker in group_data:
                entry = group_data[ticker]
                if isinstance(entry, dict):
                    raw = entry.get("price")
                    break

        # Fallback: legacy market.core path
        if raw is None:
            raw = (context.get("market", {})
                          .get("core", {})
                          .get(ticker, {})
                          .get("price"))

    if raw is None:
        return None

    try:
        return float(str(raw).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        print(f"[prices] WARNING: could not parse price for {ticker}: {raw!r}")
        return None


def build_price_map(context: dict, tickers: Optional[list] = None) -> dict[str, float]:
    """
    Return {ticker: price} for all tickers in TRADING_UNIVERSE (or supplied list).
    Tickers with no price are omitted -- callers should treat absence as PRICE UNAVAILABLE.
    """
    from core.constants import TRADING_UNIVERSE
    targets = tickers if tickers is not None else list(TRADING_UNIVERSE)
    result = {}
    for t in targets:
        px = get_price(t, context)
        if px is not None:
            result[t] = px
        else:
            print(f"[prices] PRICE UNAVAILABLE: {t}")
    return result
