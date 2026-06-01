"""
Volume Profile 分析模块 - 成交量分布 & S/R共振检测
"""

import pandas as pd
import numpy as np
from typing import List, Dict


def calc_volume_profile(df_1h: pd.DataFrame, num_bins: int = 50) -> dict:
    """
    计算Volume Profile
    
    算法：
    1. 找出价格范围 (global_low ~ global_high)
    2. 均分为num_bins个价位
    3. 对每根K线，其成交量按价格范围均匀分配到对应bin中
    4. 统计每个bin的总成交量
    
    Args:
        df_1h: 1H K线数据，必须包含 open, high, low, close, vol 列
        num_bins: 价位数量，默认50
        
    Returns:
        dict: {
            "bins": [{price, volume}, ...],  # 每个价位中心的成交量
            "poc": float,                     # Point of Control - 成交量最大的价位
            "va_high": float,                 # Value Area 上沿（包含70%成交量的范围）
            "va_low": float,                  # Value Area 下沿
            "high_volume_nodes": [float, ...]  # 高成交量节点（成交量 > POC成交量的30%）
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
    
    # 1. 找出价格范围
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
    
    # 2. 均分为num_bins个价位
    bin_size = (global_high - global_low) / num_bins
    bin_volumes = [0.0] * num_bins
    bin_prices = [global_low + bin_size * (i + 0.5) for i in range(num_bins)]
    
    # 3. 对每根K线，其成交量按价格范围均匀分配到对应bin中
    for _, row in df_1h.iterrows():
        candle_low = float(row["low"])
        candle_high = float(row["high"])
        candle_vol = float(row["vol"]) if pd.notna(row["vol"]) else 0.0
        
        if candle_vol <= 0 or candle_high <= candle_low:
            continue
        
        # 找到该K线覆盖的bin范围
        vol_per_price = candle_vol / (candle_high - candle_low)
        
        for i in range(num_bins):
            bin_low = global_low + bin_size * i
            bin_high = bin_low + bin_size
            
            # 计算K线与bin的重叠部分
            overlap_low = max(candle_low, bin_low)
            overlap_high = min(candle_high, bin_high)
            
            if overlap_high > overlap_low:
                overlap_ratio = (overlap_high - overlap_low) / (candle_high - candle_low)
                bin_volumes[i] += candle_vol * overlap_ratio
    
    # 4. 构建bins列表
    bins = []
    for i in range(num_bins):
        bins.append({
            "price": round(bin_prices[i], 2),
            "volume": round(bin_volumes[i], 4)
        })
    
    # 5. 找POC（成交量最大的价位）
    max_vol_idx = int(np.argmax(bin_volumes))
    poc = bin_prices[max_vol_idx]
    poc_volume = bin_volumes[max_vol_idx]
    
    # 6. 计算Value Area（包含70%成交量的范围）
    total_volume = sum(bin_volumes)
    va_target = total_volume * 0.70
    
    # 从POC向两侧扩展，直到包含70%成交量
    va_low_idx = max_vol_idx
    va_high_idx = max_vol_idx
    va_volume = bin_volumes[max_vol_idx]
    
    while va_volume < va_target and (va_low_idx > 0 or va_high_idx < num_bins - 1):
        # 比较两侧，优先扩展成交量更大的一侧
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
    
    # 7. 找高成交量节点（成交量 > POC成交量的30%）
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
    检查S/R与Volume Profile的共振
    
    共振条件：S/R区间与Volume Profile的高成交量节点或POC重叠
    - POC在S/R区间内 → 强共振 ("strong")
    - 任何高成交量节点在S/R区间内 → 共振 ("normal")  
    - Value Area边界在S/R区间内 → 弱共振 ("weak")
    
    Args:
        sr_zones: S/R区间列表（含zone_low, zone_high）
        vp_result: calc_volume_profile的结果
        instrument: 品种名
        
    Returns:
        修改后的sr_zones，每个zone增加resonance字段:
        {
            ...原有字段,
            "resonance": None / "strong" / "normal" / "weak",
            "resonance_reason": ""  # 共振原因描述
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
        
        # 1. 检查POC是否在S/R区间内 → 强共振
        if poc > 0 and zone_low <= poc <= zone_high:
            resonance = "strong"
            resonance_reason = f"POC({poc:.2f})在区间内"
        # 2. 检查高成交量节点是否在S/R区间内 → 普通共振
        elif hvns:
            matching_hvns = [hvn for hvn in hvns if zone_low <= hvn <= zone_high]
            if matching_hvns:
                resonance = "normal"
                hvn_str = ", ".join([f"{h:.2f}" for h in matching_hvns[:3]])
                resonance_reason = f"高成交量节点({hvn_str})在区间内"
        # 3. 检查Value Area边界是否在S/R区间内 → 弱共振
        if resonance is None and va_high > 0 and va_low > 0:
            va_boundary_in_zone = False
            va_boundary_desc = ""
            if zone_low <= va_high <= zone_high:
                va_boundary_in_zone = True
                va_boundary_desc = f"VA上沿({va_high:.2f})"
            if zone_low <= va_low <= zone_high:
                va_boundary_in_zone = True
                va_boundary_desc = (va_boundary_desc + ", " if va_boundary_desc else "") + f"VA下沿({va_low:.2f})"
            if va_boundary_in_zone:
                resonance = "weak"
                resonance_reason = f"{va_boundary_desc}在区间内"
        
        zone["resonance"] = resonance
        zone["resonance_reason"] = resonance_reason
    
    return sr_zones
