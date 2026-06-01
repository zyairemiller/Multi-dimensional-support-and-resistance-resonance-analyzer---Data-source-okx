"""
HTML图表生成模块 - 使用Lightweight Charts + ECharts生成交互式HTML
深色主题，专业级交易图表
"""

import json
from datetime import datetime
from typing import List, Dict


# 品种精度映射
PRECISION_MAP = {
    "BTC": 1,
    "ETH": 2,
    "XAU": 2,
    "XAG": 3,
    "BTC_SPOT": 1,
    "ETH_SPOT": 2
}

# 品种中文名
NAME_MAP = {
    "BTC": "比特币",
    "ETH": "以太坊",
    "XAU": "黄金",
    "XAG": "白银",
    "BTC_SPOT": "比特币现货",
    "ETH_SPOT": "以太坊现货"
}

# 信号颜色
SIGNAL_COLORS = {
    "LONG": "#00E676",
    "SHORT": "#FF1744",
    "NEUTRAL": "#FFD600"
}


def _format_price(value: float, instrument: str) -> str:
    """格式化价格"""
    precision = PRECISION_MAP.get(instrument, 2)
    return f"{value:.{precision}f}"


def _get_display_name(instrument: str) -> str:
    """获取品种显示名（现货品种标注"(现货)"）"""
    name = NAME_MAP.get(instrument, instrument)
    if instrument.endswith("_SPOT"):
        name = name + " (现货)"
    return name


def build_html(analysis_results: List[Dict]) -> str:
    """
    生成交互式HTML文件

    Args:
        analysis_results: 各品种分析结果列表
            每项包含: instrument, inst_id, trend, sr_zones, sr_scored,
                      big_order, oi_change, funding_check, big_order_confirm,
                      signal, df_1h_json, df_4h_json, ema144_series_json, ema169_series_json,
                      vp_result, liquidation_data, order_walls

    Returns:
        HTML字符串
    """
    # 准备各品种数据
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
            # 多周期K线数据
            "candles_1h": result.get("candles_1h_json", "[]"),
            "candles_4h": result.get("candles_4h_json", "[]"),
            "candles_1d": result.get("candles_1d_json", "[]"),
            # 多周期EMA数据
            "ema144_1h": result.get("ema144_1h_json", "[]"),
            "ema169_1h": result.get("ema169_1h_json", "[]"),
            "ema144_4h": result.get("ema144_4h_json", "[]"),
            "ema169_4h": result.get("ema169_4h_json", "[]"),
            "ema144_1d": result.get("ema144_1d_json", "[]"),
            "ema169_1d": result.get("ema169_1d_json", "[]"),
            # 分析数据
            "vp_result": result.get("vp_result", {}),
            "liquidation_data": result.get("liquidation_data", []),
            "order_walls": result.get("order_walls", {"bid_walls": [], "ask_walls": [], "bid_total": 0, "ask_total": 0, "imbalance": 1.0}),
            "heatmap_data": result.get("heatmap_data", []),
        })

    instruments_json = json.dumps(instruments_data, ensure_ascii=False, default=str)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OKX 交易信号分析</title>
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

  /* 顶部标题栏 - 像桌面应用 */
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

  /* 实时刷新状态指示器 */
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

  /* 总览栏 - 紧凑横向排列 */
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

  /* 主工作区 - flex填满 */
  .workspace {{
    flex: 1;
    display: flex;
    overflow: hidden;
  }}

  /* 左侧品种导航栏 */
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

  /* 内容区 */
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

  /* 图表区 */
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

  /* 右侧面板 */
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

  /* 趋势卡片 */
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

  /* S/R 列表 */
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

  /* Volume Profile 摘要 */
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

  /* 信号详情 */
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

  /* 大单摘要 */
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

  /* 订单墙卡片 */
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


  /* 时间周期切换按钮 */
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

  /* 清算热力表格 */
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

  /* 平板: 面板占15% */
  @media (max-width: 1024px) {{
    .main-content.active {{
      grid-template-columns: 1fr 15%;
    }}
  }}
  /* 手机: 面板下沉 */
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
    <span class="icon">📊</span> OKX 交易信号分析
  </div>
  <div class="header-right">
    <div class="live-status">
      <div class="live-dot" id="live-dot"></div>
      <span class="live-label" id="live-label">等待连接...</span>
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

// 渲染总览卡片
function renderOverview() {{
  const container = document.getElementById('overview');
  container.innerHTML = DATA.map((d, i) => {{
    const sig = d.signal.direction;
    const cls = sig === 'LONG' ? 'long' : sig === 'SHORT' ? 'short' : 'neutral';
    const sigText = sig === 'LONG' ? '🟢 做多' : sig === 'SHORT' ? '🔴 做空' : '🟡 观望';
    const trendText = d.trend.trend === 'GOLDEN_CROSS' ? '多头' : d.trend.trend === 'DEATH_CROSS' ? '空头' : '缠绕';
    const strengthText = d.trend.trend_strength === 'early' ? '初期' : d.trend.trend_strength === 'mid' ? '中期' : '过热';
    return `
      <div class="overview-card ${{cls}}" data-instrument="${{d.instrument}}" onclick="switchTab(${{i}})">
        <div class="inst-name">${{d.instrument}}</div>
        <div class="inst-full">${{d.name}}</div>
        <div class="live-price" id="price-${{d.instrument}}">
          <span class="price-loading">加载中...</span>
        </div>
        <div class="signal-badge">${{sigText}}</div>
        <div class="trend-info">
          趋势: <span>${{trendText}} (${{strengthText}})</span> · 分离度: <span>${{d.trend.separation_pct}}%</span>
        </div>
      </div>
    `;
  }}).join('');
}}

// 渲染左侧品种导航栏
function renderSidebar() {{
  const container = document.getElementById('sidebar');
  const shortNames = {{'BTC': 'BTC', 'ETH': 'ETH', 'XAU': 'XAU', 'XAG': 'XAG', 'BTC_SPOT': 'BTC*', 'ETH_SPOT': 'ETH*'}};
  container.innerHTML =
    '<button class="sidebar-tradfi" onclick="location.href=&quot;/tradfi&quot;" title="TradFi 全品种分析">TF</button>' +
    '<div class="sidebar-sep"></div>' +
    DATA.map((d, i) => `
    <button class="sidebar-item ${{i===0?'active':''}}" onclick="switchTab(${{i}})" title="${{d.name}}">${{shortNames[d.instrument] || d.instrument}}</button>
  `).join('');
}}

// 渲染内容区
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
  const label = trend.trend === 'GOLDEN_CROSS' ? '多头趋势' : trend.trend === 'DEATH_CROSS' ? '空头趋势' : '缠绕观望';
  const strengthMap = {{early: '初期', mid: '中期', overheated: '过热⚠️'}};
  const prec = PRECISION_MAP[d.instrument] || 2;
  return `
    <div class="panel-card">
      <div class="card-header">📈 EMA趋势</div>
      <div class="card-body">
        <div class="trend-status">
          <div class="trend-dot ${{cls}}"></div>
          <div class="trend-label ${{cls}}">${{label}}</div>
        </div>
        <div class="trend-detail">
          EMA144: <span>${{trend.ema144.toFixed(prec)}}</span><br>
          EMA169: <span>${{trend.ema169.toFixed(prec)}}</span><br>
          分离度: <span>${{trend.separation_pct}}%</span><br>
          强度: <span>${{strengthMap[trend.trend_strength] || trend.trend_strength}}</span>
        </div>
      </div>
    </div>
  `;
}}

function renderSRCurd(d) {{
  const zones = d.sr_zones || [];
  const scored = d.sr_scored || [];
  // 合并显示
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
      <div class="card-header">🎯 支撑阻力</div>
      <div class="card-body">
        ${{allSR.length === 0 ? '<div style="color:#6b7280;font-size:12px;">暂无有效S/R</div>' : allSR.map(sr => {{
          // 共振标签
          let resonanceBadge = '';
          if (sr.resonance === 'strong') {{
            resonanceBadge = '<span class="sr-badge resonance-strong">⚡共振</span>';
          }} else if (sr.resonance === 'normal') {{
            resonanceBadge = '<span class="sr-badge resonance-normal">共振</span>';
          }} else if (sr.resonance === 'weak') {{
            resonanceBadge = '<span class="sr-badge resonance-weak">弱共振</span>';
          }}
          // 清算区标签
          let liqBadge = sr.is_liquidation ? '<span class="sr-badge liquidation">清算区</span>' : '';
          // 订单墙标签
          let wallBadge = sr.is_order_wall ? '<span class="sr-badge order-wall">订单墙</span>' : '';

          return `
            <div class="sr-item">
              <div>
                <span class="sr-level">${{sr.level.toFixed(prec)}}</span>
                <span class="sr-type ${{sr.type}}">${{sr.type === 'support' ? '支撑' : '阻力'}}</span>
              </div>
              <div>
                <span class="sr-score ${{sr.strength}}">${{sr.score}}分·${{sr.strength === 'super' ? '超级' : sr.strength === 'strong' ? '强' : '弱'}}</span>
                <div class="sr-badges">
                  ${{sr.is_psychological ? '<span class="sr-badge">整数关口</span>' : ''}}
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

  // 只显示评级≥2的重要清算价位，按评级降序→总清算量降序排列
  const filtered = hm.filter(h => h.rating >= 2)
    .sort((a, b) => b.rating - a.rating || b.total_liq - a.total_liq);

  // 评级描述映射
  const ratingLabel = {{5: '磁石', 4: '强', 3: '中', 2: '弱'}};
  const ratingCls = {{5: 'magnet', 4: 'strong', 3: 'medium', 2: 'weak'}};

  // 找最大total_liq用于条形图比例
  const maxTotal = filtered.length > 0 ? Math.max(...filtered.map(f => f.total_liq)) : 1;

  // 生成星级
  function stars(r) {{
    let s = '';
    for (let i = 1; i <= 5; i++) {{
      s += '<span class="liq-star ' + (i <= r ? 'on' : 'off') + '">★</span>';
    }}
    return s;
  }}

  // 格式化清算量（智能单位）
  function fmtLiq(v) {{
    if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿';
    if (v >= 1e4) return (v / 1e4).toFixed(1) + '万';
    return v.toFixed(0);
  }}

  if (filtered.length === 0) {{
    return `
      <div class="panel-card">
        <div class="card-header">🔥 清算热力图</div>
        <div class="card-body">
          <div class="liq-empty">${{d.instrument.endsWith('_SPOT') ? '现货品种无清算数据' : '暂无重要清算价位'}}</div>
        </div>
      </div>
    `;
  }}

  // 统计摘要
  const totalLong = filtered.reduce((s, f) => s + f.long_liq, 0);
  const totalShort = filtered.reduce((s, f) => s + f.short_liq, 0);
  const totalAll = totalLong + totalShort;
  const longPct = totalAll > 0 ? (totalLong / totalAll * 100) : 50;
  const shortPct = totalAll > 0 ? (totalShort / totalAll * 100) : 50;
  const magnetCount = filtered.filter(f => f.rating >= 5).length;

  return `
    <div class="panel-card">
      <div class="card-header">🔥 清算热力图 <span style="font-size:10px;color:#6b7280;font-weight:400;">(${{filtered.length}}个价位)</span></div>
      <div class="card-body" style="padding:6px 0;">
        <!-- 摘要条 -->
        <div style="padding:0 10px 6px;border-bottom:1px solid #1e2738;">
          <div class="liq-bar-container" style="height:6px;">
            <div class="liq-bar-long" style="width:${{longPct}}%"></div>
            <div class="liq-bar-short" style="width:${{shortPct}}%"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:3px;font-size:10px;">
            <span style="color:#00E676;">多头 ${{fmtLiq(totalLong)}}</span>
            <span style="color:#6b7280;">${{magnetCount > 0 ? '⚡磁石级' + magnetCount + '个' : ''}}</span>
            <span style="color:#FF1744;">空头 ${{fmtLiq(totalShort)}}</span>
          </div>
        </div>
        <!-- 表格 -->
        <table class="liq-table">
          <thead>
            <tr>
              <th>价格</th>
              <th>评级</th>
              <th>方向</th>
              <th>清算量</th>
            </tr>
          </thead>
          <tbody>
            ${{filtered.slice(0, 15).map(f => {{
              const longRatio = f.long_liq / (f.total_liq || 0.001);
              const shortRatio = f.short_liq / (f.total_liq || 0.001);
              let dirCls = 'balanced';
              let dirText = '均衡';
              if (longRatio > 0.6) {{ dirCls = 'long-dom'; dirText = '偏多'; }}
              else if (shortRatio > 0.6) {{ dirCls = 'short-dom'; dirText = '偏空'; }}

              // 多空比条形图
              const barLongPct = (f.long_liq / (f.total_liq || 0.001) * 100).toFixed(0);
              const barShortPct = (100 - parseInt(barLongPct));

              return `
                <tr>
                  <td style="font-weight:600;color:#d1d5db;font-variant-numeric:tabular-nums;">${{f.price.toFixed(prec)}}</td>
                  <td>
                    <div class="liq-rating">${{stars(f.rating)}}</div>
                    <span class="liq-level-badge ${{ratingCls[f.rating] || 'weak'}}">${{ratingLabel[f.rating] || '弱'}}</span>
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
        ${{filtered.length > 15 ? '<div style="text-align:center;padding:4px 0;color:#6b7280;font-size:10px;">还有' + (filtered.length - 15) + '个弱价位未显示</div>' : ''}}
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

  // 如果没有订单墙数据，显示提示
  if (bidWalls.length === 0 && askWalls.length === 0) {{
    return `
      <div class="panel-card">
        <div class="card-header">🧱 订单墙</div>
        <div class="card-body">
          <div style="color:#6b7280;font-size:12px;">未检测到显著订单墙</div>
          <div class="imbalance-label">买卖比: ${{imbalance.toFixed(2)}}</div>
        </div>
      </div>
    `;
  }}

  // 买卖比条形图
  const totalVol = bidTotal + askTotal;
  const bidPct = totalVol > 0 ? (bidTotal / totalVol * 100) : 50;
  const askPct = totalVol > 0 ? (askTotal / totalVol * 100) : 50;

  // 买卖比颜色
  let imbalanceCls = '';
  let imbalanceText = '';
  if (imbalance > 1.5) {{
    imbalanceCls = 'bullish';
    imbalanceText = '偏多';
  }} else if (imbalance < 0.67) {{
    imbalanceCls = 'bearish';
    imbalanceText = '偏空';
  }} else {{
    imbalanceText = '均衡';
  }}

  return `
    <div class="panel-card">
      <div class="card-header">🧱 订单墙</div>
      <div class="card-body">
        <div style="font-size:11px;color:#6b7280;margin-bottom:6px;">买单墙</div>
        ${{bidWalls.slice(0, 5).map(w => `
          <div class="wall-item">
            <span class="wall-price" style="color:#00E676;">${{w.price.toFixed(prec)}}</span>
            <span class="wall-size">${{w.size.toFixed(2)}}</span>
            <span class="wall-strength bid">${{w.strength}}x</span>
          </div>
        `).join('')}}
        ${{bidWalls.length === 0 ? '<div style="color:#6b7280;font-size:11px;">无</div>' : ''}}

        <div style="font-size:11px;color:#6b7280;margin-top:8px;margin-bottom:6px;">卖单墙</div>
        ${{askWalls.slice(0, 5).map(w => `
          <div class="wall-item">
            <span class="wall-price" style="color:#FF1744;">${{w.price.toFixed(prec)}}</span>
            <span class="wall-size">${{w.size.toFixed(2)}}</span>
            <span class="wall-strength ask">${{w.strength}}x</span>
          </div>
        `).join('')}}
        ${{askWalls.length === 0 ? '<div style="color:#6b7280;font-size:11px;">无</div>' : ''}}

        <div style="margin-top:10px;">
          <div class="imbalance-bar">
            <div class="bid-bar" style="width:${{bidPct}}%"></div>
            <div class="ask-bar" style="width:${{askPct}}%"></div>
          </div>
          <div class="imbalance-label ${{imbalanceCls}}">
            买卖比: ${{imbalance.toFixed(2)}} (${{imbalanceText}})
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
          高成交量节点 (${{nodes.length}}个):
          ${{topNodes.length > 0 ? '<br>' + topNodes.map(n => '&nbsp;&nbsp;▸ <span>' + n.toFixed(prec) + '</span>').join('<br>') : ' <span>无</span>'}}
          ${{nodes.length > 3 ? '<br><span style="font-size:10px;color:#6b7280;">&nbsp;&nbsp;...还有 ' + (nodes.length - 3) + ' 个</span>' : ''}}
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

  // 现货品种无数据时
  const isSpotNoData = bo.buy_count === 0 && bo.sell_count === 0;

  return `
    <div class="panel-card">
      <div class="card-header">🐋 大单 & 资金</div>
      <div class="card-body">
        ${{isSpotNoData ? '<div style="color:#6b7280;font-size:12px;margin-bottom:8px;">现货品种无逐笔成交数据</div>' : `
        <div class="big-order-bar">
          <div class="buy" style="width:${{buyPct}}%"></div>
          <div class="sell" style="width:${{sellPct}}%"></div>
        </div>
        <div class="big-order-stat">
          <span class="buy-stat">买入 ${{(bo.buy_volume||0).toFixed(2)}} (${{bo.buy_count||0}}笔)</span>
          <span class="sell-stat">卖出 ${{(bo.sell_volume||0).toFixed(2)}} (${{bo.sell_count||0}}笔)</span>
        </div>
        <div style="margin-top:8px;font-size:12px;color:#9ca3af;">
          大单比率: <span style="color:#fff;">${{(bo.big_ratio||1).toFixed(2)}}</span> ·
          信号: <span style="color:${{(bo.signal==='BULLISH'?'#00E676':bo.signal==='BEARISH'?'#FF1744':'#FFD600')}}">${{bo.signal||'NEUTRAL'}}</span>
        </div>
        `}}
        <div style="margin-top:6px;font-size:12px;color:#9ca3af;">
          资金费率: <span style="color:#fff;">${{fc.status === 'N/A' ? 'N/A (现货)' : ((fc.rate||0)*100).toFixed(4) + '%'}}'</span>
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
      <div class="card-header">🔔 交易信号</div>
      <div class="card-body">
        <div style="font-size:18px;font-weight:700;color:${{dir==='LONG'?'#00E676':dir==='SHORT'?'#FF1744':'#FFD600'}};">
          ${{dir === 'LONG' ? '▲ 做多 LONG' : dir === 'SHORT' ? '▼ 做空 SHORT' : '— 观望 NEUTRAL'}}
        </div>
        ${{dir !== 'NEUTRAL' ? `
        <div class="signal-detail">
          <div class="price-row">
            <span class="price-label">入场价</span>
            <span class="price-value">${{sig.entry_price.toFixed(prec)}}</span>
          </div>
          <div class="price-row">
            <span class="price-label">止损</span>
            <span class="price-value red">${{sig.stop_loss.toFixed(prec)}}</span>
          </div>
          <div class="price-row">
            <span class="price-label">止盈</span>
            <span class="price-value green">${{sig.take_profit.toFixed(prec)}}</span>
          </div>
          <div class="price-row">
            <span class="price-label">盈亏比</span>
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
    ema_trend: 'EMA趋势方向',
    in_support_zone: '价格在支撑区间',
    in_resistance_zone: '价格在阻力区间',
    bullish_candle: '1H多头反转K线',
    bearish_candle: '1H空头反转K线',
    big_order_confirm: '大单确认',
    funding_rate_ok: '资金费率正常'
  }};
  return `
    <div class="panel-card">
      <div class="card-header">✅ 信号检查清单</div>
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

// 切换Tab（懒加载：首次切换到某品种时才创建图表）
function switchTab(idx) {{
  document.querySelectorAll('.sidebar-item').forEach((s, i) => {{
    s.classList.toggle('active', i === idx);
  }});
  document.querySelectorAll('.main-content').forEach((c, i) => {{
    c.classList.toggle('active', i === idx);
  }});
  // 懒加载：图表未创建则创建
  if (!chartCreated[idx]) {{
    createChartFor(idx);
  }}
}}

// 创建图表
// 存储图表引用，用于实时更新
const chartRefs = [];
// 标记哪些品种的图表已创建
const chartCreated = [];
// 当前选中的时间周期
const currentTF = {{}};

function createChartFor(idx) {{
  const d = DATA[idx];
  const container = document.getElementById('chart-' + idx);
  if (!container || chartCreated[idx]) return;
  chartCreated[idx] = true;

  // 默认显示1H周期
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

    // K线
    const candleSeries = chart.addCandlestickSeries({{
      upColor: '#00E676',
      downColor: '#FF1744',
      borderUpColor: '#00E676',
      borderDownColor: '#FF1744',
      wickUpColor: '#00E676',
      wickDownColor: '#FF1744',
    }});

    // 解析默认1H K线数据
    let candles;
    try {{
      candles = JSON.parse(d.candles_1h);
    }} catch(e) {{
      candles = [];
    }}

    // 存储图表引用
    chartRefs[idx] = {{
      chart: chart,
      candleSeries: candleSeries,
      instrument: d.instrument,
      candles: candles,
      ema144Series: null,
      ema169Series: null,
      priceLines: [],
    }};

    // 加载默认周期的数据
    loadChartData(idx, '1H');

    // 响应式
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

// 加载指定周期的K线和EMA数据到图表
function loadChartData(idx, tf) {{
  const ref = chartRefs[idx];
  const d = DATA[idx];
  if (!ref || !d) return;

  // 获取对应周期的K线数据
  let candles;
  const candlesKey = 'candles_' + tf.toLowerCase();
  try {{
    candles = JSON.parse(d[candlesKey]);
  }} catch(e) {{
    candles = [];
  }}

  if (candles.length === 0) return;

  // 清除旧的EMA线和价格线
  if (ref.ema144Series) {{
    ref.chart.removeSeries(ref.ema144Series);
    ref.ema144Series = null;
  }}
  if (ref.ema169Series) {{
    ref.chart.removeSeries(ref.ema169Series);
    ref.ema169Series = null;
  }}
  // 清除旧的价格线
  if (ref.priceLines) {{
    ref.priceLines.forEach(line => ref.candleSeries.removePriceLine(line));
    ref.priceLines = [];
  }}

  // 设置K线数据
  const chartData = candles.map(c => ({{
    time: c.time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }})).sort((a, b) => a.time - b.time);
  ref.candleSeries.setData(chartData);
  ref.candles = candles;

  // 获取对应周期的EMA数据
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

  // 添加EMA144 - 黄色
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

  // 添加EMA169 - 白色
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

  // 添加S/R价格线
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
      title += ' ⚡共振';
    }} else if (sr.resonance === 'normal') {{
      title += ' 共振';
    }} else if (sr.resonance === 'weak') {{
      title += ' 弱共振';
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

  // 添加订单墙价格线
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
      title: '买墙 ' + wall.price.toFixed(prec) + ' [' + wall.strength + 'x]',
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
      title: '卖墙 ' + wall.price.toFixed(prec) + ' [' + wall.strength + 'x]',
    }});
    ref.priceLines.push(priceLine);
  }});

  // 信号标记
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

  // 滚动到最新数据，fitContent显示全部数据，用户可自由缩放
  ref.chart.timeScale().fitContent();
}}

// 切换时间周期
function switchTimeframe(idx, tf) {{
  // 懒加载：如果图表尚未创建，先创建
  if (!chartCreated[idx]) {{
    createChartFor(idx);
  }}
  currentTF[idx] = tf;

  // 更新按钮样式
  const container = document.getElementById('chart-' + idx);
  if (!container) return;
  const buttons = container.closest('.chart-area').querySelectorAll('.tf-btn');
  buttons.forEach(btn => {{
    btn.classList.toggle('active', btn.textContent.trim() === tf);
  }});

  // 加载新周期的数据
  loadChartData(idx, tf);
}}

// 精度映射
const PRECISION_MAP = {json.dumps(PRECISION_MAP)};

// ============= 实时价格刷新 =============
// 品种到OKX instId的映射
const INSTRUMENT_INSTID_MAP = {{
  'BTC': 'BTC-USDT-SWAP',
  'ETH': 'ETH-USDT-SWAP',
  'XAU': 'XAU-USDT-SWAP',
  'XAG': 'XAG-USDT-SWAP',
  'BTC_SPOT': 'BTC-USDT',
  'ETH_SPOT': 'ETH-USDT',
}};

// 存储上一次价格，用于计算涨跌
const prevPrices = {{}};

// 刷新间隔（毫秒）
const REFRESH_INTERVAL = 10000;

// 连续失败计数器（函数外部，跨调用持久化）
let _consecutiveFails = 0;

// 更新刷新状态指示器
function updateLiveStatus(success, errorMsg) {{
  const dot = document.getElementById('live-dot');
  const label = document.getElementById('live-label');
  const time = document.getElementById('live-time');

  if (success) {{
    dot.className = 'live-dot active';
    label.className = 'live-label active';
    label.textContent = '实时更新中';
    const now = new Date();
    time.textContent = '最后刷新: ' + now.toLocaleTimeString('zh-CN', {{hour12: false}});
  }} else {{
    dot.className = 'live-dot error';
    label.className = 'live-label error';
    label.textContent = errorMsg || '连接失败';
  }}
}}

// 格式化价格
function formatLivePrice(value, instrument) {{
  const prec = PRECISION_MAP[instrument] || 2;
  return parseFloat(value).toFixed(prec);
}}

// 更新总览卡片中的价格显示
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

// 更新图表中最新K线（仅在1H周期时更新）
function updateChartCandle(instrument, lastPrice) {{
  const idx = DATA.findIndex(d => d.instrument === instrument);
  if (idx < 0) return;
  const ref = chartRefs[idx];
  if (!ref || !ref.candleSeries || ref.candles.length === 0) return;
  // 仅在1H周期下实时更新
  if (currentTF[idx] !== '1H') return;

  const candles = ref.candles;
  const lastCandle = candles[candles.length - 1];
  const price = parseFloat(lastPrice);

  // 更新最新K线的close价格
  const updatedCandle = {{
    time: lastCandle.time,
    open: lastCandle.open,
    high: Math.max(lastCandle.high, price),
    low: Math.min(lastCandle.low, price),
    close: price,
  }};
  ref.candleSeries.update(updatedCandle);
}}

// 从OKX API获取最新价格
async function fetchLatestPrices() {{
  const swapInstruments = DATA.filter(d => !d.instrument.endsWith('_SPOT'));
  const spotInstruments = DATA.filter(d => d.instrument.endsWith('_SPOT'));

  let allTickers = {{}};
  let swapOk = false;
  let spotOk = false;

  // 带重试的通用获取方法
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
        // API返回错误码也要重试（比如限流）
        if (attempt < retries) {{
          console.warn('获取' + instType + ' ticker API返回code=' + (data.code || '?') + '，重试...');
          continue;
        }}
        return false;
      }} catch(e) {{
        if (attempt >= retries) {{
          console.warn('获取' + instType + ' ticker失败（已重试' + retries + '次）:', e.message);
          return false;
        }}
      }}
    }}
    return false;
  }}

  // 获取永续合约价格
  if (swapInstruments.length > 0) {{
    swapOk = await fetchTickers('SWAP', 2);
  }}

  // 获取现货价格（现货对实时性要求低，失败不报警）
  if (spotInstruments.length > 0) {{
    spotOk = await fetchTickers('SPOT', 1);
  }}

  // 更新各品种价格
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

  // 状态判定：连续失败2次才报警，单次失败静默
  if (updatedAny) {{
    _consecutiveFails = 0;
    updateLiveStatus(true, '');
  }} else if (!swapOk && swapInstruments.length > 0) {{
    _consecutiveFails++;
    if (_consecutiveFails >= 2) {{
      // 连续失败2次以上才报错
      updateLiveStatus(false, '价格刷新异常');
    }}
    // 第一次失败保持上次状态不变
  }} else {{
    updateLiveStatus(false, '等待数据');
  }}
}}

// 启动价格刷新
function startPriceRefresh() {{
  // 立即执行一次
  fetchLatestPrices();
  // 定时刷新
  setInterval(fetchLatestPrices, REFRESH_INTERVAL);
}}

// 初始化
renderOverview();
renderSidebar();
renderContent();
createChartFor(0);  // 懒加载：仅首屏创建激活品种的图表
startPriceRefresh();
</script>

</body>
</html>"""

    return html
