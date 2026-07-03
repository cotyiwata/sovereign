#!/usr/bin/env python3
# nodes/n05_brief.py — Node 05: HTML Brief Renderer v3.0
# Sovereign Intelligence System — redesigned brief format

import sys
import json
import re
import yaml
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT as VAULT, load_config
from core.constants import (
    CRYPTO_TICKERS,
    PRICE_SANITY_THRESHOLD_CRYPTO,
    PRICE_SANITY_THRESHOLD_EQUITY,
)
from core.style import SOVEREIGN_CSS

BRIEFS_DIR   = VAULT / "02-Market-Intel" / "Daily-Briefs"
LEVELS_FILE  = VAULT / "Data" / "watched_levels.yaml"
CONTEXT_FILE = VAULT / "Output" / "context.json"
CONFIG = load_config()

_LABELS   = CONFIG.get("section_names", {}).get("stream_a", {})
S_PULSE   = _LABELS.get("pulse",     "PULSE")
S_REGIME  = _LABELS.get("regime",    "REGIME")
S_NEWS    = "NEWS"
S_SCAN    = _LABELS.get("scan",      "SCAN")
S_SYNTH   = _LABELS.get("synthesis", "SYNTHESIS")
S_LEVELS  = _LABELS.get("levels",    "LEVELS")
S_FORWARD    = "FORWARD 72H"
S_PORTFOLIO  = _LABELS.get("portfolio_watch", "PORTFOLIO WATCH")

SCAN_GROUPS = [
    ("CRYPTO",          ["BTC", "ETH", "SOL"]),
    ("AI &amp; SEMIS",  ["NVDA", "TSLA"]),
    ("AI ENERGY NEXUS", ["VST", "CEG", "VRT"]),
    ("MACRO",           ["SPY", "QQQ"]),
]


# ─── CRITIC GATE SEVERITY TIERS ─────────────────────────────
# Hard fails halt delivery — no HTML written, rejection .md created.
# Soft fails render the brief with a FLAG banner (existing behavior).
CRITIC_HARD_FAIL = frozenset({
    "PRICE_LEVEL_SANITY",    # hallucinated price levels
    "FORWARD_72H_LEVELS",    # empty LIKELY EXPRESSION
    "EXECUTABLE_TRADE",      # no executable trade in any scenario
})
CRITIC_SOFT_FAIL = frozenset({
    "EXPRESSION_TARGET",
    "NEWS_EXTERNAL_SOURCE",
    "NEWS_FABRICATED",
    "BEAR_EXPRESSION",
    "SYNTHESIS",
    "FORWARD_72H",
    "CALENDAR",
    "SCAN",
    "NO_EMPTY_SECTIONS",
    "MOST_LIKELY_NO_TRADE_REASON",
    "SETUP_SIGNAL_PRESENT",
    "PRICE_LEVEL_SANITY_UNVERIFIED",
})

TAG_CSS = {
    "CRYPTO": "tag-crypto",
    "MACRO":  "tag-macro",
    "AI":     "tag-ai",
    "ENERGY": "tag-energy",
}

CALENDAR_CSS = """
.calendar-strip{margin:18px 0;border-radius:6px;overflow:hidden;border:1px solid #2a2a2a}
.cal-row{display:grid;grid-template-columns:90px 1fr 80px 80px 90px;align-items:center;padding:8px 14px;border-bottom:1px solid #1e1e1e;font-size:13px}
.cal-row:last-child{border-bottom:none}
.cal-row:nth-child(even){background:#111}
.cal-dt{font-family:'IBM Plex Mono',monospace;color:#6b7280;font-size:11px}
.cal-name{color:#e5e7eb;font-weight:500}
.cal-prior{color:#9ca3af;font-size:12px}
.cal-est{color:#f59e0b;font-family:'IBM Plex Mono',monospace;font-size:12px}
.cal-reaction{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.cal-high{color:#ef4444}.cal-med{color:#f59e0b}.cal-low{color:#6b7280}
.cal-empty{padding:12px 14px;color:#4b5563;font-size:13px;font-style:italic}
"""

FORWARD_CSS = """
.fw-block{margin:10px 0;padding:14px;border-radius:6px;border-left:3px solid #374151;background:#0f0f0f}
.fw-block.fl-likely{border-left-color:#3b82f6}
.fw-block.fl-bull{border-left-color:#10b981}
.fw-block.fl-bear{border-left-color:#ef4444}
.fw-scenario{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.fl-likely .fw-scenario{color:#3b82f6}
.fl-bull .fw-scenario{color:#10b981}
.fl-bear .fw-scenario{color:#ef4444}
.fw-prob{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#9ca3af;margin-bottom:8px}
.fw-meta{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}
.fw-key{font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em}
.fw-path{font-size:13px;color:#d1d5db;line-height:1.5;margin-bottom:6px}
.fw-incomplete{border-left-color:#f97316!important;opacity:.8}
.synth-flagged{background:#2d1a00;border-radius:3px;padding:1px 4px}
.synth-incomplete{border-left:3px solid #f97316;padding-left:10px}
"""


# ─── DATA LOADERS ────────────────────────────────────────────

def load_context():
    try:
        return json.loads(CONTEXT_FILE.read_text())
    except Exception:
        return {}


def load_levels():
    try:
        raw   = LEVELS_FILE.read_text()
        lines = [l for l in raw.split("\n") if not l.startswith("#")]
        return yaml.safe_load("\n".join(lines)) or {}
    except Exception:
        return {}


# ─── PARSERS ─────────────────────────────────────────────────

def get_latest_brief():
    files = sorted([f for f in BRIEFS_DIR.glob("Brief_*.md") if "_REJECTED" not in f.name], reverse=True)
    if not files:
        print("[node5] No brief files found.")
        return None, None
    return files[0], files[0].read_text(encoding="utf-8")


def parse_frontmatter(text):
    data = {}
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for field in ["date", "time", "btc", "fear_greed", "dominant_narrative",
                      "critic", "model", "spy", "macro_regime"]:
            m = re.search(rf"^{field}:\s*(.+)$", fm, re.MULTILINE)
            if m:
                data[field] = m.group(1).strip().strip('"')
    return data


def parse_sections(text):
    sections = {}
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()
    body = re.split(r"^---\s*$", body, maxsplit=2, flags=re.MULTILINE)[-1].strip()
    # Normalize markdown bold labels e.g. **LIKELY:** -> LIKELY:
    body = re.sub(r"\*\*([A-Z][A-Z 0-9]+:)\*\*", r"\1", body)

    headers = [S_PULSE, S_REGIME, S_NEWS, S_SCAN, S_LEVELS, S_SYNTH, S_FORWARD, S_PORTFOLIO]
    keys    = ["pulse", "regime", "news", "scan", "levels", "synthesis", "forward", "portfolio_watch"]

    for i, key in enumerate(keys):
        label     = headers[i]
        nxt       = "|".join(re.escape(h) for h in headers[i+1:]) if i < len(headers)-1 else None
        pattern   = (rf"^#{{0,2}}\s*{re.escape(label)}[^\n]*\n(.*?)(?=^#{{0,2}}\s*(?:{nxt})[^\n]*$|\Z)"
                     if nxt else
                     rf"^#{{0,2}}\s*{re.escape(label)}[^\n]*\n(.*?)$")
        m = re.search(pattern, body, re.DOTALL | re.MULTILINE)
        sections[key] = m.group(1).strip() if m else ""

    # Fallback: LLM omits FORWARD 72H header — find LIKELY: block in raw body
    if not sections.get("forward"):
        m = re.search(r"^(LIKELY:.*)", body, re.DOTALL | re.MULTILINE)
        if m:
            forward_raw = m.group(1).strip()
            sections["forward"] = forward_raw
            # Trim forward content out of whichever section absorbed it
            for k in ("synthesis", "levels"):
                if forward_raw[:40] in sections.get(k, ""):
                    idx = sections[k].find(forward_raw[:40])
                    sections[k] = sections[k][:idx].strip()

    sections["posture"] = ""
    for line in reversed(body.split("\n")):
        if line.strip() in {"Hold", "Watch", "Opportunity"}:
            sections["posture"] = line.strip()
            break
    return sections


def parse_news(news_text):
    featured, quick_items = None, []
    if not news_text:
        return featured, quick_items
    lines = news_text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if re.match(r"FEATURED\s*\|", line, re.IGNORECASE):
            parts = re.split(r"\s*\|\s*", line, maxsplit=2)
            cat  = parts[1].strip().upper() if len(parts) > 1 else "CRYPTO"
            head = parts[2].strip()         if len(parts) > 2 else ""
            body_lines, signal = [], ""
            i += 1
            while i < len(lines):
                l = lines[i].strip()
                if re.match(r"(FEATURED|QUICK)\s*\|", l, re.IGNORECASE):
                    break
                if re.match(r"SIGNAL:\s*", l, re.IGNORECASE):
                    signal = re.sub(r"^SIGNAL:\s*", "", l, flags=re.IGNORECASE).strip()
                elif l:
                    body_lines.append(l)
                i += 1
            featured = {"category": cat, "headline": head,
                        "body": " ".join(body_lines), "signal": signal}
            continue
        if re.match(r"QUICK\s*\|", line, re.IGNORECASE):
            parts = re.split(r"\s*\|\s*", line, maxsplit=2)
            cat  = parts[1].strip().upper() if len(parts) > 1 else "CRYPTO"
            head = parts[2].strip()         if len(parts) > 2 else ""
            body_lines = []
            i += 1
            while i < len(lines):
                l = lines[i].strip()
                if re.match(r"(FEATURED|QUICK)\s*\|", l, re.IGNORECASE):
                    break
                if l:
                    body_lines.append(l)
                i += 1
            quick_items.append({"category": cat, "headline": head,
                                 "body": " ".join(body_lines)})
            continue
        i += 1
    return featured, quick_items


# ─── LEVEL HELPERS ───────────────────────────────────────────

def _direction(data):
    cur, ma20 = data.get("current", 0), data.get("ma20", 0)
    if not cur or not ma20:
        return "flat", "&#9654; FLAT"
    r = cur / ma20
    if r > 1.02: return "up",   "&#9650; TREND"
    if r < 0.98: return "down", "&#9660; WEAK"
    return "flat", "&#9654; AT MA20"


def _flags(data):
    cur, res = data.get("current", 0), data.get("resistance", 0)
    sup, atr = data.get("support", 0), data.get("atr14", 1) or 1
    ma200    = data.get("ma200", 0)
    out = ""
    if res and cur and (res - cur) <= atr:
        out += '<span class="flag flag-amber">AT R</span>'
    if sup and cur and (cur - sup) <= atr:
        out += '<span class="flag flag-amber">AT S</span>'
    if ma200 and cur and cur < ma200 * 0.97:
        out += '<span class="flag flag-red">&lt;MA200</span>'
    return out


def _group_read(ticker_data_list):
    if not ticker_data_list:
        return ""
    above  = sum(1 for _, d in ticker_data_list if d.get("current", 0) > d.get("ma20", 0))
    total  = len(ticker_data_list)
    at_res = [t for t, d in ticker_data_list
              if d.get("resistance") and d.get("current") and
              (d["resistance"] - d["current"]) <= (d.get("atr14", 1) or 1)]
    read = f"{above}/{total} above MA20"
    if at_res:
        read += f" · {chr(44).join(at_res)} at resistance"
    return read


# ─── RENDER HELPERS ──────────────────────────────────────────

def render_text_block(text):
    if not text:
        return "<p>&#8212;</p>"
    html = ""
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^([A-Z][A-Z &]+:)", r"<span class='label'>\1</span>", line)
        line = line.replace("&#x2192;", "<span class='arrow'>&#x2192;</span>")
        line = line.replace("&#9650;", "<span class='up'>&#9650;</span>")
        line = line.replace("&#9660;", "<span class='down'>&#9660;</span>")
        if line.startswith("•"):
            html += f"<li>{line[1:].strip()}</li>"
        else:
            html += f"<p>{line}</p>"
    return html


def render_stat_bar(fm, ctx):
    btc_price = fm.get("btc", "&#8212;")
    btc_data  = ctx.get("market", {}).get("crypto", {}).get("BTC", {})
    btc_chg   = btc_data.get("change_24h", "&#8212;")
    spy_data  = ctx.get("market", {}).get("core", {}).get("SPY", {})
    spy_price = spy_data.get("price", fm.get("spy", "&#8212;"))
    spy_chg   = spy_data.get("change_24h", "&#8212;")
    gold_sig  = ctx.get("market", {}).get("core", {}).get("Gold", {}).get("signal", "&#8212;")
    dxy_sig   = ctx.get("market", {}).get("core", {}).get("DXY",  {}).get("signal", "&#8212;")
    fg        = fm.get("fear_greed", "&#8212;")

    def chg_class(v):
        v_str = str(v)
        if "▼" in v_str:
            return "down"
        if "▲" in v_str:
            return "up"
        try:
            return "up" if float(re.sub(r"[^0-9.\-]", "", v_str)) > 0 else "down"
        except Exception:
            return "neutral"

    fg_class = "fear"
    try:
        fgv = int(re.sub(r"[^0-9]", "", fg.split("(")[0].strip()))
        if fgv >= 55:   fg_class = "up"
        elif fgv >= 35: fg_class = "neutral"
    except Exception:
        pass

    return (
        '<div class="stat-bar">' +
        '<div class="stat stat-primary">' +
        '<div class="stat-label">BTC</div>' +
        f'<div class="stat-value-primary">{btc_price}</div>' +
        f'<div class="stat-change {chg_class(btc_chg)}">{btc_chg}</div>' +
        '</div><div class="stat-divider-v"></div>' +
        '<div class="stat stat-primary">' +
        '<div class="stat-label">SPY</div>' +
        f'<div class="stat-value-primary">{spy_price}</div>' +
        f'<div class="stat-change {chg_class(spy_chg)}">{spy_chg}</div>' +
        '</div><div class="stat-divider-v"></div>' +
        '<div class="stat stat-secondary">' +
        '<div class="stat-label">Gold</div>' +
        f'<div class="stat-value-secondary">{gold_sig}</div></div>' +
        '<div class="stat stat-secondary">' +
        '<div class="stat-label">DXY</div>' +
        f'<div class="stat-value-secondary">{dxy_sig}</div></div>' +
        '<div class="stat stat-secondary">' +
        '<div class="stat-label">Fear &amp; Greed</div>' +
        f'<div class="stat-value-secondary {fg_class}">{fg}</div></div>' +
        '</div>'
    )


def render_catalyst_strip(ctx):
    upcoming = ctx.get("catalysts", {}).get("upcoming", [])
    e_today  = ctx.get("earnings", {}).get("day_of", [])
    e_coming = ctx.get("earnings", {}).get("upcoming", []) or []
    pills, action = [], ""
    for e in upcoming:
        days, label = e.get("days_away", 99), e.get("event", "Event")
        if days == 0:
            pills.append(f'<span class="cat-pill cat-hot">{label} TODAY</span>')
            action = "High-impact event today &#8212; reduce new entries"
        elif days <= 2:
            pills.append(f'<span class="cat-pill cat-hot">{label} +{days}d</span>')
            if not action:
                action = f"Reduce new entries before {label} print"
        elif days <= 7:
            pills.append(f'<span class="cat-pill cat-warm">{label} +{days}d</span>')
        else:
            pills.append(f'<span class="cat-pill cat-cool">{label} +{days}d</span>')
    for e in e_today:
        pills.append(f'<span class="cat-pill cat-hot">EARNINGS: {e.get("ticker","?")}</span>')
    for e in e_coming[:3]:
        days, tick = e.get("days_away", 99), e.get("ticker", "?")
        cls = "cat-warm" if days <= 7 else "cat-cool"
        pills.append(f'<span class="cat-pill {cls}">{tick} Earnings +{days}d</span>')
    if not pills:
        return ""
    note_html = f'<div class="catalyst-note">{action}</div>' if action else ""
    return ('<div class="catalyst-strip">' +
            '<div class="catalyst-label">Catalysts</div>' +
            f'<div class="catalyst-pills">{"".join(pills)}</div>' +
            f'{note_html}</div>')


def render_pulse_callouts(fm, ctx, levels):
    narrative = fm.get("dominant_narrative", "&#8212;")
    regime    = ctx.get("market", {}).get("macro_regime", {})
    macro_str = (f"Fed: {regime.get('fed','?')}&nbsp;&#183;&nbsp;Inflation: {regime.get('inflation','?')}"
                 if regime else fm.get("macro_regime", "&#8212;"))
    btc_data = ctx.get("market", {}).get("crypto", {}).get("BTC", {})
    btc_chg  = btc_data.get("change_24h", "&#8212;")
    btc_sup  = levels.get("BTC", {}).get("support", 0)
    btc_note = f"Above ${btc_sup:,.0f} support" if btc_sup else ""
    try:
        chg_class = "up" if float(re.sub(r"[^0-9.\-]", "", str(btc_chg))) > 0 else "down"
    except Exception:
        chg_class = "neutral"
    return ('<div class="callout-row">' +
            '<div class="callout">' +
            '<div class="callout-label">Dominant Narrative</div>' +
            f'<div class="callout-val" style="font-size:12px;line-height:1.5;margin-top:2px">{narrative}</div></div>' +
            '<div class="callout">' +
            '<div class="callout-label">Macro Regime</div>' +
            f'<div class="callout-val">{macro_str}</div></div>' +
            '<div class="callout">' +
            '<div class="callout-label">BTC 24h</div>' +
            f'<div class="callout-val {chg_class}">{btc_chg}</div>' +
            f'<div class="callout-note">{btc_note}</div></div>' +
            '</div>')


def render_news(news_text):
    featured, quick_items = parse_news(news_text)
    if not featured and not quick_items:
        return '<p style="color:#5a5248;font-style:italic">No news signals captured today.</p>'
    html = ""
    if featured:
        tag_css  = TAG_CSS.get(featured["category"], "tag-crypto")
        body_html = "".join(f"<p>{p.strip()}</p>"
                            for p in re.split(r"(?<=[.!?])\s+", featured["body"]) if p.strip())
        sig_html  = ('<div class="news-signal">' +
                     '<div class="news-signal-label">Signal</div>' +
                     f'<div class="news-signal-text">{featured["signal"]}</div></div>'
                     ) if featured.get("signal") else ""
        html += ('<div class="news-featured">' +
                 f'<div class="news-featured-tag">Featured &middot; {featured["category"]}</div>' +
                 f'<div class="news-featured-headline">{featured["headline"]}</div>' +
                 f'<div class="news-featured-body">{body_html}</div>' +
                 f'{sig_html}</div>')
    if quick_items:
        items_html = ""
        for item in quick_items:
            tag_css = TAG_CSS.get(item["category"], "tag-crypto")
            items_html += ('<div class="news-item">' +
                           f'<div class="news-item-tag {tag_css}">{item["category"]}</div>' +
                           '<div class="news-item-content">' +
                           f'<div class="news-item-headline">{item["headline"]}</div>' +
                           f'<div class="news-item-body">{item["body"]}</div>' +
                           '</div></div>')
        html += f'<div class="news-items">{items_html}</div>'
    return html


def render_scan_groups(levels):
    if not levels:
        return "<p>Level data unavailable.</p>"
    html = ""
    for group_name, tickers in SCAN_GROUPS:
        ticker_data = [(t, levels[t]) for t in tickers if t in levels]
        if not ticker_data:
            continue
        read = _group_read(ticker_data)
        rows = ""
        for ticker, data in ticker_data:
            cur       = data.get("current", 0)
            res       = data.get("resistance", 0)
            sup       = data.get("support", 0)
            dir_cls, dir_lbl = _direction(data)
            flags     = _flags(data)
            level_str = ""
            if res: level_str += f"R: ${res:,.0f}"
            if sup: level_str += f"&nbsp;|&nbsp; S: ${sup:,.0f}"
            if flags: level_str += f" {flags}"
            rows += (f'<tr>' +
                     f'<td class="td-ticker">{ticker}</td>' +
                     f'<td class="td-price">${cur:,.2f}</td>' +
                     f'<td class="td-dir"><span class="dir-{dir_cls}">{dir_lbl}</span></td>' +
                     f'<td class="td-level">{level_str}</td></tr>')
        html += ('<div class="scan-group">' +
                 '<div class="scan-group-header">' +
                 f'<div class="scan-group-label">{group_name}</div>' +
                 f'<div class="scan-group-read">{read}</div></div>' +
                 f'<table class="scan-table">{rows}</table></div>')
    return html


def render_synthesis(synthesis_text):
    """P4d: Render synthesis. Flag sentences missing price/% data.
    Handles both raw-sentence and labeled-field formats.
    Labeled format: WORD(S): value on separate lines (Session 82+ output).
    POSTURE label exempt from digit requirement — structurally qualitative.
    """
    if not synthesis_text:
        return "<p>&#8212;</p>"

    _LABEL_LINE   = re.compile(r"^[A-Z][A-Z ]{1,30}:\s*\S", re.MULTILINE)
    _DIGIT_EXEMPT = re.compile(r"^(?:POSTURE|SETUP SIGNAL)\s*:", re.IGNORECASE)

    if _LABEL_LINE.search(synthesis_text.strip()):
        # Labeled format — one field per non-empty line
        fields = [ln.strip() for ln in synthesis_text.strip().splitlines() if ln.strip()]
        no_digit = [
            i for i, s in enumerate(fields)
            if not re.search(r"\d", s) and not _DIGIT_EXEMPT.match(s)
        ]
        separator = "<br>"
    else:
        # Sentence format — original logic
        fields    = [s.strip() for s in re.split(r"(?<=[.!?])\s+", synthesis_text.strip()) if s.strip()]
        no_digit  = [i for i, s in enumerate(fields) if not re.search(r"\d", s)]
        separator = " "

    parts = []
    for i, s in enumerate(fields):
        if i in no_digit:
            parts.append(f'<span class="synth-flagged">{s}</span>')
        else:
            parts.append(s)
    cls = " synth-incomplete" if no_digit else ""
    return f'<div class="synthesis-text{cls}">' + separator.join(parts) + "</div>"


def render_portfolio_watch(portfolio_text):
    """Render PORTFOLIO WATCH section. Plain text block with thesis-validation framing."""
    if not portfolio_text or not portfolio_text.strip():
        return '<p style="color:#5a5248;font-style:italic">Portfolio Watch — no analysis generated.</p>'
    return render_text_block(portfolio_text)


def render_forward_rows(forward_text):
    """P4c: Parse structured FORWARD 72H blocks. Validate required fields. Log INCOMPLETE."""
    REQUIRED = ["SCENARIO", "PROBABILITY", "TRIGGER", "PATH", "EXPRESSION", "INVALIDATION"]
    LABELS   = ["LIKELY", "BULL", "BEAR"]
    CSS_MAP  = {"LIKELY": "fl-likely", "BULL": "fl-bull", "BEAR": "fl-bear"}
    DISPLAY  = {"LIKELY": "Most Likely", "BULL": "Bull Case", "BEAR": "Bear Case"}

    if not forward_text:
        return "<p>&#8212;</p>"

    # Normalize markdown bold labels (**LIKELY:** → LIKELY:) before structured detection
    forward_text = re.sub(r"\*\*([A-Z][A-Z 0-9]+:)\*\*", r"\1", forward_text)

    is_structured = "SCENARIO:" in forward_text and any(
        f"{lbl}:" in forward_text for lbl in LABELS
    )

    if not is_structured:
        likely = re.search(r"LIKELY:\s*(.+?)(?=BULL:|BEAR:|$)", forward_text, re.DOTALL | re.IGNORECASE)
        bull   = re.search(r"BULL:\s*(.+?)(?=LIKELY:|BEAR:|$)", forward_text, re.DOTALL | re.IGNORECASE)
        bear   = re.search(r"BEAR:\s*(.+?)(?=LIKELY:|BULL:|$)", forward_text, re.DOTALL | re.IGNORECASE)
        if not (likely or bull or bear):
            return render_text_block(forward_text)
        rows = ""
        if likely:
            rows += ('<div class="forward-row">'
                     '<div class="forward-label fl-likely">Most Likely</div>'
                     f'<div class="forward-text">{likely.group(1).strip()}</div></div>')
        if bull:
            rows += ('<div class="forward-row">'
                     '<div class="forward-label fl-bull">Bull case</div>'
                     f'<div class="forward-text">{bull.group(1).strip()}</div></div>')
        if bear:
            rows += ('<div class="forward-row">'
                     '<div class="forward-label fl-bear">Bear case</div>'
                     f'<div class="forward-text">{bear.group(1).strip()}</div></div>')
        return f'<div class="forward-rows">{rows}</div>'

    def parse_block(label, text):
        others   = [lbl for lbl in LABELS if lbl != label]
        next_pat = "|".join(rf"^{o}:" for o in others)
        m = re.search(
            rf"^{label}:\s*\n(.*?)(?={next_pat}|\Z)",
            text, re.DOTALL | re.MULTILINE | re.IGNORECASE
        )
        if not m:
            return None
        block_text = m.group(1)
        fields = {}
        for field in REQUIRED:
            fm = re.search(
                rf"^{field}:\s*(.+?)(?=^[A-Z]{{3,}}:|\Z)",
                block_text, re.DOTALL | re.MULTILINE
            )
            fields[field] = fm.group(1).strip() if fm else ""
        return fields

    blocks, incomplete = {}, []
    for lbl in LABELS:
        b = parse_block(lbl, forward_text)
        if b is None:
            incomplete.append(f"{lbl}: block missing")
            continue
        missing = [f for f in REQUIRED if not b.get(f)]
        if missing:
            incomplete.append(f"{lbl}: missing {missing}")
        blocks[lbl] = b

    if incomplete:
        print(f"[node5] FORWARD 72H INCOMPLETE: {incomplete}")

    rows = ""
    for lbl in LABELS:
        if lbl not in blocks:
            continue
        b            = blocks[lbl]
        css          = CSS_MAP[lbl]
        dsp          = DISPLAY[lbl]
        prob         = b.get("PROBABILITY",  "")
        scenario     = b.get("SCENARIO",     "")
        trigger      = b.get("TRIGGER",      "")
        expression   = b.get("EXPRESSION",   "")
        invalidation = b.get("INVALIDATION", "")
        path         = b.get("PATH",         "")
        prob_html    = f' <span class="fw-prob">{prob}</span>' if prob else ""
        rows += (
            f'<div class="forward-row">'
            f'<div class="forward-label {css}">{dsp}{prob_html}</div>'
            f'<div class="forward-fields">'
            f'<div class="fw-scenario">{scenario}</div>'
            f'<div class="fw-meta">'
            f'<span class="fw-key">Trigger</span> {trigger} &nbsp;&middot;&nbsp;'
            f'<span class="fw-key">Expression</span> {expression} &nbsp;&middot;&nbsp;'
            f'<span class="fw-key">Invalidation</span> {invalidation}'
            f'</div>'
            f'<div class="fw-path">{path}</div>'
            f'</div></div>'
        )

    status_cls = " fw-incomplete" if incomplete else ""
    return f'<div class="forward-rows{status_cls}">{rows}</div>'


def render_posture_close(posture_word, forward_text):
    """P4e: [WORD] — [today meaning] — [level that changes it]"""
    pw_lower = posture_word.lower() if posture_word else "none"
    meaning      = ""
    change_level = ""

    if forward_text and "SCENARIO:" in forward_text:
        m = re.search(r"LIKELY:.*?SCENARIO:\s*(.+?)(?=\n[A-Z][A-Z])", forward_text, re.DOTALL)
        if m:
            meaning = m.group(1).strip().split("\n")[0].strip()
        if posture_word == "Opportunity":
            t = re.search(r"BEAR:.*?TRIGGER:\s*(.+?)(?=\n[A-Z][A-Z])", forward_text, re.DOTALL)
        else:
            t = re.search(r"BULL:.*?TRIGGER:\s*(.+?)(?=\n[A-Z][A-Z])", forward_text, re.DOTALL)
        if t:
            change_level = t.group(1).strip().split("\n")[0].strip()
    else:
        likely_m = re.search(r"LIKELY:\s*(.+?)(?=BULL:|BEAR:|$)", forward_text or "", re.DOTALL | re.IGNORECASE)
        bear_m   = re.search(r"BEAR:\s*(.+?)(?=LIKELY:|BULL:|$)",  forward_text or "", re.DOTALL | re.IGNORECASE)
        if likely_m:
            meaning = likely_m.group(1).strip()
        if bear_m:
            change_level = bear_m.group(1).strip()

    if not meaning:
        meaning = "Monitor current positions within established risk parameters."
    if not change_level:
        change_level = "No specific invalidation threshold set."

    return (
        '<div class="posture-close">'
        f'<span class="posture-badge pb-{pw_lower}">{posture_word or "&#8212;"}</span>'
        '<span class="posture-sep"> &#8212; </span>'
        f'<span class="posture-meaning">{meaning}</span>'
        '<span class="posture-sep"> &#8212; </span>'
        f'<span class="posture-change">{change_level}</span>'
        '</div>'
    )


def render_calendar_alerts(ctx: dict) -> str:
    """P4a: Render 72h calendar strip. Source: context["calendar_alerts"]."""
    alerts = ctx.get("calendar_alerts", [])
    if not alerts:
        return '<div class="calendar-strip no-events">&#128197;  NO EVENTS IN 72H WINDOW</div>'
    rows = []
    for ev in alerts:
        date_  = ev.get("date", "")
        time_  = str(ev.get("time", "")).replace(" ET", "").strip()
        name   = ev.get("name", "?")
        prior  = ev.get("prior", "&#8212;")
        est    = ev.get("est",   "&#8212;")
        react  = ev.get("reaction", "")
        react_html = f'<span class="cal-reaction">{react}</span>' if react else ""
        rows.append(
            '<div class="cal-row">'
            f'<span class="cal-dt">{date_} {time_}</span>'
            '<span class="cal-sep"> | </span>'
            f'<span class="cal-name">{name}</span>'
            '<span class="cal-sep"> | </span>'
            f'<span class="cal-prior">Prior: {prior}</span>'
            f'<span class="cal-est"> &middot; Est: {est}</span>'
            f'{react_html}'
            '</div>'
        )
    return '<div class="calendar-strip">' + "".join(rows) + '</div>'


def run_critic_gate(fm, sections, ctx):
    """P4f: Completeness gate. FAIL logged + written to audit_history.json."""
    import json as _json
    from datetime import datetime as _dt

    checks = {}

    # 1. FORWARD 72H: all 3 scenario blocks present + at least one price level
    fwd = sections.get("forward", "")
    # Normalize markdown bold labels in forward block
    fwd = re.sub(r"\*\*([A-Z][A-Z 0-9]+:)\*\*", r"\1", fwd)
    checks["FORWARD_72H"] = (
        bool(re.search(r"LIKELY:", fwd, re.IGNORECASE)) and
        bool(re.search(r"BULL:",   fwd, re.IGNORECASE)) and
        bool(re.search(r"BEAR:",   fwd, re.IGNORECASE)) and
        bool(re.search(r"\d",      fwd))
    )

    # 2. SYNTHESIS: all 5 labeled fields must be present by label name
    synth = sections.get("synthesis", "")
    _synth_labels = ["CONFLUENCE", "ROTATION", "ASYMMETRIC SETUP", "POSTURE DERIVATION", "POSTURE"]
    checks["SYNTHESIS"] = all(label in synth.upper() for label in _synth_labels)

    # 3. CALENDAR: key present in ctx (even empty list = rendered)
    checks["CALENDAR"] = "calendar_alerts" in ctx

    # 4. SCAN: no missing data markers
    scan = sections.get("scan", "")
    checks["SCAN"] = "MISSING DATA" not in scan and "NO DATA" not in scan

    # 5. BEAR expression must not contain LONG
    bear_block = re.search(r'BEAR:(.*)', fwd, re.DOTALL | re.IGNORECASE)
    bear_text  = bear_block.group(1) if bear_block else ""
    bear_expr  = re.search(r'EXPRESSION:(.*?)(?=INVALIDATION:|$)', bear_text, re.DOTALL | re.IGNORECASE)
    bear_expr_text = bear_expr.group(1).strip() if bear_expr else ""
    checks["BEAR_EXPRESSION"] = "LONG" not in bear_expr_text.upper()

    # 6. EXECUTABLE_TRADE: at least one EXPRESSION must not be NO TRADE
    def _expr_is_no_trade(block_text):
        m = re.search(r'EXPRESSION:(.*?)(?=INVALIDATION:|$)', block_text, re.DOTALL | re.IGNORECASE)
        if not m:
            return True
        t = m.group(1).strip().upper()
        return t.startswith('NO TRADE') or t == ''
    _likely_b = re.search(r'LIKELY:(.*?)(?=BULL:|$)',   fwd, re.DOTALL | re.IGNORECASE)
    _bull_b   = re.search(r'BULL:(.*?)(?=BEAR:|$)',     fwd, re.DOTALL | re.IGNORECASE)
    _bear_b   = re.search(r'BEAR:(.*)',                 fwd, re.DOTALL | re.IGNORECASE)
    _lt  = _likely_b.group(1) if _likely_b else ''
    _bt  = _bull_b.group(1)   if _bull_b   else ''
    _bt2 = _bear_b.group(1)   if _bear_b   else ''
    checks['EXECUTABLE_TRADE'] = not (_expr_is_no_trade(_lt) and _expr_is_no_trade(_bt) and _expr_is_no_trade(_bt2))

    # 7. NO_EMPTY_SECTIONS: PULSE, REGIME, SYNTHESIS must not be dash-only or empty
    def _is_dash_only(text):
        return bool(re.match(r'^[\s\u2014\-]+$', text.strip())) or text.strip() in ('\u2014', '-', '')
    checks['NO_EMPTY_SECTIONS'] = not any(
        _is_dash_only(sections.get(k, ''))
        for k in ('pulse', 'regime', 'synthesis')
    )

    # 8. NEWS_EXTERNAL_SOURCE: SOURCE fields in NEWS must not cite Chronicle
    _news_sources = re.findall(r'SOURCE:\s*(.+)', sections.get("news", ""), re.IGNORECASE)
    checks["NEWS_EXTERNAL_SOURCE"] = not any("chronicle" in s.lower() for s in _news_sources)

    # 9. MOST_LIKELY_NO_TRADE_REASON: NO TRADE in LIKELY EXPRESSION must name blocking condition
    _likely_expr_m = re.search(r'EXPRESSION:(.*?)(?=INVALIDATION:|$)', _lt, re.DOTALL | re.IGNORECASE)
    _likely_expr_t = _likely_expr_m.group(1).strip() if _likely_expr_m else ""
    _no_trade_reasons = ["SETUP SCORE BELOW THRESHOLD", "R/R BELOW MINIMUM",
                         "NO STRUCTURAL LEVEL AVAILABLE", "POSTURE CONFLICT"]
    if "NO TRADE" in _likely_expr_t.upper():
        checks["MOST_LIKELY_NO_TRADE_REASON"] = any(r in _likely_expr_t.upper() for r in _no_trade_reasons)
    else:
        checks["MOST_LIKELY_NO_TRADE_REASON"] = True

    # 10. SETUP_SIGNAL_PRESENT: SYNTHESIS must contain a valid SETUP SIGNAL field
    _ss_m = re.search(r'SETUP SIGNAL:\s*(.+)', synth, re.IGNORECASE)
    if not _ss_m:
        checks["SETUP_SIGNAL_PRESENT"] = False
    else:
        _ss_v = _ss_m.group(1).strip()
        checks["SETUP_SIGNAL_PRESENT"] = (
            bool(_ss_v) and _ss_v not in ("-", "\u2014")
        )

    # 12. PRICE_LEVEL_SANITY: levels in FORWARD 72H, LEVELS, SYNTHESIS must not
    #     deviate beyond asset-class ceiling from current price in context.json
    #     Crypto (BTC/ETH/SOL): 40% — can legitimately target multi-month moves
    #     Equities/ETFs: 20% — wide enough to catch confabulation, not legit targets
    try:
        _ctx_px = {}
        for _t in ["BTC", "ETH", "SOL"]:
            _raw = ctx.get("market", {}).get("crypto", {}).get(_t, {}).get("price")
            if _raw:
                try: _ctx_px[_t] = float(str(_raw).replace(",", "").replace("$", "").strip())
                except: pass
        # Build flat equity price lookup from context["equities"][group][ticker]
        _eq_flat_gate = {}
        for _grp in ctx.get("equities", {}).values():
            if isinstance(_grp, dict):
                for _et, _ed in _grp.items():
                    if isinstance(_ed, dict) and _ed.get("price"):
                        try:
                            _eq_flat_gate[_et] = float(
                                str(_ed["price"]).replace(",", "").replace("$", "").strip()
                            )
                        except: pass
        for _t in ["SPY", "QQQ", "NVDA", "TSLA", "VST", "CEG", "VRT", "NNE", "SMR", "WATT"]:
            _raw = ctx.get("market", {}).get("core", {}).get(_t, {}).get("price")
            if not _raw:
                _raw_f = _eq_flat_gate.get(_t)
                if _raw_f is not None:
                    _ctx_px[_t] = _raw_f
                    continue
            if _raw:
                try: _ctx_px[_t] = float(str(_raw).replace(",", "").replace("$", "").strip())
                except: pass
        for _t, _label in [("GLD", "Gold"), ("TLT", "TLT"), ("Oil", "Oil")]:
            _asset = ctx.get("market", {}).get("core", {}).get(_label, {})
            _raw = _asset.get("price")
            if _raw:
                try: _ctx_px[_t] = float(_raw)
                except: pass
        _sane = True
        _scan_text = fwd + "\n" + synth + "\n" + sections.get("levels", "")
        for _line in _scan_text.split("\n"):
            _lu = _line.upper()
            # Skip lines with multiple tickers — cross-ticker numbers cause false fails
            _tickers_on_line = [_t for _t in _ctx_px if re.search(r'\b' + _t + r'\b', _lu)]
            if len(_tickers_on_line) > 1:
                continue
            # Normalize index names to prevent "500" in "S&P 500" parsing as price
            _line_norm = re.sub(r'S&P\s*500', 'SP500INDEX', _line, flags=re.IGNORECASE)
            for _t, _actual in _ctx_px.items():
                if re.search(r'\b' + _t + r'\b', _lu):
                    # Strip commas before scanning — prevents 77,357 splitting into 357
                    _stripped = _line_norm.replace(",", "")
                    for _n in re.findall(r'\b(\d{3,}(?:\.\d+)?)\b', _stripped):
                        _val = float(_n)
                        _thresh = PRICE_SANITY_THRESHOLD_CRYPTO if _t in CRYPTO_TICKERS else PRICE_SANITY_THRESHOLD_EQUITY
                        if _val >= 100 and abs(_val - _actual) / _actual > _thresh:
                            _sane = False
        checks["PRICE_LEVEL_SANITY"] = _sane

        # Soft-flag: ASYMMETRIC SETUP names a ticker with no price anchor
        _asym_m = re.search(r'ASYMMETRIC SETUP:\s*([A-Z]{2,6})', synth, re.IGNORECASE)
        if _asym_m:
            _asym_ticker = _asym_m.group(1).upper()
            if _asym_ticker not in _ctx_px:
                checks["PRICE_LEVEL_SANITY_UNVERIFIED"] = False
                print(f"[node5] PRICE_LEVEL_SANITY: {_asym_ticker} in ASYMMETRIC SETUP has no price anchor — unverifiable")
            else:
                checks["PRICE_LEVEL_SANITY_UNVERIFIED"] = True
    except Exception:
        checks["PRICE_LEVEL_SANITY"] = True

    # 13. FORWARD_72H_LEVELS: LIKELY EXPRESSION must not be empty or a bare dash
    _likely_b13 = re.search(r'LIKELY:(.*?)(?=BULL:|$)', fwd, re.DOTALL | re.IGNORECASE)
    _likely_t13 = _likely_b13.group(1) if _likely_b13 else ""
    _likely_expr13 = re.search(r'EXPRESSION:(.*?)(?=INVALIDATION:|$)', _likely_t13, re.DOTALL | re.IGNORECASE)
    _likely_expr_val = _likely_expr13.group(1).strip() if _likely_expr13 else ""
    checks["FORWARD_72H_LEVELS"] = (
        bool(_likely_expr_val) and
        _likely_expr_val not in ("-", "\u2014", "\u2013") and
        len(_likely_expr_val) > 5
    )

    # Gate 14 (BEST_SETUP_UNIVERSE) retired Session A — replaced by SETUP_SIGNAL_PRESENT

    # 15. NEWS_FABRICATED: >1 SOURCE field with NO EXTERNAL SIGNAL = LLM invented news
    _news_src_lines = re.findall(r'SOURCE:\s*(.+)', sections.get("news", ""), re.IGNORECASE)
    _fab_count = sum(1 for s in _news_src_lines if "NO EXTERNAL SIGNAL" in s.upper())
    checks["NEWS_FABRICATED"] = _fab_count <= 1

    # Gate 16 (BEST_SETUP_RR) retired Session A — R/R validation moves to Plays in Session C

    passed   = all(checks.values())
    status   = "PASS" if passed else "FAIL"
    failures = [k for k, v in checks.items() if not v]
    hard_failures = [k for k in failures if k in CRITIC_HARD_FAIL]

    if not passed:
        import logging as _log
        _log.warning("[node5] CRITIC GATE %s: %s", status, failures)
        try:
            audit_path = VAULT / "Data" / "audit_history.json"
            history = []
            if audit_path.exists():
                history = _json.loads(audit_path.read_text())
            history.append({
                "date":     _dt.now().strftime("%Y-%m-%d"),
                "source":   "critic_gate",
                "status":   status,
                "failures": failures,
                "checks":   checks,
            })
            audit_path.write_text(_json.dumps(history, indent=2))
        except Exception as e:
            print(f"[node5] audit_history write failed: {e}")

    checks_html = "".join(
        '<span class="gate-check ' + ('gate-pass' if v else 'gate-fail') + '">' + k + '</span>'
        for k, v in checks.items()
    )
    return status, checks_html, hard_failures



def _write_rejection(brief_path, gate_status, hard_failures, raw_text):
    """Write rejection notice to vault and log. Called when hard critic fails detected."""
    import logging as _rlog
    from datetime import datetime as _rdt
    fail_str = " | ".join(hard_failures)
    _rlog.warning("[node5] BRIEF REJECTED — hard fails: %s", fail_str)

    log_path = VAULT / "logs" / "sovereign_daily.log"
    try:
        ts = _rdt.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as _f:
            _f.write(f"{ts} BRIEF_REJECTED — {fail_str}\n")
    except Exception as _e:
        print(f"[node5] Log write failed: {_e}")

    rej_stem = brief_path.stem + "_REJECTED"
    rej_path = BRIEFS_DIR / (rej_stem + ".md")
    ts_str   = _rdt.now().strftime("%Y-%m-%d %H:%M")
    content  = (
        f"---\ndate: {ts_str}\ntype: brief_rejected\nstatus: REJECTED\n"
        f"hard_failures: {hard_failures}\n---\n\n"
        f"# BRIEF REJECTED — {ts_str}\n\n"
        f"**Hard failures:** {fail_str}\n\n"
        f"Re-run after diagnosing: `daily --only \"HTML Renderer\"`\n\n---\n\n"
        f"## RAW LLM OUTPUT (for manual review)\n\n{raw_text}\n"
    )
    try:
        rej_path.write_text(content, encoding="utf-8")
        print(f"[node5] Rejection notice → {rej_path.name}")
    except Exception as _e:
        print(f"[node5] Rejection notice write failed: {_e}")

    print(f"\u274c [NODE 5] BRIEF REJECTED — {fail_str}")
    print(f"   Diagnose and re-run: daily --only \"HTML Renderer\"")

def render_html(fm, sections, levels, ctx, source_filename, gate_status, gate_checks_html):
    date    = fm.get("date", "&#8212;")
    time_str= fm.get("time", "&#8212;")
    model   = fm.get("model", "&#8212;")
    critic  = fm.get("critic", "&#8212;")
    gate_cls = "pass" if gate_status == "PASS" else "fail"
    posture = sections.get("posture", "")

    calendar_html   = render_calendar_alerts(ctx)
    stat_bar       = render_stat_bar(fm, ctx)
    catalyst_strip = render_catalyst_strip(ctx)
    pulse_callouts = render_pulse_callouts(fm, ctx, levels)
    pulse_html     = render_text_block(sections.get("pulse", ""))
    regime_html    = render_text_block(sections.get("regime", ""))
    news_html      = render_news(sections.get("news", ""))
    scan_html      = render_scan_groups(levels)
    synth_html     = render_synthesis(sections.get("synthesis", ""))
    forward_html        = render_forward_rows(sections.get("forward", ""))
    portfolio_watch_html = render_portfolio_watch(sections.get("portfolio_watch", ""))
    posture_close  = render_posture_close(posture, sections.get("forward", ""))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sovereign Intelligence Brief &#8212; {date}</title>
  <style>{SOVEREIGN_CSS}{CALENDAR_CSS}{FORWARD_CSS}</style>
</head>
<body>
  <div class="header">
    <div class="header-top">
      <div>
        <div class="system-name">Sovereign Intelligence System</div>
        <div class="brief-title">Daily Intelligence Brief</div>
      </div>
      <div class="brief-meta">
        <div>{date} &nbsp;{time_str}</div>
        <div>{model}</div>
        <div>{source_filename}</div>
      </div>
    </div>
    {calendar_html}
    {stat_bar}
    {catalyst_strip}
  </div>

  <div class="section">
    <div class="section-header">
      <div class="section-dot"></div>
      <span class="section-title">{S_PULSE}</span>
    </div>
    {pulse_html}
    {pulse_callouts}
  </div>

  <div class="section">
    <div class="section-header">
      <div class="section-dot"></div>
      <span class="section-title">{S_REGIME}</span>
    </div>
    {regime_html}
  </div>

  <div class="section">
    <div class="section-header">
      <div class="section-dot"></div>
      <span class="section-title">{S_NEWS}</span>
      <span class="section-sub">Signal-relevant &middot; RSS + RAG synthesis</span>
    </div>
    {news_html}
  </div>

  <div class="section">
    <div class="section-header">
      <div class="section-dot"></div>
      <span class="section-title">{S_SCAN}</span>
      <span class="section-sub">S/R &middot; 20d rolling &middot; watched_levels.yaml</span>
    </div>
    {scan_html}
  </div>

  <div class="section">
    <div class="section-header">
      <div class="section-dot"></div>
      <span class="section-title">{S_FORWARD}</span>
    </div>
    {forward_html}
  </div>

  <div class="section">
    <div class="section-header">
      <div class="section-dot"></div>
      <span class="section-title">{S_SYNTH}</span>
    </div>
    <div class="synthesis-block">{synth_html}</div>
  </div>

  {posture_close}

  <div class="section">
    <div class="section-header">
      <div class="section-dot"></div>
      <span class="section-title">{S_PORTFOLIO}</span>
      <span class="section-sub">RAG-backed thesis validation &middot; foundational research</span>
    </div>
    {portfolio_watch_html}
  </div>

  <div class="critic-block">
    <span class="gate-status gate-{gate_cls}">CRITIC {gate_status}</span>
    <span class="gate-checks">{gate_checks_html}</span>
    <span style="color:#6e7681;font-size:10px;margin-left:8px;">{critic}</span>
  </div>
  <div class="footer">
    <span>SOVEREIGN INTELLIGENCE SYSTEM</span>
    <span>Generated {datetime.now().strftime("%Y-%m-%d %I:%M %p")}</span>
  </div>
</body>
</html>"""


# ─── R/R CORRECTION ──────────────────────────────────────────

def correct_best_setup_rr(sections: dict) -> dict:
    """Compute R/R deterministically from BEST SETUP TODAY entry/stop/target.
    Overwrites stated R/R if off by >0.1. Demotes if below BRIEF_RR_MIN."""
    from core.constants import BRIEF_RR_MIN as _RR_MIN
    synth = sections.get("synthesis", "")
    m = re.search(r'(BEST SETUP TODAY:\s*)(.+)', synth, re.IGNORECASE)
    if not m or "NONE" in m.group(2).upper():
        return sections

    line = m.group(2)
    entry_m  = re.search(r'Entry:\s*\$?([\d,.]+)',  line, re.IGNORECASE)
    stop_m   = re.search(r'Stop:\s*\$?([\d,.]+)',   line, re.IGNORECASE)
    target_m = re.search(r'Target:\s*\$?([\d,.]+)', line, re.IGNORECASE)

    if not (entry_m and stop_m and target_m):
        return sections

    try:
        entry  = float(entry_m.group(1).replace(",", ""))
        stop   = float(stop_m.group(1).replace(",", ""))
        target = float(target_m.group(1).replace(",", ""))
        risk   = abs(entry - stop)
        reward = abs(target - entry)
        if risk == 0:
            return sections
        computed = round(reward / risk, 2)
    except Exception:
        return sections

    if computed < _RR_MIN:
        # Demote: replace entire BEST SETUP TODAY value
        new_value = f"WATCH LEVEL — R/R BELOW MINIMUM (computed: {computed})"
        new_synth = synth[:m.start(2)] + new_value + synth[m.end(2):]
        print(f"[node5] BEST SETUP demoted — computed R/R {computed} < {_RR_MIN}")
    else:
        # Correct stated R/R if off by >0.1
        rr_m = re.search(r'R/R:\s*([\d.]+)', line, re.IGNORECASE)
        if rr_m:
            stated = float(rr_m.group(1))
            if abs(stated - computed) > 0.1:
                corrected_line = line[:rr_m.start(1)] + str(computed) + line[rr_m.end(1):]
                new_synth = synth[:m.start(2)] + corrected_line + synth[m.end(2):]
                print(f"[node5] BEST SETUP R/R corrected {stated} → {computed}")
            else:
                return sections
        else:
            return sections

    sections = dict(sections)
    sections["synthesis"] = new_synth
    return sections


# ─── UNIVERSE FILTER ────────────────────────────────────────────

def correct_best_setup_universe(sections: dict) -> dict:
    """If BEST SETUP TODAY ticker is not in TRADING_UNIVERSE, replace with
    NONE before the critic gate runs — avoids hard fail on fixable LLM error."""
    from core.constants import TRADING_UNIVERSE as _TU
    synth = sections.get("synthesis", "")
    m = re.search(r'(BEST SETUP TODAY:\s*)([A-Z]{2,6})(.*)', synth, re.IGNORECASE)
    if not m:
        return sections
    ticker = m.group(2).upper()
    if ticker in _TU or ticker == "NONE":
        return sections
    print(f"[node5] BEST SETUP ticker {ticker} outside TRADING_UNIVERSE — substituting NONE")
    new_line = m.group(1) + "NONE — ticker outside trading universe (was: " + ticker + ")"
    new_synth = synth[:m.start()] + new_line + synth[m.end():]
    sections = dict(sections)
    sections["synthesis"] = new_synth
    return sections


# ─── ENTRY DISTANCE GATE ────────────────────────────────────────

def correct_best_setup_distance(sections: dict, ctx: dict) -> dict:
    """If BEST SETUP TODAY entry is >3% from current price, reformat as
    CONDITIONAL TRIGGER. If no setup within 3% exists, output NO EXECUTABLE
    SETUP TODAY."""
    from core.constants import BEST_SETUP_ENTRY_DISTANCE_MAX as _MAX_DIST
    synth = sections.get("synthesis", "")
    m = re.search(r'(BEST SETUP TODAY:\s*)(.+)', synth, re.IGNORECASE)
    if not m:
        return sections
    line = m.group(2)
    if "NONE" in line.upper() or "WATCH LEVEL" in line.upper() or "NO EXECUTABLE" in line.upper():
        return sections

    # Extract ticker + entry price
    ticker_m = re.search(r'BEST SETUP TODAY:\s*([A-Z]{2,6})', synth, re.IGNORECASE)
    entry_m  = re.search(r'Entry:\s*\$?([\d,.]+)', line, re.IGNORECASE)
    if not (ticker_m and entry_m):
        return sections

    ticker = ticker_m.group(1).upper()
    try:
        entry = float(entry_m.group(1).replace(",", ""))
    except Exception:
        return sections

    # Pull current price from context.json
    current = None
    _crypto = ctx.get("market", {}).get("crypto", {}).get(ticker, {}).get("price")
    if _crypto:
        try:
            current = float(str(_crypto).replace(",", "").replace("$", "").strip())
        except Exception:
            pass
    if current is None:
        _core = ctx.get("market", {}).get("core", {}).get(ticker, {}).get("price")
        if _core:
            try:
                current = float(str(_core).replace(",", "").replace("$", "").strip())
            except Exception:
                pass

    if current is None or current == 0:
        return sections  # can't evaluate — leave as-is

    distance = abs(entry - current) / current

    if distance > _MAX_DIST:
        pct = round(distance * 100, 1)
        direction_m = re.search(r'(LONG|SHORT)', line, re.IGNORECASE)
        direction = direction_m.group(1).upper() if direction_m else "TRADE"
        new_value = (
            f"CONDITIONAL TRIGGER — {ticker} {direction} | "
            f"Entry {entry:,.0f} is {pct}% from current ({current:,.0f}) | "
            f"Watch for price to reach entry zone before acting"
        )
        new_synth = synth[:m.start(2)] + new_value + synth[m.end(2):]
        print(f"[node5] BEST SETUP reformatted as CONDITIONAL TRIGGER — {pct}% from current")
        sections = dict(sections)
        sections["synthesis"] = new_synth

    return sections


# ─── ENTRY POINT ─────────────────────────────────────────────

def run():
    print("\n&#128213;  [NODE 5 &#8212; HTML RENDERER v3.0] Starting...")
    path, text = get_latest_brief()
    if not path:
        print("&#10060; [NODE 5] No brief found. Skipping.")
        return

    fm       = parse_frontmatter(text)
    sections = parse_sections(text)
    levels   = load_levels()
    ctx      = load_context()

    gate_status, gate_checks_html, hard_failures = run_critic_gate(fm, sections, ctx)

    if hard_failures:
        _write_rejection(path, gate_status, hard_failures, text)
        return None

    # Task 6: if NEWS section is fabricated (>1 NO EXTERNAL SIGNAL SOURCE),
    # replace with empty string so render_news() emits the honest fallback.
    _news_src_check = re.findall(r'SOURCE:\s*(.+)', sections.get("news", ""), re.IGNORECASE)
    if sum(1 for s in _news_src_check if "NO EXTERNAL SIGNAL" in s.upper()) > 1:
        print("[node5] NEWS section fabricated — substituting empty fallback")
        sections = dict(sections)
        sections["news"] = ""

    # Task 6: substitute fabricated NEWS before render
    _fab_src = re.findall(r'SOURCE:\s*(.+)', sections.get("news", ""), re.IGNORECASE)
    if sum(1 for s in _fab_src if "NO EXTERNAL SIGNAL" in s.upper()) > 1:
        print("[node5] NEWS fabricated — substituting empty fallback")
        sections = dict(sections)
        sections["news"] = ""

    # correct_best_setup_* functions retired Session A — BEST SETUP TODAY replaced by SETUP SIGNAL
    html = render_html(fm, sections, levels, ctx, path.name, gate_status, gate_checks_html)
    output_path = path.with_suffix(".html")
    output_path.write_text(html, encoding="utf-8")
    print(f"&#10003; [NODE 5] HTML brief rendered &#8594; {output_path.name}")
    return output_path.name


if __name__ == "__main__":
    run()