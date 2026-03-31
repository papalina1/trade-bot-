"""
Fair Value Gap (FVG) detection logic.

A FVG is a 3-candle pattern where candle 2 moves so strongly that it leaves
a price imbalance (gap) between candle 1 and candle 3.

Bullish FVG : candle1.high < candle3.low  — gap above candle 1, below candle 3
Bearish FVG : candle1.low  > candle3.high — gap below candle 1, above candle 3

Quality filters applied:
  1. Minimum gap size  — gap must be >= MIN_GAP_PCT % of price (no micro-gaps)
  2. Unfilled check    — no subsequent candle has fully closed through the zone
  3. Recency           — only FVGs formed within the last LOOKBACK candles count
"""

from dataclasses import dataclass
import pandas as pd

# Minimum gap as a fraction of price (0.05% = 5 pips on EUR, ~$1.55 on Gold, ~$41 on BTC)
MIN_GAP_PCT: float = 0.0005   # 0.05%

# How many recent candles to scan for FVGs
LOOKBACK: int = 20


@dataclass
class FVG:
    type: str          # 'bullish' | 'bearish'
    low: float         # bottom of the imbalance zone
    high: float        # top of the imbalance zone
    gap_size: float    # absolute size of the gap
    candle_index: int  # position of the middle candle in the full dataframe

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2

    def __repr__(self) -> str:
        return (
            f"FVG({self.type} zone={self.low:.5f}-{self.high:.5f} "
            f"gap={self.gap_size:.5f})"
        )


def _gap_is_significant(gap_size: float, reference_price: float) -> bool:
    """Gap must be at least MIN_GAP_PCT of the current price."""
    return gap_size >= reference_price * MIN_GAP_PCT


def _fvg_is_unfilled(df: pd.DataFrame, fvg: FVG) -> bool:
    """
    A bullish FVG is filled (invalidated) when a candle's LOW closes below
    the bottom of the gap (fvg.low).
    A bearish FVG is filled when a candle's HIGH closes above fvg.high.

    We check every candle that formed AFTER the FVG (c3 onwards).
    """
    # candle_index is the middle candle; c3 is at candle_index + 1
    after = df.iloc[fvg.candle_index + 2:]

    for _, c in after.iterrows():
        if fvg.type == "bullish" and c["low"] < fvg.low:
            return False   # gap has been filled — invalid
        if fvg.type == "bearish" and c["high"] > fvg.high:
            return False   # gap has been filled — invalid

    return True


def detect_fvg(df: pd.DataFrame) -> list[FVG]:
    """
    Detect all valid, unfilled FVGs in the last LOOKBACK candles of df.

    Args:
        df: Full OHLC dataframe (columns: open, high, low, close).
            Must contain at least 3 rows.

    Returns:
        List of valid FVG objects ordered oldest → newest.
    """
    fvgs: list[FVG] = []

    if len(df) < 3:
        return fvgs

    # Only inspect the most recent LOOKBACK candles for new formations,
    # but pass the full df so we can check subsequent candles for fill status.
    scan_start = max(1, len(df) - LOOKBACK - 1)

    for i in range(scan_start, len(df) - 1):
        c1 = df.iloc[i - 1]
        c3 = df.iloc[i + 1]
        ref_price = float(c3["close"])

        # --- Bullish FVG ---
        if c1["high"] < c3["low"]:
            gap = float(c3["low"]) - float(c1["high"])
            if not _gap_is_significant(gap, ref_price):
                continue
            fvg = FVG(
                type="bullish",
                low=float(c1["high"]),
                high=float(c3["low"]),
                gap_size=gap,
                candle_index=i,
            )
            if _fvg_is_unfilled(df, fvg):
                fvgs.append(fvg)

        # --- Bearish FVG ---
        elif c1["low"] > c3["high"]:
            gap = float(c1["low"]) - float(c3["high"])
            if not _gap_is_significant(gap, ref_price):
                continue
            fvg = FVG(
                type="bearish",
                low=float(c3["high"]),
                high=float(c1["low"]),
                gap_size=gap,
                candle_index=i,
            )
            if _fvg_is_unfilled(df, fvg):
                fvgs.append(fvg)

    return fvgs


def price_in_fvg(price: float, fvg: FVG) -> bool:
    """
    Entry condition: current price has returned (retested) into the FVG zone.
    """
    return fvg.low <= price <= fvg.high
