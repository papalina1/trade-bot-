"""
Technical indicators used by the trend filter.

Uses only pandas (no external TA library required) so installation is trivial.
Swap out the EMA calculations here if you want to add pandas-ta / TA-Lib later.
"""

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def get_trend(df: pd.DataFrame) -> str:
    """
    Determine current trend direction using EMA 50 and EMA 200.

    Rules:
        bullish  -> close > EMA50 AND close > EMA200
        bearish  -> close < EMA50 AND close < EMA200
        neutral  -> mixed / insufficient data

    Args:
        df: OHLC dataframe with at least 200 rows.

    Returns:
        'bullish' | 'bearish' | 'neutral'
    """
    if len(df) < 200:
        return "neutral"

    closes = df["close"]
    ema50 = ema(closes, 50).iloc[-1]
    ema200 = ema(closes, 200).iloc[-1]
    price = closes.iloc[-1]

    if price > ema50 and price > ema200:
        return "bullish"
    if price < ema50 and price < ema200:
        return "bearish"
    return "neutral"


def get_ema_values(df: pd.DataFrame) -> dict:
    """Return the latest EMA50 and EMA200 values (for debugging / API)."""
    closes = df["close"]
    return {
        "ema50": round(ema(closes, 50).iloc[-1], 5),
        "ema200": round(ema(closes, 200).iloc[-1], 5),
    }
