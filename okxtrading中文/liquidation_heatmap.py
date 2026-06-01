"""
清算热力图计算模块 - 方案二：基于资金费率+OI推算杠杆分布

核心算法：
1. 基于1H K线计算每个时段的VWAP作为入场均价估计
2. 按杠杆分布权重分配OI到各杠杆区间
3. 计算每个杠杆区间的做多/做空强平价
4. 按价格网格累加清算名义价值
5. 应用时间衰减
6. 输出热力图数据

参考: 清算.txt
- 多头清算价: P_liq = P_entry × (1 - 1/Leverage + MMR)
- 空头清算价: P_liq = P_entry × (1 + 1/Leverage - MMR)
- 时间衰减: e^(-λt), λ=0.02 (半衰期约35小时)
"""

import math
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ========== 杠杆分布权重（可配置常量）==========
# 权重来源：基于 OKX 公开市场数据与主流交易所清算分布实证估计
# 反映不同品种交易者的典型杠杆偏好分布：
#   BTC/ETH：高波动+深度好，20x-50x 为主力区间（70%），10x 老手保守仓（20%），100x 专业赌徒（10%）
#   XAU/XAG：商品属性+流动性相对弱，10x-20x 为主力（70%），5x 低频保守仓（20%），50x 激进仓（10%）
# 如需根据市场情况调整（如极端波动期提高低杠杆权重），直接修改以下字典即可。
LEVERAGE_WEIGHTS: dict = {
    "BTC": {20: 0.40, 50: 0.30, 10: 0.20, 100: 0.10},
    "ETH": {20: 0.40, 50: 0.30, 10: 0.20, 100: 0.10},
    "XAU": {10: 0.40, 20: 0.30, 5: 0.20, 50: 0.10},
    "XAG": {10: 0.40, 20: 0.30, 5: 0.20, 50: 0.10},
    "BTC_SPOT": {},
    "ETH_SPOT": {},
}

# 品种维持保证金率（简化固定值）
# OKX实际MMR是梯度的，这里用简化值
MMR_MAP = {
    "BTC": 0.004,
    "ETH": 0.005,
    "XAU": 0.006,
    "XAG": 0.006,
    "BTC_SPOT": 0,
    "ETH_SPOT": 0,
}

# 价格网格精度（每个网格的价格步长）
GRID_STEP = {
    "BTC": 50,
    "ETH": 2,
    "XAU": 5,
    "XAG": 0.05,
    "BTC_SPOT": 50,
    "ETH_SPOT": 2,
}

# 时间衰减系数 λ
DECAY_LAMBDA = 0.02  # 半衰期约35小时


def compute_liquidation_heatmap(
    instrument: str,
    df_1h: pd.DataFrame,
    oi_current: dict,
    funding_data: dict,
) -> List[Dict]:
    """
    计算清算热力图数据

    核心算法：
    1. 基于1H K线计算每个时段的VWAP作为入场均价估计
    2. 按杠杆分布权重分配OI到各杠杆区间
    3. 计算每个杠杆区间的做多/做空强平价
    4. 按价格网格累加清算名义价值
    5. 应用时间衰减
    6. 输出热力图数据

    Args:
        instrument: 品种名，如 BTC, ETH
        df_1h: 1H K线数据
        oi_current: 当前OI数据 {oi, oiCcy}
        funding_data: 资金费率数据 {fundingRate, ...}

    Returns:
        热力图数据列表: [{price, long_liq, short_liq, total_liq, rating}, ...]
    """
    # 现货品种不支持
    if instrument.endswith("_SPOT"):
        return []

    leverage_weights = LEVERAGE_WEIGHTS.get(instrument, LEVERAGE_WEIGHTS["BTC"])
    if not leverage_weights:
        return []

    mmr = MMR_MAP.get(instrument, 0.005)
    grid_step = GRID_STEP.get(instrument, 50)

    current_price = float(df_1h["close"].iloc[-1])
    total_oi = oi_current.get("oi", 0) if oi_current else 0
    if total_oi <= 0:
        logger.warning(f"清算热力图: {instrument} OI为0，跳过")
        return []

    # 资金费率调整：高费率→高杠杆仓位更多
    funding_rate = float(funding_data.get("fundingRate", 0) if funding_data else 0)
    leverage_multiplier = 1.0 + abs(funding_rate) * 50  # 费率越高，高杠杆权重越大

    # 调整杠杆权重
    adjusted_weights = {}
    for lev, w in leverage_weights.items():
        if lev >= 50:
            adjusted_weights[lev] = w * leverage_multiplier
        else:
            adjusted_weights[lev] = w
    # 归一化
    total_w = sum(adjusted_weights.values())
    for lev in adjusted_weights:
        adjusted_weights[lev] /= total_w

    # 使用最近168根（7天）1H数据
    lookback = min(168, len(df_1h))
    recent_df = df_1h.iloc[-lookback:]

    now_ts = datetime.now(timezone.utc).timestamp()

    # 价格网格累加器
    grid_long = {}   # price_grid -> long_liquidation_value
    grid_short = {}   # price_grid -> short_liquidation_value

    # 先计算总成交量用于加权
    total_volume = recent_df["vol"].sum()
    if total_volume <= 0:
        total_volume = 1.0

    for idx, row in recent_df.iterrows():
        # 计算VWAP作为入场均价估计
        row_high = float(row["high"]) if pd.notna(row["high"]) else current_price
        row_low = float(row["low"]) if pd.notna(row["low"]) else current_price
        row_close = float(row["close"]) if pd.notna(row["close"]) else current_price
        row_open = float(row["open"]) if pd.notna(row["open"]) else current_price
        volume = float(row.get("vol", 0) or 0)

        vwap = (row_high + row_low + row_close) / 3.0

        # 时间衰减
        candle_ts = row["ts"]
        if hasattr(candle_ts, "timestamp"):
            hours_ago = (now_ts - candle_ts.value / 1_000_000_000) / 3600
        else:
            try:
                hours_ago = (now_ts - pd.Timestamp(candle_ts).timestamp()) / 3600
            except Exception:
                hours_ago = 24  # fallback
        decay = math.exp(-DECAY_LAMBDA * max(0, hours_ago))

        # 计算该时段的OI份额
        # 每根K线的OI份额 ≈ 总OI × (该时段成交量占比) × 时间衰减
        # 注意: decay只在这里用一次，不要在后面再乘
        oi_share = total_oi * (volume / total_volume) * decay

        # 判断多空比例（基于K线方向 + 资金费率修正）
        candle_body = row_close - row_open
        base_long_ratio = 0.55 if candle_body >= 0 else 0.45
        # 资金费率修正：正费率→偏多持仓更多，负费率→偏空持仓更多
        fr = float(funding_rate) if funding_rate else 0
        long_ratio = max(0.2, min(0.8, base_long_ratio + fr * 10))
        short_ratio = 1.0 - long_ratio

        # 对每个杠杆区间计算强平价
        for leverage, weight in adjusted_weights.items():
            position_value = oi_share * weight

            # 做多强平价: P_liq = P_entry × (1 - 1/Leverage + MMR)
            long_liq_price = vwap * (1 - 1 / leverage + mmr)
            # 做空强平价: P_liq = P_entry × (1 + 1/Leverage - MMR)
            short_liq_price = vwap * (1 + 1 / leverage - mmr)

            # 映射到价格网格
            long_grid = round(long_liq_price / grid_step) * grid_step
            short_grid = round(short_liq_price / grid_step) * grid_step

            long_value = position_value * long_ratio
            short_value = position_value * short_ratio

            grid_long[long_grid] = grid_long.get(long_grid, 0) + long_value
            grid_short[short_grid] = grid_short.get(short_grid, 0) + short_value

    # 汇总结果
    all_grids = set(grid_long.keys()) | set(grid_short.keys())

    if not all_grids:
        logger.info(f"清算热力图: {instrument} 无有效网格数据")
        return []

    # 计算统计量用于评级
    all_totals = [grid_long.get(g, 0) + grid_short.get(g, 0) for g in all_grids]
    avg_liq = float(np.mean(all_totals)) if all_totals else 1

    heatmap_data = []
    price_range_pct = 0.10  # 只保留当前价±10%范围
    for grid_price in sorted(all_grids):
        if abs(grid_price - current_price) / current_price > price_range_pct:
            continue

        long_liq = grid_long.get(grid_price, 0)
        short_liq = grid_short.get(grid_price, 0)
        total_liq = long_liq + short_liq

        # 评级（参考清算.txt的5星系统简化版）
        ratio = total_liq / avg_liq if avg_liq > 0 else 0
        if ratio >= 5:
            rating = 5  # 磁石级
        elif ratio >= 3:
            rating = 4  # 强清算区
        elif ratio >= 2:
            rating = 3  # 中等
        elif ratio >= 1.5:
            rating = 2  # 弱
        else:
            rating = 1  # 极弱

        heatmap_data.append({
            "price": round(grid_price, 2),
            "long_liq": round(long_liq, 4),
            "short_liq": round(short_liq, 4),
            "total_liq": round(total_liq, 4),
            "rating": rating,
        })

    logger.info(f"清算热力图计算完成: {instrument} 共 {len(heatmap_data)} 个价格网格")
    return heatmap_data
