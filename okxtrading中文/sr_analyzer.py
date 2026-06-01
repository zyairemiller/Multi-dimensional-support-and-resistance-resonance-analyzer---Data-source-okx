"""
支撑阻力识别模块 - 结构性S/R + 心理关口 + FVG + 清算区 + 订单墙 + 评分系统
"""

import pandas as pd
import numpy as np
from typing import Literal, Optional

SRLevelType = Literal["support", "resistance"]
SRStrength = Literal["super", "strong", "weak"]


def find_structural_sr(df_1d: pd.DataFrame, lookback: int = 60) -> list:
    """
    用局部极值法识别结构性支撑阻力

    高点左右两侧都低于当前高点 -> 确认为阻力
    低点左右两侧都高于当前低点 -> 确认为支撑

    Args:
        df_1d: 1D K线数据
        lookback: 回看天数

    Returns:
        list of dict: [{level, type, touch_count}, ...]
    """
    df = df_1d.tail(lookback).reset_index(drop=True)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    sr_levels = []
    window = 3  # 左右各看3根K线

    # 找阻力位（局部高点）
    for i in range(window, len(highs) - window):
        is_swing_high = True
        for j in range(i - window, i + window + 1):
            if j != i and highs[j] >= highs[i]:
                is_swing_high = False
                break

        if is_swing_high:
            sr_levels.append({
                "level": float(highs[i]),
                "type": "resistance",
                "touch_count": 1
            })

    # 找支撑位（局部低点）
    for i in range(window, len(lows) - window):
        is_swing_low = True
        for j in range(i - window, i + window + 1):
            if j != i and lows[j] <= lows[i]:
                is_swing_low = False
                break

        if is_swing_low:
            sr_levels.append({
                "level": float(lows[i]),
                "type": "support",
                "touch_count": 1
            })

    # 统计触及次数
    for sr in sr_levels:
        count = 0
        for c in closes:
            # 价格穿越该水平后反转，算一次触及
            if sr["type"] == "resistance" and c >= sr["level"] * 0.998 and c <= sr["level"] * 1.005:
                count += 1
            elif sr["type"] == "support" and c <= sr["level"] * 1.002 and c >= sr["level"] * 0.995:
                count += 1
        sr["touch_count"] = max(count, 1)

    # 合并距离 < 0.5% 的相邻S/R
    sr_levels = _merge_nearby_levels(sr_levels, 0.005)

    return sr_levels


def _merge_nearby_levels(levels: list, threshold_pct: float) -> list:
    """
    合并距离过近的S/R水平

    Args:
        levels: S/R水平列表
        threshold_pct: 合并阈值百分比（如0.005 = 0.5%）

    Returns:
        合并后的S/R列表
    """
    if not levels:
        return levels

    # 按价格排序
    sorted_levels = sorted(levels, key=lambda x: x["level"])
    merged = [sorted_levels[0]]

    for level in sorted_levels[1:]:
        prev = merged[-1]
        # 如果距离小于阈值且类型相同，合并
        if abs(level["level"] - prev["level"]) / prev["level"] < threshold_pct:
            # 保留触及次数更多的
            if level["touch_count"] > prev["touch_count"]:
                merged[-1] = level
            else:
                prev["touch_count"] += level["touch_count"]
        else:
            merged.append(level)

    return merged


def find_psychological_levels(current_price: float, instrument: str) -> list:
    """
    识别心理关口位 — 基于当前价格动态计算整数关口

    算法：以当前价格为中心，生成 ±15% 范围内的整数关口。
    步长按品种区分：BTC=1000 / ETH=100 / XAU=50 / XAG=1

    Args:
        current_price: 当前价格
        instrument: 品种名称（BTC/ETH/XAU/XAG/BTC_SPOT/ETH_SPOT）

    Returns:
        list of float: 附近的心理关口位
    """
    levels = []

    # 现货品种与对应swap品种使用相同规则
    base_instrument = instrument.replace("_SPOT", "")

    # 按品种确定关口步长
    step_map = {
        "BTC": 1000,
        "ETH": 100,
        "XAU": 50,
        "XAG": 1,
    }
    step = step_map.get(base_instrument, int(current_price * 0.05))

    # 动态生成 ±15% 范围内的所有整数关口
    lower = int(current_price * 0.85 / step) * step
    upper = int(current_price * 1.15 / step) * step

    p = lower
    while p <= upper:
        if p > 0 and abs(p - current_price) / current_price < 0.15:
            levels.append(float(p))
        p += step

    return levels


def find_fvg(df_1d: pd.DataFrame) -> list:
    """
    识别1D级别的跳空缺口(FVG - Fair Value Gap)

    向上缺口：前一根high < 后一根low
    向下缺口：前一根low > 后一根high
    只标记未回补的缺口

    Args:
        df_1d: 1D K线数据

    Returns:
        list of dict: [{level, type, gap_high, gap_low, filled}, ...]
    """
    fvgs = []
    highs = df_1d["high"].values
    lows = df_1d["low"].values
    closes = df_1d["close"].values

    for i in range(2, len(df_1d)):
        # 向上缺口：第i-2根的high < 第i根的low（中间留有空白）
        if highs[i - 2] < lows[i]:
            gap_low = float(highs[i - 2])
            gap_high = float(lows[i])
            level = (gap_low + gap_high) / 2

            # 检查是否已被回补
            filled = False
            for j in range(i, len(df_1d)):
                if lows[j] <= gap_low:
                    filled = True
                    break

            if not filled:
                fvgs.append({
                    "level": level,
                    "type": "support",
                    "gap_high": gap_high,
                    "gap_low": gap_low,
                    "filled": False
                })

        # 向下缺口：第i-2根的low > 第i根的high
        elif lows[i - 2] > highs[i]:
            gap_high = float(lows[i - 2])
            gap_low = float(highs[i])
            level = (gap_low + gap_high) / 2

            # 检查是否已被回补
            filled = False
            for j in range(i, len(df_1d)):
                if highs[j] >= gap_high:
                    filled = True
                    break

            if not filled:
                fvgs.append({
                    "level": level,
                    "type": "resistance",
                    "gap_high": gap_high,
                    "gap_low": gap_low,
                    "filled": False
                })

    return fvgs


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    计算ATR(平均真实波幅)

    Args:
        df: K线数据
        period: ATR周期

    Returns:
        最新ATR值
    """
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    trs = []
    for i in range(1, len(df)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
        trs.append(tr)

    if len(trs) < period:
        return float(np.mean(trs)) if trs else 0.0

    # 用EMA计算ATR
    atr = np.mean(trs[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(trs)):
        atr = trs[i] * k + atr * (1 - k)

    return float(atr)


def score_sr(level: dict, df_1d: pd.DataFrame, psychological_levels: list, fvgs: list, liquidation_levels: list = [], order_walls: dict = None) -> dict:
    """
    对S/R水平进行评分

    评分标准：
    - 每触及一次 +1分
    - 反转幅度>2ATR +2分
    - 是整数关口 +1分
    - 是FVG +1分
    - 是清算密集区 +1分
    - S/R附近有同方向订单墙 +1分

    >=5分 = 超级S/R, 3-4 = 强S/R, 1-2 = 弱S/R

    Args:
        level: S/R水平 {level, type, touch_count}
        df_1d: 1D K线数据
        psychological_levels: 心理关口列表
        fvgs: FVG列表
        liquidation_levels: 清算密集区列表 [{price, volume, side}, ...]
        order_walls: 订单墙数据，可选，默认None
            {"bid_walls": [...], "ask_walls": [...], ...}

    Returns:
        dict: {level, type, score, strength, touch_count, is_psychological, is_fvg, is_liquidation, is_order_wall}
    """
    score = 0
    price = level["level"]
    level_type = level["type"]

    # 1. 触及次数加分
    touch_count = level.get("touch_count", 1)
    score += min(touch_count, 5)  # 最多加5分

    # 2. 反转幅度>2ATR加分
    atr = _calc_atr(df_1d)
    if atr > 0:
        closes = df_1d["close"].values
        for i in range(1, len(closes)):
            if level_type == "resistance":
                # 价格触及阻力后回落
                if df_1d["high"].values[i] >= price * 0.998:
                    reversal = price - closes[i]
                    if reversal > 2 * atr:
                        score += 2
                        break
            elif level_type == "support":
                # 价格触及支撑后反弹
                if df_1d["low"].values[i] <= price * 1.002:
                    reversal = closes[i] - price
                    if reversal > 2 * atr:
                        score += 2
                        break

    # 3. 整数关口加分
    is_psychological = any(abs(p - price) / price < 0.002 for p in psychological_levels)
    if is_psychological:
        score += 1

    # 4. FVG加分
    is_fvg = any(abs(f["level"] - price) / price < 0.003 for f in fvgs)
    if is_fvg:
        score += 1

    # 5. 清算密集区加分
    is_liquidation = any(abs(lq["price"] - price) / price < 0.005 for lq in liquidation_levels)
    if is_liquidation:
        score += 1

    # 6. 订单墙加分：如果S/R附近有同方向的订单墙 -> score +1
    # 买单墙+支撑 / 卖单墙+阻力
    is_order_wall = False
    if order_walls is not None:
        if level_type == "support":
            # 支撑位附近找买单墙
            bid_walls = order_walls.get("bid_walls", [])
            for wall in bid_walls:
                wall_price = wall.get("price", 0)
                if wall_price > 0 and abs(wall_price - price) / price < 0.005:
                    is_order_wall = True
                    score += 1
                    break
        elif level_type == "resistance":
            # 阻力位附近找卖单墙
            ask_walls = order_walls.get("ask_walls", [])
            for wall in ask_walls:
                wall_price = wall.get("price", 0)
                if wall_price > 0 and abs(wall_price - price) / price < 0.005:
                    is_order_wall = True
                    score += 1
                    break

    # 确定强度
    if score >= 5:
        strength = "super"
    elif score >= 3:
        strength = "strong"
    else:
        strength = "weak"

    return {
        "level": price,
        "type": level_type,
        "score": score,
        "strength": strength,
        "touch_count": touch_count,
        "is_psychological": is_psychological,
        "is_fvg": is_fvg,
        "is_liquidation": is_liquidation,
        "is_order_wall": is_order_wall
    }


def get_sr_zones(sr_levels: list, instrument: str) -> list:
    """
    将S/R点转换为区间

    BTC/BTC_SPOT +-0.3%, ETH/ETH_SPOT +-0.4%, XAU +-0.3%, XAG +-0.5%

    Args:
        sr_levels: 评分后的S/R列表
        instrument: 品种名称

    Returns:
        list of dict: [{level, type, zone_low, zone_high, score, strength}, ...]
    """
    zone_pct = {
        "BTC": 0.003,
        "BTC_SPOT": 0.003,
        "ETH": 0.004,
        "ETH_SPOT": 0.004,
        "XAU": 0.003,
        "XAG": 0.005
    }
    pct = zone_pct.get(instrument, 0.003)

    zones = []
    for sr in sr_levels:
        price = sr["level"]
        zones.append({
            "level": price,
            "type": sr["type"],
            "zone_low": price * (1 - pct),
            "zone_high": price * (1 + pct),
            "score": sr.get("score", 0),
            "strength": sr.get("strength", "weak")
        })

    return zones
