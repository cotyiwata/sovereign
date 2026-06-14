SOVEREIGN_CSS = """
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0e0c08; color: #d4cdc0;
      font-family: 'IBM Plex Sans', -apple-system, 'Helvetica Neue', sans-serif;
      font-size: 15px; line-height: 1.85;
      padding: 40px 32px 56px; max-width: 800px; margin: 0 auto;
    }
    .header { border-bottom: 1px solid #1e1c18; padding-bottom: 24px; margin-bottom: 0; }
    .header-top { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px; }
    .system-name { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; color: #f5a623; letter-spacing: 0.2em; text-transform: uppercase; font-weight: 600; margin-bottom: 6px; }
    .brief-title { font-size: 22px; font-weight: 300; color: #e8e0d0; letter-spacing: 0.02em; }
    .brief-meta { text-align: right; font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 11px; color: #4a4540; line-height: 2; }
    .stat-bar { display: flex; gap: 0; flex-wrap: wrap; margin-top: 20px; border: 1px solid #1e1c18; border-radius: 6px; overflow: hidden; align-items: stretch; }
    .stat { padding: 13px 14px; border-right: 1px solid #1e1c18; background: #111009; }
    .stat:last-child { border-right: none; }
    .stat-primary { flex: 2; min-width: 120px; }
    .stat-secondary { flex: 1; min-width: 80px; background: #0e0d0a; display: flex; flex-direction: column; justify-content: center; }
    .stat-divider-v { width: 1px; background: #2a2520; flex-shrink: 0; }
    .stat-label { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; color: #5a5248; letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 5px; }
    .stat-value-primary { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 22px; font-weight: 600; color: #e8e0d0; line-height: 1; }
    .stat-change { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 11px; margin-top: 4px; }
    .stat-value-secondary { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 12px; font-weight: 600; color: #7a7268; }
    .catalyst-strip { margin-top: 10px; padding: 10px 16px; background: #13110c; border: 1px solid #2a2218; border-radius: 6px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .catalyst-label { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; color: #7a5a20; letter-spacing: 0.12em; text-transform: uppercase; flex-shrink: 0; }
    .catalyst-pills { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .cat-pill { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 11px; padding: 2px 10px; border-radius: 4px; font-weight: 600; letter-spacing: 0.06em; }
    .cat-hot { background: rgba(248,113,113,0.12); color: #f87171; border: 1px solid rgba(248,113,113,0.25); }
    .cat-warm { background: rgba(245,166,35,0.1); color: #f5a623; border: 1px solid rgba(245,166,35,0.2); }
    .cat-cool { background: rgba(90,90,90,0.15); color: #7a7268; border: 1px solid #2a2520; }
    .catalyst-note { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 11px; color: #6a5a3a; margin-left: auto; }
    .section { margin-top: 32px; }
    .section-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #1a1816; }
    .section-dot { width: 5px; height: 5px; border-radius: 50%; background: #f5a623; flex-shrink: 0; }
    .section-title { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 11px; font-weight: 600; color: #f5a623; letter-spacing: 0.16em; text-transform: uppercase; }
    .section-sub { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; color: #3a3530; letter-spacing: 0.08em; margin-left: auto; }
    p { margin-bottom: 14px; color: #d4cdc0; }
    p:last-child { margin-bottom: 0; }
    li { margin-left: 16px; margin-bottom: 6px; color: #c8c0b4; list-style: none; padding-left: 12px; position: relative; }
    li::before { content: '•'; position: absolute; left: 0; color: #4a4038; }
    .label { font-family: 'IBM Plex Mono', 'SF Mono', monospace; color: #8a7a68; font-weight: 600; font-size: 12px; letter-spacing: 0.06em; }
    .arrow { color: #6a5a4a; }
    .up { color: #4ade80; font-weight: 600; }
    .down { color: #f87171; font-weight: 600; }
    .callout-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
    .callout { flex: 1; min-width: 140px; padding: 12px 14px; background: #111009; border: 1px solid #1e1c18; border-radius: 6px; }
    .callout-label { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; color: #5a5248; letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 5px; }
    .callout-val { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 13px; font-weight: 600; color: #e8e0d0; margin-bottom: 2px; line-height: 1.4; }
    .callout-note { font-size: 12px; color: #6a6058; }
    .news-featured { padding: 18px 20px; background: #111009; border: 1px solid #1e1c18; border-radius: 6px; margin-bottom: 16px; }
    .news-featured-tag { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 9px; color: #f5a623; letter-spacing: 0.16em; text-transform: uppercase; margin-bottom: 8px; }
    .news-featured-headline { font-size: 16px; font-weight: 500; color: #e8e0d0; margin-bottom: 12px; line-height: 1.5; }
    .news-featured-body { font-size: 14px; color: #b8b0a4; line-height: 1.85; }
    .news-featured-body p { margin-bottom: 10px; }
    .news-featured-body p:last-child { margin-bottom: 0; }
    .news-featured-body strong { color: #e0d8c8; }
    .news-signal { margin-top: 12px; padding: 8px 12px; background: #0e0c08; border-left: 2px solid rgba(245,166,35,0.25); }
    .news-signal-label { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 9px; color: #6a5a30; letter-spacing: 0.14em; text-transform: uppercase; margin-bottom: 2px; }
    .news-signal-text { font-size: 13px; color: #9a8a68; }
    .news-items { display: flex; flex-direction: column; }
    .news-item { padding: 12px 0; border-bottom: 1px solid #161412; display: flex; gap: 12px; align-items: flex-start; }
    .news-item:last-child { border-bottom: none; }
    .news-item-tag { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 9px; font-weight: 600; padding: 2px 7px; border-radius: 3px; letter-spacing: 0.08em; text-transform: uppercase; flex-shrink: 0; margin-top: 3px; }
    .tag-crypto { background: rgba(245,166,35,0.1); color: #c08830; border: 1px solid rgba(245,166,35,0.2); }
    .tag-macro { background: rgba(90,130,200,0.1); color: #6a90c8; border: 1px solid rgba(90,130,200,0.2); }
    .tag-ai { background: rgba(74,222,128,0.08); color: #4a9a68; border: 1px solid rgba(74,222,128,0.18); }
    .tag-energy { background: rgba(180,90,90,0.1); color: #b06060; border: 1px solid rgba(180,90,90,0.2); }
    .news-item-headline { font-size: 14px; color: #d4ccc0; font-weight: 500; margin-bottom: 4px; line-height: 1.5; }
    .news-item-body { font-size: 13px; color: #7a7268; line-height: 1.7; }
    .scan-group { margin-bottom: 22px; }
    .scan-group:last-child { margin-bottom: 0; }
    .scan-group-header { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
    .scan-group-label { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; font-weight: 600; color: #7a6a50; letter-spacing: 0.14em; text-transform: uppercase; flex-shrink: 0; }
    .scan-group-read { font-size: 13px; color: #6a6560; font-style: italic; }
    .scan-table { width: 100%; border-collapse: collapse; }
    .scan-table tr { border-bottom: 1px solid #131210; }
    .scan-table tr:last-child { border-bottom: none; }
    .scan-table td { padding: 8px 10px; font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 12px; vertical-align: middle; }
    .td-ticker { color: #e8e0d0; font-weight: 600; width: 70px; }
    .td-price { color: #c0b8b0; width: 90px; }
    .td-dir { width: 82px; }
    .td-level { color: #6a6860; font-size: 11px; }
    .dir-up { color: #4ade80; font-size: 10px; letter-spacing: 0.06em; }
    .dir-down { color: #f87171; font-size: 10px; letter-spacing: 0.06em; }
    .dir-flat { color: #7a7068; font-size: 10px; letter-spacing: 0.06em; }
    .flag { display: inline-block; font-size: 9px; padding: 1px 6px; border-radius: 3px; letter-spacing: 0.06em; font-weight: 600; vertical-align: middle; margin-left: 4px; }
    .flag-amber { background: rgba(245,166,35,0.12); color: #f5a623; border: 1px solid rgba(245,166,35,0.2); }
    .flag-red { background: rgba(248,113,113,0.1); color: #f87171; border: 1px solid rgba(248,113,113,0.18); }
    .forward-rows { display: flex; flex-direction: column; gap: 10px; }
    .forward-row { display: flex; gap: 14px; align-items: flex-start; }
    .forward-label { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; flex-shrink: 0; padding-top: 3px; width: 90px; }
    .fl-likely { color: #a09080; }
    .fl-bull { color: #4ade80; }
    .fl-bear { color: #f87171; }
    .forward-text { font-size: 14px; color: #b0a8a0; line-height: 1.75; }
    .synthesis-block { padding: 20px 22px; background: #111009; border: 1px solid #1e1c18; border-left: 3px solid #f5a623; border-radius: 6px; }
    .synthesis-block p { color: #e0d8c8; font-size: 15px; line-height: 1.95; margin-bottom: 10px; }
    .synthesis-block p:last-child { margin-bottom: 0; }
    .posture-close { margin-top: 32px; padding: 18px 22px; background: #111009; border: 1px solid #1e1c18; border-radius: 6px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
    .posture-badge { font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 13px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; padding: 6px 18px; border-radius: 4px; flex-shrink: 0; }
    .pb-hold { background: rgba(200,169,122,0.1); color: #c8a97a; border: 1px solid rgba(200,169,122,0.25); }
    .pb-watch { background: rgba(245,166,35,0.1); color: #f5a623; border: 1px solid rgba(245,166,35,0.25); }
    .pb-opportunity { background: rgba(74,222,128,0.1); color: #4ade80; border: 1px solid rgba(74,222,128,0.25); }
    .posture-action { font-size: 14px; color: #b0a898; line-height: 1.6; }
    .posture-action strong { color: #e8e0d0; }
    .critic-block { margin-top: 20px; padding: 10px 16px; background: #111009; border: 1px solid #1e1c18; border-radius: 4px; font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 11px; color: #6a6058; }
    .critic-block span { color: #4ade80; font-weight: 600; }
    .footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid #141210; display: flex; justify-content: space-between; font-family: 'IBM Plex Mono', 'SF Mono', monospace; font-size: 10px; color: #2e2c28; letter-spacing: 0.06em; }
"""
