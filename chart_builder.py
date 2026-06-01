"""
HTML Chart Generation Module - Uses Lightweight Charts + ECharts to generate interactive HTML
Dark theme, professional-grade trading charts
"""

import json
from datetime import datetime
from typing import List, Dict


# Instrument precision mapping
PRECISION_MAP = {
    "BTC": 1,
    "ETH": 2,
    "XAU": 2,
    "XAG": 3,
    "BTC_SPOT": 1,
    "ETH_SPOT": 2
}

# Instrument display names
NAME_MAP = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "XAU": "Gold",
    "XAG": "Silver",
    "BTC_SPOT": "Bitcoin Spot",
    "ETH_SPOT": "Ethereum Spot"
}

# Signal colors
SIGNAL_COLORS = {
    "LONG": "#00E676",
    "SHORT": "#FF1744",
    "NEUTRAL": "#FFD600"
}


def _format_price(value: float, instrument: str) -> str:
    """Format price"""
    precision = PRECISION_MAP.get(instrument, 2)
    return f"{value:.{precision}f}"


def _get_display_name(instrument: str) -> str:
    """Get instrument display name (spot instruments marked with '(Spot)')"""
    name = NAME_MAP.get(instrument, instrument)
    if instrument.endswith("_SPOT"):
        name = name + " (Spot)"
    return name


def build_html(analysis_results: List[Dict]) -> str:
    """
    Generate interactive HTML file

    Args:
        analysis_results: List of analysis results for each instrument
            Each item contains: instrument, inst_id, trend, sr_zones, sr_scored,
                      big_order, oi_change, funding_check, big_order_confirm,
                      signal, df_1h_json, df_4h_json, ema144_series_json, ema169_series_json,
                      vp_result, liquidation_data, order_walls

    Returns:
        HTML string
    """
    # Prepare data for each instrument
    instruments_data = []
    for result in analysis_results:
        inst = result["instrument"]
        instruments_data.append({
            "instrument": inst,
            "name": _get_display_name(inst),
            "inst_id": result["inst_id"],
            "trend": result["trend"],
            "sr_zones": result.get("sr_zones", []),
            "sr_scored": result.get("sr_scored", []),
            "big_order": result.get("big_order", {}),
            "oi_change": result.get("oi_change", {}),
            "funding_check": result.get("funding_check", {}),
            "big_order_confirm": result.get("big_order_confirm", {}),
            "signal": result["signal"],
            # Multi-timeframe candle data
            "candles_1h": result.get("candles_1h_json", "[]"),
            "candles_4h": result.get("candles_4h_json", "[]"),
            "candles_1d": result.get("candles_1d_json", "[]"),
            # Multi-timeframe EMA data
            "ema144_1h": result.get("ema144_1h_json", "[]"),
            "ema169_1h": result.get("ema169_1h_json", "[]"),
            "ema144_4h": result.get("ema144_4h_json", "[]"),
            "ema169_4h": result.get("ema169_4h_json", "[]"),
            "ema144_1d": result.get("ema144_1d_json", "[]"),
            "ema169_1d": result.get("ema169_1d_json", "[]"),
            # Analysis data
            "vp_result": result.get("vp_result", {}),
            "liquidation_data": result.get("liquidation_data", []),
            "order_walls": result.get("order_walls", {"bid_walls": [], "ask_walls": [], "bid_total": 0, "ask_total": 0, "imbalance": 1.0}),
            "heatmap_data": result.get("heatmap_data", []),
        })

    instruments_json = json.dumps(instruments_data, ensure_ascii=False, default=str)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OKX Trading Signal Analysis</title>
<script src="/static/lightweight-charts.standalone.production.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{
    height: 100%;
    overflow: hidden;
  }}
  body {{
    font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0e17;
    color: #e1e5eb;
    display: flex;
    flex-direction: column;
  }}

  /* Top header bar - like desktop app */
  .app-header {{
    background: #0d1321;
    padding: 8px 16px;
    border-bottom: 1px solid #1e2738;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
    height: 42px;
  }}
  .app-header .app-title {{
    font-size: 14px;
    font-weight: 700;
    color: #fff;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .app-header .app-title .icon {{ font-size: 18px; }}
  .app-header .header-right {{
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 11px;
    color: #6b7280;
  }}

  /* Live refresh status indicator */
  .live-status {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
  }}
  .live-dot {{
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #4b5563;
    flex-shrink: 0;
  }}
  .live-dot.active {{
    background: #00E676;
    box-shadow: 0 0 4px rgba(0,230,118,0.6);
    animation: pulse 2s infinite;
  }}
  .live-dot.error {{
    background: #FF1744;
    box-shadow: 0 0 4px rgba(255,23,68,0.6);
  }}
  @keyframes pulse {{
    0% {{ opacity: 1; }}
    50% {{ opacity: 0.5; }}
    100% {{ opacity: 1; }}
  }}
  .live-label {{ color: #6b7280; }}
  .live-label.active {{ color: #00E676; }}
  .live-label.error {{ color: #FF1744; }}

  /* Overview bar - compact horizontal layout */
  .overview {{
    display: flex;
    gap: 8px;
    padding: 8px 12px;
    background: #0d1321;
    border-bottom: 1px solid #1e2738;
    flex-shrink: 0;
    overflow-x: auto;
  }}
  .overview-card {{
    background: #131825;
    border: 1px solid #1e2738;
    border-radius: 8px;
    padding: 8px 12px;
    position: relative;
    overflow: hidden;
    min-width: 180px;
    flex-shrink: 0;
    cursor: pointer;
    transition: border-color 0.2s;
  }}
  .overview-card:hover {{
    border-color: #2d3a4f;
  }}
  .overview-card::before {{
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
  }}
  .overview-card.long::before {{ background: #00E676; }}
  .overview-card.short::before {{ background: #FF1744; }}
  .overview-card.neutral::before {{ background: #FFD600; }}
  .overview-card .inst-name {{
    font-size: 13px;
    font-weight: 700;
    color: #fff;
  }}
  .overview-card .inst-full {{
    font-size: 10px;
    color: #6b7280;
    margin-top: 1px;
  }}
  .overview-card .live-price {{
    margin-top: 4px;
    padding: 4px 8px;
    background: rgba(255,255,255,0.03);
    border-radius: 4px;
    display: flex;
    align-items: baseline;
    gap: 6px;
  }}
  .overview-card .live-price .price-value {{
    font-size: 16px;
    font-weight: 700;
    color: #fff;
    font-variant-numeric: tabular-nums;
  }}
  .overview-card .live-price .price-change {{
    font-size: 11px;
    font-weight: 600;
  }}
  .overview-card .live-price .price-change.up {{ color: #00E676; }}
  .overview-card .live-price .price-change.down {{ color: #FF1744; }}
  .overview-card .live-price .price-loading {{
    font-size: 11px;
    color: #6b7280;
  }}
  .overview-card .signal-badge {{
    display: inline-block;
    margin-top: 4px;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 700;
  }}
  .overview-card.long .signal-badge {{ background: rgba(0,230,118,0.15); color: #00E676; }}
  .overview-card.short .signal-badge {{ background: rgba(255,23,68,0.15); color: #FF1744; }}
  .overview-card.neutral .signal-badge {{ background: rgba(255,214,0,0.15); color: #FFD600; }}
  .overview-card .trend-info {{
    margin-top: 4px;
    font-size: 10px;
    color: #6b7280;
  }}
  .overview-card .trend-info span {{ color: #d1d5db; font-weight: 500; }}

  /* Main workspace - flex fill */
  .workspace {{
    flex: 1;
    display: flex;
    overflow: hidden;
  }}

  /* Left sidebar instrument navigation */
  .sidebar {{
    width: 48px;
    background: #0b0f1a;
    border-right: 1px solid #1e2738;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding-top: 4px;
    flex-shrink: 0;
  }}
  .sidebar-item {{
    width: 40px;
    height: 40px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    margin-bottom: 4px;
    transition: all 0.2s;
    border: none;
    background: transparent;
    color: #6b7280;
    font-size: 11px;
    font-weight: 700;
  }}
  .sidebar-item:hover {{
    background: #1a2035;
    color: #d1d5db;
  }}
  .sidebar-item.active {{
    background: #1e2738;
    color: #FFD600;
  }}
  .sidebar-sep {{
    width: 32px;
    height: 1px;
    background: #1e2738;
    margin: 6px 0;
    flex-shrink: 0;
  }}
  .sidebar-tradfi {{
    width: 40px;
    height: 32px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    margin-bottom: 2px;
    transition: all 0.2s;
    border: 1px solid #388bfd33;
    background: #0d419d22;
    color: #58a6ff;
    font-size: 10px;
    font-weight: 700;
    flex-shrink: 0;
  }}
  .sidebar-tradfi:hover {{
    background: #0d419d44;
    border-color: #58a6ff88;
    color: #79c0ff;
  }}

  /* Content area */
  .main-content {{
    flex: 1;
    display: none;
    overflow: hidden;
    min-height: 0;
  }}
  .main-content.active {{
    display: grid;
    grid-template-columns: 1fr 420px;
    gap: 0;
    height: 100%;
    min-height: 0;
  }}

  /* Chart area */
  .chart-area {{
    background: #131825;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-height: 0;
  }}
  .chart-header {{
    padding: 8px 14px;
    border-bottom: 1px solid #1e2738;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
  }}
  .chart-header .title {{
    font-size: 14px;
    font-weight: 600;
    color: #fff;
  }}

  /* Right panel */
  .info-panel {{
    display: flex;
    flex-direction: column;
    gap: 0;
    overflow-y: auto;
    overflow-x: hidden;
    background: #0d1321;
    min-height: 0;
    scrollbar-width: thin;
    scrollbar-color: #1e2738 transparent;
  }}
  .info-panel::-webkit-scrollbar {{
    width: 6px;
  }}
  .info-panel::-webkit-scrollbar-track {{
    background: transparent;
  }}
  .info-panel::-webkit-scrollbar-thumb {{
    background: #1e2738;
    border-radius: 3px;
  }}
  .panel-card {{
    background: transparent;
    border-bottom: 1px solid #1e2738;
    flex-shrink: 0;
  }}
  .panel-card .card-header {{
    padding: 12px 16px;
    font-size: 13px;
    font-weight: 700;
    color: #d1d5db;
    letter-spacing: 0.3px;
    user-select: none;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .panel-card .card-body {{
    padding: 14px 16px;
    font-size: 12px;
    overflow-y: visible;
  }}

  /* Trend card */
  .trend-status {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }}
  .trend-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }}
  .trend-dot.golden {{ background: #00E676; }}
  .trend-dot.death {{ background: #FF1744; }}
  .trend-dot.entangled {{ background: #FFD600; }}
  .trend-label {{
    font-weight: 600;
    font-size: 13px;
  }}
  .trend-label.golden {{ color: #00E676; }}
  .trend-label.death {{ color: #FF1744; }}
  .trend-label.entangled {{ color: #FFD600; }}
  .trend-detail {{
    font-size: 11px;
    color: #6b7280;
    line-height: 1.7;
  }}
  .trend-detail span {{ color: #d1d5db; font-weight: 500; }}

  /* S/R list */
  .sr-item {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;
    border-bottom: 1px solid #1a2035;
  }}
  .sr-item:last-child {{ border-bottom: none; }}
  .sr-level {{
    font-weight: 600;
    font-size: 12px;
    color: #d1d5db;
  }}
  .sr-type {{
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    font-weight: 600;
  }}
  .sr-type.support {{ background: rgba(0,230,118,0.1); color: #00E676; }}
  .sr-type.resistance {{ background: rgba(255,23,68,0.1); color: #FF1744; }}
  .sr-score {{
    font-size: 10px;
  }}
  .sr-score.super {{ color: #FFD600; font-weight: 700; }}
  .sr-score.strong {{ color: #FFD600; }}
  .sr-score.weak {{ color: #6b7280; }}
  .sr-badges {{
    display: flex;
    gap: 3px;
    flex-wrap: wrap;
  }}
  .sr-badge {{
    font-size: 9px;
    padding: 1px 4px;
    border-radius: 2px;
    background: rgba(255,255,255,0.05);
    color: #9ca3af;
  }}
  .sr-badge.resonance-strong {{
    background: rgba(255,214,0,0.2);
    color: #FFD600;
    font-weight: 600;
  }}
  .sr-badge.resonance-normal {{
    color: #FFD600;
    background: rgba(255,214,0,0.1);
  }}
  .sr-badge.resonance-weak {{
    color: #6b7280;
    background: rgba(255,255,255,0.03);
  }}
  .sr-badge.liquidation {{
    color: #FF9100;
    background: rgba(255,145,0,0.1);
  }}
  .sr-badge.order-wall {{
    color: #00BCD4;
    background: rgba(0,188,212,0.1);
  }}

  /* Volume Profile summary */
  .vp-summary {{
    font-size: 11px;
    color: #9ca3af;
    line-height: 1.7;
  }}
  .vp-summary span {{
    color: #d1d5db;
    font-weight: 500;
  }}

  /* Checklist */
  .checklist-item {{
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 0;
    font-size: 12px;
  }}
  .check-icon {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 700;
  }}
  .check-icon.pass {{ background: rgba(0,230,118,0.2); color: #00E676; }}
  .check-icon.fail {{ background: rgba(255,23,68,0.2); color: #FF1744; }}

  /* Signal details */
  .signal-detail {{
    background: rgba(255,255,255,0.03);
    border-radius: 6px;
    padding: 8px;
    margin-top: 6px;
  }}
  .signal-detail .price-row {{
    display: flex;
    justify-content: space-between;
    padding: 2px 0;
    font-size: 12px;
  }}
  .signal-detail .price-label {{ color: #6b7280; }}
  .signal-detail .price-value {{ font-weight: 600; color: #fff; }}
  .signal-detail .price-value.green {{ color: #00E676; }}
  .signal-detail .price-value.red {{ color: #FF1744; }}

  /* Big order summary */
  .big-order-bar {{
    display: flex;
    height: 5px;
    border-radius: 3px;
    overflow: hidden;
    margin: 6px 0;
  }}
  .big-order-bar .buy {{ background: #00E676; }}
  .big-order-bar .sell {{ background: #FF1744; }}
  .big-order-stat {{
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: #9ca3af;
  }}
  .big-order-stat .buy-stat {{ color: #00E676; }}
  .big-order-stat .sell-stat {{ color: #FF1744; }}

  /* Order wall card */
  .wall-item {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 0;
    font-size: 11px;
  }}
  .wall-item .wall-price {{
    font-weight: 600;
    color: #d1d5db;
  }}
  .wall-item .wall-size {{
    color: #9ca3af;
  }}
  .wall-item .wall-strength {{
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 3px;
    font-weight: 600;
  }}
  .wall-item .wall-strength.bid {{ background: rgba(0,230,118,0.15); color: #00E676; }}
  .wall-item .wall-strength.ask {{ background: rgba(255,23,68,0.15); color: #FF1744; }}
  .imbalance-bar {{
    display: flex;
    height: 6px;
    border-radius: 3px;
    overflow: hidden;
    margin: 4px 0;
    background: #1e2738;
  }}
  .imbalance-bar .bid-bar {{ background: #00E676; }}
  .imbalance-bar .ask-bar {{ background: #FF1744; }}
  .imbalance-label {{
    font-size: 11px;
    color: #9ca3af;
    margin-top: 2px;
  }}
  .imbalance-label.bullish {{ color: #00E676; }}
  .imbalance-label.bearish {{ color: #FF1744; }}


  /* Timeframe toggle buttons */
  .tf-buttons {{
    display: flex;
    gap: 1px;
    background: #0d1321;
    border-radius: 4px;
    padding: 2px;
  }}
  .tf-btn {{
    padding: 3px 10px;
    border-radius: 3px;
    border: none;
    background: transparent;
    color: #6b7280;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s;
    font-family: inherit;
  }}
  .tf-btn:hover {{
    color: #d1d5db;
  }}
  .tf-btn.active {{
    background: #1e2738;
    color: #FFD600;
  }}

  /* Liquidation heatmap table */
  .liq-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }}
  .liq-table th {{
    text-align: left;
    padding: 5px 8px;
    color: #6b7280;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    border-bottom: 1px solid #1e2738;
    white-space: nowrap;
  }}
  .liq-table td {{
    padding: 5px 8px;
    border-bottom: 1px solid #131825;
    vertical-align: middle;
  }}
  .liq-table tr:hover {{
    background: rgba(255,255,255,0.02);
  }}
  .liq-rating {{
    display: flex;
    flex-direction: row;
    gap: 1px;
    line-height: 1;
  }}
  .liq-star {{
    font-size: 8px;
    line-height: 1.2;
  }}
  .liq-star.on {{ color: #FFD600; }}
  .liq-star.off {{ color: #2a2f3a; }}
  .liq-level-badge {{
    display: inline-block;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.3px;
    margin-top: 2px;
  }}
  .liq-level-badge.magnet {{
    background: rgba(255,23,68,0.2);
    color: #FF1744;
    border: 1px solid rgba(255,23,68,0.3);
  }}
  .liq-level-badge.strong {{
    background: rgba(255,145,0,0.15);
    color: #FF9100;
    border: 1px solid rgba(255,145,0,0.2);
  }}
  .liq-level-badge.medium {{
    background: rgba(255,214,0,0.12);
    color: #FFD600;
    border: 1px solid rgba(255,214,0,0.15);
  }}
  .liq-level-badge.weak {{
    background: rgba(107,114,128,0.1);
    color: #6b7280;
    border: 1px solid rgba(107,114,128,0.15);
  }}
  .liq-direction {{
    font-size: 10px;
    font-weight: 600;
  }}
  .liq-direction.long-dom {{
    color: #00E676;
  }}
  .liq-direction.short-dom {{
    color: #FF1744;
  }}
  .liq-direction.balanced {{
    color: #6b7280;
  }}
  .liq-bar-container {{
    display: flex;
    height: 3px;
    border-radius: 2px;
    overflow: hidden;
    background: #1a2035;
    margin-top: 2px;
  }}
  .liq-bar-long {{
    background: #00E676;
    border-radius: 2px 0 0 2px;
  }}
  .liq-bar-short {{
    background: #FF1744;
    border-radius: 0 2px 2px 0;
  }}
  .liq-empty {{
    color: #6b7280;
    font-size: 12px;
    text-align: center;
    padding: 12px 0;
  }}

  /* Tablet: panel takes 15% */
  @media (max-width: 1024px) {{
    .main-content.active {{
      grid-template-columns: 1fr 15%;
    }}
  }}
  /* Mobile: panel drops down */
  @media (max-width: 900px) {{
    .main-content.active {{
      grid-template-columns: 1fr;
    }}
    .overview {{
      flex-wrap: wrap;
    }}
  }}
</style>
</head>
<body>

<div class="app-header">
  <div class="app-title">
    <span class="icon">📊</span> OKX Trading Signal Analysis
  </div>
  <div class="header-right">
    <div class="live-status">
      <div class="live-dot" id="live-dot"></div>
      <span class="live-label" id="live-label">Waiting for connection...</span>
      <span class="live-time" id="live-time"></span>
    </div>
  </div>
</div>

<div class="overview" id="overview"></div>

<div class="workspace">
  <div class="sidebar" id="sidebar"></div>
  <div id="content-area" style="flex:1;overflow:hidden;min-height:0;"></div>
</div>

<script>
const DATA = {instruments_json};

// Render overview cards
function renderOverview() {{
  const container = document.getElementById('overview');
  container.innerHTML = DATA.map((d, i) => {{
    const sig = d.signal.direction;
    const cls = sig === 'LONG' ? 'long' : sig === 'SHORT' ? 'short' : 'neutral';
    const sigText = sig === 'LONG' ? '🟢 LONG' : sig === 'SHORT' ? '🔴 SHORT' : '🟡 NEUTRAL';
    const trendText = d.trend.trend === 'GOLDEN_CROSS' ? 'Bullish' : d.trend.trend === 'DEATH_CROSS' ? 'Bearish' : 'Entangled';
    const strengthText = d.trend.trend_strength === 'early' ? 'Early' : d.trend.trend_strength === 'mid' ? 'Mid' : 'Overheated';
    return `
      <div class="overview-card ${{cls}}" data-instrument="${{d.instrument}}" onclick="switchTab(${{i}})">
        <div class="inst-name">${{d.instrument}}</div>
        <div class="inst-full">${{d.name}}</div>
        <div class="live-price" id="price-${{d.instrument}}">
          <span class="price-loading">Loading...</span>
        </div>
        <div class="signal-badge">${{sigText}}</div>
        <div class="trend-info">
          Trend: <span>${{trendText}} (${{strengthText}})</span> · Separation: <span>${{d.trend.separation_pct}}%</span>
        </div>
      </div>
    `;
  }}).join('');
}}

// Render left sidebar instrument navigation
function renderSidebar() {{
  const container = document.getElementById('sidebar');
  const shortNames = {{'BTC': 'BTC', 'ETH': 'ETH', 'XAU': 'XAU', 'XAG': 'XAG', 'BTC_SPOT': 'BTC*', 'ETH_SPOT': 'ETH*'}};
  container.innerHTML =
    '<button class="sidebar-tradfi" onclick="location.href=&quot;/tradfi&quot;" title="TradFi All Instruments Analysis">TF</button>' +
    '<div class="sidebar-sep"></div>' +
    DATA.map((d, i) => `
    <button class="sidebar-item ${{i===0?'active':''}}" onclick="switchTab(${{i}})" title="${{d.name}}">${{shortNames[d.instrument] || d.instrument}}</button>
  `).join('');
}}

// Render content area
function renderContent() {{
  const container = document.getElementById('content-area');
  container.innerHTML = DATA.map((d, i) => `
    <div class="main-content ${{i===0?'active':''}}" id="content-${{i}}">
      <div class="chart-area">
        <div class="chart-header">
          <div class="title">${{d.name}} (${{d.instrument}})</div>
          <div style="display:flex;align-items:center;gap:8px;">
            <div class="tf-buttons">
              <button class="tf-btn active" onclick="switchTimeframe(${{i}}, '1H')">1H</button>
              <button class="tf-btn" onclick="switchTimeframe(${{i}}, '4H')">4H</button>
              <button class="tf-btn" onclick="switchTimeframe(${{i}}, '1D')">1D</button>
            </div>
          </div>
        </div>
        <div style="position:relative;width:100%;flex:1;min-height:0;">
          <div id="chart-${{i}}" style="width:100%;height:100%;"></div>
        </div>
      </div>
      <div class="info-panel">
        ${{renderTrendCard(d)}}
        ${{renderSRCurd(d)}}
        ${{renderLiquidationCard(d)}}
        ${{renderOrderWallCard(d)}}
        ${{renderVPCard(d)}}
        ${{renderBigOrderCard(d)}}
        ${{renderSignalCard(d)}}
        ${{renderChecklistCard(d)}}
      </div>
    </div>
  `).join('');
}}

function renderTrendCard(d) {{
  const trend = d.trend;
  const cls = trend.trend === 'GOLDEN_CROSS' ? 'golden' : trend.trend === 'DEATH_CROSS' ? 'death' : 'entangled';
  const label = trend.trend === 'GOLDEN_CROSS' ? 'Bullish Trend' : trend.trend === 'DEATH_CROSS' ? 'Bearish Trend' : 'Entangled - Neutral';
  const strengthMap = {{early: 'Early', mid: 'Mid', overheated: 'Overheated⚠️'}};
  const prec = PRECISION_MAP[d.instrument] || 2;
  return `
    <div class="panel-card">
      <div class="card-header">📈 EMA Trend</div>
      <div class="card-body">
        <div class="trend-status">
          <div class="trend-dot ${{cls}}"></div>
          <div class="trend-label ${{cls}}">${{label}}</div>
        </div>
        <div class="trend-detail">
          EMA144: <span>${{trend.ema144.toFixed(prec)}}</span><br>
          EMA169: <span>${{trend.ema169.toFixed(prec)}}</span><br>
          Separation: <span>${{trend.separation_pct}}%</span><br>
          Strength: <span>${{strengthMap[trend.trend_strength] || trend.trend_strength}}</span>
        </div>
      </div>
    </div>
  `;
}}

function renderSRCurd(d) {{
  const zones = d.sr_zones || [];
  const scored = d.sr_scored || [];
  // Merge display
  const allSR = zones.map(z => {{
    const s = scored.find(sc => Math.abs(sc.level - z.level) / z.level < 0.005);
    return {{
      ...z,
      score: s ? s.score : z.score,
      strength: s ? s.strength : z.strength,
      is_psychological: s ? s.is_psychological : false,
      is_fvg: s ? s.is_fvg : false,
      is_liquidation: s ? s.is_liquidation : false,
      is_order_wall: s ? s.is_order_wall : false,
      resonance: z.resonance || null,
      resonance_reason: z.resonance_reason || ''
    }};
  }}).sort((a,b) => b.level - a.level);

  const prec = PRECISION_MAP[d.instrument] || 2;
  return `
    <div class="panel-card">
      <div class="card-header">🎯 Support & Resistance</div>
      <div class="card-body">
        ${{allSR.length === 0 ? '<div style="color:#6b7280;font-size:12px;">No valid S/R</div>' : allSR.map(sr => {{
          // Resonance badge
          let resonanceBadge = '';
          if (sr.resonance === 'strong') {{
            resonanceBadge = '<span class="sr-badge resonance-strong">⚡Resonance</span>';
          }} else if (sr.resonance === 'normal') {{
            resonanceBadge = '<span class="sr-badge resonance-normal">Resonance</span>';
          }} else if (sr.resonance === 'weak') {{
            resonanceBadge = '<span class="sr-badge resonance-weak">Weak Resonance</span>';
          }}
          // Liquidation zone badge
          let liqBadge = sr.is_liquidation ? '<span class="sr-badge liquidation">Liquidation Zone</span>' : '';
          // Order wall badge
          let wallBadge = sr.is_order_wall ? '<span class="sr-badge order-wall">Order Wall</span>' : '';

          return `
            <div class="sr-item">
              <div>
                <span class="sr-level">${{sr.level.toFixed(prec)}}</span>
                <span class="sr-type ${{sr.type}}">${{sr.type === 'support' ? 'Support' : 'Resistance'}}</span>
              </div>
              <div>
                <span class="sr-score ${{sr.strength}}">${{sr.score}}·${{sr.strength === 'super' ? 'Super' : sr.strength === 'strong' ? 'Strong' : 'Weak'}}</span>
                <div class="sr-badges">
                  ${{sr.is_psychological ? '<span class="sr-badge">Round Number</span>' : ''}}
                  ${{sr.is_fvg ? '<span class="sr-badge">FVG</span>' : ''}}
                  ${{liqBadge}}
                  ${{wallBadge}}
                  ${{resonanceBadge}}
                </div>
              </div>
            </div>
          `;
        }}).join('')}}
      </div>
    </div>
  `;
}}

function renderLiquidationCard(d) {{
  const hm = d.heatmap_data || [];
  const prec = PRECISION_MAP[d.instrument] || 2;

  // Only show important liquidation levels with rating>=2, sorted by rating desc then total liq desc
  const filtered = hm.filter(h => h.rating >= 2)
    .sort((a, b) => b.rating - a.rating || b.total_liq - a.total_liq);

  // Rating description mapping
  const ratingLabel = {{5: 'Magnet', 4: 'Strong', 3: 'Medium', 2: 'Weak'}};
  const ratingCls = {{5: 'magnet', 4: 'strong', 3: 'medium', 2: 'weak'}};

  // Find max total_liq for bar chart ratio
  const maxTotal = filtered.length > 0 ? Math.max(...filtered.map(f => f.total_liq)) : 1;

  // Generate star rating
  function stars(r) {{
    let s = '';
    for (let i = 1; i <= 5; i++) {{
      s += '<span class="liq-star ' + (i <= r ? 'on' : 'off') + '">★</span>';
    }}
    return s;
  }}

  // Format liquidation volume (smart units)
  function fmtLiq(v) {{
    if (v >= 1e8) return (v / 1e8).toFixed(1) + 'B';
    if (v >= 1e4) return (v / 1e4).toFixed(1) + 'K';
    return v.toFixed(0);
  }}

  if (filtered.length === 0) {{
    return `
      <div class="panel-card">
        <div class="card-header">🔥 Liquidation Heatmap</div>
        <div class="card-body">
          <div class="liq-empty">${{d.instrument.endsWith('_SPOT') ? 'No liquidation data for spot' : 'No significant liquidation levels'}}</div>
        </div>
      </div>
    `;
  }}

  // Summary statistics
  const totalLong = filtered.reduce((s, f) => s + f.long_liq, 0);
  const totalShort = filtered.reduce((s, f) => s + f.short_liq, 0);
  const totalAll = totalLong + totalShort;
  const longPct = totalAll > 0 ? (totalLong / totalAll * 100) : 50;
  const shortPct = totalAll > 0 ? (totalShort / totalAll * 100) : 50;
  const magnetCount = filtered.filter(f => f.rating >= 5).length;

  return `
    <div class="panel-card">
      <div class="card-header">🔥 Liquidation Heatmap <span style="font-size:10px;color:#6b7280;font-weight:400;">(${{filtered.length}} levels)</span></div>
      <div class="card-body" style="padding:6px 0;">
        <!-- Summary bar -->
        <div style="padding:0 10px 6px;border-bottom:1px solid #1e2738;">
          <div class="liq-bar-container" style="height:6px;">
            <div class="liq-bar-long" style="width:${{longPct}}%"></div>
            <div class="liq-bar-short" style="width:${{shortPct}}%"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:3px;font-size:10px;">
            <span style="color:#00E676;">Bullish ${{fmtLiq(totalLong)}}</span>
            <span style="color:#6b7280;">${{magnetCount > 0 ? '⚡Magnet x' + magnetCount : ''}}</span>
            <span style="color:#FF1744;">Bearish ${{fmtLiq(totalShort)}}</span>
          </div>
        </div>
        <!-- Table -->
        <table class="liq-table">
          <thead>
            <tr>
              <th>Price</th>
              <th>Rating</th>
              <th>Direction</th>
              <th>Liq. Volume</th>
            </tr>
          </thead>
          <tbody>
            ${{filtered.slice(0, 15).map(f => {{
              const longRatio = f.long_liq / (f.total_liq || 0.001);
              const shortRatio = f.short_liq / (f.total_liq || 0.001);
              let dirCls = 'balanced';
              let dirText = 'Balanced';
              if (longRatio > 0.6) {{ dirCls = 'long-dom'; dirText = 'Long-leaning'; }}
              else if (shortRatio > 0.6) {{ dirCls = 'short-dom'; dirText = 'Short-leaning'; }}

              // Long/Short ratio bar chart
              const barLongPct = (f.long_liq / (f.total_liq || 0.001) * 100).toFixed(0);
              const barShortPct = (100 - parseInt(barLongPct));

              return `
                <tr>
                  <td style="font-weight:600;color:#d1d5db;font-variant-numeric:tabular-nums;">${{f.price.toFixed(prec)}}</td>
                  <td>
                    <div class="liq-rating">${{stars(f.rating)}}</div>
                    <span class="liq-level-badge ${{ratingCls[f.rating] || 'weak'}}">${{ratingLabel[f.rating] || 'Weak'}}</span>
                  </td>
                  <td>
                    <span class="liq-direction ${{dirCls}}">${{dirText}}</span>
                    <div class="liq-bar-container">
                      <div class="liq-bar-long" style="width:${{barLongPct}}%"></div>
                      <div class="liq-bar-short" style="width:${{barShortPct}}%"></div>
                    </div>
                  </td>
                  <td style="color:#9ca3af;font-variant-numeric:tabular-nums;">${{fmtLiq(f.total_liq)}}</td>
                </tr>
              `;
            }}).join('')}}
          </tbody>
        </table>
        ${{filtered.length > 15 ? '<div style="text-align:center;padding:4px 0;color:#6b7280;font-size:10px;">+' + (filtered.length - 15) + ' more weak levels not shown</div>' : ''}}
      </div>
    </div>
  `;
}}

function renderOrderWallCard(d) {{
  const ow = d.order_walls || {{}};
  const bidWalls = ow.bid_walls || [];
  const askWalls = ow.ask_walls || [];
  const imbalance = ow.imbalance || 1.0;
  const bidTotal = ow.bid_total || 0;
  const askTotal = ow.ask_total || 0;
  const prec = PRECISION_MAP[d.instrument] || 2;

  // If no order wall data, show message
  if (bidWalls.length === 0 && askWalls.length === 0) {{
    return `
      <div class="panel-card">
        <div class="card-header">🧱 Order Walls</div>
        <div class="card-body">
          <div style="color:#6b7280;font-size:12px;">No significant order walls detected</div>
          <div class="imbalance-label">Bid/Ask Ratio: ${{imbalance.toFixed(2)}}</div>
        </div>
      </div>
    `;
  }}

  // Bid/Ask ratio bar chart
  const totalVol = bidTotal + askTotal;
  const bidPct = totalVol > 0 ? (bidTotal / totalVol * 100) : 50;
  const askPct = totalVol > 0 ? (askTotal / totalVol * 100) : 50;

  // Bid/Ask ratio color
  let imbalanceCls = '';
  let imbalanceText = '';
  if (imbalance > 1.5) {{
    imbalanceCls = 'bullish';
    imbalanceText = 'Long-leaning';
  }} else if (imbalance < 0.67) {{
    imbalanceCls = 'bearish';
    imbalanceText = 'Short-leaning';
  }} else {{
    imbalanceText = 'Balanced';
  }}

  return `
    <div class="panel-card">
      <div class="card-header">🧱 Order Walls</div>
      <div class="card-body">
        <div style="font-size:11px;color:#6b7280;margin-bottom:6px;">Bid Walls</div>
        ${{bidWalls.slice(0, 5).map(w => `
          <div class="wall-item">
            <span class="wall-price" style="color:#00E676;">${{w.price.toFixed(prec)}}</span>
            <span class="wall-size">${{w.size.toFixed(2)}}</span>
            <span class="wall-strength bid">${{w.strength}}x</span>
          </div>
        `).join('')}}
        ${{bidWalls.length === 0 ? '<div style="color:#6b7280;font-size:11px;">None</div>' : ''}}

        <div style="font-size:11px;color:#6b7280;margin-top:8px;margin-bottom:6px;">Ask Walls</div>
        ${{askWalls.slice(0, 5).map(w => `
          <div class="wall-item">
            <span class="wall-price" style="color:#FF1744;">${{w.price.toFixed(prec)}}</span>
            <span class="wall-size">${{w.size.toFixed(2)}}</span>
            <span class="wall-strength ask">${{w.strength}}x</span>
          </div>
        `).join('')}}
        ${{askWalls.length === 0 ? '<div style="color:#6b7280;font-size:11px;">None</div>' : ''}}

        <div style="margin-top:10px;">
          <div class="imbalance-bar">
            <div class="bid-bar" style="width:${{bidPct}}%"></div>
            <div class="ask-bar" style="width:${{askPct}}%"></div>
          </div>
          <div class="imbalance-label ${{imbalanceCls}}">
            Bid/Ask Ratio: ${{imbalance.toFixed(2)}} (${{imbalanceText}})
          </div>
        </div>
      </div>
    </div>
  `;
}}

function renderVPCard(d) {{
  const vp = d.vp_result || {{}};
  const prec = PRECISION_MAP[d.instrument] || 2;
  if (!vp.poc) {{
    return '';
  }}
  const nodes = (vp.high_volume_nodes || []).sort((a,b) => a - b);
  const topNodes = nodes.slice(0, 3);
  return `
    <div class="panel-card">
      <div class="card-header">📊 Volume Profile</div>
      <div class="card-body">
        <div class="vp-summary">
          POC: <span>${{vp.poc.toFixed(prec)}}</span><br>
          Value Area: <span>${{(vp.va_low||0).toFixed(prec)}} ~ ${{(vp.va_high||0).toFixed(prec)}}</span><br>
          High Volume Nodes (${{nodes.length}}):
          ${{topNodes.length > 0 ? '<br>' + topNodes.map(n => '&nbsp;&nbsp;▸ <span>' + n.toFixed(prec) + '</span>').join('<br>') : ' <span>None</span>'}}
          ${{nodes.length > 3 ? '<br><span style="font-size:10px;color:#6b7280;">&nbsp;&nbsp;...and ' + (nodes.length - 3) + ' more</span>' : ''}}
        </div>
      </div>
    </div>
  `;
}}

function renderBigOrderCard(d) {{
  const bo = d.big_order || {{}};
  const fc = d.funding_check || {{}};
  const prec = PRECISION_MAP[d.instrument] || 2;
  const total = (bo.buy_volume || 0) + (bo.sell_volume || 0);
  const buyPct = total > 0 ? (bo.buy_volume / total * 100) : 50;
  const sellPct = total > 0 ? (bo.sell_volume / total * 100) : 50;

  // When spot instrument has no data
  const isSpotNoData = bo.buy_count === 0 && bo.sell_count === 0;

  return `
    <div class="panel-card">
      <div class="card-header">🐋 Big Orders & Funding</div>
      <div class="card-body">
        ${{isSpotNoData ? '<div style="color:#6b7280;font-size:12px;margin-bottom:8px;">No tick trade data for spot</div>' : `
        <div class="big-order-bar">
          <div class="buy" style="width:${{buyPct}}%"></div>
          <div class="sell" style="width:${{sellPct}}%"></div>
        </div>
        <div class="big-order-stat">
          <span class="buy-stat">Buy ${{(bo.buy_volume||0).toFixed(2)}} (${{bo.buy_count||0}} trades)</span>
          <span class="sell-stat">Sell ${{(bo.sell_volume||0).toFixed(2)}} (${{bo.sell_count||0}} trades)</span>
        </div>
        <div style="margin-top:8px;font-size:12px;color:#9ca3af;">
          Big Order Ratio: <span style="color:#fff;">${{(bo.big_ratio||1).toFixed(2)}}</span> ·
          Signal: <span style="color:${{(bo.signal==='BULLISH'?'#00E676':bo.signal==='BEARISH'?'#FF1744':'#FFD600')}}">${{bo.signal||'NEUTRAL'}}</span>
        </div>
        `}}
        <div style="margin-top:6px;font-size:12px;color:#9ca3af;">
          Funding Rate: <span style="color:#fff;">${{fc.status === 'N/A' ? 'N/A (Spot)' : ((fc.rate||0)*100).toFixed(4) + '%'}}'</span>
          ${{fc.status !== 'N/A' ? `<span style="color:${{(fc.status==='NORMAL'?'#00E676':fc.status==='ELEVATED'?'#FFD600':'#FF1744')}}">(${{fc.status||'UNKNOWN'}})</span>` : ''}}
        </div>
        ${{fc.warning && fc.status !== 'N/A' ? '<div style="font-size:11px;color:#FF9100;margin-top:4px;">⚠️ '+fc.warning+'</div>' : ''}}
      </div>
    </div>
  `;
}}

function renderSignalCard(d) {{
  const sig = d.signal;
  const dir = sig.direction;
  const cls = dir === 'LONG' ? 'green' : dir === 'SHORT' ? 'red' : '';
  const prec = PRECISION_MAP[d.instrument] || 2;
  return `
    <div class="panel-card">
      <div class="card-header">🔔 Trading Signal</div>
      <div class="card-body">
        <div style="font-size:18px;font-weight:700;color:${{dir==='LONG'?'#00E676':dir==='SHORT'?'#FF1744':'#FFD600'}};">
          ${{dir === 'LONG' ? '▲ LONG' : dir === 'SHORT' ? '▼ SHORT' : '— NEUTRAL'}}
        </div>
        ${{dir !== 'NEUTRAL' ? `
        <div class="signal-detail">
          <div class="price-row">
            <span class="price-label">Entry Price</span>
            <span class="price-value">${{sig.entry_price.toFixed(prec)}}</span>
          </div>
          <div class="price-row">
            <span class="price-label">Stop Loss</span>
            <span class="price-value red">${{sig.stop_loss.toFixed(prec)}}</span>
          </div>
          <div class="price-row">
            <span class="price-label">Take Profit</span>
            <span class="price-value green">${{sig.take_profit.toFixed(prec)}}</span>
          </div>
          <div class="price-row">
            <span class="price-label">Risk/Reward</span>
            <span class="price-value">${{sig.risk_reward_ratio}}:1</span>
          </div>
        </div>
        ` : ''}}
        <div style="margin-top:8px;font-size:12px;color:#9ca3af;line-height:1.6;">
          ${{(sig.reasons||[]).map(r => '• ' + r).join('<br>')}}
        </div>
      </div>
    </div>
  `;
}}

function renderChecklistCard(d) {{
  const cl = d.signal.checklist || {{}};
  const labels = {{
    ema_trend: 'EMA Trend Direction',
    in_support_zone: 'Price in Support Zone',
    in_resistance_zone: 'Price in Resistance Zone',
    bullish_candle: '1H Bullish Reversal Candle',
    bearish_candle: '1H Bearish Reversal Candle',
    big_order_confirm: 'Big Order Confirmation',
    funding_rate_ok: 'Funding Rate Normal'
  }};
  return `
    <div class="panel-card">
      <div class="card-header">✅ Signal Checklist</div>
      <div class="card-body">
        ${{Object.entries(cl).map(([k, v]) => `
          <div class="checklist-item">
            <div class="check-icon ${{v?'pass':'fail'}}">${{v?'✓':'✗'}}</div>
            <span>${{labels[k] || k}}</span>
          </div>
        `).join('')}}
      </div>
    </div>
  `;
}}

// Switch Tab (lazy loading: chart created only on first switch to instrument)
function switchTab(idx) {{
  document.querySelectorAll('.sidebar-item').forEach((s, i) => {{
    s.classList.toggle('active', i === idx);
  }});
  document.querySelectorAll('.main-content').forEach((c, i) => {{
    c.classList.toggle('active', i === idx);
  }});
  // Lazy loading: create chart if not yet created
  if (!chartCreated[idx]) {{
    createChartFor(idx);
  }}
}}

// Create chart
// Store chart references for real-time updates
const chartRefs = [];
// Track which instrument charts have been created
const chartCreated = [];
// Current selected timeframe
const currentTF = {{}};

function createChartFor(idx) {{
  const d = DATA[idx];
  const container = document.getElementById('chart-' + idx);
  if (!container || chartCreated[idx]) return;
  chartCreated[idx] = true;

  // Default to 1H timeframe
  currentTF[idx] = '1H';

  try {{
    const chart = LightweightCharts.createChart(container, {{
      width: container.clientWidth,
      height: container.clientHeight || 800,
      layout: {{
        background: {{ type: 'solid', color: '#131825' }},
        textColor: '#6b7280',
        fontSize: 12,
      }},
      grid: {{
        vertLines: {{ color: '#1e2738' }},
        horzLines: {{ color: '#1e2738' }},
      }},
      crosshair: {{
        mode: LightweightCharts.CrosshairMode.Normal,
      }},
      rightPriceScale: {{
        borderColor: '#1e2738',
      }},
      timeScale: {{
        borderColor: '#1e2738',
        timeVisible: true,
        secondsVisible: false,
      }},
    }});

    // Candlestick
    const candleSeries = chart.addCandlestickSeries({{
      upColor: '#00E676',
      downColor: '#FF1744',
      borderUpColor: '#00E676',
      borderDownColor: '#FF1744',
      wickUpColor: '#00E676',
      wickDownColor: '#FF1744',
    }});

    // Parse default 1H candle data
    let candles;
    try {{
      candles = JSON.parse(d.candles_1h);
    }} catch(e) {{
      candles = [];
    }}

    // Store chart references
    chartRefs[idx] = {{
      chart: chart,
      candleSeries: candleSeries,
      instrument: d.instrument,
      candles: candles,
      ema144Series: null,
      ema169Series: null,
      priceLines: [],
    }};

    // Load data for default timeframe
    loadChartData(idx, '1H');

    // Responsive
    const resizeObserver = new ResizeObserver(entries => {{
      for (const entry of entries) {{
        chart.applyOptions({{
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        }});
      }}
    }});
    resizeObserver.observe(container);
  }} catch(err) {{
    console.error('[Chart] Error creating chart for', d.instrument, ':', err);
  }}
}}

// Load candle and EMA data for specified timeframe
function loadChartData(idx, tf) {{
  const ref = chartRefs[idx];
  const d = DATA[idx];
  if (!ref || !d) return;

  // Get candle data for the specified timeframe
  let candles;
  const candlesKey = 'candles_' + tf.toLowerCase();
  try {{
    candles = JSON.parse(d[candlesKey]);
  }} catch(e) {{
    candles = [];
  }}

  if (candles.length === 0) return;

  // Clear old EMA lines and price lines
  if (ref.ema144Series) {{
    ref.chart.removeSeries(ref.ema144Series);
    ref.ema144Series = null;
  }}
  if (ref.ema169Series) {{
    ref.chart.removeSeries(ref.ema169Series);
    ref.ema169Series = null;
  }}
  // Clear old price lines
  if (ref.priceLines) {{
    ref.priceLines.forEach(line => ref.candleSeries.removePriceLine(line));
    ref.priceLines = [];
  }}

  // Set candlestick data
  const chartData = candles.map(c => ({{
    time: c.time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }})).sort((a, b) => a.time - b.time);
  ref.candleSeries.setData(chartData);
  ref.candles = candles;

  // Get EMA data for the specified timeframe
  let ema144Data, ema169Data;
  try {{
    ema144Data = JSON.parse(d['ema144_' + tf.toLowerCase()]);
  }} catch(e) {{
    ema144Data = [];
  }}
  try {{
    ema169Data = JSON.parse(d['ema169_' + tf.toLowerCase()]);
  }} catch(e) {{
    ema169Data = [];
  }}

  // Add EMA144 - Yellow
  if (ema144Data.length > 0) {{
    const ema144Series = ref.chart.addLineSeries({{
      color: '#FFD600',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
      title: 'EMA144',
    }});
    ema144Series.setData(ema144Data.filter(e => e.value !== null));
    ref.ema144Series = ema144Series;
  }}

  // Add EMA169 - White
  if (ema169Data.length > 0) {{
    const ema169Series = ref.chart.addLineSeries({{
      color: '#FFFFFF',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
      title: 'EMA169',
    }});
    ema169Series.setData(ema169Data.filter(e => e.value !== null));
    ref.ema169Series = ema169Series;
  }}

  // Add S/R price lines
  const srZones = d.sr_zones || [];
  srZones.forEach(sr => {{
    const isSuper = sr.strength === 'super';
    let lineColor;
    if (isSuper) {{
      lineColor = '#FFD600';
    }} else {{
      lineColor = sr.type === 'support' ? '#00E676' : '#FF1744';
    }}

    let title = (sr.type === 'support' ? 'S' : 'R') + ' ' + sr.level.toFixed(PRECISION_MAP[d.instrument]||2) + ' [' + sr.strength + ']';
    if (sr.resonance === 'strong') {{
      title += ' ⚡Resonance';
    }} else if (sr.resonance === 'normal') {{
      title += ' Resonance';
    }} else if (sr.resonance === 'weak') {{
      title += ' Weak Resonance';
    }}

    const priceLine = ref.candleSeries.createPriceLine({{
      price: sr.level,
      color: lineColor,
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: title,
    }});
    ref.priceLines.push(priceLine);
  }});

  // Add order wall price lines
  const ow = d.order_walls || {{}};
  const bidWalls = ow.bid_walls || [];
  const askWalls = ow.ask_walls || [];
  const prec = PRECISION_MAP[d.instrument] || 2;

  bidWalls.forEach(wall => {{
    const priceLine = ref.candleSeries.createPriceLine({{
      price: wall.price,
      color: '#00E676',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: 'Bid Wall ' + wall.price.toFixed(prec) + ' [' + wall.strength + 'x]',
    }});
    ref.priceLines.push(priceLine);
  }});

  askWalls.forEach(wall => {{
    const priceLine = ref.candleSeries.createPriceLine({{
      price: wall.price,
      color: '#FF1744',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: 'Ask Wall ' + wall.price.toFixed(prec) + ' [' + wall.strength + 'x]',
    }});
    ref.priceLines.push(priceLine);
  }});

  // Signal markers
  const signal = d.signal;
  if (signal.direction !== 'NEUTRAL' && chartData.length > 0) {{
    const lastCandle = chartData[chartData.length - 1];
    const marker = {{
      time: lastCandle.time,
      position: signal.direction === 'LONG' ? 'belowBar' : 'aboveBar',
      color: signal.direction === 'LONG' ? '#00E676' : '#FF1744',
      shape: signal.direction === 'LONG' ? 'arrowUp' : 'arrowDown',
      text: signal.direction,
    }};
    ref.candleSeries.setMarkers([marker]);
  }}

  // Scroll to latest data, fitContent shows all data, user can freely zoom
  ref.chart.timeScale().fitContent();
}}

// Switch timeframe
function switchTimeframe(idx, tf) {{
  // Lazy loading: create chart if not yet created
  if (!chartCreated[idx]) {{
    createChartFor(idx);
  }}
  currentTF[idx] = tf;

  // Update button styles
  const container = document.getElementById('chart-' + idx);
  if (!container) return;
  const buttons = container.closest('.chart-area').querySelectorAll('.tf-btn');
  buttons.forEach(btn => {{
    btn.classList.toggle('active', btn.textContent.trim() === tf);
  }});

  // Load data for new timeframe
  loadChartData(idx, tf);
}}

// Precision mapping
const PRECISION_MAP = {json.dumps(PRECISION_MAP)};

// ============= Real-time Price Refresh =============
// Instrument to OKX instId mapping
const INSTRUMENT_INSTID_MAP = {{
  'BTC': 'BTC-USDT-SWAP',
  'ETH': 'ETH-USDT-SWAP',
  'XAU': 'XAU-USDT-SWAP',
  'XAG': 'XAG-USDT-SWAP',
  'BTC_SPOT': 'BTC-USDT',
  'ETH_SPOT': 'ETH-USDT',
}};

// Store previous prices for calculating change
const prevPrices = {{}};

// Refresh interval (milliseconds)
const REFRESH_INTERVAL = 10000;

// Consecutive failure counter (outside function, persistent across calls)
let _consecutiveFails = 0;

// Update refresh status indicator
function updateLiveStatus(success, errorMsg) {{
  const dot = document.getElementById('live-dot');
  const label = document.getElementById('live-label');
  const time = document.getElementById('live-time');

  if (success) {{
    dot.className = 'live-dot active';
    label.className = 'live-label active';
    label.textContent = 'Live Updating';
    const now = new Date();
    time.textContent = 'Last refresh: ' + now.toLocaleTimeString('zh-CN', {{hour12: false}});
  }} else {{
    dot.className = 'live-dot error';
    label.className = 'live-label error';
    label.textContent = errorMsg || 'Connection failed';
  }}
}}

// Format price
function formatLivePrice(value, instrument) {{
  const prec = PRECISION_MAP[instrument] || 2;
  return parseFloat(value).toFixed(prec);
}}

// Update price display in overview card
function updateOverviewPrice(instrument, lastPrice, open24h) {{
  const priceEl = document.getElementById('price-' + instrument);
  if (!priceEl) return;

  const formattedPrice = formatLivePrice(lastPrice, instrument);
  const prevPrice = prevPrices[instrument];
  let changeHtml = '';

  if (open24h) {{
    const change = parseFloat(lastPrice) - parseFloat(open24h);
    const changePct = (change / parseFloat(open24h) * 100).toFixed(2);
    const changeCls = change >= 0 ? 'up' : 'down';
    const sign = change >= 0 ? '+' : '';
    changeHtml = `<span class="price-change ${{changeCls}}">${{sign}}${{changePct}}%</span>`;
  }} else if (prevPrice !== undefined) {{
    const change = parseFloat(lastPrice) - prevPrice;
    const changeCls = change >= 0 ? 'up' : 'down';
    const sign = change >= 0 ? '+' : '';
    const changeVal = formatLivePrice(Math.abs(change), instrument);
    changeHtml = `<span class="price-change ${{changeCls}}">${{sign}}${{changeVal}}</span>`;
  }}

  priceEl.innerHTML = `
    <span class="price-value">${{formattedPrice}}</span>
    ${{changeHtml}}
  `;

  prevPrices[instrument] = parseFloat(lastPrice);
}}

// Update latest candle in chart (only for 1H timeframe)
function updateChartCandle(instrument, lastPrice) {{
  const idx = DATA.findIndex(d => d.instrument === instrument);
  if (idx < 0) return;
  const ref = chartRefs[idx];
  if (!ref || !ref.candleSeries || ref.candles.length === 0) return;
  // Only update in real-time for 1H timeframe
  if (currentTF[idx] !== '1H') return;

  const candles = ref.candles;
  const lastCandle = candles[candles.length - 1];
  const price = parseFloat(lastPrice);

  // Update latest candle close price
  const updatedCandle = {{
    time: lastCandle.time,
    open: lastCandle.open,
    high: Math.max(lastCandle.high, price),
    low: Math.min(lastCandle.low, price),
    close: price,
  }};
  ref.candleSeries.update(updatedCandle);
}}

// Fetch latest prices from OKX API
async function fetchLatestPrices() {{
  const swapInstruments = DATA.filter(d => !d.instrument.endsWith('_SPOT'));
  const spotInstruments = DATA.filter(d => d.instrument.endsWith('_SPOT'));

  let allTickers = {{}};
  let swapOk = false;
  let spotOk = false;

  // Generic fetch method with retry
  async function fetchTickers(instType, retries = 2) {{
    for (let attempt = 0; attempt <= retries; attempt++) {{
      try {{
        if (attempt > 0) {{
          await new Promise(r => setTimeout(r, 2000 * attempt));
        }}
        const resp = await fetch('/api/tickers?instType=' + instType);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        if (data.code === '0' && data.data) {{
          data.data.forEach(t => {{ allTickers[t.instId] = t; }});
          return true;
        }}
        // API error code also needs retry (e.g., rate limiting)
        if (attempt < retries) {{
          console.warn('Fetching ' + instType + ' ticker API returned code=' + (data.code || '?') + ', retrying...');
          continue;
        }}
        return false;
      }} catch(e) {{
        if (attempt >= retries) {{
          console.warn('Fetching ' + instType + ' ticker failed (retried ' + retries + ' times):', e.message);
          return false;
        }}
      }}
    }}
    return false;
  }}

  // Fetch swap contract prices
  if (swapInstruments.length > 0) {{
    swapOk = await fetchTickers('SWAP', 2);
  }}

  // Fetch spot prices (spot has lower real-time requirement, silent on failure)
  if (spotInstruments.length > 0) {{
    spotOk = await fetchTickers('SPOT', 1);
  }}

  // Update prices for each instrument
  let updatedAny = false;
  DATA.forEach(d => {{
    const instId = INSTRUMENT_INSTID_MAP[d.instrument];
    const ticker = allTickers[instId];
    if (ticker && ticker.last) {{
      updateOverviewPrice(d.instrument, ticker.last, ticker.open24h);
      updateChartCandle(d.instrument, ticker.last);
      updatedAny = true;
    }}
  }});

  // Status determination: alert only after 2 consecutive failures, single failure silent
  if (updatedAny) {{
    _consecutiveFails = 0;
    updateLiveStatus(true, '');
  }} else if (!swapOk && swapInstruments.length > 0) {{
    _consecutiveFails++;
    if (_consecutiveFails >= 2) {{
      // Report error only after 2+ consecutive failures
      updateLiveStatus(false, 'Price refresh error');
    }}
    // First failure keeps previous status unchanged
  }} else {{
    updateLiveStatus(false, 'Waiting for data');
  }}
}}

// Start price refresh
function startPriceRefresh() {{
  // Execute once immediately
  fetchLatestPrices();
  // Periodic refresh
  setInterval(fetchLatestPrices, REFRESH_INTERVAL);
}}

// Initialize
renderOverview();
renderSidebar();
renderContent();
createChartFor(0);  // Lazy loading: only create chart for active instrument on first screen
startPriceRefresh();
</script>

</body>
</html>"""

    return html
