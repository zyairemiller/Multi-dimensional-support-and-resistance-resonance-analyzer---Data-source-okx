"""
Signal Generation Module - Combine EMA trend, S/R, Big Order confirmation, Order Walls to generate trading signals
"""

import pandas as pd
import numpy as np
from typing import Literal, Optional

SignalDirection = Literal["LONG", "SHORT", "NEUTRAL"]


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate ATR"""
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
    Check for Bullish reversal candlestick patterns on 1H timeframe

    Pattern recognition:
    - Hammer: Lower shadow > body*2, upper shadow < body*0.5
    - Bullish Engulfing: Current bullish candle body completely engulfs previous bearish candle body
    - Long lower shadow: Lower shadow > 60% of total candle range

    Args:
        df_1h: 1H candlestick data

    Returns:
        dict: {detected: bool, pattern: str}
    """
    if len(df_1h) < 3:
        return {"detected": False, "pattern": "Insufficient data"}

    latest = df_1h.iloc[-1]
    prev = df_1h.iloc[-2]

    o, h, l, c = latest["open"], latest["high"], latest["low"], latest["close"]
    body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l
    total_range = h - l

    # Prevent division by zero
    if total_range == 0 or body == 0:
        # Doji handling
        if total_range > 0 and lower_shadow / total_range > 0.6:
            return {"detected": True, "pattern": "Long Lower Shadow Doji"}
        return {"detected": False, "pattern": "No clear pattern"}

    # Hammer
    if lower_shadow > body * 2 and upper_shadow < body * 0.5 and c > o:
        return {"detected": True, "pattern": "Hammer"}

    # Long lower shadow
    if lower_shadow / total_range > 0.6 and c > o:
        return {"detected": True, "pattern": "Long Lower Shadow Bullish Candle"}

    # Bullish Engulfing
    prev_body = abs(prev["close"] - prev["open"])
    if (prev["close"] < prev["open"]  # Previous bearish candle
        and c > o  # Current bullish candle
        and c > prev["open"]  # Current close > Previous open
        and o < prev["close"]  # Current open < Previous close
        and body > prev_body * 1.1):  # Larger body
        return {"detected": True, "pattern": "Bullish Engulfing"}

    return {"detected": False, "pattern": "No clear bullish pattern"}


def _check_bearish_candle(df_1h: pd.DataFrame) -> dict:
    """
    Check for Bearish reversal candlestick patterns on 1H timeframe

    Pattern recognition:
    - Shooting Star: Upper shadow > body*2, lower shadow < body*0.5
    - Bearish Engulfing: Current bearish candle body completely engulfs previous bullish candle body
    - Long upper shadow: Upper shadow > 60% of total candle range

    Args:
        df_1h: 1H candlestick data

    Returns:
        dict: {detected: bool, pattern: str}
    """
    if len(df_1h) < 3:
        return {"detected": False, "pattern": "Insufficient data"}

    latest = df_1h.iloc[-1]
    prev = df_1h.iloc[-2]

    o, h, l, c = latest["open"], latest["high"], latest["low"], latest["close"]
    body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l
    total_range = h - l

    if total_range == 0 or body == 0:
        if total_range > 0 and upper_shadow / total_range > 0.6:
            return {"detected": True, "pattern": "Long Upper Shadow Doji"}
        return {"detected": False, "pattern": "No clear pattern"}

    # Shooting Star
    if upper_shadow > body * 2 and lower_shadow < body * 0.5 and c < o:
        return {"detected": True, "pattern": "Shooting Star"}

    # Long upper shadow
    if upper_shadow / total_range > 0.6 and c < o:
        return {"detected": True, "pattern": "Long Upper Shadow Bearish Candle"}

    # Bearish Engulfing
    prev_body = abs(prev["close"] - prev["open"])
    if (prev["close"] > prev["open"]  # Previous bullish candle
        and c < o  # Current bearish candle
        and c < prev["open"]  # Current close < Previous open
        and o > prev["close"]  # Current open > Previous close
        and body > prev_body * 1.1):
        return {"detected": True, "pattern": "Bearish Engulfing"}

    return {"detected": False, "pattern": "No clear bearish pattern"}


def _price_in_zone(price: float, zone: dict) -> bool:
    """Check if price is within S/R zone"""
    return zone["zone_low"] <= price <= zone["zone_high"]


def _find_nearest_sr(price: float, sr_zones: list, sr_type: str) -> dict:
    """
    Find nearest S/R of specified type

    Args:
        price: Current price
        sr_zones: List of S/R zones
        sr_type: "support" or "resistance"

    Returns:
        Nearest S/R zone, or None
    """
    candidates = [z for z in sr_zones if z["type"] == sr_type]
    if not candidates:
        return None

    # For support, find below price; for resistance, find above price
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
    Find next S/R level (for Take Profit)

    Args:
        price: Current price
        sr_zones: List of S/R zones
        sr_type: "support" or "resistance"

    Returns:
        Next S/R zone, or None
    """
    candidates = [z for z in sr_zones if z["type"] == sr_type]
    if not candidates:
        return None

    if sr_type == "resistance":
        # For long take profit, find resistance above
        above = [z for z in candidates if z["level"] > price]
        if above:
            return min(above, key=lambda z: z["level"])
    else:
        # For short take profit, find support below
        below = [z for z in candidates if z["level"] < price]
        if below:
            return max(below, key=lambda z: z["level"])

    return None


def _check_wall_near_sr(walls: list, sr_zone: dict, tolerance_pct: float = 0.005) -> list:
    """
    Check if order walls are near S/R zone

    Args:
        walls: Order wall list [{"price": float, "size": float, "strength": float}, ...]
        sr_zone: S/R zone {"level": float, "zone_low": float, "zone_high": float, ...}
        tolerance_pct: Price tolerance percentage

    Returns:
        List of order walls near S/R
    """
    nearby_walls = []
    for wall in walls:
        wall_price = wall.get("price", 0)
        if wall_price <= 0:
            continue
        # Check if wall price is within zone_low~zone_high, or within tolerance of level
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
    Generate trading signal

    Long conditions (all must be met):
    1. 1D EMA144 > EMA169 (Bullish trend)
    2. 4H price within support zone
    3. 1H bullish reversal candlestick
    4. Big order confirmation
    5. Funding rate < 0.05%

    Short conditions = Mirror image

    Order wall confirmation acts as bonus, does not change existing checklist structure:
    - For long: Bid wall near support -> Extra confirmation
    - For short: Ask wall near resistance -> Extra confirmation

    Args:
        trend: EMA trend analysis result
        sr_zones: List of S/R zones
        big_order_result: Big order confirmation result
        df_4h: 4H candlestick data
        df_1h: 1H candlestick data
        funding_check: Funding rate check result
        instrument: Instrument name
        order_walls: Order wall data, optional, default None
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

    # Precision settings
    precision_map = {"BTC": 1, "ETH": 2, "XAU": 2, "XAG": 3, "BTC_SPOT": 1, "ETH_SPOT": 2}
    precision = precision_map.get(instrument, 2)

    checklist = {}
    reasons = []

    # ============ Long Check ============
    if trend_state == "GOLDEN_CROSS":
        # Condition 1: EMA bullish trend
        checklist["ema_trend"] = True
        reasons.append(f"EMA144({trend['ema144']:.{precision}f}) > EMA169({trend['ema169']:.{precision}f}), Bullish trend")

        # Condition 2: 4H price in support zone
        price_4h = float(df_4h["close"].iloc[-1])
        support_zone = _find_nearest_sr(price_4h, sr_zones, "support")
        in_support = support_zone is not None and _price_in_zone(price_4h, support_zone)
        # Relaxed condition: Price near support zone also counts
        near_support = (support_zone is not None and
                       abs(price_4h - support_zone["level"]) / price_4h < 0.01)
        checklist["in_support_zone"] = in_support or near_support
        if in_support:
            reasons.append(f"4H price({price_4h:.{precision}f}) is in support zone({support_zone['zone_low']:.{precision}f}-{support_zone['zone_high']:.{precision}f})")
        elif near_support:
            reasons.append(f"4H price({price_4h:.{precision}f}) is near support({support_zone['level']:.{precision}f})")
        else:
            reasons.append(f"4H price({price_4h:.{precision}f}) is not near any support zone")

        # Condition 3: 1H bullish reversal candle
        bull_candle = _check_bullish_candle(df_1h)
        checklist["bullish_candle"] = bull_candle["detected"]
        if bull_candle["detected"]:
            reasons.append(f"1H bullish reversal pattern detected: {bull_candle['pattern']}")
        else:
            reasons.append(f"1H no bullish reversal pattern detected")

        # Condition 4: Big order confirmation
        checklist["big_order_confirm"] = big_order_result.get("confirmed", False)
        for r in big_order_result.get("reasons", []):
            reasons.append(r)

        # Condition 5: Funding rate
        funding_rate_pct = abs(funding_check.get("rate_pct", 0))
        checklist["funding_rate_ok"] = funding_rate_pct < 0.05
        if funding_rate_pct < 0.05:
            reasons.append(f"Funding rate normal({funding_rate_pct:.4f}%)")
        else:
            reasons.append(f"Funding rate elevated({funding_rate_pct:.4f}%), high risk for long")

        # Order wall bonus confirmation (doesn't change checklist structure, only adds reasons)
        wall_bonus = False
        if order_walls is not None:
            bid_walls = order_walls.get("bid_walls", [])
            # For long, check if there are bid walls near support
            if support_zone and bid_walls:
                nearby_bid_walls = _check_wall_near_sr(bid_walls, support_zone)
                if nearby_bid_walls:
                    wall_bonus = True
                    for wall in nearby_bid_walls:
                        reasons.append(f"Order wall bonus: Bid wall near support at {wall['price']:.{precision}f} [{wall['strength']}x]")
            # Buy/sell ratio skewed bullish
            imbalance = order_walls.get("imbalance", 1.0)
            if imbalance > 1.5:
                reasons.append(f"Order wall bonus: Buy/Sell ratio {imbalance} skewed bullish, buying pressure dominant")

        # Generate long signal
        all_pass = all(checklist.values())
        if all_pass:
            # Stop Loss: Support zone lower boundary - 1ATR
            if support_zone:
                stop_loss = support_zone["zone_low"] - atr
            else:
                stop_loss = current_price - 2 * atr

            # Take Profit: Next resistance, at least 2:1 Risk/Reward
            next_resistance = _find_next_sr(current_price, sr_zones, "resistance")
            if next_resistance:
                take_profit = next_resistance["level"]
            else:
                take_profit = current_price + 3 * atr

            # Ensure Risk/Reward at least 2:1
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

    # ============ Short Check ============
    elif trend_state == "DEATH_CROSS":
        # Condition 1: EMA bearish trend
        checklist["ema_trend"] = True
        reasons.append(f"EMA144({trend['ema144']:.{precision}f}) < EMA169({trend['ema169']:.{precision}f}), Bearish trend")

        # Condition 2: 4H price in resistance zone
        price_4h = float(df_4h["close"].iloc[-1])
        resistance_zone = _find_nearest_sr(price_4h, sr_zones, "resistance")
        in_resistance = resistance_zone is not None and _price_in_zone(price_4h, resistance_zone)
        near_resistance = (resistance_zone is not None and
                          abs(price_4h - resistance_zone["level"]) / price_4h < 0.01)
        checklist["in_resistance_zone"] = in_resistance or near_resistance
        if in_resistance:
            reasons.append(f"4H price({price_4h:.{precision}f}) is in resistance zone({resistance_zone['zone_low']:.{precision}f}-{resistance_zone['zone_high']:.{precision}f})")
        elif near_resistance:
            reasons.append(f"4H price({price_4h:.{precision}f}) is near resistance({resistance_zone['level']:.{precision}f})")
        else:
            reasons.append(f"4H price({price_4h:.{precision}f}) is not near any resistance zone")

        # Condition 3: 1H bearish reversal candle
        bear_candle = _check_bearish_candle(df_1h)
        checklist["bearish_candle"] = bear_candle["detected"]
        if bear_candle["detected"]:
            reasons.append(f"1H bearish reversal pattern detected: {bear_candle['pattern']}")
        else:
            reasons.append(f"1H no bearish reversal pattern detected")

        # Condition 4: Big order confirmation
        short_direction = "SHORT"
        checklist["big_order_confirm"] = big_order_result.get("confirmed", False)
        for r in big_order_result.get("reasons", []):
            reasons.append(r)

        # Condition 5: Funding rate (high rate is actually favorable for shorting)
        funding_rate_pct = funding_check.get("rate_pct", 0)
        checklist["funding_rate_ok"] = True  # Relaxed condition for shorting
        reasons.append(f"Funding rate: {funding_rate_pct:.4f}% ({funding_check.get('status', 'UNKNOWN')})")

        # Order wall bonus confirmation (doesn't change checklist structure, only adds reasons)
        wall_bonus = False
        if order_walls is not None:
            ask_walls = order_walls.get("ask_walls", [])
            # For short, check if there are ask walls near resistance
            if resistance_zone and ask_walls:
                nearby_ask_walls = _check_wall_near_sr(ask_walls, resistance_zone)
                if nearby_ask_walls:
                    wall_bonus = True
                    for wall in nearby_ask_walls:
                        reasons.append(f"Order wall bonus: Ask wall near resistance at {wall['price']:.{precision}f} [{wall['strength']}x]")
            # Buy/sell ratio skewed bearish
            imbalance = order_walls.get("imbalance", 1.0)
            if imbalance < 0.67:
                reasons.append(f"Order wall bonus: Buy/Sell ratio {imbalance} skewed bearish, selling pressure dominant")

        # Generate short signal
        all_pass = all(checklist.values())
        if all_pass:
            # Stop Loss: Resistance zone upper boundary + 1ATR
            if resistance_zone:
                stop_loss = resistance_zone["zone_high"] + atr
            else:
                stop_loss = current_price + 2 * atr

            # Take Profit: Next support
            next_support = _find_next_sr(current_price, sr_zones, "support")
            if next_support:
                take_profit = next_support["level"]
            else:
                take_profit = current_price - 3 * atr

            # Ensure Risk/Reward at least 2:1
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

    # ============ Entangled / Standby ============
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
        reasons.append(f"EMA entangled, trend unclear, recommend standby")
        reasons.append(f"EMA144={trend['ema144']:.{precision}f}, EMA169={trend['ema169']:.{precision}f}, Separation={trend['separation_pct']:.3f}%")

    # Calculate Risk/Reward ratio
    risk_reward_ratio = 0.0
    if direction in ("LONG", "SHORT") and stop_loss != 0:
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        if risk > 0:
            risk_reward_ratio = round(reward / risk, 2)

    # Format prices
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
