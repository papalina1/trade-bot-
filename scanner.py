"""
Main scanning engine.

Loops through every (pair, timeframe) in the watchlist, runs the full
analysis pipeline, and fires a Telegram alert when a high-score setup
is detected.

Anti-spam: each (pair, timeframe) has a per-process cooldown tracked in
_last_alerts.  Alerts within the cooldown window are silently skipped.
"""

import json
import logging
from datetime import datetime, timedelta

from data.mock_data import get_ohlc
from fvg import detect_fvg, price_in_fvg
from indicators import get_trend
from telegram_bot import TelegramBot
from utils.scoring import calculate_score
from utils.session import get_active_sessions, is_session_active, primary_session
from utils.trade_log import add_trade, get_all_trades, update_result

logger = logging.getLogger(__name__)

# In-memory cooldown store  { (pair, timeframe): datetime_of_last_alert }
_last_alerts: dict[tuple[str, str], datetime] = {}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open("config.json") as f:
        return json.load(f)


def _cooldown_minutes(config: dict) -> int:
    return int(config.get("alert_cooldown_minutes", 5))


def _min_score(config: dict) -> int:
    return int(config.get("min_score", 80))


def _rr(config: dict) -> float:
    return float(config.get("risk_reward_ratio", 2.0))


# ---------------------------------------------------------------------------
# TP / SL calculation
# ---------------------------------------------------------------------------

def _calculate_tp_sl(
    direction: str,
    entry: float,
    fvg_low: float,
    fvg_high: float,
    rr: float,
) -> tuple[float, float]:
    """
    Returns (tp, sl).

    BUY  → SL below the FVG zone, TP at entry + risk * RR
    SELL → SL above the FVG zone, TP at entry - risk * RR
    """
    if direction == "BUY":
        sl = fvg_low
        risk = max(entry - sl, fvg_high - fvg_low)   # at least the gap size
        tp = entry + risk * rr
    else:
        sl = fvg_high
        risk = max(sl - entry, fvg_high - fvg_low)
        tp = entry - risk * rr
    return round(tp, 6), round(sl, 6)


# ---------------------------------------------------------------------------
# Auto-resolve pending trades
# ---------------------------------------------------------------------------

def _auto_resolve(pair: str, timeframe: str, df) -> None:
    """
    For every pending trade on this pair/timeframe that has a TP and SL,
    scan subsequent candles to see if price hit TP or SL first.
    """
    pending = [
        t for t in get_all_trades()
        if t["result"] == "pending"
        and t["pair"] == pair
        and t["timeframe"] == timeframe
        and t.get("tp") is not None
        and t.get("sl") is not None
    ]
    if not pending:
        return

    for trade in pending:
        try:
            trade_time = datetime.fromisoformat(trade["timestamp"])
            tp = trade["tp"]
            sl = trade["sl"]
            direction = trade["direction"]

            after = df[df.index > trade_time]
            if after.empty:
                continue

            for _, candle in after.iterrows():
                if direction == "BUY":
                    if float(candle["low"]) <= sl:
                        update_result(trade["id"], "loss")
                        logger.info(f"Auto-resolved trade {trade['id']} {pair}/{timeframe} → LOSS (SL hit)")
                        break
                    if float(candle["high"]) >= tp:
                        update_result(trade["id"], "win")
                        logger.info(f"Auto-resolved trade {trade['id']} {pair}/{timeframe} → WIN (TP hit)")
                        break
                else:
                    if float(candle["high"]) >= sl:
                        update_result(trade["id"], "loss")
                        logger.info(f"Auto-resolved trade {trade['id']} {pair}/{timeframe} → LOSS (SL hit)")
                        break
                    if float(candle["low"]) <= tp:
                        update_result(trade["id"], "win")
                        logger.info(f"Auto-resolved trade {trade['id']} {pair}/{timeframe} → WIN (TP hit)")
                        break
        except Exception:
            logger.exception(f"Auto-resolve failed for trade {trade['id']}")


# ---------------------------------------------------------------------------
# Cooldown helpers
# ---------------------------------------------------------------------------

def _on_cooldown(pair: str, timeframe: str, cooldown_min: int) -> bool:
    key = (pair, timeframe)
    last = _last_alerts.get(key)
    if last is None:
        return False
    return datetime.utcnow() - last < timedelta(minutes=cooldown_min)


def _set_cooldown(pair: str, timeframe: str) -> None:
    _last_alerts[(pair, timeframe)] = datetime.utcnow()


# ---------------------------------------------------------------------------
# Single-pair analysis
# ---------------------------------------------------------------------------

def scan_pair(pair: str, timeframe: str, config: dict, bot: TelegramBot) -> dict | None:
    """
    Analyse one (pair, timeframe).

    Returns the alert dict if an alert was sent, otherwise None.
    """
    cooldown = _cooldown_minutes(config)
    min_score = _min_score(config)
    rr = _rr(config)

    # --- Cooldown check ---
    if _on_cooldown(pair, timeframe, cooldown):
        logger.debug(f"[{pair}/{timeframe}] Skipped — on cooldown")
        return None

    # --- Fetch data ---
    df = get_ohlc(pair, timeframe, n_candles=300)

    if len(df) < 210:
        logger.warning(f"[{pair}/{timeframe}] Not enough candles ({len(df)})")
        return None

    current_price = float(df["close"].iloc[-1])

    # --- Auto-resolve pending trades using latest candles ---
    _auto_resolve(pair, timeframe, df)

    # --- Trend filter ---
    trend = get_trend(df)
    if trend == "neutral":
        logger.debug(f"[{pair}/{timeframe}] Neutral trend — skipped")
        return None

    # --- Session filter ---
    session_active = is_session_active()
    session_name = primary_session()

    # --- FVG detection ---
    # Pass the full df so detect_fvg can verify FVGs are still unfilled.
    # Recency window (LOOKBACK candles) is enforced inside detect_fvg itself.
    fvgs = detect_fvg(df)

    if not fvgs:
        logger.debug(f"[{pair}/{timeframe}] No FVGs in recent candles")
        return None

    # --- Evaluate each FVG (most recent first) ---
    for fvg in reversed(fvgs):

        # Direction must match trend
        trend_aligned = (
            (fvg.type == "bullish" and trend == "bullish")
            or (fvg.type == "bearish" and trend == "bearish")
        )

        # Entry condition: price has retested the FVG zone
        if not price_in_fvg(current_price, fvg):
            continue

        # Score
        breakdown = calculate_score(
            fvg_detected=True,
            trend_aligned=trend_aligned,
            session_active=session_active,
            liquidity_sweep=True,  # mocked as always True
        )

        if breakdown.total < min_score:
            logger.debug(
                f"[{pair}/{timeframe}] Score {breakdown.total} < {min_score} — skipped"
            )
            continue

        direction = "BUY" if fvg.type == "bullish" else "SELL"
        zone = (fvg.low, fvg.high)
        tp, sl = _calculate_tp_sl(direction, current_price, fvg.low, fvg.high, rr)

        logger.info(
            f"[{pair}/{timeframe}] ALERT {direction} | score={breakdown.total} "
            f"| session={session_name} | trend={trend} | zone={zone} "
            f"| entry={current_price:.5f} | TP={tp:.5f} | SL={sl:.5f}"
        )

        # --- Send Telegram alert ---
        bot.send_alert(
            pair=pair,
            timeframe=timeframe,
            direction=direction,
            score=breakdown.total,
            session=session_name,
            trend=trend,
            fvg_zone=zone,
            entry=current_price,
            tp=tp,
            sl=sl,
        )

        # --- Persist to trade log ---
        entry = add_trade(
            pair=pair,
            timeframe=timeframe,
            direction=direction,
            score=breakdown.total,
            session=session_name,
            fvg_zone=zone,
            entry_price=current_price,
            tp=tp,
            sl=sl,
        )

        # --- Set cooldown so we don't spam ---
        _set_cooldown(pair, timeframe)

        return entry  # one alert per pair/timeframe per scan

    logger.debug(f"[{pair}/{timeframe}] FVGs found but no valid entry at current price")
    return None


# ---------------------------------------------------------------------------
# Full watchlist scan
# ---------------------------------------------------------------------------

def run_scan(bot: TelegramBot | None = None) -> list[dict]:
    """
    Scan every pair/timeframe in the watchlist.

    Args:
        bot: TelegramBot instance.  If None, a fresh one is built from config.

    Returns:
        List of alert dicts that were sent during this scan.
    """
    config = load_config()

    if bot is None:
        tg = config.get("telegram", {})
        bot = TelegramBot(
            bot_token=tg.get("bot_token", ""),
            chat_id=tg.get("chat_id", ""),
        )

    watchlist: dict[str, list[str]] = config.get("watchlist", {})
    alerts_sent: list[dict] = []

    logger.info(f"--- Scan started at {datetime.utcnow().isoformat(timespec='seconds')} UTC ---")

    for pair, timeframes in watchlist.items():
        for timeframe in timeframes:
            try:
                result = scan_pair(pair, timeframe, config, bot)
                if result:
                    alerts_sent.append(result)
            except Exception:
                logger.exception(f"Unexpected error scanning {pair}/{timeframe}")

    logger.info(f"--- Scan complete | {len(alerts_sent)} alert(s) sent ---")
    return alerts_sent
