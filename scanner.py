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
from utils.trade_log import add_trade

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

        logger.info(
            f"[{pair}/{timeframe}] ALERT {direction} | score={breakdown.total} "
            f"| session={session_name} | trend={trend} | zone={zone} "
            f"| gap={fvg.gap_size:.5f} | price={current_price:.5f}"
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
        )

        # --- Persist to trade log ---
        entry = add_trade(
            pair=pair,
            timeframe=timeframe,
            direction=direction,
            score=breakdown.total,
            session=session_name,
            fvg_zone=zone,
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
