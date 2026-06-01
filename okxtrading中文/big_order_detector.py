"""
大单检测模块 - 分析主动买卖力量、持仓量变化、资金费率
"""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

SignalType = Literal["BULLISH", "BEARISH", "NEUTRAL"]


# 大单门槛（按品种）
BIG_ORDER_THRESHOLD = {
    "BTC": 10.0,       # >=10 BTC
    "ETH": 500.0,      # >=500 ETH
    "XAU": 500.0,      # >=500 oz
    "XAG": 10000.0,    # >=10000 oz
    "BTC_SPOT": 10.0,  # >=10 BTC
    "ETH_SPOT": 500.0, # >=500 ETH
}


def _get_instrument_from_inst_id(inst_id: str) -> str:
    """从instId提取品种名称"""
    return inst_id.split("-")[0]


def analyze_big_orders(trades: list, inst_id: str) -> dict:
    """
    分析大单情况

    定义大单门槛: BTC>=10BTC, ETH>=500ETH, XAU>=500oz, XAG>=10000oz
    计算主动买入量 vs 主动卖出量

    当输入trades为空时（如现货品种），返回合理的默认值

    Args:
        trades: 逐笔成交列表（现货品种可能为空列表）
        inst_id: 产品ID

    Returns:
        dict: {
            buy_volume, sell_volume, buy_count, sell_count,
            ratio, signal, big_buy_volume, big_sell_volume,
            big_buy_count, big_sell_count
        }
    """
    instrument = _get_instrument_from_inst_id(inst_id)
    threshold = BIG_ORDER_THRESHOLD.get(instrument, 10.0)

    # 现货品种trades为空时，返回默认值
    if not trades:
        logger.info(f"无逐笔成交数据 ({inst_id})，返回默认大单分析结果")
        return {
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "buy_count": 0,
            "sell_count": 0,
            "ratio": 1.0,
            "big_buy_volume": 0.0,
            "big_sell_volume": 0.0,
            "big_buy_count": 0,
            "big_sell_count": 0,
            "big_ratio": 1.0,
            "signal": "NEUTRAL"
        }

    buy_volume = 0.0
    sell_volume = 0.0
    buy_count = 0
    sell_count = 0
    big_buy_volume = 0.0
    big_sell_volume = 0.0
    big_buy_count = 0
    big_sell_count = 0

    for trade in trades:
        sz = trade.get("sz", 0)
        side = trade.get("side", "")
        px = trade.get("px", 0)
        volume = sz  # 用数量而非金额

        if side == "buy":
            buy_volume += volume
            buy_count += 1
            if sz >= threshold:
                big_buy_volume += volume
                big_buy_count += 1
        elif side == "sell":
            sell_volume += volume
            sell_count += 1
            if sz >= threshold:
                big_sell_volume += volume
                big_sell_count += 1

    # 计算比率
    if sell_volume > 0:
        ratio = buy_volume / sell_volume
    elif buy_volume > 0:
        ratio = 99.0
    else:
        ratio = 1.0

    # 大单比率
    if big_sell_volume > 0:
        big_ratio = big_buy_volume / big_sell_volume
    elif big_buy_volume > 0:
        big_ratio = 99.0
    else:
        big_ratio = 1.0

    # 判断信号（优先看大单方向）
    if big_ratio > 2.0:
        signal = "BULLISH"
    elif big_ratio < 0.5:
        signal = "BEARISH"
    elif ratio > 1.5:
        signal = "BULLISH"
    elif ratio < 0.67:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {
        "buy_volume": round(buy_volume, 4),
        "sell_volume": round(sell_volume, 4),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "ratio": round(ratio, 3),
        "big_buy_volume": round(big_buy_volume, 4),
        "big_sell_volume": round(big_sell_volume, 4),
        "big_buy_count": big_buy_count,
        "big_sell_count": big_sell_count,
        "big_ratio": round(big_ratio, 3),
        "signal": signal
    }


def analyze_oi_change(oi_current: dict, oi_previous: dict) -> dict:
    """
    分析持仓量变化

    Args:
        oi_current: 当前持仓量 {oi, oiCcy, ts}
        oi_previous: 之前的持仓量（如15分钟前）

    Returns:
        dict: {direction, change_pct, signal}
    """
    current_oi = float(oi_current.get("oi", 0)) if oi_current else 0
    previous_oi = float(oi_previous.get("oi", 0)) if oi_previous else 0

    if previous_oi == 0:
        return {
            "direction": "UNKNOWN",
            "change_pct": 0.0,
            "signal": "NEUTRAL"
        }

    change_pct = (current_oi - previous_oi) / previous_oi * 100

    if change_pct > 1.0:
        direction = "INCREASING"
        signal = "BULLISH"  # OI增加通常意味着新资金入场
    elif change_pct < -1.0:
        direction = "DECREASING"
        signal = "BEARISH"  # OI减少通常意味着资金离场
    else:
        direction = "STABLE"
        signal = "NEUTRAL"

    return {
        "direction": direction,
        "change_pct": round(change_pct, 3),
        "signal": signal
    }


def check_funding_rate(funding_data: dict) -> dict:
    """
    检查资金费率状态

    正常(0.01%) / 偏高(0.05%+) / 极高(0.1%+) / 负值

    现货品种funding_data为None时，返回默认值

    Args:
        funding_data: 资金费率数据（现货品种可能为None）

    Returns:
        dict: {rate, rate_pct, status, warning}
    """
    if not funding_data:
        return {
            "rate": 0.0,
            "rate_pct": 0.0,
            "status": "N/A",
            "warning": "现货品种无资金费率数据"
        }

    rate = float(funding_data.get("fundingRate", 0))
    rate_pct = rate * 100  # 转为百分比

    if rate < 0:
        status = "NEGATIVE"
        warning = "资金费率为负，空头付费给多头，市场偏空情绪但可能反转"
    elif rate_pct <= 0.01:
        status = "NORMAL"
        warning = ""
    elif rate_pct <= 0.05:
        status = "ELEVATED"
        warning = "资金费率偏高，多头情绪过热，注意回调风险"
    elif rate_pct <= 0.1:
        status = "HIGH"
        warning = "资金费率很高，市场极度偏多，高风险"
    else:
        status = "EXTREME"
        warning = "资金费率极高，市场极度过热，强烈回调风险"

    return {
        "rate": rate,
        "rate_pct": round(rate_pct, 4),
        "status": status,
        "warning": warning
    }


def big_order_confirmation(direction: str, trades_data: dict, oi_data: dict, funding_data: dict, df_1h=None) -> dict:
    """
    大单综合确认

    综合判断：大单方向 + OI方向 + 资金费率

    当big_order不可用（现货品种）时，checklist中相关项标记为False，
    但不阻断信号流程

    Args:
        direction: 信号方向 "LONG" 或 "SHORT"
        trades_data: analyze_big_orders 的返回结果
        oi_data: analyze_oi_change 的返回结果
        funding_data: check_funding_rate 的返回结果

    Returns:
        dict: {confirmed: bool, reasons: [], score: int}
    """
    reasons = []
    score = 0

    # NEUTRAL方向：不做确认，直接返回
    if direction == "NEUTRAL":
        return {
            "confirmed": False,
            "reasons": ["趋势不明确，无需大单确认"],
            "score": 0
        }

    # 检查是否为现货品种（trades_data中无成交数据）
    is_spot = trades_data.get("buy_count", 0) == 0 and trades_data.get("sell_count", 0) == 0

    if is_spot:
        reasons.append("现货品种无逐笔成交数据，大单确认不可用")
        # OI和资金费率也不可用，直接返回未确认
        return {
            "confirmed": False,
            "reasons": reasons,
            "score": 0
        }

    # 1. 大单方向确认
    big_signal = trades_data.get("signal", "NEUTRAL")
    if direction == "LONG" and big_signal == "BULLISH":
        reasons.append(f"大单方向一致：主动买入>2x卖出 (比率={trades_data.get('big_ratio', 1):.2f})")
        score += 3
    elif direction == "SHORT" and big_signal == "BEARISH":
        reasons.append(f"大单方向一致：主动卖出>2x买入 (比率={trades_data.get('big_ratio', 1):.2f})")
        score += 3
    elif big_signal == "NEUTRAL":
        reasons.append("大单方向中性，无明显偏向")
        score += 1
    else:
        reasons.append(f"大单方向矛盾：期望{direction}，实际{big_signal}")
        score -= 2

    # 2. OI方向确认
    oi_signal = oi_data.get("signal", "NEUTRAL")
    oi_direction = oi_data.get("direction", "UNKNOWN")
    if direction == "LONG" and oi_signal == "BULLISH":
        reasons.append(f"OI上升({oi_data.get('change_pct', 0):.2f}%)，新资金入场确认多头")
        score += 2
    elif direction == "SHORT" and oi_signal == "BEARISH":
        reasons.append(f"OI下降({oi_data.get('change_pct', 0):.2f}%)，资金离场确认空头")
        score += 2
    elif oi_signal == "NEUTRAL":
        reasons.append(f"OI变化不大({oi_direction})")
        score += 0
    else:
        reasons.append(f"OI方向矛盾：期望{direction}方向，实际OI {oi_direction}")
        score -= 1

    # 2b. OI-价格背离检测
    # 基于 OI 与价格变动的方向关系判断背离
    if df_1h is not None and not df_1h.empty and oi_direction != "UNKNOWN":
        try:
            lookback = min(20, len(df_1h))  # 近20根1H K线
            recent = df_1h.iloc[-lookback:]
            current_close = float(recent["close"].iloc[-1])
            prev_high = float(recent["high"].iloc[:-1].max())
            prev_low = float(recent["low"].iloc[:-1].min())
            price_new_high = current_close > prev_high
            price_new_low = current_close < prev_low
            oi_rising = oi_direction == "INCREASING"
            oi_falling = oi_direction == "DECREASING"

            if price_new_high and oi_falling:
                reasons.append(f"OI-价格背离：价格创新高但OI下降 → 看跌背离")
                score -= 3
            elif price_new_low and oi_rising:
                reasons.append(f"OI-价格确认：价格创新低OI上升 → 看跌确认")
                score -= 2
            elif price_new_low and oi_falling:
                reasons.append(f"OI-价格背离：价格创新低OI下降 → 看涨背离")
                score += 3
            elif price_new_high and oi_rising:
                reasons.append(f"OI-价格确认：价格创新高OI上升 → 看涨确认")
                score += 2
        except Exception:
            pass  # 背离检测失败不影响主流程

    # 3. 资金费率确认
    funding_status = funding_data.get("status", "UNKNOWN")
    if direction == "LONG":
        if funding_status in ("NORMAL", "NEGATIVE"):
            reasons.append(f"资金费率{funding_status}，做多环境良好")
            score += 2
        elif funding_status == "ELEVATED":
            reasons.append("资金费率偏高，做多需谨慎")
            score += 0
        else:
            reasons.append("资金费率过高，做多风险大")
            score -= 2
    elif direction == "SHORT":
        if funding_status in ("HIGH", "EXTREME"):
            reasons.append(f"资金费率{funding_status}，做空有费率优势")
            score += 2
        elif funding_status == "ELEVATED":
            reasons.append("资金费率偏高，做空有一定优势")
            score += 1
        elif funding_status == "NEGATIVE":
            reasons.append("资金费率为负，做空成本高")
            score -= 1
        else:
            reasons.append("资金费率正常，做空无费率优势")
            score += 0

    confirmed = score >= 4

    return {
        "confirmed": confirmed,
        "reasons": reasons,
        "score": score
    }
