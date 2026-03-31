"""
Telegram Bot integration.

If bot_token is not configured (still the placeholder), messages are printed
to the console instead so the rest of the system works without any setup.
"""

import logging
import requests

logger = logging.getLogger(__name__)

_PLACEHOLDER = "YOUR_BOT_TOKEN_HERE"


class TelegramBot:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token.strip()
        self.chat_id = str(chat_id).strip()
        self._configured = bool(
            self.bot_token and self.bot_token != _PLACEHOLDER
        )

        if not self._configured:
            logger.warning(
                "Telegram bot_token not set — alerts will be printed to console only. "
                "Edit config.json to add your token."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_message(self, text: str) -> bool:
        if not self._configured:
            print(f"\n{'='*50}\n[TELEGRAM ALERT]\n{text}\n{'='*50}\n")
            return True
        return self._post(text)

    def send_alert(
        self,
        pair: str,
        timeframe: str,
        direction: str,
        score: int,
        session: str,
        trend: str,
        fvg_zone: tuple[float, float] | None = None,
    ) -> bool:
        emoji = "🟢" if direction.upper() == "BUY" else "🔴"
        zone_str = ""
        if fvg_zone:
            zone_str = f"\n📍 FVG Zone: {fvg_zone[0]:.5f} – {fvg_zone[1]:.5f}"

        text = (
            f"{emoji} <b>{pair} {timeframe} {direction.upper()} opportunity</b>\n"
            f"📊 Score: <b>{score}</b>\n"
            f"🕐 Session: {session}\n"
            f"📈 Trend: {trend.capitalize()}"
            f"{zone_str}\n"
            f"✅ FVG retest confirmed"
        )
        return self.send_message(text)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _post(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Telegram message sent successfully")
            return True
        except requests.RequestException as exc:
            logger.error(f"Failed to send Telegram message: {exc}")
            return False
