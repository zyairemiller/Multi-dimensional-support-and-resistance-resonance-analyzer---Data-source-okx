"""
信号生成模块 - 综合EMA趋势、S/R、大单确认、订单墙生成交易信号
"""

import pandas as pd
import numpy as np
from typing import Literal, Optional

SignalDirection = Literal["LONG", "SHORT", "NEUTRAL"]


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """计算ATR"""
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

    atr = np.mean(trs[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(trs)):
        atr = trs[i] * k + atr * (1 - k)

    return float(atr)


def _check_bullish_candle(df_1h: pd.DataFrame) -> dict:
    """
    检查1H级别是否出现多头反转K线

    形态识别：
    - 锤子线：下影线 > 实体*2，上影线 < 实体*0.5
    - 看涨吞没：当前阳线实体完全包裹前一根阴线实体
    - 长下影线：下影线占K线总长>60%

    Args:
        df_1h: 1H K线数据

    Returns:
        dict: {detected: bool, pattern: str}
    """
    if len(df_1h) < 3:
        return {"detected": False, "pattern": "数据不足"}

    latest = df_1h.iloc[-1]
    prev = df_1h.iloc[-2]

    o, h, l, c = latest["open"], latest["high"], latest["low"], latest["close"]
    body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l
    total_range = h - l

    # 防止除零
    if total_range == 0 or body == 0:
        # 十字星处理
        if total_range > 0 and lower_shadow / total_range > 0.6:
            return {"detected": True, "pattern": "长下影十字星"}
        return {"detected": False, "pattern": "无明确形态"}

    # 锤子线
    if lower_shadow > body * 2 and upper_shadow < body * 0.5 and c > o:
        return {"detected": True, "pattern": "锤子线"}

    # 长下影线
    if lower_shadow / total_range > 0.6 and c > o:
        return {"detected": True, "pattern": "长下影阳线"}

    # 看涨吞没
    prev_body = abs(prev["close"] - prev["open"])
    if (prev["close"] < prev["open"]  # 前一根阴线
        and c > o  # 当前阳线
        and c > prev["open"]  # 当前收盘>前开盘
        and o < prev["close"]  # 当前开盘<前收盘
        and body > prev_body * 1.1):  # 实体更大
        return {"detected": True, "pattern": "看涨吞没"}

    return {"detected": False, "pattern": "无明确多头形态"}


def _check_bearish_candle(df_1h: pd.DataFrame) -> dict:
    """
    检查1H级别是否出现空头反转K线

    形态识别：
    - 射击之星：上影线 > 实体*2，下影线 < 实体*0.5
    - 看跌吞没：当前阴线实体完全包裹前一根阳线实体
    - 长上影线：上影线占K线总长>60%

    Args:
        df_1h: 1H K线数据

    Returns:
        dict: {detected: bool, pattern: str}
    """
    if len(df_1h) < 3:
        return {"detected": False, "pattern": "数据不足"}

    latest = df_1h.iloc[-1]
    prev = df_1h.iloc[-2]

    o, h, l, c = latest["open"], latest["high"], latest["low"], latest["close"]
    body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l
    total_range = h - l

    if total_range == 0 or body == 0:
        if total_range > 0 and upper_shadow / total_range > 0.6:
            return {"detected": True, "pattern": "长上影十字星"}
        return {"detected": False, "pattern": "无明确形态"}

    # 射击之星
    if upper_shadow > body * 2 and lower_shadow < body * 0.5 and c < o:
        return {"detected": True, "pattern": "射击之星"}

    # 长上影线
    if upper_shadow / total_range > 0.6 and c < o:
        return {"detected": True, "pattern": "长上影阴线"}

    # 看跌吞没
    prev_body = abs(prev["close"] - prev["open"])
    if (prev["close"] > prev["open"]  # 前一根阳线
        and c < o  # 当前阴线
        and c < prev["open"]  # 当前收盘<前开盘
        and o > prev["close"]  # 当前开盘>前收盘
        and body > prev_body * 1.1):
        return {"detected": True, "pattern": "看跌吞没"}

    return {"detected": False, "pattern": "无明确空头形态"}


def _price_in_zone(price: float, zone: dict) -> bool:
    """判断价格是否在S/R区间内"""
    return zone["zone_low"] <= price <= zone["zone_high"]


def _find_nearest_sr(price: float, sr_zones: list, sr_type: str) -> dict:
    """
    找最近的指定类型S/R

    Args:
        price: 当前价格
        sr_zones: S/R区间列表
        sr_type: "support" 或 "resistance"

    Returns:
        最近的S/R区间，或None
    """
    candidates = [z for z in sr_zones if z["type"] == sr_type]
    if not candidates:
        return None

    # 对于支撑，找价格下方的；对于阻力，找价格上方的
    if sr_type == "support":
        below = [z for z in candidates if z["level"] < price]
        if below:
            return max(below, key=lambda z: z["level"])
        return max(candidates, key=lambda z: z["level"])
    else:
        above = [z for z in candidates if z["level"] > price]
        if above:
            return min(above, key=lambda z: z["level"])
        return min(candidates, key=lambda z: z["level"])


def _find_next_sr(price: float, sr_zones: list, sr_type: str) -> dict:
    """
    找下一S/R位（用于止盈）

    Args:
        price: 当前价格
        sr_zones: S/R区间列表
        sr_type: "support" 或 "resistance"

    Returns:
        下一S/R区间，或None
    """
    candidates = [z for z in sr_zones if z["type"] == sr_type]
    if not candidates:
        return None

    if sr_type == "resistance":
        # 做多止盈找上方的阻力
        above = [z for z in candidates if z["level"] > price]
        if above:
            return min(above, key=lambda z: z["level"])
    else:
        # 做空止盈找下方的支撑
        below = [z for z in candidates if z["level"] < price]
        if below:
            return max(below, key=lambda z: z["level"])

    return None


def _check_wall_near_sr(walls: list, sr_zone: dict, tolerance_pct: float = 0.005) -> list:
    """
    检查订单墙是否在S/R区间附近

    Args:
        walls: 订单墙列表 [{"price": float, "size": float, "strength": float}, ...]
        sr_zone: S/R区间 {"level": float, "zone_low": float, "zone_high": float, ...}
        tolerance_pct: 价格容差百分比

    Returns:
        在S/R附近的订单墙列表
    """
    nearby_walls = []
    for wall in walls:
        wall_price = wall.get("price", 0)
        if wall_price <= 0:
            continue
        # 检查wall.price在zone_low~zone_high区间内，或距离level在tolerance内
        if (sr_zone["zone_low"] <= wall_price <= sr_zone["zone_high"] or
                abs(wall_price - sr_zone["level"]) / sr_zone["level"] < tolerance_pct):
            nearby_walls.append(wall)
    return nearby_walls


def generate_signal(
    trend: dict,
    sr_zones: list,
    big_order_result: dict,
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    funding_check: dict,
    instrument: str,
    order_walls: dict = None
) -> dict:
    """
    生成交易信号

    做多条件（全部满足）：
    1. 1D EMA144 > EMA169 (多头趋势)
    2. 4H价格在支撑区间内
    3. 1H出现多头反转K线
    4. 大单确认
    5. 资金费率<0.05%

    做空条件 = 镜像

    订单墙确认作为bonus加分，不改变现有checklist结构：
    - 做多时，支撑位附近有买单墙 -> 额外确认
    - 做空时，阻力位附近有卖单墙 -> 额外确认

    Args:
        trend: EMA趋势分析结果
        sr_zones: S/R区间列表
        big_order_result: 大单确认结果
        df_4h: 4H K线数据
        df_1h: 1H K线数据
        funding_check: 资金费率检查结果
        instrument: 品种名称
        order_walls: 订单墙数据，可选，默认None
            {"bid_walls": [...], "ask_walls": [...], "imbalance": float, ...}

    Returns:
        dict: {
            direction, entry_price, stop_loss, take_profit,
            reasons, checklist, risk_reward_ratio
        }
    """
    trend_state = trend.get("trend", "ENTANGLED")
    current_price = float(df_1h["close"].iloc[-1])
    atr = _calc_atr(df_1h)

    # 精度设置
    precision_map = {"BTC": 1, "ETH": 2, "XAU": 2, "XAG": 3, "BTC_SPOT": 1, "ETH_SPOT": 2}
    precision = precision_map.get(instrument, 2)

    checklist = {}
    reasons = []

    # ============ 做多检查 ============
    if trend_state == "GOLDEN_CROSS":
        # 条件1: EMA多头趋势
        checklist["ema_trend"] = True
        reasons.append(f"EMA144({trend['ema144']:.{precision}f}) > EMA169({trend['ema169']:.{precision}f})，多头趋势")

        # 条件2: 4H价格在支撑区间
        price_4h = float(df_4h["close"].iloc[-1])
        support_zone = _find_nearest_sr(price_4h, sr_zones, "support")
        in_support = support_zone is not None and _price_in_zone(price_4h, support_zone)
        # 放宽条件：价格接近支撑区也算
        near_support = (support_zone is not None and
                       abs(price_4h - support_zone["level"]) / price_4h < 0.01)
        checklist["in_support_zone"] = in_support or near_support
        if in_support:
            reasons.append(f"4H价格({price_4h:.{precision}f})在支撑区间({support_zone['zone_low']:.{precision}f}-{support_zone['zone_high']:.{precision}f})")
        elif near_support:
            reasons.append(f"4H价格({price_4h:.{precision}f})接近支撑位({support_zone['level']:.{precision}f})")
        else:
            reasons.append(f"4H价格({price_4h:.{precision}f})未在支撑区间附近")

        # 条件3: 1H多头反转K线
        bull_candle = _check_bullish_candle(df_1h)
        checklist["bullish_candle"] = bull_candle["detected"]
        if bull_candle["detected"]:
            reasons.append(f"1H出现多头反转形态: {bull_candle['pattern']}")
        else:
            reasons.append(f"1H未出现多头反转形态")

        # 条件4: 大单确认
        checklist["big_order_confirm"] = big_order_result.get("confirmed", False)
        for r in big_order_result.get("reasons", []):
            reasons.append(r)

        # 条件5: 资金费率
        funding_rate_pct = abs(funding_check.get("rate_pct", 0))
        checklist["funding_rate_ok"] = funding_rate_pct < 0.05
        if funding_rate_pct < 0.05:
            reasons.append(f"资金费率正常({funding_rate_pct:.4f}%)")
        else:
            reasons.append(f"资金费率偏高({funding_rate_pct:.4f}%)，做多风险大")

        # 订单墙bonus确认（不改变checklist结构，只加reasons）
        wall_bonus = False
        if order_walls is not None:
            bid_walls = order_walls.get("bid_walls", [])
            # 做多时，检查支撑位附近是否有买单墙
            if support_zone and bid_walls:
                nearby_bid_walls = _check_wall_near_sr(bid_walls, support_zone)
                if nearby_bid_walls:
                    wall_bonus = True
                    for wall in nearby_bid_walls:
                        reasons.append(f"订单墙bonus: 支撑位附近有买单墙 {wall['price']:.{precision}f} [{wall['strength']}x]")
            # 买卖比偏多
            imbalance = order_walls.get("imbalance", 1.0)
            if imbalance > 1.5:
                reasons.append(f"订单墙bonus: 买卖比{imbalance}偏多，买方力量占优")

        # 生成做多信号
        all_pass = all(checklist.values())
        if all_pass:
            # 止损：支撑区间下沿 - 1ATR
            if support_zone:
                stop_loss = support_zone["zone_low"] - atr
            else:
                stop_loss = current_price - 2 * atr

            # 止盈：下一阻力位，至少2:1盈亏比
            next_resistance = _find_next_sr(current_price, sr_zones, "resistance")
            if next_resistance:
                take_profit = next_resistance["level"]
            else:
                take_profit = current_price + 3 * atr

            # 确保盈亏比至少2:1
            risk = current_price - stop_loss
            reward = take_profit - current_price
            if risk > 0 and reward / risk < 2:
                take_profit = current_price + risk * 2

            direction = "LONG"
            entry_price = current_price
        else:
            direction = "NEUTRAL"
            entry_price = current_price
            stop_loss = 0
            take_profit = 0

    # ============ 做空检查 ============
    elif trend_state == "DEATH_CROSS":
        # 条件1: EMA空头趋势
        checklist["ema_trend"] = True
        reasons.append(f"EMA144({trend['ema144']:.{precision}f}) < EMA169({trend['ema169']:.{precision}f})，空头趋势")

        # 条件2: 4H价格在阻力区间
        price_4h = float(df_4h["close"].iloc[-1])
        resistance_zone = _find_nearest_sr(price_4h, sr_zones, "resistance")
        in_resistance = resistance_zone is not None and _price_in_zone(price_4h, resistance_zone)
        near_resistance = (resistance_zone is not None and
                          abs(price_4h - resistance_zone["level"]) / price_4h < 0.01)
        checklist["in_resistance_zone"] = in_resistance or near_resistance
        if in_resistance:
            reasons.append(f"4H价格({price_4h:.{precision}f})在阻力区间({resistance_zone['zone_low']:.{precision}f}-{resistance_zone['zone_high']:.{precision}f})")
        elif near_resistance:
            reasons.append(f"4H价格({price_4h:.{precision}f})接近阻力位({resistance_zone['level']:.{precision}f})")
        else:
            reasons.append(f"4H价格({price_4h:.{precision}f})未在阻力区间附近")

        # 条件3: 1H空头反转K线
        bear_candle = _check_bearish_candle(df_1h)
        checklist["bearish_candle"] = bear_candle["detected"]
        if bear_candle["detected"]:
            reasons.append(f"1H出现空头反转形态: {bear_candle['pattern']}")
        else:
            reasons.append(f"1H未出现空头反转形态")

        # 条件4: 大单确认
        short_direction = "SHORT"
        checklist["big_order_confirm"] = big_order_result.get("confirmed", False)
        for r in big_order_result.get("reasons", []):
            reasons.append(r)

        # 条件5: 资金费率（做空时高费率反而有利）
        funding_rate_pct = funding_check.get("rate_pct", 0)
        checklist["funding_rate_ok"] = True  # 做空时费率条件放宽
        reasons.append(f"资金费率: {funding_rate_pct:.4f}% ({funding_check.get('status', 'UNKNOWN')})")

        # 订单墙bonus确认（不改变checklist结构，只加reasons）
        wall_bonus = False
        if order_walls is not None:
            ask_walls = order_walls.get("ask_walls", [])
            # 做空时，检查阻力位附近是否有卖单墙
            if resistance_zone and ask_walls:
                nearby_ask_walls = _check_wall_near_sr(ask_walls, resistance_zone)
                if nearby_ask_walls:
                    wall_bonus = True
                    for wall in nearby_ask_walls:
                        reasons.append(f"订单墙bonus: 阻力位附近有卖单墙 {wall['price']:.{precision}f} [{wall['strength']}x]")
            # 买卖比偏空
            imbalance = order_walls.get("imbalance", 1.0)
            if imbalance < 0.67:
                reasons.append(f"订单墙bonus: 买卖比{imbalance}偏空，卖方力量占优")

        # 生成做空信号
        all_pass = all(checklist.values())
        if all_pass:
            # 止损：阻力区间上沿 + 1ATR
            if resistance_zone:
                stop_loss = resistance_zone["zone_high"] + atr
            else:
                stop_loss = current_price + 2 * atr

            # 止盈：下一支撑位
            next_support = _find_next_sr(current_price, sr_zones, "support")
            if next_support:
                take_profit = next_support["level"]
            else:
                take_profit = current_price - 3 * atr

            # 确保盈亏比至少2:1
            risk = stop_loss - current_price
            reward = current_price - take_profit
            if risk > 0 and reward / risk < 2:
                take_profit = current_price - risk * 2

            direction = "SHORT"
            entry_price = current_price
        else:
            direction = "NEUTRAL"
            entry_price = current_price
            stop_loss = 0
            take_profit = 0

    # ============ 缠绕/观望 ============
    else:
        direction = "NEUTRAL"
        entry_price = current_price
        stop_loss = 0
        take_profit = 0
        checklist["ema_trend"] = False
        checklist["in_sr_zone"] = False
        checklist["reversal_candle"] = False
        checklist["big_order_confirm"] = False
        checklist["funding_rate_ok"] = False
        reasons.append(f"EMA缠绕，趋势不明确，建议观望")
        reasons.append(f"EMA144={trend['ema144']:.{precision}f}, EMA169={trend['ema169']:.{precision}f}, 分离度={trend['separation_pct']:.3f}%")

    # 计算盈亏比
    risk_reward_ratio = 0.0
    if direction in ("LONG", "SHORT") and stop_loss != 0:
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        if risk > 0:
            risk_reward_ratio = round(reward / risk, 2)

    # 格式化价格
    if direction in ("LONG", "SHORT"):
        entry_price = round(entry_price, precision)
        stop_loss = round(stop_loss, precision)
        take_profit = round(take_profit, precision)

    return {
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reasons": reasons,
        "checklist": checklist,
        "risk_reward_ratio": risk_reward_ratio
    }
