"""
core/badges.py — Single badge system.

In v2.7.1 there are 13 separate CSS classes (.conv-high, .conv-med, .dir-long,
.dir-short, .lev-badge, .lev-none, .posture-accumulating, .posture-watching,
.posture-paused, .posture-invalidated, .sig-increase, .sig-hold, .sig-reduce)
that all have IDENTICAL shape — same font, weight, padding, border, border-radius,
letter-spacing — only the color tokens differ.

This module collapses them into:
  - .badge          (shared base shape — defined once)
  - .badge-green    (LONG, INCREASE, ACCUMULATING, HIGH conviction)
  - .badge-amber    (HOLD, WATCHING, MED conviction, warning)
  - .badge-red      (SHORT, REDUCE, INVALIDATED, no leverage)
  - .badge-blue     (LEVERAGE, override tag)
  - .badge-gold     (PAUSED — neutral hold tone)

Plus a render_badge() helper so call sites become declarative:
    render_badge("HIGH CONVICTION", "green")

Net effect: ~80 lines of duplicated CSS → ~30 lines.
"""
from typing import Literal

BadgeColor = Literal["green", "amber", "red", "blue", "gold", "muted"]

# Color token table — single source of truth for badge colors.
# (text, border_rgba, bg_rgba)
_BADGE_COLORS = {
    "green": ("#4ade80", "rgba(74,222,128,0.3)",  "rgba(74,222,128,0.08)"),
    "amber": ("#f5a623", "rgba(245,166,35,0.3)",  "rgba(245,166,35,0.08)"),
    "red":   ("#f87171", "rgba(248,113,113,0.3)", "rgba(248,113,113,0.08)"),
    "blue":  ("#60a5fa", "rgba(96,165,250,0.3)",  "rgba(96,165,250,0.08)"),
    "gold":  ("#a89068", "rgba(168,144,104,0.3)", "rgba(168,144,104,0.08)"),
    "muted": ("#5a5248", "rgba(90,82,72,0.3)",    "rgba(90,82,72,0.08)"),
}

BADGE_CSS = """
.badge {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 3px;
    letter-spacing: 0.08em;
    border: 1px solid;
    display: inline-block;
}
.badge-green { color: #4ade80; border-color: rgba(74,222,128,0.3);  background: rgba(74,222,128,0.08); }
.badge-amber { color: #f5a623; border-color: rgba(245,166,35,0.3);  background: rgba(245,166,35,0.08); }
.badge-red   { color: #f87171; border-color: rgba(248,113,113,0.3); background: rgba(248,113,113,0.08); }
.badge-blue  { color: #60a5fa; border-color: rgba(96,165,250,0.3);  background: rgba(96,165,250,0.08); }
.badge-gold  { color: #a89068; border-color: rgba(168,144,104,0.3); background: rgba(168,144,104,0.08); }
.badge-muted { color: #5a5248; border-color: rgba(90,82,72,0.3);    background: rgba(90,82,72,0.08); }

.badge-sm { font-size: 9px; padding: 2px 6px; letter-spacing: 0.1em; }
.badge-tag { font-size: 9px; letter-spacing: 0.1em; }
"""


def render_badge(label: str, color: BadgeColor, small: bool = False) -> str:
    """Render a badge span. Use small=True for inline warning tags
    (counter-trend, ATR target, R/R adjusted, etc.)."""
    cls = f"badge badge-{color}"
    if small:
        cls += " badge-sm"
    return f'<span class="{cls}">{label}</span>'


# ── Semantic mappers — map domain values to (label, color) ───────────────
def conviction_badge(conviction: str) -> str:
    label = "HIGH CONVICTION" if conviction.upper() == "HIGH" else "MED CONVICTION"
    color: BadgeColor = "green" if conviction.upper() == "HIGH" else "amber"
    return render_badge(label, color)


def direction_badge(direction: str, leverage: str) -> str:
    color: BadgeColor = "green" if direction.upper() == "LONG" else "red"
    return render_badge(f"{direction.upper()} · {leverage}", color)


_POSTURE_COLORS: dict[str, BadgeColor] = {
    "ACCUMULATING": "green",
    "WATCHING":     "amber",
    "PAUSED":       "gold",
    "INVALIDATED":  "red",
}


def posture_badge(posture: str) -> str:
    color = _POSTURE_COLORS.get(posture.upper(), "amber")
    return render_badge(posture.upper(), color)


_SIGNAL_COLORS: dict[str, BadgeColor] = {
    "INCREASE": "green",
    "HOLD":     "amber",
    "REDUCE":   "red",
}


def signal_badge(signal: str) -> str:
    color = _SIGNAL_COLORS.get(signal.upper(), "amber")
    return render_badge(signal.upper(), color)


_REGIME_DISPLAY: dict[str, tuple[str, BadgeColor]] = {
    "STRONG_UPTREND":   ("UPTREND",   "green"),
    "STRONG_DOWNTREND": ("DOWNTREND", "red"),
    "CHOPPY":           ("CHOPPY",    "amber"),
    "CONSOLIDATING":    ("COILED",    "blue"),
}


def regime_badge(regime: str) -> str:
    if regime not in _REGIME_DISPLAY:
        return ""
    label, color = _REGIME_DISPLAY[regime]
    return render_badge(label, color, small=True)


def setup_score_badge(score: int) -> str:
    """Score 0-10. >=7 green, 5-6 muted, <5 amber."""
    from .constants import SCORE_BAND_GREEN, SCORE_BAND_GREY
    if score >= SCORE_BAND_GREEN:
        color: BadgeColor = "green"
    elif score >= SCORE_BAND_GREY:
        color = "muted"
    else:
        color = "amber"
    return render_badge(f"SETUP {score}/10", color, small=True)


_FLAG_LABELS = {
    "SAME_DAY_REENTRY": "⚠ SAME-DAY REENTRY",
    "CONTRARIAN_FEAR":  "⚠ CONTRARIAN FEAR",
    "SHORTING_GREED":   "⚠ SHORTING GREED",
    "POSTURE_DRIFT":    "⚠ POSTURE DRIFT",
    "RATE_HEADWIND":    "⚠ RATE HEADWIND",
    "DOLLAR_STRENGTH":  "⚠ DXY HEADWIND",
}


def flag_badges(flags: list) -> str:
    """Render all macro/coherence gate flags as small amber badges."""
    if not flags:
        return ""
    return " ".join(
        render_badge(_FLAG_LABELS.get(f, f"⚠ {f}"), "amber", small=True)
        for f in flags
    )
