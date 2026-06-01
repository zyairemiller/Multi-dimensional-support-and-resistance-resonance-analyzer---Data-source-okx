"""
Support/Resistance Identification Module - Structural S/R + Psychological Levels + FVG + Liquidation Zones + Order Walls + Scoring System
"""

import pandas as pd
import numpy as np
from typing import Literal, Optional

SRLevelType = Literal["support", "resistance"]
SRStrength = Literal["super", "strong", "weak"]


def find_structural_sr(df_1d: pd.DataFrame, lookback: int = 60) -> list:
    """
    Identify structural support/resistance using local extrema method

    High point with both sides lower than current high -> Confirmed as resistance
    Low point with both sides higher than current low -> Confirmed as support

    Args:
        df_1d: 1D candlestick data
        lookback: Number of lookback days

    Returns:
        list of dict: [{level, type, touch_count}, ...]
    """
    df = df_1d.tail(lookback).reset_index(drop=True)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    sr_levels = []
    window = 3  # Look at 3 candles on each side

    # Find resistance levels (local highs)
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

    # Find support levels (local lows)
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

    # Count touch occurrences
    for sr in sr_levels:
        count = 0
        for c in closes:
            # Price crossing this level and reversing counts as a touch
            if sr["type"] == "resistance" and c >= sr["level"] * 0.998 and c <= sr["level"] * 1.005:
                count += 1
            elif sr["type"] == "support" and c <= sr["level"] * 1.002 and c >= sr["level"] * 0.995:
                count += 1
        sr["touch_count"] = max(count, 1)

    # Merge adjacent S/R levels within 0.5% distance
    sr_levels = _merge_nearby_levels(sr_levels, 0.005)

    return sr_levels


def _merge_nearby_levels(levels: list, threshold_pct: float) -> list:
    """
    Merge S/R levels that are too close together

    Args:
        levels: List of S/R levels
        threshold_pct: Merge threshold percentage (e.g., 0.005 = 0.5%)

    Returns:
        Merged S/R list
    """
    if not levels:
        return levels

    # Sort by price
    sorted_levels = sorted(levels, key=lambda x: x["level"])
    merged = [sorted_levels[0]]

    for level in sorted_levels[1:]:
        prev = merged[-1]
        # If distance is less than threshold and same type, merge
        if abs(level["level"] - prev["level"]) / prev["level"] < threshold_pct:
            # Keep the one with more touches
            if level["touch_count"] > prev["touch_count"]:
                merged[-1] = level
            else:
                prev["touch_count"] += level["touch_count"]
        else:
            merged.append(level)

    return merged


def find_psychological_levels(current_price: float, instrument: str) -> list:
    """
    Identify psychological levels - dynamically calculate round number levels based on current price

    Algorithm: Center on current price, generate round number levels within ±15% range.
    Step size varies by instrument: BTC=1000 / ETH=100 / XAU=50 / XAG=1

    Args:
        current_price: Current price
        instrument: Instrument name (BTC/ETH/XAU/XAG/BTC_SPOT/ETH_SPOT)

    Returns:
        list of float: Nearby psychological levels
    """
    levels = []

    # Spot instruments use the same rules as their corresponding swap instruments
    base_instrument = instrument.replace("_SPOT", "")

    # Determine level step by instrument
    step_map = {
        "BTC": 1000,
        "ETH": 100,
        "XAU": 50,
        "XAG": 1,
    }
    step = step_map.get(base_instrument, int(current_price * 0.05))

    # Dynamically generate all round number levels within ±15% range
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
    Identify 1D-level Fair Value Gaps (FVG)

    Upward gap: Previous candle high < Next candle low
    Downward gap: Previous candle low > Next candle high
    Only mark unfilled gaps

    Args:
        df_1d: 1D candlestick data

    Returns:
        list of dict: [{level, type, gap_high, gap_low, filled}, ...]
    """
    fvgs = []
    highs = df_1d["high"].values
    lows = df_1d["low"].values
    closes = df_1d["close"].values

    for i in range(2, len(df_1d)):
        # Upward gap: candle i-2 high < candle i low (gap in between)
        if highs[i - 2] < lows[i]:
            gap_low = float(highs[i - 2])
            gap_high = float(lows[i])
            level = (gap_low + gap_high) / 2

            # Check if already filled
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

        # Downward gap: candle i-2 low > candle i high
        elif lows[i - 2] > highs[i]:
            gap_high = float(lows[i - 2])
            gap_low = float(highs[i])
            level = (gap_low + gap_high) / 2

            # Check if already filled
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
    Calculate ATR (Average True Range)

    Args:
        df: Candlestick data
        period: ATR period

    Returns:
        Latest ATR value
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

    # Calculate ATR using EMA
    atr = np.mean(trs[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(trs)):
        atr = trs[i] * k + atr * (1 - k)

    return float(atr)


def score_sr(level: dict, df_1d: pd.DataFrame, psychological_levels: list, fvgs: list, liquidation_levels: list = [], order_walls: dict = None) -> dict:
    """
    Score an S/R level

    Scoring criteria:
    - +1 point per touch
    - +2 points for reversal > 2ATR
    - +1 point if it's a psychological level
    - +1 point if it's an FVG
    - +1 point if it's a liquidation concentration zone
    - +1 point if there's a same-direction order wall near S/R

    >=5 points = Super S/R, 3-4 = Strong S/R, 1-2 = Weak S/R

    Args:
        level: S/R level {level, type, touch_count}
        df_1d: 1D candlestick data
        psychological_levels: List of psychological levels
        fvgs: List of FVGs
        liquidation_levels: List of liquidation concentration zones [{price, volume, side}, ...]
        order_walls: Order wall data, optional, default None
            {"bid_walls": [...], "ask_walls": [...], ...}

    Returns:
        dict: {level, type, score, strength, touch_count, is_psychological, is_fvg, is_liquidation, is_order_wall}
    """
    score = 0
    price = level["level"]
    level_type = level["type"]

    # 1. Touch count bonus
    touch_count = level.get("touch_count", 1)
    score += min(touch_count, 5)  # Max 5 points

    # 2. Reversal > 2ATR bonus
    atr = _calc_atr(df_1d)
    if atr > 0:
        closes = df_1d["close"].values
        for i in range(1, len(closes)):
            if level_type == "resistance":
                # Price touched resistance and pulled back
                if df_1d["high"].values[i] >= price * 0.998:
                    reversal = price - closes[i]
                    if reversal > 2 * atr:
                        score += 2
                        break
            elif level_type == "support":
                # Price touched support and bounced
                if df_1d["low"].values[i] <= price * 1.002:
                    reversal = closes[i] - price
                    if reversal > 2 * atr:
                        score += 2
                        break

    # 3. Psychological level bonus
    is_psychological = any(abs(p - price) / price < 0.002 for p in psychological_levels)
    if is_psychological:
        score += 1

    # 4. FVG bonus
    is_fvg = any(abs(f["level"] - price) / price < 0.003 for f in fvgs)
    if is_fvg:
        score += 1

    # 5. Liquidation concentration zone bonus
    is_liquidation = any(abs(lq["price"] - price) / price < 0.005 for lq in liquidation_levels)
    if is_liquidation:
        score += 1

    # 6. Order wall bonus: If there's a same-direction order wall near S/R -> score +1
    # Bid wall + Support / Ask wall + Resistance
    is_order_wall = False
    if order_walls is not None:
        if level_type == "support":
            # Look for bid walls near support
            bid_walls = order_walls.get("bid_walls", [])
            for wall in bid_walls:
                wall_price = wall.get("price", 0)
                if wall_price > 0 and abs(wall_price - price) / price < 0.005:
                    is_order_wall = True
                    score += 1
                    break
        elif level_type == "resistance":
            # Look for ask walls near resistance
            ask_walls = order_walls.get("ask_walls", [])
            for wall in ask_walls:
                wall_price = wall.get("price", 0)
                if wall_price > 0 and abs(wall_price - price) / price < 0.005:
                    is_order_wall = True
                    score += 1
                    break

    # Determine strength
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
    Convert S/R points to zones

    BTC/BTC_SPOT +-0.3%, ETH/ETH_SPOT +-0.4%, XAU +-0.3%, XAG +-0.5%

    Args:
        sr_levels: Scored S/R list
        instrument: Instrument name

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
