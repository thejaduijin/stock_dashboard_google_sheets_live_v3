#!/usr/bin/env python3
"""
build_static_dashboard.py
Generates a self-contained static index.html from output.json
that works on GitHub Pages without a backend server.
"""

import json
import os
from datetime import datetime

OUTPUT_PATH = "output.json"
STATIC_INDEX_PATH = "_site/index.html"

def load_output():
    if not os.path.exists(OUTPUT_PATH):
        print(f"WARNING: {OUTPUT_PATH} not found. Creating empty dashboard.")
        return {
            "timestamp": datetime.now().isoformat(),
            "engine": "-",
            "kpi": {"universe": 0, "in_debate": 0, "buy_signals": 0, "top_pick": "-", "top_pick_confidence": 0},
            "verdicts": [],
            "total_verdicts": 0,
        }
    with open(OUTPUT_PATH, "r") as f:
        return json.load(f)

def generate_static_html(data):
    kpi = data.get("kpi", {})
    verdicts = data.get("verdicts", [])
    engine = data.get("engine", "-")
    timestamp = data.get("timestamp", "-")

    buy_count = sum(1 for v in verdicts if v.get("verdict") == "BUY")
    watch_count = sum(1 for v in verdicts if v.get("verdict") == "WATCH")
    avoid_count = sum(1 for v in verdicts if v.get("verdict") == "AVOID")

    verdict_rows = []
    for v in verdicts[:20]:
        symbol = v.get("symbol", "?")
        name = v.get("name", symbol)
        verdict = v.get("verdict", "WATCH")
        confidence = v.get("confidence", 0)
        winner = v.get("winner", "-")
        rationale = v.get("rationale", "")
        live_price = v.get("live_price", "-")
        day_change = v.get("day_change_pct", "-")

        badge_color = {"BUY": "#16a34a", "WATCH": "#ca8a04", "AVOID": "#dc2626"}.get(verdict, "#6b7280")
        change_color = "#16a34a"
        try:
            if day_change and float(str(day_change).replace("+","")) < 0:
                change_color = "#dc2626"
        except:
            pass
        rationale_short = rationale[:80] + "..." if len(rationale) > 80 else rationale

        verdict_rows.append(
            '<tr style="border-bottom:1px solid #e5e7eb">'
            f'<td style="padding:12px 16px;font-weight:600">{symbol}</td>'
            f'<td style="padding:12px 16px;color:#6b7280;font-size:13px">{name}</td>'
            f'<td style="padding:12px 16px"><span style="background:{badge_color};color:#fff;padding:4px 10px;border-radius:12px;font-size:12px;font-weight:600">{verdict}</span></td>'
            f'<td style="padding:12px 16px;text-align:center;font-weight:700">{confidence}/10</td>'
            f'<td style="padding:12px 16px;color:#6b7280;font-size:13px">{winner}</td>'
            f'<td style="padding:12px 16px;color:#6b7280;font-size:13px">&#8377;{live_price}</td>'
            f'<td style="padding:12px 16px;color:{change_color};font-weight:600">{day_change}%</td>'
            f'<td style="padding:12px 16px;color:#6b7280;font-size:12px;max-width:200px">{rationale_short}</td>'
            '</tr>'
        )

    if not verdict_rows:
        verdicts_html = '<tr><td colspan="8" style="padding:40px;text-align:center;color:#9ca3af">No verdicts yet. Run the pipeline to generate analysis.</td></tr>'
    else:
        verdicts_html = "\n".join(verdict_rows)

    top_pick = kpi.get("top_pick", "-")
    universe = kpi.get("universe", 0)
    in_debate = kpi.get("in_debate", 0)
    buy_signals = kpi.get("buy_signals", 0)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AgentDesk - Indian Stock Analysis</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; background: #f9fafb; color: #111827; line-height: 1.5; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 32px; flex-wrap: wrap; gap: 16px; }}
    .brand {{ display: flex; align-items: baseline; gap: 12px; }}
    .brand h1 {{ font-size: 28px; font-weight: 700; letter-spacing: -0.5px; }}
    .brand span {{ color: #6b7280; font-size: 14px; }}
    .badge {{ background: #111827; color: #fff; padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 500; }}
    .section-title {{ font-size: 12px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 16px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }}
    .kpi-card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; }}
    .kpi-label {{ font-size: 12px; font-weight: 600; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
    .kpi-value {{ font-size: 32px; font-weight: 700; color: #111827; }}
    .kpi-sub {{ font-size: 13px; color: #6b7280; margin-top: 4px; }}
    .buy-signals {{ color: #16a34a; }}
    .agents-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 32px; }}
    .agent-card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; }}
    .agent-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
    .agent-icon {{ font-size: 20px; }}
    .agent-name {{ font-weight: 600; font-size: 15px; }}
    .agent-role {{ font-size: 13px; color: #6b7280; margin-bottom: 12px; }}
    .agent-status {{ display: inline-flex; align-items: center; gap: 6px; background: #dcfce7; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; color: #16a34a; text-transform: uppercase; }}
    .agent-stats {{ display: flex; justify-content: space-between; margin-top: 16px; padding-top: 16px; border-top: 1px dashed #e5e7eb; }}
    .agent-stat {{ text-align: center; }}
    .agent-stat-value {{ font-size: 20px; font-weight: 700; }}
    .agent-stat-label {{ font-size: 11px; color: #9ca3af; text-transform: uppercase; margin-top: 2px; }}
    .verdicts-table {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; overflow: hidden; }}
    .verdicts-table table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .verdicts-table th {{ background: #f9fafb; padding: 12px 16px; text-align: left; font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }}
    .verdicts-table td {{ padding: 12px 16px; }}
    .footer {{ text-align: center; padding: 24px; color: #9ca3af; font-size: 13px; margin-top: 24px; }}
    .timestamp {{ text-align: right; font-size: 12px; color: #9ca3af; margin-bottom: 16px; }}
    @media (max-width: 768px) {{ .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }} .agents-grid {{ grid-template-columns: 1fr; }} .verdicts-table {{ overflow-x: auto; }} .verdicts-table table {{ min-width: 800px; }} }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <h1>AgentDesk</h1>
        <span>Indian stock analysis - 8 agents on duty</span>
      </div>
      <div class="badge">Live - Auto-Updated</div>
    </header>

    <div class="timestamp">Last updated: {timestamp} - Engine: {engine}</div>

    <div class="section-title">Overview</div>
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Universe</div>
        <div class="kpi-value">{universe}</div>
        <div class="kpi-sub">stocks screened</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">In Debate</div>
        <div class="kpi-value">{in_debate}</div>
        <div class="kpi-sub">shortlisted for analysis</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Buy Signals</div>
        <div class="kpi-value buy-signals">{buy_signals}</div>
        <div class="kpi-sub">{buy_count} BUY - {watch_count} WATCH - {avoid_count} AVOID</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Top Pick</div>
        <div class="kpi-value" style="font-size:20px">{top_pick}</div>
        <div class="kpi-sub">highest confidence</div>
      </div>
    </div>

    <div class="section-title">Agent Panel</div>
    <div class="agents-grid">
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#128301;</span><span class="agent-name">Scout</span></div>
        <div class="agent-role">screens the stock universe for movers</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">{universe}</div><div class="agent-stat-label">Scanned</div></div>
          <div class="agent-stat"><div class="agent-stat-value">{in_debate}</div><div class="agent-stat-label">Shortlisted</div></div>
        </div>
      </div>
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#128200;</span><span class="agent-name">Technician</span></div>
        <div class="agent-role">reads price action, RVOL and trend</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">{in_debate}</div><div class="agent-stat-label">Analyzed</div></div>
          <div class="agent-stat"><div class="agent-stat-value">-</div><div class="agent-stat-label">Avg RVOL</div></div>
        </div>
      </div>
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#129518;</span><span class="agent-name">Fundamentalist</span></div>
        <div class="agent-role">weighs valuation and analyst targets</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">{in_debate}</div><div class="agent-stat-label">Covered</div></div>
          <div class="agent-stat"><div class="agent-stat-value">-</div><div class="agent-stat-label">Avg Upside</div></div>
        </div>
      </div>
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#128240;</span><span class="agent-name">Newsdesk</span></div>
        <div class="agent-role">pulls live news and scores sentiment</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">-</div><div class="agent-stat-label">Headlines</div></div>
          <div class="agent-stat"><div class="agent-stat-value">-</div><div class="agent-stat-label">Net Tone</div></div>
        </div>
      </div>
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#128002;</span><span class="agent-name">Bull</span></div>
        <div class="agent-role">argues the case to buy</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">{in_debate}</div><div class="agent-stat-label">Cases</div></div>
          <div class="agent-stat"><div class="agent-stat-value">-</div><div class="agent-stat-label">Avg Score</div></div>
        </div>
      </div>
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#128059;</span><span class="agent-name">Bear</span></div>
        <div class="agent-role">argues the case against</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">{in_debate}</div><div class="agent-stat-label">Cases</div></div>
          <div class="agent-stat"><div class="agent-stat-value">-</div><div class="agent-stat-label">Avg Score</div></div>
        </div>
      </div>
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#9878;</span><span class="agent-name">Judge</span></div>
        <div class="agent-role">weighs the debate, issues verdict + confidence</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">{in_debate}</div><div class="agent-stat-label">Verdicts</div></div>
          <div class="agent-stat"><div class="agent-stat-value" style="color:#16a34a">{buy_signals}</div><div class="agent-stat-label">Buy</div></div>
        </div>
      </div>
      <div class="agent-card">
        <div class="agent-header"><span class="agent-icon">&#128235;</span><span class="agent-name">Messenger</span></div>
        <div class="agent-role">sends signals to Telegram</div>
        <span class="agent-status">&#9679; Done</span>
        <div class="agent-stats">
          <div class="agent-stat"><div class="agent-stat-value">{buy_signals}</div><div class="agent-stat-label">Sent</div></div>
          <div class="agent-stat"><div class="agent-stat-value" style="font-size:14px">{engine}</div><div class="agent-stat-label">Engine</div></div>
        </div>
      </div>
    </div>

    <div class="section-title">Latest Verdicts ({len(verdicts)})</div>
    <div class="verdicts-table">
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Name</th>
            <th>Verdict</th>
            <th style="text-align:center">Conf</th>
            <th>Winner</th>
            <th>Price</th>
            <th>Change</th>
            <th>Rationale</th>
          </tr>
        </thead>
        <tbody>
          {verdicts_html}
        </tbody>
      </table>
    </div>

    <div class="footer">
      Built from {universe} real stocks - data pulled {timestamp} - engine: {engine}<br>
      <span style="color:#9ca3af;font-size:12px">Analysis only. No trade was placed. Not investment advice.</span>
    </div>
  </div>
</body>
</html>'''

    return html

def main():
    data = load_output()
    html = generate_static_html(data)

    os.makedirs("_site", exist_ok=True)
    with open(STATIC_INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Static dashboard generated: {STATIC_INDEX_PATH}")
    print(f"   Universe: {data['kpi'].get('universe', 0)} stocks")
    print(f"   Verdicts: {len(data.get('verdicts', []))}")
    print(f"   BUY signals: {data['kpi'].get('buy_signals', 0)}")

if __name__ == "__main__":
    main()
