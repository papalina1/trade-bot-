"""
Market data provider.

Default: real data via yfinance (free, no API key).
Fallback: mock OHLC generator if the network call fails.

To plug in a different source (MetaTrader, CCXT, Alpha Vantage…),
replace only the `fetch_real_ohlc` function — the rest of the system
uses get_ohlc() and never cares about the source.
"""

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol mapping  (your internal name → yfinance ticker)
# ---------------------------------------------------------------------------

SYMBOL_MAP: dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "XAUUSD": "GC=F",       # Gold futures
    "XAGUSD": "SI=F",       # Silver futures
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
}

# ---------------------------------------------------------------------------
# Timeframe mapping  (your label → yfinance interval + lookback period)
# ---------------------------------------------------------------------------
# yfinance limits:  1m  → max 7 days
#                   2m–90m → max 60 days
#                   1h+    → max 730 days

TF_MAP: dict[str, tuple[str, str]] = {
    "M1":  ("1m",  "5d"),
    "M2":  ("2m",  "30d"),
    "M5":  ("5m",  "30d"),
    "M15": ("15m", "30d"),
    "M30": ("30m", "30d"),
    "H1":  ("60m", "60d"),
    "H4":  ("1h",  "60d"),   # resampled below
    "D1":  ("1d",  "730d"),
}


# ---------------------------------------------------------------------------
# Real data fetcher
# ---------------------------------------------------------------------------

def fetch_real_ohlc(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Pull OHLC candles from Yahoo Finance.

    Returns a DataFrame indexed by UTC timestamp with columns:
        open, high, low, close
    """
    yf_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)
    interval, period = TF_MAP.get(timeframe.upper(), ("5m", "30d"))

    resample_h4 = timeframe.upper() == "H4"
    if resample_h4:
        interval, period = "1h", "60d"

    logger.debug(f"Fetching {yf_symbol} interval={interval} period={period}")

    ticker = yf.Ticker(yf_symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)

    if df.empty:
        raise ValueError(f"yfinance returned no data for {yf_symbol}")

    # Normalise column names
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close"]].dropna()

    # Resample 1h → 4h if needed
    if resample_h4:
        df = (
            df.resample("4h")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )

    df.index.name = "timestamp"
    # Strip timezone so the rest of the system stays tz-naive
    df.index = df.index.tz_localize(None) if df.index.tzinfo is None else df.index.tz_convert("UTC").tz_localize(None)

    return df


# ---------------------------------------------------------------------------
# Mock fallback (used only when yfinance fails)
# ---------------------------------------------------------------------------

_SYMBOL_DEFAULTS: dict[str, dict] = {
    "EURUSD": {"base": 1.0850, "vol": 0.0008},
    "XAUUSD": {"base": 2320.0, "vol": 2.50},
    "BTCUSD": {"base": 65_000.0, "vol": 300.0},
}
_DEFAULT_PARAMS = {"base": 1.0, "vol": 0.001}

_TF_MINUTES: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440,
}


def generate_mock_ohlc(symbol: str, timeframe: str, n_candles: int = 300) -> pd.DataFrame:
    params = _SYMBOL_DEFAULTS.get(symbol.upper(), _DEFAULT_PARAMS)
    base: float = params["base"]
    vol: float = params["vol"]
    minutes: int = _TF_MINUTES.get(timeframe.upper(), 5)

    rng = np.random.default_rng()
    returns = rng.normal(0, vol, n_candles)

    # Inject spike candles to create FVG patterns
    for pos in rng.integers(50, n_candles - 3, size=5):
        returns[pos] += rng.choice([-1, 1]) * vol * rng.uniform(3.0, 6.0)

    closes = base + np.cumsum(returns)
    closes = np.maximum(closes, base * 0.5)

    opens = np.empty(n_candles)
    opens[0] = base
    opens[1:] = closes[:-1]

    wicks = np.abs(rng.normal(0, vol * 0.6, n_candles))
    highs = np.maximum(opens, closes) + wicks
    lows = np.minimum(opens, closes) - wicks

    end_time = datetime.utcnow().replace(second=0, microsecond=0)
    timestamps = [
        end_time - timedelta(minutes=minutes * (n_candles - 1 - i))
        for i in range(n_candles)
    ]

    return pd.DataFrame(
        {"open": np.round(opens, 5), "high": np.round(highs, 5),
         "low": np.round(lows, 5), "close": np.round(closes, 5)},
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )


# ---------------------------------------------------------------------------
# Public interface — called by scanner.py
# ---------------------------------------------------------------------------

def get_ohlc(symbol: str, timeframe: str, n_candles: int = 300) -> pd.DataFrame:
    """
    Returns real OHLC data from yfinance.
    Falls back to mock data if the network call fails.
    """
    try:
        df = fetch_real_ohlc(symbol, timeframe)
        logger.info(f"[{symbol}/{timeframe}] Real data loaded — {len(df)} candles, "
                    f"latest close: {df['close'].iloc[-1]:.5f}")
        return df
    except Exception as exc:
        logger.warning(f"[{symbol}/{timeframe}] yfinance failed ({exc}) — using mock data")
        return generate_mock_ohlc(symbol, timeframe, n_candles)
