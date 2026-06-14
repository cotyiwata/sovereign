"""
Macro regime gate suite — v1.0
Five amber, non-veto gates. Surface conflicts. Coty decides.

Gates:
  CONTRARIAN_FEAR   — Buying extreme fear HIGH conviction LONG
  SHORTING_GREED    — Shorting extreme greed HIGH conviction SHORT
  POSTURE_DRIFT     — Brief says Hold, play is HIGH conviction
  RATE_HEADWIND     — TLT falling, long AI & SEMIS
  DOLLAR_STRENGTH   — DXY rising, long CRYPTO
"""


# ---------------------------------------------------------------------------
# Individual gate functions
# Each returns a flag string or None.
# ---------------------------------------------------------------------------

def check_contrarian_fear(play: dict, pulse: dict, posture: str) -> str | None:
    """
    Fire when fear/greed < 25 AND HIGH conviction LONG.
    Extreme fear + high conviction long = confirm capitulation before sizing up.
    """
    try:
        fg = float(pulse.get("fear_greed", 50))
    except (TypeError, ValueError):
        return None

    if (
        fg < 25
        and play.get("conviction", "").upper() == "HIGH"
        and play.get("direction", "").upper() == "LONG"
    ):
        return "CONTRARIAN_FEAR"
    return None


def check_shorting_greed(play: dict, pulse: dict, posture: str) -> str | None:
    """
    Fire when fear/greed > 75 AND HIGH conviction SHORT.
    Greed can stay elevated longer than shorts stay solvent.
    """
    try:
        fg = float(pulse.get("fear_greed", 50))
    except (TypeError, ValueError):
        return None

    if (
        fg > 75
        and play.get("conviction", "").upper() == "HIGH"
        and play.get("direction", "").upper() == "SHORT"
    ):
        return "SHORTING_GREED"
    return None


def check_posture_drift(play: dict, pulse: dict, posture: str) -> str | None:
    """
    Fire when daily posture == Hold AND play is HIGH conviction.
    Brief says hold; plays system is going high conviction. One is wrong.
    """
    if (
        str(posture).strip().lower() == "hold"
        and play.get("conviction", "").upper() == "HIGH"
    ):
        return "POSTURE_DRIFT"
    return None


def check_rate_headwind(play: dict, pulse: dict, posture: str) -> str | None:
    """
    Fire when TLT falling > 0.5% AND long AI & SEMIS.
    Rising rates compress growth multiples.
    pulse["tlt_change"] expected as float (pct change, e.g. -0.8 = -0.8%).
    """
    try:
        tlt_change = float(pulse.get("tlt_change", 0))
    except (TypeError, ValueError):
        return None

    if (
        tlt_change < -0.5
        and play.get("section", "").upper() == "AI & SEMIS"
        and play.get("direction", "").upper() == "LONG"
    ):
        return "RATE_HEADWIND"
    return None


def check_dollar_strength(play: dict, pulse: dict, posture: str) -> str | None:
    """
    Fire when DXY rising > 0.3% AND long CRYPTO.
    Strong dollar is a headwind for crypto — not a veto, but flag the conflict.
    pulse["dxy_change"] expected as float (pct change, e.g. +0.5 = +0.5%).
    """
    try:
        dxy_change = float(pulse.get("dxy_change", 0))
    except (TypeError, ValueError):
        return None

    if (
        dxy_change > 0.3
        and play.get("section", "").upper() == "CRYPTO"
        and play.get("direction", "").upper() == "LONG"
    ):
        return "DOLLAR_STRENGTH"
    return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_GATE_FNS = [
    check_contrarian_fear,
    check_shorting_greed,
    check_posture_drift,
    check_rate_headwind,
    check_dollar_strength,
]

_GATE_LABELS = {
    "CONTRARIAN_FEAR": "⚠ CONTRARIAN FEAR",
    "SHORTING_GREED":  "⚠ SHORTING GREED",
    "POSTURE_DRIFT":   "⚠ POSTURE DRIFT",
    "RATE_HEADWIND":   "⚠ RATE HEADWIND",
    "DOLLAR_STRENGTH": "⚠ DXY HEADWIND",
}


def run_all_gates(plays: list, pulse: dict, posture: str) -> list:
    """
    Run all five macro gates against every play.
    Appends flag strings to play["flags"]. Non-destructive — never removes.
    """
    for play in plays:
        if "flags" not in play:
            play["flags"] = []
        for gate_fn in _GATE_FNS:
            flag = gate_fn(play, pulse, posture)
            if flag:
                play["flags"].append(flag)
                print(f"[macro_gates] {flag} → {play.get('ticker')}")
    return plays


def gate_badge_html(flag: str) -> str:
    """Return amber badge HTML for a given flag string."""
    label = _GATE_LABELS.get(flag, f"⚠ {flag}")
    return f'<span class="badge badge-warning">{label}</span>'
