"""
Persistent trade log stored in data/trade_log.json.

Each entry represents one alert that was sent.  The 'result' field starts as
'pending' and can be updated via the API when you know the outcome.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

LOG_PATH = Path("data/trade_log.json")

TradeResult = Literal["pending", "win", "loss"]


# ---------------------------------------------------------------------------
# Low-level I/O
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    try:
        with open(LOG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"Failed to read trade log: {exc}")
        return []


def _save(trades: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump(trades, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_trade(
    pair: str,
    timeframe: str,
    direction: str,
    score: int,
    session: str,
    fvg_zone: tuple[float, float] | None = None,
) -> dict:
    """Append a new trade alert to the log and return it."""
    trades = _load()
    entry = {
        "id": len(trades) + 1,
        "pair": pair,
        "timeframe": timeframe,
        "direction": direction,
        "score": score,
        "session": session,
        "fvg_zone": list(fvg_zone) if fvg_zone else None,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        "result": "pending",
    }
    trades.append(entry)
    _save(trades)
    logger.info(f"Trade logged: {pair} {timeframe} {direction} score={score}")
    return entry


def update_result(trade_id: int, result: TradeResult) -> dict | None:
    """Set the outcome of a logged trade.  Returns updated entry or None."""
    trades = _load()
    for trade in trades:
        if trade["id"] == trade_id:
            trade["result"] = result
            _save(trades)
            return trade
    return None


def get_all_trades() -> list[dict]:
    return _load()


def get_stats() -> dict:
    trades = _load()
    total = len(trades)
    resolved = [t for t in trades if t["result"] != "pending"]
    wins = [t for t in resolved if t["result"] == "win"]

    winrate = round(len(wins) / len(resolved) * 100, 1) if resolved else 0.0

    return {
        "number_of_trades": total,
        "pending": total - len(resolved),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(resolved) - len(wins),
        "winrate": winrate,
    }
