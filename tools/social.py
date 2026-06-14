#!/usr/bin/env python3
"""
node10_social.py — Node 10: Social Content Generator
Sovereign Intelligence System

Reads: today's Daily Brief + Plays JSON sidecar + Ignition file
Generates: X thread (3-5 tweets) + Beehiiv newsletter blurb
Output: 00-Inbox/Social/SocialContent_YYYY-MM-DD_HHMM.md + .html
Alias: social
Final node in run_all_daily.py (runs after Ignition).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import glob
import re
from datetime import datetime
from pathlib import Path

from core.config import VAULT_ROOT
from core.llm import generate

VAULT      = VAULT_ROOT
INBOX      = VAULT / "00-Inbox"
BRIEFS_DIR = VAULT / "02-Market-Intel" / "Daily-Briefs"
IGNITION_DIR = VAULT / "05-Ignition"
TRADING_DIR  = VAULT / "01-Trading"
OUTPUT_DIR   = VAULT / "Output"

GEN_MODEL = "gemma3:12b"

NOW       = datetime.now()
TODAY     = NOW.strftime("%Y-%m-%d")
TIMESTAMP = NOW.strftime("%Y-%m-%d_%H%M")

def call_ollama(prompt: str, model: str = GEN_MODEL) -> str:
    try:
        return generate(prompt, system="", model=model, temperature=0.75, max_tokens=1200)
    except Exception as e:
        print(f"  [Ollama error] {e}")
        return ""

# ── Source loaders ─────────────────────────────────────────────────────────────

def load_daily_brief() -> dict:
    """Load today's earliest daily brief and parse key fields."""
    pattern = str(BRIEFS_DIR / f"Brief_{TODAY}_*.md")
    files = sorted(glob.glob(pattern))
    if not files:
        print("  [Node 10] No daily brief found for today — running with partial context.")
        return {}

    brief_path = files[-1]
    text = Path(brief_path).read_text(encoding="utf-8")

    data = {"raw": text, "path": brief_path}

    # Parse YAML frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                data[k.strip()] = v.strip().strip('"').strip("'")

    # Extract SYNTHESIS section
    synth_match = re.search(r"##\s*SYNTHESIS\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    if synth_match:
        data["synthesis"] = synth_match.group(1).strip()

    # Extract posture word (lone word on its own line at end of synthesis)
    if "synthesis" in data:
        lines = [l.strip() for l in data["synthesis"].splitlines() if l.strip()]
        last = lines[-1] if lines else ""
        if last in ("Hold", "Watch", "Opportunity"):
            data["posture"] = last

    print(f"  [Node 10] Brief loaded: {Path(brief_path).name}")
    return data


def load_plays_sidecar() -> list:
    """Load today's plays JSON sidecar (output of plays_html_renderer v2.1)."""
    pattern = str(BRIEFS_DIR / f"Plays_{TODAY}_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        # Fallback: try Output/context.json plays section
        ctx_path = OUTPUT_DIR / "context.json"
        if ctx_path.exists():
            try:
                ctx = json.loads(ctx_path.read_text())
                plays = ctx.get("plays", [])
                if plays:
                    print(f"  [Node 10] Plays loaded from context.json ({len(plays)} plays)")
                    return plays
            except Exception:
                pass
        print("  [Node 10] No plays sidecar found — running without plays.")
        return []

    plays = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    print(f"  [Node 10] Plays loaded: {Path(files[-1]).name} ({len(plays)} plays)")
    return plays


def load_ignition() -> str:
    """Load today's ignition file (most recent)."""
    pattern = str(IGNITION_DIR / f"Ignition_{TODAY}_*.md")
    files = sorted(glob.glob(pattern))
    if not files:
        print("  [Node 10] No ignition file found today.")
        return ""

    text = Path(files[-1]).read_text(encoding="utf-8")
    # Strip frontmatter
    text = re.sub(r"^---\n.*?\n---\n?", "", text, flags=re.DOTALL).strip()
    print(f"  [Node 10] Ignition loaded: {Path(files[-1]).name}")
    return text[:800]  # cap to avoid bloating prompt


def load_trade_log() -> list:
    """Load recent open positions from trade_log.json."""
    log_path = TRADING_DIR / "trade_log.json"
    if not log_path.exists():
        return []
    try:
        entries = json.loads(log_path.read_text())
        open_trades = [e for e in entries if e.get("taken") and not e.get("close_date")]
        return open_trades[:5]  # cap at 5
    except Exception:
        return []


def extract_plays(plays_data) -> list:
    """Extract flat play list from sidecar dict or legacy list format."""
    if isinstance(plays_data, dict):
        return [p for p in plays_data.get("actives", []) if isinstance(p, dict)]
    if isinstance(plays_data, list):
        return [p for p in plays_data if isinstance(p, dict)]
    return []


# ── Context builder ────────────────────────────────────────────────────────────

def build_context(brief: dict, plays: list, ignition: str, open_trades: list) -> str:
    lines = []

    # Brief metadata
    if brief:
        lines.append("=== TODAY'S MARKET BRIEF ===")
        for key in ("date", "btc", "fear_greed", "dominant_narrative", "posture"):
            if key in brief:
                lines.append(f"{key.upper()}: {brief[key]}")
        if "synthesis" in brief:
            lines.append(f"\nSYNTHESIS:\n{brief['synthesis'][:600]}")
    else:
        lines.append("=== NO BRIEF AVAILABLE ===")

    # Plays
    if plays:
        lines.append("\n=== ACTIVE PLAYS ===")
    valid_plays = extract_plays(plays)
    for p in valid_plays[:6]:
            ticker = p.get("ticker", "?")
            conviction = p.get("conviction", "")
            section = p.get("section", "")
            rr = p.get("rr", "")
            narrative = p.get("narrative", p.get("thesis", ""))
            entry = p.get("entry", p.get("entry_price", ""))
            target = p.get("target", "")
            stop = p.get("stop", "")

            line = f"  {ticker} [{conviction}] {section}"
            if rr:
                line += f" | R/R {rr}"
            if entry:
                line += f" | entry ~{entry}"
            if target:
                line += f" | target {target}"
            if stop:
                line += f" | stop {stop}"
            lines.append(line)
            if narrative:
                lines.append(f"    → {str(narrative)[:180]}")
    else:
        lines.append("\n=== NO ACTIVE PLAYS ===")

    # Open trades
    if open_trades:
        lines.append("\n=== OPEN POSITIONS ===")
        for t in open_trades:
            lines.append(f"  {t['ticker']} — entered {t.get('date','')} @ {t.get('entry_price','?')} [{t.get('conviction','')}]")

    # Ignition
    if ignition:
        lines.append(f"\n=== CREATIVE SPARK (for wild card tweet) ===\n{ignition[:400]}")

    return "\n".join(lines)


# ── Prompt templates ───────────────────────────────────────────────────────────

THREAD_PROMPT = """
You are Coty, writing an X thread about your small-account trading challenge.

VOICE — non-negotiable:
- First person. "I'm watching," not "The System detects."
- Direct, confident, a little raw. Sharp. Self-aware.
- You sound like a trader who thinks for himself, not a bot, not an influencer.
- No capitalized mysticism: never "The System," "The Nexus," "The Gauntlet," "The Architect."
- No finance-bro clichés: no "skin in the game," no "signal over noise," no "alpha," no "conviction" as a noun, no "vibe check."
- No emojis as decoration. One emoji max across the whole thread, only if it earns its place.
- No hashtags.

ABSOLUTE RULE — NO FABRICATION:
- Every ticker, price, level, and number MUST come from the VAULT CONTEXT below.
- If a number is not in context, do not invent one. Do not estimate. Do not hedge with "around."
- If context is thin, write a shorter thread. 3 tweets of truth beats 5 tweets of fiction.
- Never reference "$500-$2k" P&L unless there's an actual open_positions or trade_log number in context.

VAULT CONTEXT:
{context}

STRUCTURE (3-5 tweets, each max 280 chars):
1/ Hook: one hard observation from the brief's synthesis or posture. Real numbers only.
2-3/ The plays: pick 1-2 tickers from ACTIVE PLAYS. State ticker, direction, level, R/R, why.
4/ Optional — what you're watching or what would flip your view.
5/ Close: one line. No link unless one is provided. No "follow for more."

FORMAT:
- Number each tweet: 1/  2/  3/
- No preamble, no "Here's the thread:", no sign-off.
- Output only the numbered tweets.
"""

BLURB_PROMPT = """
You are Coty, writing the daily section of your newsletter for traders who hate retail hype.

VOICE — non-negotiable:
- First person. "I'm holding," "I'm watching," "I closed NVDA yesterday."
- Direct, confident, a little raw. You think for yourself.
- Not a bot, not a guru, not a finance influencer.
- No capitalized mysticism: never "The System," "Nexus," "Gauntlet," "Architect," "Dispatch."
- No finance clichés: no "skin in the game," no "alpha," no "conviction" as a noun, no "liquidity grab" unless you're describing a specific event with a price.
- No "narrative confluence." No "posturing." Use real words.

ABSOLUTE RULE — NO FABRICATION:
- Every ticker, price, level, yield, and P&L number MUST come from VAULT CONTEXT below.
- If trade log is empty, say "No open positions" — do not invent a percentage.
- If there are no active plays, say what you're watching instead — do not invent one.
- Do not reference yields, indices, or macro levels unless they appear in context.

VAULT DATA:
{context}

STRUCTURE (2-3 short paragraphs, ~80-140 words each):
1/ Today's read: posture, what the brief's synthesis actually says, one or two concrete numbers.
2/ The book: open positions (or why you're flat), active plays (or what you're waiting for).
3/ Optional — one thread you're pulling on. Could be macro, could be a stock, could be something from the ignition layer. Keep it grounded.

CONSTRAINTS:
- No headers, no "## Dispatch from" titles.
- No "Welcome back," no "In today's edition."
- No sign-off.
- Output only the paragraphs.
"""

# ── Content generator ──────────────────────────────────────────────────────────

def generate_content(context_str: str) -> dict:
    print("  [Node 10] Generating X thread...")
    thread_raw = call_ollama(THREAD_PROMPT.format(context=context_str))

    print("  [Node 10] Generating Beehiiv blurb...")
    blurb_raw = call_ollama(BLURB_PROMPT.format(context=context_str))

    return {"thread": thread_raw, "blurb": blurb_raw}


# ── Parse thread into tweet list ───────────────────────────────────────────────

def parse_thread(raw: str) -> list:
    tweets = []
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r"^(\d+)[/\.]\s*(.+)", line)
        if m:
            tweets.append(m.group(2).strip())
    # Fallback: split by blank lines if numbered format not found
    if not tweets:
        blocks = [b.strip() for b in re.split(r"\n\n+", raw) if b.strip()]
        tweets = blocks[:5]
    return tweets


# ── Markdown renderer ──────────────────────────────────────────────────────────

def render_markdown(content: dict, brief: dict, plays: list, open_trades: list) -> str:
    posture = brief.get("posture", "—")
    btc = brief.get("btc", "—")
    spy = brief.get("fear_greed", "—")
    narrative = brief.get("dominant_narrative", "")

    tweets = parse_thread(content["thread"])

    lines = [
        f"---",
        f"date: {TODAY}",
        f"time: {NOW.strftime('%H:%M')}",
        f"type: social_content",
        f"posture: {posture}",
        f"btc: {btc}",
        f"plays: {len(plays)}",
        f"open_positions: {len(open_trades)}",
        f"---",
        f"",
        f"# Social Content — {TODAY}",
        f"",
        f"> **Posture:** {posture} | **BTC:** {btc} | **Fear & Greed:** {spy}",
        f"",
        f"---",
        f"",
        f"## X THREAD",
        f"",
    ]

    for i, tweet in enumerate(tweets, 1):
        char_count = len(tweet)
        flag = " ⚠️ OVER 280" if char_count > 280 else f" ({char_count})"
        lines.append(f"**{i}/**")
        lines.append(f"{tweet}{flag}")
        lines.append(f"")

    lines += [
        f"---",
        f"",
        f"## BEEHIIV BLURB",
        f"",
        content["blurb"],
        f"",
        f"---",
        f"",
        f"## CONTEXT SNAPSHOT",
        f"",
    ]

    if plays:
        lines.append(f"**Active Plays ({len(plays)}):**")
    valid_plays = extract_plays(plays)
    for p in valid_plays[:6]:
            ticker = p.get("ticker", "?")
            conviction = p.get("conviction", "")
            rr = p.get("rr", "")
            rr_str = f" | R/R {rr}" if rr else ""
            lines.append(f"- {ticker} [{conviction}]{rr_str}")
            lines.append(f"")

    if open_trades:
        lines.append(f"**Open Positions:**")
        for t in open_trades:
            lines.append(f"- {t['ticker']} @ {t.get('entry_price','?')} [{t.get('conviction','')}] ({t.get('date','')})")
        lines.append(f"")

    if narrative:
        lines.append(f"**Dominant Narrative:** {narrative}")
        lines.append(f"")

    return "\n".join(lines)


# ── HTML renderer ──────────────────────────────────────────────────────────────

SOVEREIGN_STYLE = """
  :root {
    --bg: #0e0c08;
    --bg2: #141210;
    --bg3: #1a1714;
    --border: #2a2520;
    --amber: #f5a623;
    --amber-dim: #c4831a;
    --green: #4ade80;
    --red: #f87171;
    --text: #e8e0d0;
    --text-dim: #8a7e6e;
    --text-muted: #5a5248;
    --accent: #f5a623;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Mono', 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.6;
    padding: 32px 24px;
    max-width: 860px;
    margin: 0 auto;
  }
  .header {
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 28px;
  }
  .header h1 {
    font-family: 'IBM Plex Sans', 'Helvetica Neue', sans-serif;
    font-size: 22px;
    font-weight: 700;
    color: var(--amber);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .header .meta {
    color: var(--text-dim);
    font-size: 11px;
    margin-top: 6px;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }
  .meta span { display: flex; align-items: center; gap: 6px; }
  .posture-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .posture-Opportunity { background: rgba(74,222,128,0.15); color: var(--green); border: 1px solid rgba(74,222,128,0.3); }
  .posture-Watch { background: rgba(245,166,35,0.15); color: var(--amber); border: 1px solid rgba(245,166,35,0.3); }
  .posture-Hold { background: rgba(138,126,110,0.15); color: var(--text-dim); border: 1px solid var(--border); }

  .section-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 14px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }
  .section { margin-bottom: 36px; }

  /* X Thread */
  .thread { display: flex; flex-direction: column; gap: 4px; }
  .tweet-row {
    display: flex;
    gap: 12px;
    padding: 14px 16px;
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 4px;
    transition: border-color 0.15s;
    position: relative;
  }
  .tweet-row:hover { border-color: var(--amber-dim); }
  .tweet-num {
    color: var(--amber);
    font-weight: 700;
    font-size: 12px;
    min-width: 24px;
    flex-shrink: 0;
    padding-top: 1px;
  }
  .tweet-body { flex: 1; color: var(--text); line-height: 1.65; }
  .tweet-count {
    position: absolute;
    bottom: 8px;
    right: 12px;
    font-size: 10px;
    color: var(--text-muted);
  }
  .tweet-count.over { color: var(--red); font-weight: 700; }
  .connector {
    width: 2px;
    height: 6px;
    background: var(--border);
    margin-left: 28px;
  }
  .copy-btn {
    background: var(--bg3);
    border: 1px solid var(--border);
    color: var(--text-dim);
    font-family: inherit;
    font-size: 10px;
    padding: 3px 10px;
    cursor: pointer;
    border-radius: 3px;
    float: right;
    margin-top: -2px;
    transition: all 0.15s;
    letter-spacing: 0.05em;
  }
  .copy-btn:hover { border-color: var(--amber); color: var(--amber); }
  .copy-all-btn {
    background: transparent;
    border: 1px solid var(--amber-dim);
    color: var(--amber);
    font-family: inherit;
    font-size: 11px;
    padding: 6px 16px;
    cursor: pointer;
    border-radius: 3px;
    letter-spacing: 0.05em;
    margin-top: 12px;
    transition: all 0.15s;
  }
  .copy-all-btn:hover { background: rgba(245,166,35,0.1); }

  /* Blurb */
  .blurb-box {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 20px 22px;
    position: relative;
  }
  .blurb-box p { margin-bottom: 14px; line-height: 1.75; color: var(--text); }
  .blurb-box p:last-child { margin-bottom: 0; }

  /* Snapshot */
  .snapshot-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
    gap: 8px;
    margin-top: 4px;
  }
  .snap-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 10px 12px;
  }
  .snap-ticker { color: var(--amber); font-weight: 700; font-size: 13px; }
  .snap-badge {
    display: inline-block;
    font-size: 9px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 2px;
    margin-left: 6px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .badge-HIGH { background: rgba(74,222,128,0.15); color: var(--green); }
  .badge-MED { background: rgba(245,166,35,0.15); color: var(--amber); }
  .badge-LOW { background: rgba(138,126,110,0.15); color: var(--text-dim); }
  .snap-detail { color: var(--text-dim); font-size: 11px; margin-top: 3px; }

  .toast {
    position: fixed; bottom: 24px; right: 24px;
    background: var(--bg3); border: 1px solid var(--amber-dim);
    color: var(--amber); font-family: inherit; font-size: 12px;
    padding: 10px 18px; border-radius: 4px;
    opacity: 0; transition: opacity 0.2s;
    pointer-events: none;
  }
  .toast.show { opacity: 1; }
"""

def render_html(content: dict, brief: dict, plays: list, open_trades: list) -> str:
    posture = brief.get("posture", "Hold")
    btc = brief.get("btc", "—")
    fg = brief.get("fear_greed", "—")
    narrative = brief.get("dominant_narrative", "")
    date_str = NOW.strftime("%A, %B %-d")
    time_str = NOW.strftime("%H:%M")

    tweets = parse_thread(content["thread"])

    # Build tweet rows
    tweet_html_parts = []
    for i, tweet in enumerate(tweets):
        char_count = len(tweet)
        over = char_count > 280
        count_class = "tweet-count over" if over else "tweet-count"
        connector = '<div class="connector"></div>' if i < len(tweets) - 1 else ""
        tweet_safe = tweet.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        tweet_html_parts.append(f"""
        <div class="tweet-row">
          <div class="tweet-num">{i+1}/</div>
          <div class="tweet-body">{tweet_safe}</div>
          <span class="{count_class}">{char_count}/280</span>
          <button class="copy-btn" onclick="copyTweet(this, `{tweet_safe}`)">copy</button>
        </div>
        {connector}""")

    tweets_html = "\n".join(tweet_html_parts)

    # Build blurb paragraphs
    blurb_paras = [p.strip() for p in content["blurb"].split("\n\n") if p.strip()]
    blurb_html = "\n".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in blurb_paras)

    # All tweets text for copy-all
    all_tweets_str = "\\n\\n".join(
        f"{i+1}/ {t}" for i, t in enumerate(tweets)
    ).replace("`", "\\`").replace("\\n", "\\\\n")

    # Plays snapshot
    plays_html = ""
    if plays:
        cards = []
    valid_plays = extract_plays(plays)
    for p in valid_plays[:6]:
            ticker = p.get("ticker", "?")
            conviction = p.get("conviction", "MED")
            rr = p.get("rr", "")
            section = p.get("section", "")
            rr_str = f"R/R {rr} · " if rr else ""
            cards.append(f"""
            <div class="snap-card">
              <div><span class="snap-ticker">{ticker}</span>
              <span class="snap-badge badge-{conviction}">{conviction}</span></div>
              <div class="snap-detail">{rr_str}{section}</div>
            </div>""")
            plays_html = f"""
            <div class="section">
            <div class="section-label">Active Plays — {len(plays)}</div>
            <div class="snapshot-grid">{''.join(cards)}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Social Content — {TODAY}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;700&family=IBM+Plex+Sans:wght@400;600;700&display=swap" rel="stylesheet">
  <style>{SOVEREIGN_STYLE}</style>
</head>
<body>

<div class="header">
  <h1>⚡ Social Content</h1>
  <div class="meta">
    <span>{date_str} · {time_str}</span>
    <span>BTC {btc}</span>
    <span>F&G {fg}</span>
    <span><span class="posture-badge posture-{posture}">{posture}</span></span>
    {f'<span style="color:var(--text-muted);font-size:11px;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{narrative}</span>' if narrative else ''}
  </div>
</div>

<div class="section">
  <div class="section-label">X Thread</div>
  <div class="thread">
    {tweets_html}
  </div>
  <button class="copy-all-btn" onclick="copyAll()">copy full thread</button>
</div>

<div class="section" style="margin-top: 32px;">
  <div class="section-label" style="display:flex;justify-content:space-between;align-items:center;">
    <span>Beehiiv Newsletter Blurb</span>
    <button class="copy-btn" onclick="copyBlurb()" style="float:none;margin:0;">copy blurb</button>
  </div>
  <div class="blurb-box">
    {blurb_html}
  </div>
</div>

{plays_html}

<div id="toast" class="toast">Copied ✓</div>

<script>
function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg || 'Copied ✓';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}}

function copyTweet(btn, text) {{
  navigator.clipboard.writeText(text).then(() => showToast('Tweet copied'));
}}

function copyAll() {{
  const tweets = [];
  document.querySelectorAll('.tweet-body').forEach((el, i) => {{
    tweets.push((i+1) + '/ ' + el.textContent.trim());
  }});
  navigator.clipboard.writeText(tweets.join('\\n\\n')).then(() => showToast('Full thread copied'));
}}

function copyBlurb() {{
  const blurb = document.querySelector('.blurb-box').innerText.trim();
  navigator.clipboard.writeText(blurb).then(() => showToast('Blurb copied'));
}}
</script>

</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'─'*52}")
    print(f"  NODE 10 — SOCIAL CONTENT GENERATOR")
    print(f"  {TIMESTAMP}")
    print(f"{'─'*52}")

    # Load sources
    brief = load_daily_brief()
    plays = load_plays_sidecar()
    ignition = load_ignition()
    open_trades = load_trade_log()

    if not brief and not plays:
        print("  [Node 10] HALT: No brief or plays found. Refusing to generate from empty context.")
        print("  [Node 10] If running in pipeline, an upstream node likely failed.")
        print("  [Node 10] If running manually before daily pipeline, run `daily` first.")
        sys.exit(0)  # graceful exit — don't halt the pipeline, just skip social
    if not brief:
        print("  [Node 10] HALT: No brief found. Cannot generate market commentary without it.")
        sys.exit(0)

    # Build context string
    context_str = build_context(brief, plays, ignition, open_trades)

    # Generate
    content = generate_content(context_str)

    if not content["thread"] and not content["blurb"]:
        print("  [Node 10] ERROR: Ollama returned empty. Check model availability.")
        sys.exit(1)

    # Render outputs
    md_text = render_markdown(content, brief, plays, open_trades)
    html_text = render_html(content, brief, plays, open_trades)

    social_dir = INBOX / "Social"
    social_dir.mkdir(exist_ok=True)
    md_path = social_dir / f"SocialDraft_{TIMESTAMP}.md"
    html_path = social_dir / f"SocialDraft_{TIMESTAMP}.html"

    md_path.write_text(md_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")

    print(f"\n  ✅ Output written:")
    print(f"     {md_path.name}")
    print(f"     {html_path.name}")
    print(f"\n  Thread: {len(parse_thread(content['thread']))} tweets")
    print(f"  Blurb:  {len([p for p in content['blurb'].split(chr(10)*2) if p.strip()])} paragraphs")
    print(f"{'─'*52}\n")


if __name__ == "__main__":
    main()
