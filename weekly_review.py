#!/usr/bin/env python3
"""
weekly_review.py — Sovereign Intelligence System
Tier 7 | Weekly synthesis from last 7 daily briefs + live market data
Cron: Sunday 7AM  ->  0 7 * * 0
Output: 02-Market-Intel/Weekly-Reviews/Weekly_YYYY-MM-DD.md + .html
"""

import os, sys, re, glob, json, logging, requests
from datetime import datetime
from pathlib import Path
from core.rag.retriever import retrieve_for_node

import yaml
import yfinance as yf

BASE        = Path.home() / "sovereign"
CONFIG_PATH = BASE / "config.yaml"
BRIEFS_DIR  = BASE / "02-Market-Intel" / "Daily-Briefs"
OUTPUT_DIR  = BASE / "02-Market-Intel" / "Weekly-Reviews"
SCRIPTS_DIR = BASE / "Scripts"
LOG_PATH    = BASE / "logs" / "sovereign_daily.log"

sys.path.insert(0, str(SCRIPTS_DIR))
from core.style import SOVEREIGN_CSS

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [weekly_review] %(message)s"
)
log = logging.getLogger(__name__)

with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

OLLAMA_URL = config.get("ollama_url", "http://localhost:11434")
MODEL      = config.get("ollama_model", "gemma3:12b")
CRITIC     = config.get("ollama_fallback", "mistral:7b")
NUM_BRIEFS = 7
CTX_WINDOW = 16384


def load_briefs(n):
    files = sorted(glob.glob(str(BRIEFS_DIR / "Brief_*.md")))[-n:]
    briefs = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            date_match = re.search(r"Brief_(\d{4}-\d{2}-\d{2})", fp)
            label = date_match.group(1) if date_match else Path(fp).stem
            briefs.append({"label": label, "content": content})
        except Exception as e:
            log.warning(f"Could not read {fp}: {e}")
    log.info(f"Loaded {len(briefs)} briefs")
    return briefs


def fetch_btc():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=10
        )
        d = r.json()["bitcoin"]
        return {"price": d["usd"], "change_24h": round(d.get("usd_24h_change", 0), 2)}
    except Exception as e:
        log.warning(f"BTC fetch failed: {e}")
        return {"price": 0, "change_24h": 0}


def fetch_spy():
    try:
        t = yf.Ticker("SPY")
        hist = t.history(period="2d")
        if len(hist) >= 2:
            prev = hist["Close"].iloc[-2]
            curr = hist["Close"].iloc[-1]
            chg = round(((curr - prev) / prev) * 100, 2)
        else:
            curr = hist["Close"].iloc[-1]
            chg = 0.0
        return {"price": round(float(curr), 2), "change_pct": chg}
    except Exception as e:
        log.warning(f"SPY fetch failed: {e}")
        return {"price": 0.0, "change_pct": 0.0}


def fetch_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        d = r.json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception as e:
        log.warning(f"F&G fetch failed: {e}")
        return {"value": 0, "label": "Unknown"}


def ollama(model, prompt):
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"num_ctx": CTX_WINDOW}},
            timeout=360
        )
        return r.json().get("response", "").strip()
    except Exception as e:
        log.error(f"Ollama [{model}] error: {e}")
        return ""


def build_synthesis_prompt(briefs, btc, spy, fg, trade_stats=None, thesis=None):
    brief_block = ""
    for b in briefs:
        brief_block += f"\n\n{'='*60}\nDAILY BRIEF — {b['label']}\n{'='*60}\n{b['content']}"
    # Trade stats block
    if trade_stats and trade_stats.get("closed", 0) > 0:
        trade_block = (
            f"Hit Rate: {trade_stats['hit_rate']}% | "
            f"Avg Win: {trade_stats['avg_win']:+.2f}% | "
            f"Avg Loss: {trade_stats['avg_loss']:+.2f}% | "
            f"Expectancy: {trade_stats['expectancy']:+.2f}% | "
            f"Closed: {trade_stats['closed']} trades"
        )
    elif trade_stats:
        trade_block = (
            f"No closed trades yet. "
            f"{trade_stats.get('taken', 0)} positions taken, "
            f"{trade_stats.get('pending', 0)} pending review."
        )
    else:
        trade_block = "No trade log data available."

    # Thesis block
    if thesis:
        thesis_block = (
            f"Bias: {thesis.get('market_bias', 'N/A')} | Posture: {thesis.get('posture', 'N/A')}\n"
            f"Thesis: {thesis.get('key_thesis', 'N/A')}\n"
            f"Watching: {thesis.get('watching', 'N/A')}\n"
            f"Invalidation: {thesis.get('invalidation', 'N/A')}"
        )
    else:
        thesis_block = "No weekly thesis set — running baseline review."

    # Feedback block — last 7 days of logged feedback
    try:
        from feedback_learner import cmd_weekly_block
        feedback_raw = cmd_weekly_block()
        feedback_section = f"\n\nFEEDBACK LOG (last 7 days — use as calibration signal, call out repeat patterns):\n{feedback_raw}\n" if feedback_raw else ""
    except Exception:
        feedback_section = ""

    return f"""You are a senior intelligence analyst writing a weekly market review for a disciplined crypto and AI/tech equity trader.

LIVE DATA (Sunday morning):
- BTC: ${btc['price']:,.0f}  ({btc['change_24h']:+.2f}% 24h)
- SPY: ${spy['price']:,.2f}  ({spy['change_pct']:+.2f}%)
- Fear & Greed Index: {fg['value']} — {fg['label']}

The following are the last {len(briefs)} daily intelligence briefs in full. Read all of them before writing.
{brief_block}

---

TRADE PERFORMANCE THIS WEEK:
{trade_block}

WEEKLY THESIS SET AT START OF WEEK:
{thesis_block}

---

Write a weekly review using EXACTLY these four section headers. Be precise, analytical, direct. No filler.

WEEK PULSE
How did the week feel as a unit? Characterize BTC, SPY, and macro energy together. What was the dominant emotional tone of market participants? 3-5 sentences.

REGIME DRIFT
How did the macro regime evolve across the 7 days? Did the dominant narrative hold, shift, or fragment? Identify inflection points. Where does the regime stand entering next week? 4-6 sentences.

SIGNAL LOG
The 6-9 most significant signals that fired this week. For each: what it was, when it appeared, and what it implies going forward. Format each as a bullet.

SYNTHESIS
Integrate everything. What is the state of the system entering next week? What should the trader be watching, positioned for, or cautious about? End with a single verdict line: one sentence capturing the week's conclusion.{feedback_section}"""


def build_critic_prompt(review):
    return f"""You are a rigorous intelligence editor. Improve this weekly market analysis.

Check for: vague language, unsupported claims, missing signals, redundancy between sections.
The SYNTHESIS verdict line must be bold and specific, not hedged.

Return ONLY the improved review with the same four headers (WEEK PULSE, REGIME DRIFT, SIGNAL LOG, SYNTHESIS). No commentary.

{review}"""


def parse_sections(text):
    sections = {"week_pulse": "", "regime_drift": "", "signal_log": "", "synthesis": ""}
    headers = ["WEEK PULSE", "REGIME DRIFT", "SIGNAL LOG", "SYNTHESIS"]
    key_map = {"WEEK PULSE": "week_pulse", "REGIME DRIFT": "regime_drift",
               "SIGNAL LOG": "signal_log", "SYNTHESIS": "synthesis"}
    for i, header in enumerate(headers):
        next_headers = headers[i+1:]
        if next_headers:
            lookahead = "|".join(re.escape(h) for h in next_headers)
            pattern = rf"{re.escape(header)}(.*?)(?={lookahead}|$)"
        else:
            pattern = rf"{re.escape(header)}(.*?)$"
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            sections[key_map[header]] = m.group(1).strip()
    if not any(sections.values()):
        log.warning("Section parse failed — raw dumped into synthesis")
        sections["synthesis"] = text.strip()
    return sections


def clean_section(text, label):
    lines = text.strip().split("\n")
    if lines and lines[0].strip().upper() == label.upper():
        lines = lines[1:]
    return "\n".join(lines).strip()


def clean_markdown(text):
    import re
    # Strip bold/italic markers, keep inner text
    text = re.sub(r'\*\*([^*]+?)\*\*', lambda m: m.group(1), text)
    text = re.sub(r'\*([^*]+?)\*', lambda m: m.group(1), text)
    # Remove lone ** lines
    text = re.sub(r'^\*\*\s*$', '', text, flags=re.MULTILINE)
    # Remove markdown headers
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    # Collapse excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def render_text_block(text):
    if not text:
        return "<p>—</p>"
    html_parts = []
    in_list = False
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue
        is_bullet = bool(re.match(r"^[-•*]\s", stripped)) or bool(re.match(r"^\d+[.)]\s", stripped))
        if is_bullet:
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            item_text = re.sub(r"^[-•*\d+.)]\s+", "", stripped)
            html_parts.append(f"  <li>{item_text}</li>")
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{stripped}</p>")
    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


def extract_verdict(synthesis):
    """Extract final verdict only if it is a short standalone sentence (<= 200 chars)."""
    lines = [l.strip() for l in synthesis.strip().split("\n") if l.strip()]
    if lines:
        last = lines[-1]
        # Only treat as verdict if it looks like a punchy closing sentence
        if 20 < len(last) <= 200 and not last.endswith(":") and len(lines) > 1:
            return last
    return ""


def render_weekly_html(sections, btc, spy, fg, ts, briefs_used):
    css = SOVEREIGN_CSS
    date_str = ts.strftime("%B %d, %Y").upper()
    week_num = ts.isocalendar()[1]
    year     = ts.year

    btc_dir = "▲" if btc["change_24h"] >= 0 else "▼"
    btc_cls = "up" if btc["change_24h"] >= 0 else "down"
    spy_dir = "▲" if spy["change_pct"] >= 0 else "▼"
    spy_cls = "up" if spy["change_pct"] >= 0 else "down"
    fg_val  = fg["value"]
    fg_cls  = "down" if fg_val <= 25 else ("up" if fg_val >= 75 else "neutral")

    wp_html  = render_text_block(clean_markdown(clean_section(sections["week_pulse"],   "WEEK PULSE")))
    rd_html  = render_text_block(clean_markdown(clean_section(sections["regime_drift"], "REGIME DRIFT")))
    sl_html  = render_text_block(clean_markdown(clean_section(sections["signal_log"],   "SIGNAL LOG")))

    synthesis_raw = clean_markdown(clean_section(sections["synthesis"], "SYNTHESIS"))
    verdict       = extract_verdict(synthesis_raw)
    synth_body    = synthesis_raw[:-len(verdict)].strip() if verdict and synthesis_raw.endswith(verdict) else synthesis_raw
    synth_html    = render_text_block(synth_body)
    verdict_html  = f'''<div class="verdict-strip"><div class="verdict-label">WEEK VERDICT</div><div class="verdict-text">{verdict}</div></div>''' if verdict else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sovereign Weekly — {date_str}</title>
<style>
{css}
.weekly-eyebrow{{display:flex;align-items:center;gap:.75rem;margin-bottom:.6rem}}
.weekly-tag{{font-size:.65rem;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#b8956a;background:#251f1a;border:1px solid #3d3028;padding:3px 9px 2px;border-radius:2px}}
.week-number{{font-size:.65rem;font-weight:600;letter-spacing:.12em;color:#5a4f44;text-transform:uppercase}}
.header .title{{font-size:1.8rem;letter-spacing:.08em}}
.header .subtitle{{font-size:.8rem;letter-spacing:.15em;color:#6b5a3e}}
.signal-log-body ul{{list-style:none;padding:0;margin:0}}
.signal-log-body ul li{{padding:.65rem 0;border-bottom:1px solid #272220;color:#c4bdb6;line-height:1.65;padding-left:1rem;position:relative}}
.signal-log-body ul li::before{{content:"›";position:absolute;left:0;color:#6b5a3e;font-weight:700}}
.signal-log-body ul li:last-child{{border-bottom:none}}
.synthesis-body{{border-left:2px solid #6b5a3e;padding-left:1.2rem}}
.synthesis-body p{{color:#ccc5bc;line-height:1.75}}
.verdict-strip{{margin-top:1.5rem;padding:.9rem 1.2rem;background:#1e1a17;border:1px solid #3d3028;border-left:3px solid #b8956a}}
.verdict-label{{font-size:.6rem;font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:#6b5a3e;margin-bottom:.35rem}}
.verdict-text{{font-size:.95rem;color:#d4c8b8;font-style:italic;line-height:1.5}}
.brief-count{{font-size:.65rem;color:#4a4038;letter-spacing:.08em}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="weekly-eyebrow">
      <span class="weekly-tag">Weekly Review</span>
      <span class="week-number">Week {week_num} · {year}</span>
    </div>
    <div class="title">SOVEREIGN WEEKLY</div>
    <div class="subtitle">{date_str}</div>
  </div>
  <div class="stat-bar">
    <div class="stat-item">
      <div class="stat-label">BTC</div>
      <div class="stat-value">${btc["price"]:,.0f}</div>
      <div class="stat-change {btc_cls}">{btc_dir} {abs(btc["change_24h"]):.2f}%</div>
    </div>
    <div class="stat-item">
      <div class="stat-label">SPY</div>
      <div class="stat-value">${spy["price"]:,.2f}</div>
      <div class="stat-change {spy_cls}">{spy_dir} {abs(spy["change_pct"]):.2f}%</div>
    </div>
    <div class="stat-item">
      <div class="stat-label">FEAR & GREED</div>
      <div class="stat-value {fg_cls}">{fg_val}</div>
      <div class="stat-change neutral">{fg["label"].upper()}</div>
    </div>
  </div>
  <div class="section">
    <div class="section-header"><span class="section-title">WEEK PULSE</span></div>
    <div class="section-body">{wp_html}</div>
  </div>
  <div class="section">
    <div class="section-header"><span class="section-title">REGIME DRIFT</span></div>
    <div class="section-body">{rd_html}</div>
  </div>
  <div class="section">
    <div class="section-header"><span class="section-title">SIGNAL LOG</span></div>
    <div class="section-body signal-log-body">{sl_html}</div>
  </div>
  <div class="section">
    <div class="section-header"><span class="section-title">SYNTHESIS</span></div>
    <div class="section-body synthesis-body">{synth_html}{verdict_html}</div>
  </div>
  <div class="footer">
    {ts.strftime("%Y-%m-%d %H:%M")} &nbsp;·&nbsp; {MODEL} → {CRITIC} &nbsp;·&nbsp;
    <span class="brief-count">{briefs_used} briefs synthesized</span> &nbsp;·&nbsp; Sovereign Intelligence System
  </div>
</div>
</body>
</html>"""


def write_md(sections, btc, spy, fg, ts, path, briefs_used):
    content = f"""---
date: {ts.strftime("%Y-%m-%d")}
time: {ts.strftime("%H:%M")}
type: weekly-review
model: {MODEL}
critic: {CRITIC}
btc: {btc["price"]:.0f}
spy: {spy["price"]:.2f}
fear_greed: {fg["value"]}
briefs_synthesized: {briefs_used}
tags: [weekly, market-review, sovereign]
---

# SOVEREIGN WEEKLY — {ts.strftime("%B %d, %Y").upper()}

## WEEK PULSE
{sections["week_pulse"]}

## REGIME DRIFT
{sections["regime_drift"]}

## SIGNAL LOG
{sections["signal_log"]}

## SYNTHESIS
{sections["synthesis"]}
"""
    path.write_text(content, encoding="utf-8")
    log.info(f"MD written: {path}")


def main():
    ts = datetime.now()
    log.info("=== weekly_review.py start ===")
    print(f"[weekly] Starting — {ts.strftime('%Y-%m-%d %H:%M')}")

    briefs = load_briefs(NUM_BRIEFS)
    if not briefs:
        print("[weekly] ERROR: No daily briefs found. Aborting.")
        sys.exit(1)
    print(f"[weekly] Loaded {len(briefs)} briefs: {', '.join(b['label'] for b in briefs)}")

    print("[weekly] Fetching live market data...")
    btc = fetch_btc()
    spy = fetch_spy()
    fg  = fetch_fear_greed()
    print(f"[weekly] BTC ${btc['price']:,.0f}  SPY ${spy['price']}  F&G {fg['value']} {fg['label']}")

    print(f"[weekly] Generating via {MODEL} (ctx={CTX_WINDOW}) — may take 2-3 min...")
    trade_stats = load_trade_stats()
    thesis     = load_weekly_thesis()
    print(f"[weekly] Trade stats: {trade_stats}")
    print(f"[weekly] Thesis loaded: {thesis is not None}")
    prompt = build_synthesis_prompt(briefs, btc, spy, fg, trade_stats, thesis)
    raw = ollama(MODEL, prompt)
    if not raw:
        print("[weekly] ERROR: Model returned empty. Check Ollama.")
        sys.exit(1)

    print(f"[weekly] Critic pass via {CRITIC}...")
    reviewed = ollama(CRITIC, build_critic_prompt(raw)) or raw

    sections = parse_sections(reviewed)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    slug      = ts.strftime("%Y-%m-%d")
    md_path   = OUTPUT_DIR / f"Weekly_{slug}.md"
    html_path = OUTPUT_DIR / f"Weekly_{slug}.html"

    write_md(sections, btc, spy, fg, ts, md_path, len(briefs))
    html_path.write_text(render_weekly_html(sections, btc, spy, fg, ts, len(briefs)), encoding="utf-8")
    log.info(f"HTML written: {html_path}")

    print(f"\n[weekly] Done.")
    print(f"  MD  -> {md_path}")
    print(f"  HTML -> {html_path}")
    log.info("=== weekly_review.py complete ===")


if __name__ == "__main__":
    main()


# ── TRADE LOG + THESIS LOADERS ────────────────────────────────────────────────

TRADE_LOG_PATH = BASE / "01-Trading" / "trade_log.json"
THESIS_PATH    = BASE / "04-Intelligence" / "weekly_thesis.md"

def load_trade_stats():
    """Load trade_log.json and compute hit rate, avg win/loss, expectancy."""
    if not TRADE_LOG_PATH.exists():
        return None
    try:
        with open(TRADE_LOG_PATH) as f:
            entries = json.load(f)
        closed = [e for e in entries if e.get("outcome_pct") is not None]
        if not closed:
            taken = [e for e in entries if e.get("taken") is True]
            return {"closed": 0, "taken": len(taken), "pending": len([e for e in entries if e.get("taken") is None])}
        wins   = [e for e in closed if e["outcome_pct"] > 0]
        losses = [e for e in closed if e["outcome_pct"] <= 0]
        hit_rate   = len(wins) / len(closed) * 100
        avg_win    = sum(e["outcome_pct"] for e in wins) / len(wins) if wins else 0
        avg_loss   = sum(e["outcome_pct"] for e in losses) / len(losses) if losses else 0
        expectancy = (hit_rate / 100 * avg_win) + ((1 - hit_rate / 100) * avg_loss)
        return {
            "closed": len(closed), "wins": len(wins), "losses": len(losses),
            "hit_rate": round(hit_rate, 1), "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2), "expectancy": round(expectancy, 2),
            "taken": len([e for e in entries if e.get("taken") is True]),
            "pending": len([e for e in entries if e.get("taken") is None])
        }
    except Exception as e:
        log.warning(f"Trade log load failed: {e}")
        return None


def load_weekly_thesis():
    """Load weekly_thesis.md frontmatter if it exists."""
    if not THESIS_PATH.exists():
        return None
    try:
        text = THESIS_PATH.read_text(encoding="utf-8")
        fm_match = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            return yaml.safe_load(fm_match.group(1))
        return None
    except Exception as e:
        log.warning(f"Thesis load failed: {e}")
        return None
