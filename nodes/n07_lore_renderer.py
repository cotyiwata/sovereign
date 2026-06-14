#!/usr/bin/env python3
# lore_html_renderer.py — Lore Dispatch HTML Renderer
# Sovereign Intelligence System — Warm Terminal Theme

import re
import sys
import yaml
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VAULT_ROOT as VAULT, load_config
CONFIG = load_config()

_active_universe = CONFIG.get("active_universe", "The-Lost-Net")
_universe_slug   = _active_universe.replace(" ", "-")
LORE_DIR         = VAULT / "03-Universes" / _universe_slug / "Daily-Expansions"

TERMINAL_CSS = """
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1e1a14;
      color: #d0c4a8;
      font-family: 'SF Mono', 'Fira Code', 'Courier New', monospace;
      font-size: 12px;
      line-height: 1.8;
      max-width: 820px;
      margin: 0 auto;
      border: 1px solid #2e2818;
    }
    .term-bar {
      background: #181410;
      border-bottom: 1px solid #2e2818;
      padding: 8px 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 10px;
      color: #3a3020;
      letter-spacing: 0.16em;
    }
    .term-bar .live { color: #a08850; }
    .term-body { padding: 20px; }
    .prompt { color: #3a3020; }
    .cmd { color: #8a7248; margin-bottom: 16px; }
    .divider { color: #2a2418; margin: 16px 0; font-size: 11px; }
    .field { display: flex; gap: 12px; margin-bottom: 3px; }
    .field-key { color: #5a4a30; min-width: 140px; }
    .field-val { color: #d0c4a8; }
    .field-val.hi   { color: #c86848; }
    .field-val.warn { color: #c8a848; }
    .field-val.dim  { color: #3a3020; }
    .section { margin: 20px 0; }
    .sec-label {
      color: #c8a848;
      font-size: 10px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .sec-label::after { content: ''; flex: 1; height: 1px; background: #28200e; }
    .tag { color: #c8a848; }
    .block {
      border-left: 2px solid #3a3020;
      padding: 12px 16px;
      margin-bottom: 8px;
      background: #1a1710;
    }
    .block p { color: #a89870; font-size: 12px; line-height: 1.85; margin-bottom: 6px; }
    .block p:last-child { margin-bottom: 0; }
    .block-atm {
      border-left: 2px solid #2e2818;
      padding: 12px 16px;
      margin-bottom: 8px;
      background: #18150e;
      font-style: italic;
    }
    .block-atm p { color: #7a6a48; font-size: 12px; line-height: 1.9; margin-bottom: 6px; }
    .block-atm p:last-child { margin-bottom: 0; }
    .block-mech {
      border-left: 2px solid #3e3020;
      padding: 12px 16px;
      background: #1c1810;
    }
    .block-mech p { color: #9a8860; font-size: 12px; line-height: 1.85; margin-bottom: 6px; }
    .block-mech p:last-child { margin-bottom: 0; }
    .block-mech strong { color: #e0c070; font-weight: 700; letter-spacing: 0.04em; }
    .block-codex {
      border-left: 2px solid #3a2818;
      padding: 12px 16px;
      background: #17130e;
    }
    .block-codex p { color: #7a6040; font-size: 12px; line-height: 1.85; margin-bottom: 6px; }
    .block-codex p:last-child { margin-bottom: 0; }
    .cursor { display: inline-block; width: 7px; height: 12px; background: #a08850; vertical-align: middle; }
    .term-footer {
      border-top: 1px solid #2a2418;
      padding: 8px 16px;
      display: flex;
      justify-content: space-between;
      font-size: 9px;
      color: #2e2818;
      letter-spacing: 0.12em;
    }
    p { margin-bottom: 6px; }
    li {
      color: #a89870;
      list-style: none;
      padding-left: 14px;
      position: relative;
      margin-bottom: 4px;
    }
    li::before { content: '›'; position: absolute; left: 0; color: #3a3020; }
"""

def get_latest_expansion():
    files = sorted(LORE_DIR.glob("Expansion_*.md"), reverse=True)
    if not files:
        return None, None
    return files[0], files[0].read_text(encoding="utf-8")

def parse_frontmatter(text):
    data = {}
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for field in ["date", "time", "universe", "arc", "world_status", "model"]:
            m = re.search(rf"^{field}:\s*(.+)$", fm, re.MULTILINE)
            if m:
                data[field] = m.group(1).strip().strip('"')
    return data

def parse_lore_sections(text):
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()
    body = re.sub(r"^[A-Z][A-Z\-]+ — \d{4}-\d{2}-\d{2}.*?\n---\n", "", body, flags=re.DOTALL).strip()

    sections = {"transmission": "", "codex": "", "atmosphere": "", "mechanic": ""}

    m = re.search(r"SECTION IV[^\n]*\n(.*?)(?=SECTION V|CODEX ENTRY|🎨|$)", body, re.DOTALL)
    if m:
        sections["transmission"] = m.group(1).strip()

    lab_match = re.search(r"(?:SECTION V[^\n]*|🎨[^\n]*)\n(.*?)(?=CODEX ENTRY|$)", body, re.DOTALL)
    lab = lab_match.group(1).strip() if lab_match else body

    atm = re.search(r"ATMOSPHERE:(.*?)(?=SYSTEM MECHANIC:|SYSTEM:|\*\*|$)", lab, re.DOTALL)
    if atm:
        sections["atmosphere"] = atm.group(1).strip()

    mec = re.search(r"(?:SYSTEM MECHANIC:|SYSTEM:)\s*(.*?)(?=CODEX ENTRY|$)", lab, re.DOTALL)
    if mec:
        sections["mechanic"] = mec.group(1).strip()
    else:
        mec2 = re.search(r"(\*\*[^*]+\*\*\..*?)(?=CODEX ENTRY|$)", lab, re.DOTALL)
        if mec2:
            sections["mechanic"] = mec2.group(1).strip()

    m = re.search(r"CODEX ENTRY[^\n]*\n(.*?)$", body, re.DOTALL)
    if m:
        sections["codex"] = m.group(1).strip()

    return sections

def render_prose(text):
    if not text:
        return ""
    html = ""
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line == "---":
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if line.startswith("•") or line.startswith("›"):
            html += f"<li>{line[1:].strip()}</li>"
        else:
            html += f"<p>{line}</p>"
    return html

def render_html(fm, sections, source_filename):
    date         = fm.get("date", "—")
    time_str     = fm.get("time", "—")
    universe     = fm.get("universe", _active_universe)
    arc          = fm.get("arc", "—")
    world_status = fm.get("world_status", "—")
    model        = fm.get("model", "—")

    node_id = date.replace("-", "")[-4:]

    transmission_html = render_prose(sections.get("transmission", ""))
    atmosphere_html   = render_prose(sections.get("atmosphere", ""))
    mechanic_html     = render_prose(sections.get("mechanic", ""))
    codex             = sections.get("codex", "").strip()

    transmission_section = f"""
    <div class="section">
      <div class="sec-label"><span class="tag">[IV]</span> FIELD TRANSMISSION</div>
      <div class="block">{transmission_html}</div>
    </div>""" if transmission_html else ""

    codex_section = f"""
    <div class="section">
      <div class="sec-label"><span class="tag">[VI]</span> CODEX ENTRY</div>
      <div class="block-codex">{render_prose(codex)}</div>
    </div>""" if codex else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{universe} // Dispatch {date}</title>
  <style>{TERMINAL_CSS}</style>
</head>
<body>
  <div class="term-bar">
    <span>{universe.upper()} // SOVEREIGN ARCHIVE // NODE-{node_id}</span>
    <span class="live">● CONNECTED</span>
  </div>

  <div class="term-body">
    <div class="cmd"><span class="prompt">root@sovereign:~$ </span>cat dispatch/{source_filename}</div>

    <div class="divider">━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</div>

    <div class="field"><span class="field-key">DATE</span><span class="field-val">{date} {time_str}</span></div>
    <div class="field"><span class="field-key">UNIVERSE</span><span class="field-val">{universe}</span></div>
    <div class="field"><span class="field-key">ACTIVE_ARC</span><span class="field-val">{arc}</span></div>
    <div class="field"><span class="field-key">WORLD_STATUS</span><span class="field-val warn">{world_status}</span></div>
    <div class="field"><span class="field-key">CLASSIFICATION</span><span class="field-val hi">// RESTRICTED //</span></div>
    <div class="field"><span class="field-key">MODEL</span><span class="field-val dim">{model}</span></div>

    <div class="divider">━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</div>

    {transmission_section}

    <div class="section">
      <div class="sec-label"><span class="tag">[V]</span> CREATIVE LAB // SOVEREIGN SPARK</div>
      <div class="block-atm">{atmosphere_html if atmosphere_html else "<p>— no signal —</p>"}</div>
      <div class="block-mech">{mechanic_html if mechanic_html else "<p>— no signal —</p>"}</div>
    </div>

    {codex_section}

    <div class="divider">━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</div>
    <div class="cmd"><span class="prompt">root@sovereign:~$ </span><span class="cursor"></span></div>
  </div>

  <div class="term-footer">
    <span>SOVEREIGN INTELLIGENCE SYSTEM // {universe.upper()}</span>
    <span>REF: {source_filename}</span>
    <span>GENERATED {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
  </div>
</body>
</html>"""

def run():
    print("\n📖 [LORE RENDERER] Starting...")
    path, text = get_latest_expansion()
    if not path:
        print("❌ [LORE RENDERER] No expansion found. Skipping.")
        return
    fm       = parse_frontmatter(text)
    sections = parse_lore_sections(text)
    html     = render_html(fm, sections, path.name)
    output_path = path.with_suffix(".html")
    output_path.write_text(html, encoding="utf-8")
    print(f"✓ [LORE RENDERER] HTML rendered → {output_path.name}")
    return output_path.name

if __name__ == "__main__":
    run()
