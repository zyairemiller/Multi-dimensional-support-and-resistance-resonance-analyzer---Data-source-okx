"""
Liquidation Heatmap Calculation Module - Method 2: Estimate leverage distribution based on funding rate + OI

Core Algorithm:
1. Calculate VWAP for each period based on 1H candles as entry price estimate
2. Distribute OI to leverage ranges based on leverage distribution weights
3. Calculate long/short liquidation price for each leverage range
4. Aggregate liquidation notional value by price grid
5. Apply time decay
6. Output heatmap data

Reference: liquidation.txt
- Long liquidation price: P_liq = P_entry * (1 - 1/Leverage + MMR)
- Short liquidation price: P_liq = P_entry * (1 + 1/Leverage - MMR)
- Time decay: e^(-λt), λ=0.02 (half-life ~35 hours)
"""

import math
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ========== Leverage Distribution Weights (configurable constants) ==========
# Weight source: Based on OKX public market data and empirical estimation of liquidation distribution across major exchanges
# Reflects typical leverage preference distribution for different instrument traders:
#   BTC/ETH: High volatility + good depth, 20x-50x is the main range (70%), 10x conservative veteran positions (20%), 100x professional gambler (10%)
#   XAU/XAG: Commodity attributes + relatively weaker liquidity, 10x-20x is the main range (70%), 5x low-frequency conservative positions (20%), 50x aggressive positions (10%)
# Adjust based on market conditions (e.g., increase low-leverage weight during extreme volatility periods) by modifying the dictionaries below.
LEVERAGE_WEIGHTS: dict = {
    "BTC": {20: 0.40, 50: 0.30, 10: 0.20, 100: 0.10},
    "ETH": {20: 0.40, 50: 0.30, 10: 0.20, 100: 0.10},
    "XAU": {10: 0.40, 20: 0.30, 5: 0.20, 50: 0.10},
    "XAG": {10: 0.40, 20: 0.30, 5: 0.20, 50: 0.10},
    "BTC_SPOT": {},
    "ETH_SPOT": {},
}

# Instrument Maintenance Margin Rate (simplified fixed values)
# OKX actual MMR is tiered; using simplified values here
MMR_MAP = {
    "BTC": 0.004,
    "ETH": 0.005,
    "XAU": 0.006,
    "XAG": 0.006,
    "BTC_SPOT": 0,
    "ETH_SPOT": 0,
}

# Price grid precision (price step per grid)
GRID_STEP = {
    "BTC": 50,
    "ETH": 2,
    "XAU": 5,
    "XAG": 0.05,
    "BTC_SPOT": 50,
    "ETH_SPOT": 2,
}

# Time decay coefficient λ
DECAY_LAMBDA = 0.02  # Half-life ~35 hours


def compute_liquidation_heatmap(
    instrument: str,
    df_1h: pd.DataFrame,
    oi_current: dict,
    funding_data: dict,
) -> List[Dict]:
    """
    Calculate liquidation heatmap data

    Core Algorithm:
    1. Calculate VWAP for each period based on 1H candles as entry price estimate
    2. Distribute OI to leverage ranges based on leverage distribution weights
    3. Calculate long/short liquidation price for each leverage range
    4. Aggregate liquidation notional value by price grid
    5. Apply time decay
    6. Output heatmap data

    Args:
        instrument: Instrument name, e.g., BTC, ETH
        df_1h: 1H candlestick data
        oi_current: Current OI data {oi, oiCcy}
        funding_data: Funding rate data {fundingRate, ...}

    Returns:
        Heatmap data list: [{price, long_liq, short_liq, total_liq, rating}, ...]
    """
    # Spot instruments not supported
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
        logger.warning(f"Liquidation heatmap: {instrument} OI is 0, skipping")
        return []

    # Funding rate adjustment: high rate -> more high-leverage positions
    funding_rate = float(funding_data.get("fundingRate", 0) if funding_data else 0)
    leverage_multiplier = 1.0 + abs(funding_rate) * 50  # Higher rate -> more high-leverage weight

    # Adjust leverage weights
    adjusted_weights = {}
    for lev, w in leverage_weights.items():
        if lev >= 50:
            adjusted_weights[lev] = w * leverage_multiplier
        else:
            adjusted_weights[lev] = w
    # Normalize
    total_w = sum(adjusted_weights.values())
    for lev in adjusted_weights:
        adjusted_weights[lev] /= total_w

    # Use most recent 168 candles (7 days) of 1H data
    lookback = min(168, len(df_1h))
    recent_df = df_1h.iloc[-lookback:]

    now_ts = datetime.now(timezone.utc).timestamp()

    # Price grid accumulators
    grid_long = {}   # price_grid -> long_liquidation_value
    grid_short = {}   # price_grid -> short_liquidation_value

    # Calculate total volume for weighting
    total_volume = recent_df["vol"].sum()
    if total_volume <= 0:
        total_volume = 1.0

    for idx, row in recent_df.iterrows():
        # Calculate VWAP as entry price estimate
        row_high = float(row["high"]) if pd.notna(row["high"]) else current_price
        row_low = float(row["low"]) if pd.notna(row["low"]) else current_price
        row_close = float(row["close"]) if pd.notna(row["close"]) else current_price
        row_open = float(row["open"]) if pd.notna(row["open"]) else current_price
        volume = float(row.get("vol", 0) or 0)

        vwap = (row_high + row_low + row_close) / 3.0

        # Time decay
        candle_ts = row["ts"]
        if hasattr(candle_ts, "timestamp"):
            hours_ago = (now_ts - candle_ts.value / 1_000_000_000) / 3600
        else:
            try:
                hours_ago = (now_ts - pd.Timestamp(candle_ts).timestamp()) / 3600
            except Exception:
                hours_ago = 24  # fallback
        decay = math.exp(-DECAY_LAMBDA * max(0, hours_ago))

        # Calculate OI share for this period
        # Each candle's OI share ≈ Total OI × (this period's volume ratio) × time decay
        # Note: decay is only used once here, don't multiply again later
        oi_share = total_oi * (volume / total_volume) * decay

        # Determine long/short ratio (based on candle direction + funding rate adjustment)
        candle_body = row_close - row_open
        base_long_ratio = 0.55 if candle_body >= 0 else 0.45
        # Funding rate adjustment: positive rate -> more long positions, negative rate -> more short positions
        fr = float(funding_rate) if funding_rate else 0
        long_ratio = max(0.2, min(0.8, base_long_ratio + fr * 10))
        short_ratio = 1.0 - long_ratio

        # Calculate liquidation price for each leverage range
        for leverage, weight in adjusted_weights.items():
            position_value = oi_share * weight

            # Long liquidation price: P_liq = P_entry * (1 - 1/Leverage + MMR)
            long_liq_price = vwap * (1 - 1 / leverage + mmr)
            # Short liquidation price: P_liq = P_entry * (1 + 1/Leverage - MMR)
            short_liq_price = vwap * (1 + 1 / leverage - mmr)

            # Map to price grid
            long_grid = round(long_liq_price / grid_step) * grid_step
            short_grid = round(short_liq_price / grid_step) * grid_step

            long_value = position_value * long_ratio
            short_value = position_value * short_ratio

            grid_long[long_grid] = grid_long.get(long_grid, 0) + long_value
            grid_short[short_grid] = grid_short.get(short_grid, 0) + short_value

    # Aggregate results
    all_grids = set(grid_long.keys()) | set(grid_short.keys())

    if not all_grids:
        logger.info(f"Liquidation heatmap: {instrument} no valid grid data")
        return []

    # Calculate statistics for rating
    all_totals = [grid_long.get(g, 0) + grid_short.get(g, 0) for g in all_grids]
    avg_liq = float(np.mean(all_totals)) if all_totals else 1

    heatmap_data = []
    price_range_pct = 0.10  # Only keep current price ±10% range
    for grid_price in sorted(all_grids):
        if abs(grid_price - current_price) / current_price > price_range_pct:
            continue

        long_liq = grid_long.get(grid_price, 0)
        short_liq = grid_short.get(grid_price, 0)
        total_liq = long_liq + short_liq

        # Rating (simplified version of the 5-star system from liquidation.txt)
        ratio = total_liq / avg_liq if avg_liq > 0 else 0
        if ratio >= 5:
            rating = 5  # Magnet level
        elif ratio >= 3:
            rating = 4  # Strong liquidation zone
        elif ratio >= 2:
            rating = 3  # Medium
        elif ratio >= 1.5:
            rating = 2  # Weak
        else:
            rating = 1  # Very weak

        heatmap_data.append({
            "price": round(grid_price, 2),
            "long_liq": round(long_liq, 4),
            "short_liq": round(short_liq, 4),
            "total_liq": round(total_liq, 4),
            "rating": rating,
        })

    logger.info(f"Liquidation heatmap calculation complete: {instrument} total {len(heatmap_data)} price grids")
    return heatmap_data
