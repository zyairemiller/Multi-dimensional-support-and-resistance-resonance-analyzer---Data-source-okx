"""
Volume Profile Analysis Module - Volume Distribution & S/R Resonance Detection
"""

import pandas as pd
import numpy as np
from typing import List, Dict


def calc_volume_profile(df_1h: pd.DataFrame, num_bins: int = 50) -> dict:
    """
    Calculate Volume Profile

    Algorithm:
    1. Find price range (global_low ~ global_high)
    2. Divide into num_bins price levels
    3. For each candle, distribute its volume proportionally across the corresponding bins
    4. Aggregate total volume for each bin

    Args:
        df_1h: 1H candlestick data, must contain open, high, low, close, vol columns
        num_bins: Number of price levels, default 50

    Returns:
        dict: {
            "bins": [{price, volume}, ...],  # Volume at each price level center
            "poc": float,                     # Point of Control - price level with highest volume
            "va_high": float,                 # Value Area upper boundary (range containing 70% volume)
            "va_low": float,                  # Value Area lower boundary
            "high_volume_nodes": [float, ...]  # High volume nodes (volume > 30% of POC volume)
        }
    """
    if df_1h.empty or len(df_1h) < 2:
        return {
            "bins": [],
            "poc": 0.0,
            "va_high": 0.0,
            "va_low": 0.0,
            "high_volume_nodes": []
        }

    # 1. Find price range
    global_low = float(df_1h["low"].min())
    global_high = float(df_1h["high"].max())

    if global_high <= global_low:
        return {
            "bins": [],
            "poc": global_low,
            "va_high": global_high,
            "va_low": global_low,
            "high_volume_nodes": []
        }

    # 2. Divide into num_bins price levels
    bin_size = (global_high - global_low) / num_bins
    bin_volumes = [0.0] * num_bins
    bin_prices = [global_low + bin_size * (i + 0.5) for i in range(num_bins)]

    # 3. For each candle, distribute its volume proportionally across the corresponding bins
    for _, row in df_1h.iterrows():
        candle_low = float(row["low"])
        candle_high = float(row["high"])
        candle_vol = float(row["vol"]) if pd.notna(row["vol"]) else 0.0

        if candle_vol <= 0 or candle_high <= candle_low:
            continue

        # Find the bin range covered by this candle
        vol_per_price = candle_vol / (candle_high - candle_low)

        for i in range(num_bins):
            bin_low = global_low + bin_size * i
            bin_high = bin_low + bin_size

            # Calculate overlap between candle and bin
            overlap_low = max(candle_low, bin_low)
            overlap_high = min(candle_high, bin_high)

            if overlap_high > overlap_low:
                overlap_ratio = (overlap_high - overlap_low) / (candle_high - candle_low)
                bin_volumes[i] += candle_vol * overlap_ratio

    # 4. Build bins list
    bins = []
    for i in range(num_bins):
        bins.append({
            "price": round(bin_prices[i], 2),
            "volume": round(bin_volumes[i], 4)
        })

    # 5. Find POC (price level with highest volume)
    max_vol_idx = int(np.argmax(bin_volumes))
    poc = bin_prices[max_vol_idx]
    poc_volume = bin_volumes[max_vol_idx]

    # 6. Calculate Value Area (range containing 70% volume)
    total_volume = sum(bin_volumes)
    va_target = total_volume * 0.70

    # Expand outward from POC until 70% volume is included
    va_low_idx = max_vol_idx
    va_high_idx = max_vol_idx
    va_volume = bin_volumes[max_vol_idx]

    while va_volume < va_target and (va_low_idx > 0 or va_high_idx < num_bins - 1):
        # Compare both sides, prefer expanding toward the side with higher volume
        low_vol = bin_volumes[va_low_idx - 1] if va_low_idx > 0 else 0
        high_vol = bin_volumes[va_high_idx + 1] if va_high_idx < num_bins - 1 else 0

        if low_vol >= high_vol and va_low_idx > 0:
            va_low_idx -= 1
            va_volume += bin_volumes[va_low_idx]
        elif va_high_idx < num_bins - 1:
            va_high_idx += 1
            va_volume += bin_volumes[va_high_idx]
        elif va_low_idx > 0:
            va_low_idx -= 1
            va_volume += bin_volumes[va_low_idx]
        else:
            break

    va_low = bin_prices[va_low_idx] - bin_size / 2
    va_high = bin_prices[va_high_idx] + bin_size / 2

    # 7. Find high volume nodes (volume > 30% of POC volume)
    hvn_threshold = poc_volume * 0.30
    high_volume_nodes = []
    for i in range(num_bins):
        if bin_volumes[i] > hvn_threshold and i != max_vol_idx:
            high_volume_nodes.append(round(bin_prices[i], 2))

    return {
        "bins": bins,
        "poc": round(poc, 2),
        "va_high": round(va_high, 2),
        "va_low": round(va_low, 2),
        "high_volume_nodes": high_volume_nodes
    }


def check_sr_vp_resonance(sr_zones: list, vp_result: dict, instrument: str) -> list:
    """
    Check resonance between S/R and Volume Profile

    Resonance conditions: S/R zone overlaps with Volume Profile high volume nodes or POC
    - POC within S/R zone -> Strong resonance ("strong")
    - Any high volume node within S/R zone -> Resonance ("normal")
    - Value Area boundary within S/R zone -> Weak resonance ("weak")

    Args:
        sr_zones: List of S/R zones (containing zone_low, zone_high)
        vp_result: Result from calc_volume_profile
        instrument: Instrument name

    Returns:
        Modified sr_zones, each zone with added resonance field:
        {
            ...original fields,
            "resonance": None / "strong" / "normal" / "weak",
            "resonance_reason": ""  # Resonance reason description
        }
    """
    if not sr_zones or not vp_result or not vp_result.get("bins"):
        for zone in sr_zones:
            zone["resonance"] = None
            zone["resonance_reason"] = ""
        return sr_zones

    poc = vp_result.get("poc", 0)
    va_high = vp_result.get("va_high", 0)
    va_low = vp_result.get("va_low", 0)
    hvns = vp_result.get("high_volume_nodes", [])

    for zone in sr_zones:
        zone_low = zone.get("zone_low", 0)
        zone_high = zone.get("zone_high", 0)

        resonance = None
        resonance_reason = ""

        # 1. Check if POC is within S/R zone -> Strong resonance
        if poc > 0 and zone_low <= poc <= zone_high:
            resonance = "strong"
            resonance_reason = f"POC({poc:.2f}) is within the zone"
        # 2. Check if high volume nodes are within S/R zone -> Normal resonance
        elif hvns:
            matching_hvns = [hvn for hvn in hvns if zone_low <= hvn <= zone_high]
            if matching_hvns:
                resonance = "normal"
                hvn_str = ", ".join([f"{h:.2f}" for h in matching_hvns[:3]])
                resonance_reason = f"High volume nodes ({hvn_str}) are within the zone"
        # 3. Check if Value Area boundaries are within S/R zone -> Weak resonance
        if resonance is None and va_high > 0 and va_low > 0:
            va_boundary_in_zone = False
            va_boundary_desc = ""
            if zone_low <= va_high <= zone_high:
                va_boundary_in_zone = True
                va_boundary_desc = f"VA upper boundary ({va_high:.2f})"
            if zone_low <= va_low <= zone_high:
                va_boundary_in_zone = True
                va_boundary_desc = (va_boundary_desc + ", " if va_boundary_desc else "") + f"VA lower boundary ({va_low:.2f})"
            if va_boundary_in_zone:
                resonance = "weak"
                resonance_reason = f"{va_boundary_desc} is within the zone"

        zone["resonance"] = resonance
        zone["resonance_reason"] = resonance_reason

    return sr_zones
