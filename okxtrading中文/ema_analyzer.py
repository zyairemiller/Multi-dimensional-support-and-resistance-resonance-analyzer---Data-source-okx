"""
EMA趋势分析模块 - 基于EMA144/169判断趋势方向
"""

import pandas as pd
import numpy as np
from typing import Literal

TrendType = Literal["GOLDEN_CROSS", "DEATH_CROSS", "ENTANGLED"]
TrendStrength = Literal["early", "mid", "overheated"]


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """
    计算指数移动平均线 (EMA)
    
    使用标准EMA公式：
    EMA(t) = price(t) * k + EMA(t-1) * (1-k)
    k = 2 / (period + 1)
    
    Args:
        series: 价格序列
        period: EMA周期
        
    Returns:
        EMA序列
    """
    k = 2.0 / (period + 1)
    ema = series.copy().astype(float)
    
    # 第一个有效值用SMA初始化
    first_valid = series.first_valid_index()
    if first_valid is None:
        return ema
    
    # 用前period个值的均值作为初始EMA
    if len(series) >= period:
        ema.iloc[:period] = np.nan
        ema.iloc[period - 1] = series.iloc[:period].mean()
        
        for i in range(period, len(series)):
            ema.iloc[i] = series.iloc[i] * k + ema.iloc[i - 1] * (1 - k)
    else:
        # 数据不足，用简单平均
        ema.iloc[-1] = series.mean()
    
    return ema


def analyze_trend(df_1d: pd.DataFrame) -> dict:
    """
    分析1D级别的EMA趋势
    
    计算EMA144和EMA169，判断趋势状态和强度
    
    Args:
        df_1d: 1D K线数据，必须包含 close 列
        
    Returns:
        dict: {
            trend: GOLDEN_CROSS / DEATH_CROSS / ENTANGLED,
            ema144: 最新EMA144值,
            ema169: 最新EMA169值,
            separation_pct: 分离度百分比,
            trend_strength: early / mid / overheated,
            ema144_series: 完整EMA144序列,
            ema169_series: 完整EMA169序列
        }
    """
    close = df_1d["close"]
    
    ema144_series = calc_ema(close, 144)
    ema169_series = calc_ema(close, 169)
    
    # 取最新有效值
    ema144_latest = ema144_series.dropna().iloc[-1] if len(ema144_series.dropna()) > 0 else close.iloc[-1]
    ema169_latest = ema169_series.dropna().iloc[-1] if len(ema169_series.dropna()) > 0 else close.iloc[-1]
    
    current_close = close.iloc[-1]
    
    # 计算分离度
    separation_pct = (ema144_latest - ema169_latest) / current_close * 100
    
    # 判断趋势状态
    diff = ema144_latest - ema169_latest
    abs_sep = abs(separation_pct)
    
    if abs_sep < 0.3:
        # 分离度极小，判定为缠绕
        trend = "ENTANGLED"
    elif diff > 0:
        trend = "GOLDEN_CROSS"
    else:
        trend = "DEATH_CROSS"
    
    # 判断趋势强度
    if trend == "ENTANGLED":
        trend_strength = "early"
    elif abs_sep < 1.0:
        trend_strength = "early"
    elif abs_sep < 4.0:
        trend_strength = "mid"
    else:
        trend_strength = "overheated"
    
    return {
        "trend": trend,
        "ema144": float(ema144_latest),
        "ema169": float(ema169_latest),
        "separation_pct": round(float(separation_pct), 3),
        "trend_strength": trend_strength,
        "ema144_series": ema144_series,
        "ema169_series": ema169_series
    }
