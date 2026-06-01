"""
Big Order Detection Module - Analyze active buy/sell forces, Open Interest changes, Funding Rate
"""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

SignalType = Literal["BULLISH", "BEARISH", "NEUTRAL"]


# Big order thresholds (by instrument)
BIG_ORDER_THRESHOLD = {
    "BTC": 10.0,       # >=10 BTC
    "ETH": 500.0,      # >=500 ETH
    "XAU": 500.0,      # >=500 oz
    "XAG": 10000.0,    # >=10000 oz
    "BTC_SPOT": 10.0,  # >=10 BTC
    "ETH_SPOT": 500.0, # >=500 ETH
}


def _get_instrument_from_inst_id(inst_id: str) -> str:
    """Extract instrument name from instId"""
    return inst_id.split("-")[0]


def analyze_big_orders(trades: list, inst_id: str) -> dict:
    """
    Analyze big order activity

    Big order thresholds: BTC>=10BTC, ETH>=500ETH, XAU>=500oz, XAG>=10000oz
    Calculate active buy volume vs active sell volume

    When input trades is empty (e.g., Spot instruments), returns reasonable defaults

    Args:
        trades: List of individual trades (may be empty for Spot instruments)
        inst_id: Product ID

    Returns:
        dict: {
            buy_volume, sell_volume, buy_count, sell_count,
            ratio, signal, big_buy_volume, big_sell_volume,
            big_buy_count, big_sell_count
        }
    """
    instrument = _get_instrument_from_inst_id(inst_id)
    threshold = BIG_ORDER_THRESHOLD.get(instrument, 10.0)

    # Return defaults when trades is empty for Spot instruments
    if not trades:
        logger.info(f"No tick-by-tick trade data ({inst_id}), returning default big order analysis result")
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
        volume = sz  # Use quantity, not value

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

    # Calculate ratios
    if sell_volume > 0:
        ratio = buy_volume / sell_volume
    elif buy_volume > 0:
        ratio = 99.0
    else:
        ratio = 1.0

    # Big order ratio
    if big_sell_volume > 0:
        big_ratio = big_buy_volume / big_sell_volume
    elif big_buy_volume > 0:
        big_ratio = 99.0
    else:
        big_ratio = 1.0

    # Determine signal (prioritize big order direction)
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
    Analyze Open Interest changes

    Args:
        oi_current: Current Open Interest {oi, oiCcy, ts}
        oi_previous: Previous Open Interest (e.g., 15 minutes ago)

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
        signal = "BULLISH"  # OI increase usually means new capital entering
    elif change_pct < -1.0:
        direction = "DECREASING"
        signal = "BEARISH"  # OI decrease usually means capital exiting
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
    Check Funding Rate status

    Normal (0.01%) / Elevated (0.05%+) / High (0.1%+) / Negative

    Returns defaults when funding_data is None for Spot instruments

    Args:
        funding_data: Funding rate data (may be None for Spot instruments)

    Returns:
        dict: {rate, rate_pct, status, warning}
    """
    if not funding_data:
        return {
            "rate": 0.0,
            "rate_pct": 0.0,
            "status": "N/A",
            "warning": "Spot instruments have no funding rate data"
        }

    rate = float(funding_data.get("fundingRate", 0))
    rate_pct = rate * 100  # Convert to percentage

    if rate < 0:
        status = "NEGATIVE"
        warning = "Funding rate is negative, shorts pay longs, bearish sentiment but potential reversal"
    elif rate_pct <= 0.01:
        status = "NORMAL"
        warning = ""
    elif rate_pct <= 0.05:
        status = "ELEVATED"
        warning = "Funding rate is elevated, bullish sentiment overheated, watch for pullback risk"
    elif rate_pct <= 0.1:
        status = "HIGH"
        warning = "Funding rate is high, market extremely bullish, high risk"
    else:
        status = "EXTREME"
        warning = "Funding rate is extremely high, market extremely overheated, strong pullback risk"

    return {
        "rate": rate,
        "rate_pct": round(rate_pct, 4),
        "status": status,
        "warning": warning
    }


def big_order_confirmation(direction: str, trades_data: dict, oi_data: dict, funding_data: dict, df_1h=None) -> dict:
    """
    Big order comprehensive confirmation

    Combined judgment: Big order direction + OI direction + Funding rate

    When big_order is unavailable (Spot instruments), related checklist items are marked False,
    but the signal flow is not blocked

    Args:
        direction: Signal direction "LONG" or "SHORT"
        trades_data: Return result from analyze_big_orders
        oi_data: Return result from analyze_oi_change
        funding_data: Return result from check_funding_rate

    Returns:
        dict: {confirmed: bool, reasons: [], score: int}
    """
    reasons = []
    score = 0

    # NEUTRAL direction: No confirmation needed, return directly
    if direction == "NEUTRAL":
        return {
            "confirmed": False,
            "reasons": ["Trend unclear, no big order confirmation needed"],
            "score": 0
        }

    # Check if it's a Spot instrument (no trade data in trades_data)
    is_spot = trades_data.get("buy_count", 0) == 0 and trades_data.get("sell_count", 0) == 0

    if is_spot:
        reasons.append("Spot instruments have no tick-by-tick trade data, big order confirmation unavailable")
        # OI and funding rate also unavailable, return unconfirmed
        return {
            "confirmed": False,
            "reasons": reasons,
            "score": 0
        }

    # 1. Big order direction confirmation
    big_signal = trades_data.get("signal", "NEUTRAL")
    if direction == "LONG" and big_signal == "BULLISH":
        reasons.append(f"Big order direction aligned: Active buying >2x selling (ratio={trades_data.get('big_ratio', 1):.2f})")
        score += 3
    elif direction == "SHORT" and big_signal == "BEARISH":
        reasons.append(f"Big order direction aligned: Active selling >2x buying (ratio={trades_data.get('big_ratio', 1):.2f})")
        score += 3
    elif big_signal == "NEUTRAL":
        reasons.append("Big order direction neutral, no clear bias")
        score += 1
    else:
        reasons.append(f"Big order direction conflicting: Expected {direction}, actual {big_signal}")
        score -= 2

    # 2. OI direction confirmation
    oi_signal = oi_data.get("signal", "NEUTRAL")
    oi_direction = oi_data.get("direction", "UNKNOWN")
    if direction == "LONG" and oi_signal == "BULLISH":
        reasons.append(f"OI rising ({oi_data.get('change_pct', 0):.2f}%), new capital entering confirms bullish")
        score += 2
    elif direction == "SHORT" and oi_signal == "BEARISH":
        reasons.append(f"OI falling ({oi_data.get('change_pct', 0):.2f}%), capital exiting confirms bearish")
        score += 2
    elif oi_signal == "NEUTRAL":
        reasons.append(f"OI relatively unchanged ({oi_direction})")
        score += 0
    else:
        reasons.append(f"OI direction conflicting: Expected {direction}, actual OI {oi_direction}")
        score -= 1

    # 2b. OI-Price divergence detection
    # Based on directional relationship between OI and price movement
    if df_1h is not None and not df_1h.empty and oi_direction != "UNKNOWN":
        try:
            lookback = min(20, len(df_1h))  # Recent 20 1H candles
            recent = df_1h.iloc[-lookback:]
            current_close = float(recent["close"].iloc[-1])
            prev_high = float(recent["high"].iloc[:-1].max())
            prev_low = float(recent["low"].iloc[:-1].min())
            price_new_high = current_close > prev_high
            price_new_low = current_close < prev_low
            oi_rising = oi_direction == "INCREASING"
            oi_falling = oi_direction == "DECREASING"

            if price_new_high and oi_falling:
                reasons.append(f"OI-Price divergence: Price making new high but OI falling -> Bearish divergence")
                score -= 3
            elif price_new_low and oi_rising:
                reasons.append(f"OI-Price confirmation: Price making new low with OI rising -> Bearish confirmation")
                score -= 2
            elif price_new_low and oi_falling:
                reasons.append(f"OI-Price divergence: Price making new low with OI falling -> Bullish divergence")
                score += 3
            elif price_new_high and oi_rising:
                reasons.append(f"OI-Price confirmation: Price making new high with OI rising -> Bullish confirmation")
                score += 2
        except Exception:
            pass  # Divergence detection failure doesn't affect main flow

    # 3. Funding rate confirmation
    funding_status = funding_data.get("status", "UNKNOWN")
    if direction == "LONG":
        if funding_status in ("NORMAL", "NEGATIVE"):
            reasons.append(f"Funding rate {funding_status}, good environment for long")
            score += 2
        elif funding_status == "ELEVATED":
            reasons.append("Funding rate elevated, caution needed for long")
            score += 0
        else:
            reasons.append("Funding rate too high, high risk for long")
            score -= 2
    elif direction == "SHORT":
        if funding_status in ("HIGH", "EXTREME"):
            reasons.append(f"Funding rate {funding_status}, short has funding advantage")
            score += 2
        elif funding_status == "ELEVATED":
            reasons.append("Funding rate elevated, short has some advantage")
            score += 1
        elif funding_status == "NEGATIVE":
            reasons.append("Funding rate negative, high cost for short")
            score -= 1
        else:
            reasons.append("Funding rate normal, no funding advantage for short")
            score += 0

    confirmed = score >= 4

    return {
        "confirmed": confirmed,
        "reasons": reasons,
        "score": score
    }
