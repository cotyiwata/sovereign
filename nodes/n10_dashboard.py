#!/usr/bin/env python3
"""
Node 9 — Performance Dashboard
Reads trade_log.json → computes stats → outputs dashboard.md + dashboard.html
"""

import json, os, sys
from core.schema import load_trade_log
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

# ── Paths ──────────────────────────────────────────────────────────────────
BASE        = Path.home() / "sovereign"
TRADE_LOG   = BASE / "01-Trading" / "trade_log.json"
OUT_DIR     = BASE / "04-Intelligence"
OUT_MD      = OUT_DIR / "dashboard.md"
OUT_HTML    = OUT_DIR / "dashboard.html"

# ── Load trades ────────────────────────────────────────────────────────────
def load_trades():
    if not TRADE_LOG.exists():
        print("⚠ No trade_log.json found — run tradelog first")
        return []
    return [t.model_dump() for t in load_trade_log(TRADE_LOG)]

# ── Stats engine ───────────────────────────────────────────────────────────
def compute_stats(trades):
    closed   = [t for t in trades if t.get("outcome_pct") is not None]
    open_t   = [t for t in trades if t.get("taken") and t.get("outcome_pct") is None]
    skipped  = [t for t in trades if not t.get("taken")]

    winners  = [t for t in closed if t["outcome_pct"] > 0]
    losers   = [t for t in closed if t["outcome_pct"] <= 0]

    win_rate   = len(winners) / len(closed) * 100 if closed else 0
    avg_win    = sum(t["outcome_pct"] for t in winners) / len(winners) if winners else 0
    avg_loss   = abs(sum(t["outcome_pct"] for t in losers) / len(losers)) if losers else 0
    expectancy = (win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss)

    best  = max(closed, key=lambda t: t["outcome_pct"]) if closed else None
    worst = min(closed, key=lambda t: t["outcome_pct"]) if closed else None

    # Streak
    streak_val, streak_type = 0, "—"
    if closed:
        streak_val = 1
        streak_type = "W" if closed[-1]["outcome_pct"] > 0 else "L"
        for t in reversed(closed[:-1]):
            cur_type = "W" if t["outcome_pct"] > 0 else "L"
            if cur_type == streak_type:
                streak_val += 1
            else:
                break

    # By section
    by_section = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pct": 0.0})
    for t in closed:
        s = t.get("section", "UNKNOWN")
        by_section[s]["trades"] += 1
        by_section[s]["total_pct"] += t["outcome_pct"]
        if t["outcome_pct"] > 0:
            by_section[s]["wins"] += 1

    # Conviction accuracy
    by_conviction = defaultdict(lambda: {"trades": 0, "wins": 0})
    for t in closed:
        c = t.get("conviction", "UNK").upper()
        by_conviction[c]["trades"] += 1
        if t["outcome_pct"] > 0:
            by_conviction[c]["wins"] += 1

    return {
        "total": len(trades),
        "closed": len(closed),
        "open": len(open_t),
        "skipped": len(skipped),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "best": best,
        "worst": worst,
        "streak_val": streak_val,
        "streak_type": streak_type,
        "by_section": dict(by_section),
        "by_conviction": dict(by_conviction),
        "closed_trades": closed,
        "open_trades": open_t,
    }

# ── Markdown output ────────────────────────────────────────────────────────
def render_md(s, trades):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"---",
        f"updated: {now}",
        f"type: dashboard",
        f"---",
        f"",
        f"# PERFORMANCE DASHBOARD",
        f"*Updated: {now}*",
        f"",
        f"## OVERVIEW",
        f"- Total logged: {s['total']} | Closed: {s['closed']} | Open: {s['open']} | Skipped: {s['skipped']}",
        f"- Win rate: {s['win_rate']:.1f}% ({s['winners']}W / {s['losers']}L)",
        f"- Avg winner: +{s['avg_win']:.2f}% | Avg loser: -{s['avg_loss']:.2f}%",
        f"- Expectancy: {s['expectancy']:+.2f}% per trade",
        f"- Streak: {s['streak_val']} {s['streak_type']}",
        f"",
    ]
    if s["best"]:
        lines.append(f"- Best: {s['best']['ticker']} +{s['best']['outcome_pct']:.2f}% ({s['best']['date']})")
    if s["worst"]:
        lines.append(f"- Worst: {s['worst']['ticker']} {s['worst']['outcome_pct']:.2f}% ({s['worst']['date']})")
    lines.append("")
    lines.append("## BY SECTION")
    for sec, d in s["by_section"].items():
        wr = d["wins"]/d["trades"]*100 if d["trades"] else 0
        lines.append(f"- {sec}: {d['trades']} trades | {wr:.0f}% WR | {d['total_pct']:+.2f}% total P&L")
    lines.append("")
    lines.append("## CONVICTION ACCURACY")
    for conv, d in s["by_conviction"].items():
        wr = d["wins"]/d["trades"]*100 if d["trades"] else 0
        lines.append(f"- {conv}: {d['trades']} trades | {wr:.0f}% WR")
    OUT_MD.write_text("\n".join(lines))
    print(f"✅ dashboard.md → {OUT_MD}")

# ── CSS ────────────────────────────────────────────────────────────────────
CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0e0c08; color:#e8d8a8; font-family:'SF Mono',monospace; padding:32px; }
h1 { font-size:22px; letter-spacing:0.2em; color:#c8a84a; margin-bottom:4px; }
.subtitle { font-size:11px; color:#6b5f42; letter-spacing:0.15em; margin-bottom:32px; }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:16px; margin-bottom:32px; }
.stat-card { background:#1a1810; border:1px solid #2a2418; border-radius:8px; padding:16px; }
.stat-label { font-size:9px; color:#6b5f42; letter-spacing:0.15em; margin-bottom:8px; }
.stat-value { font-size:26px; font-weight:700; color:#e8d8a8; }
.stat-value.green { color:#7ab87a; }
.stat-value.red { color:#c86848; }
.stat-value.amber { color:#c8a84a; }
.stat-sub { font-size:10px; color:#6b5f42; margin-top:4px; }
h2 { font-size:11px; letter-spacing:0.2em; color:#6b5f42; margin:28px 0 12px; border-bottom:1px solid #2a2418; padding-bottom:8px; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; font-size:9px; letter-spacing:0.12em; color:#6b5f42; padding:6px 12px; }
td { padding:8px 12px; border-bottom:1px solid #1a1810; }
tr:hover td { background:#1a1810; }
.badge { display:inline-block; padding:2px 8px; border-radius:3px; font-size:9px; letter-spacing:0.1em; font-weight:700; }
.badge-high { background:#2a1a10; color:#c86848; border:1px solid #3a2010; }
.badge-med  { background:#1a1a10; color:#c8a84a; border:1px solid #2a2a10; }
.win { color:#7ab87a; font-weight:700; }
.loss { color:#c86848; font-weight:700; }
.streak { font-size:28px; font-weight:700; }
.streak.W { color:#7ab87a; }
.streak.L { color:#c86848; }
.conv-bar { height:6px; background:#2a2418; border-radius:3px; margin-top:6px; }
.conv-fill { height:100%; border-radius:3px; background:linear-gradient(90deg,#c86848,#7ab87a); }
.no-data { color:#6b5f42; font-size:12px; padding:20px 0; text-align:center; }
"""

# ── HTML output ────────────────────────────────────────────────────────────
def pct_color(v):
    if v > 0: return "win"
    if v < 0: return "loss"
    return ""

def render_html(s, trades):
    now = datetime.now().strftime("%b %d, %Y %I:%M %p")
    exp_color = "green" if s["expectancy"] > 0 else "red"
    wr_color  = "green" if s["win_rate"] >= 50 else "red"

    # Stat cards
    stat_cards = f"""
<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-label">WIN RATE</div>
    <div class="stat-value {wr_color}">{s['win_rate']:.1f}%</div>
    <div class="stat-sub">{s['winners']}W / {s['losers']}L of {s['closed']} closed</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">EXPECTANCY</div>
    <div class="stat-value {exp_color}">{s['expectancy']:+.2f}%</div>
    <div class="stat-sub">per trade avg</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">AVG WINNER</div>
    <div class="stat-value green">+{s['avg_win']:.2f}%</div>
    <div class="stat-sub">avg loser: -{s['avg_loss']:.2f}%</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">STREAK</div>
    <div class="stat-value streak {s['streak_type']}">{s['streak_val']}{s['streak_type']}</div>
    <div class="stat-sub">current run</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">OPEN POSITIONS</div>
    <div class="stat-value amber">{s['open']}</div>
    <div class="stat-sub">{s['skipped']} skipped | {s['total']} logged</div>
  </div>
</div>"""

    # Best / worst
    bw = ""
    if s["best"]:
        bw += f'<tr><td>BEST</td><td>{s["best"]["ticker"]}</td><td class="win">+{s["best"]["outcome_pct"]:.2f}%</td><td>{s["best"]["date"]}</td><td>{s["best"].get("section","—")}</td></tr>'
    if s["worst"]:
        bw += f'<tr><td>WORST</td><td>{s["worst"]["ticker"]}</td><td class="loss">{s["worst"]["outcome_pct"]:.2f}%</td><td>{s["worst"]["date"]}</td><td>{s["worst"].get("section","—")}</td></tr>'

    # Section table
    sec_rows = ""
    for sec, d in s["by_section"].items():
        wr = d["wins"]/d["trades"]*100 if d["trades"] else 0
        pl_cls = "win" if d["total_pct"] > 0 else "loss"
        sec_rows += f'<tr><td>{sec}</td><td>{d["trades"]}</td><td>{wr:.0f}%</td><td class="{pl_cls}">{d["total_pct"]:+.2f}%</td></tr>'

    # Conviction table
    conv_rows = ""
    for conv, d in s["by_conviction"].items():
        wr = d["wins"]/d["trades"]*100 if d["trades"] else 0
        badge = f'badge-{"high" if conv=="HIGH" else "med"}'
        conv_rows += f'''<tr>
          <td><span class="badge {badge}">{conv}</span></td>
          <td>{d["trades"]}</td>
          <td>{wr:.0f}%
            <div class="conv-bar"><div class="conv-fill" style="width:{wr}%"></div></div>
          </td>
        </tr>'''

    # Recent closed trades
    recent = s["closed_trades"][-10:][::-1]
    trade_rows = ""
    for t in recent:
        pct = t.get("outcome_pct", 0)
        cls = pct_color(pct)
        conv = t.get("conviction","—").upper()
        badge = f'badge-{"high" if conv=="HIGH" else "med"}'
        trade_rows += f'<tr><td>{t["date"]}</td><td><b>{t["ticker"]}</b></td><td><span class="badge {badge}">{conv}</span></td><td class="{cls}">{pct:+.2f}%</td><td>{t.get("section","—")}</td></tr>'

    # Open positions
    open_rows = ""
    for t in s["open_trades"]:
        conv = t.get("conviction","—").upper()
        badge = f'badge-{"high" if conv=="HIGH" else "med"}'
        open_rows += f'<tr><td>{t["date"]}</td><td><b>{t["ticker"]}</b></td><td><span class="badge {badge}">{conv}</span></td><td>{t.get("section","—")}</td><td style="color:#6b5f42">OPEN</td></tr>'

    no_trades = s["closed"] == 0

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Performance Dashboard</title>
<style>{CSS}</style>
</head><body>
<h1>PERFORMANCE DASHBOARD</h1>
<div class="subtitle">SOVEREIGN INTELLIGENCE SYSTEM — {now}</div>

{stat_cards if not no_trades else '<div class="no-data">No closed trades yet. Log your first trade with: tradelog open TICKER</div>'}

{"" if no_trades else f'''
<h2>BEST / WORST</h2>
<table><thead><tr><th></th><th>TICKER</th><th>OUTCOME</th><th>DATE</th><th>SECTION</th></tr></thead>
<tbody>{bw}</tbody></table>

<h2>BY SECTION</h2>
<table><thead><tr><th>SECTION</th><th>TRADES</th><th>WIN RATE</th><th>TOTAL P&L</th></tr></thead>
<tbody>{sec_rows if sec_rows else "<tr><td colspan=4 class='no-data'>No data</td></tr>"}</tbody></table>

<h2>CONVICTION ACCURACY</h2>
<table><thead><tr><th>LEVEL</th><th>TRADES</th><th>WIN RATE</th></tr></thead>
<tbody>{conv_rows if conv_rows else "<tr><td colspan=3 class='no-data'>No data</td></tr>"}</tbody></table>

<h2>RECENT CLOSED</h2>
<table><thead><tr><th>DATE</th><th>TICKER</th><th>CONVICTION</th><th>OUTCOME</th><th>SECTION</th></tr></thead>
<tbody>{trade_rows if trade_rows else "<tr><td colspan=5 class='no-data'>No closed trades</td></tr>"}</tbody></table>

<h2>OPEN POSITIONS</h2>
<table><thead><tr><th>DATE</th><th>TICKER</th><th>CONVICTION</th><th>SECTION</th><th>STATUS</th></tr></thead>
<tbody>{open_rows if open_rows else "<tr><td colspan=5 class='no-data'>No open positions</td></tr>"}</tbody></table>
'''}

</body></html>"""

    OUT_HTML.write_text(html)
    print(f"✅ dashboard.html → {OUT_HTML}")

# ── Terminal summary ───────────────────────────────────────────────────────
def print_summary(s):
    print("\n" + "─"*50)
    print("  SOVEREIGN — PERFORMANCE DASHBOARD")
    print("─"*50)
    if s["closed"] == 0:
        print("  No closed trades yet.")
    else:
        wr_str = f"{s['win_rate']:.1f}% ({s['winners']}W/{s['losers']}L)"
        print(f"  Win Rate   : {wr_str}")
        print(f"  Expectancy : {s['expectancy']:+.2f}% per trade")
        print(f"  Avg Win    : +{s['avg_win']:.2f}%  |  Avg Loss: -{s['avg_loss']:.2f}%")
        print(f"  Streak     : {s['streak_val']} {s['streak_type']}")
        if s["best"]:
            print(f"  Best Trade : {s['best']['ticker']} +{s['best']['outcome_pct']:.2f}%")
        if s["worst"]:
            print(f"  Worst Trade: {s['worst']['ticker']} {s['worst']['outcome_pct']:.2f}%")
    print(f"  Open       : {s['open']}  |  Skipped: {s['skipped']}")
    print("─"*50 + "\n")

# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    trades = load_trades()
    s = compute_stats(trades)
    print_summary(s)
    render_md(s, trades)
    render_html(s, trades)
