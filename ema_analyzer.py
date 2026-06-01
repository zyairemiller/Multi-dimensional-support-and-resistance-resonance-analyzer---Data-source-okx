"""
EMA Trend Analysis Module - Determine trend direction based on EMA144/169
"""

import pandas as pd
import numpy as np
from typing import Literal

TrendType = Literal["GOLDEN_CROSS", "DEATH_CROSS", "ENTANGLED"]
TrendStrength = Literal["early", "mid", "overheated"]


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """
    Calculate Exponential Moving Average (EMA)

    Uses standard EMA formula:
    EMA(t) = price(t) * k + EMA(t-1) * (1-k)
    k = 2 / (period + 1)

    Args:
        series: Price series
        period: EMA period

    Returns:
        EMA series
    """
    k = 2.0 / (period + 1)
    ema = series.copy().astype(float)

    # Initialize first valid value with SMA
    first_valid = series.first_valid_index()
    if first_valid is None:
        return ema

    # Use mean of first `period` values as initial EMA
    if len(series) >= period:
        ema.iloc[:period] = np.nan
        ema.iloc[period - 1] = series.iloc[:period].mean()

        for i in range(period, len(series)):
            ema.iloc[i] = series.iloc[i] * k + ema.iloc[i - 1] * (1 - k)
    else:
        # Insufficient data, use simple average
        ema.iloc[-1] = series.mean()

    return ema


def analyze_trend(df_1d: pd.DataFrame) -> dict:
    """
    Analyze EMA trend on the 1D timeframe

    Calculates EMA144 and EMA169, determines trend state and strength

    Args:
        df_1d: 1D candlestick data, must contain 'close' column

    Returns:
        dict: {
            trend: GOLDEN_CROSS / DEATH_CROSS / ENTANGLED,
            ema144: Latest EMA144 value,
            ema169: Latest EMA169 value,
            separation_pct: Separation percentage,
            trend_strength: early / mid / overheated,
            ema144_series: Complete EMA144 series,
            ema169_series: Complete EMA169 series
        }
    """
    close = df_1d["close"]

    ema144_series = calc_ema(close, 144)
    ema169_series = calc_ema(close, 169)

    # Get latest valid values
    ema144_latest = ema144_series.dropna().iloc[-1] if len(ema144_series.dropna()) > 0 else close.iloc[-1]
    ema169_latest = ema169_series.dropna().iloc[-1] if len(ema169_series.dropna()) > 0 else close.iloc[-1]

    current_close = close.iloc[-1]

    # Calculate separation
    separation_pct = (ema144_latest - ema169_latest) / current_close * 100

    # Determine trend state
    diff = ema144_latest - ema169_latest
    abs_sep = abs(separation_pct)

    if abs_sep < 0.3:
        # Very small separation, determined as entangled
        trend = "ENTANGLED"
    elif diff > 0:
        trend = "GOLDEN_CROSS"
    else:
        trend = "DEATH_CROSS"

    # Determine trend strength
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
