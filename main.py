"""
FVG Trading Assistant — FastAPI entry point.

Start the server:
    uvicorn main:app --reload --port 8000

The APScheduler background job runs the scanner every N seconds
(configured via scanner_interval_seconds in config.json).
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scanner import load_config, run_scan
from telegram_bot import TelegramBot
from utils.trade_log import get_all_trades, get_stats, update_result

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

scheduler = BackgroundScheduler(timezone="UTC")
_bot: TelegramBot | None = None


def _get_bot() -> TelegramBot:
    if _bot is None:
        raise RuntimeError("Bot not initialised")
    return _bot


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot

    config = load_config()
    tg = config.get("telegram", {})

    _bot = TelegramBot(
        bot_token=tg.get("bot_token", ""),
        chat_id=tg.get("chat_id", ""),
    )

    interval: int = int(config.get("scanner_interval_seconds", 15))

    scheduler.add_job(
        run_scan,
        trigger="interval",
        seconds=interval,
        args=[_bot],
        id="fvg_scanner",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started — scanning every {interval}s")

    # Run once immediately so you see output right away
    logger.info("Running initial scan on startup…")
    run_scan(_bot)

    yield  # application runs here

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="FVG Trading Assistant",
    description="Detects Fair Value Gap setups and sends Telegram alerts",
    version="1.0.0",
    lifespan=lifespan,
)

_static = Path("static")
if _static.is_dir():
    app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
def root():
    return {"status": "running", "message": "FVG Trading Assistant is active"}


@app.get("/dashboard", tags=["Dashboard"], response_class=FileResponse)
def dashboard():
    """Serve the trading dashboard UI."""
    return FileResponse("static/dashboard.html")


@app.get("/stats", tags=["Dashboard"])
def stats():
    """
    Returns alert counts and win-rate based on resolved trades.

    Win-rate is 0 until you mark trades as win/loss via PATCH /trades/{id}.
    """
    return get_stats()


@app.get("/trades", tags=["Dashboard"])
def trades():
    """Return every logged trade alert."""
    return get_all_trades()


@app.get("/config", tags=["Dashboard"])
def config_view():
    """Return current config (bot token redacted)."""
    with open("config.json") as f:
        cfg = json.load(f)
    if "telegram" in cfg:
        cfg["telegram"]["bot_token"] = "***"
    return cfg


@app.post("/scan", tags=["Control"])
def manual_scan():
    """Trigger an immediate scan outside the scheduler."""
    alerts = run_scan(_get_bot())
    return {"alerts_sent": len(alerts), "alerts": alerts}


class ResultUpdate(BaseModel):
    result: str  # "win" | "loss"


@app.patch("/trades/{trade_id}", tags=["Dashboard"])
def update_trade_result(trade_id: int, body: ResultUpdate):
    """
    Mark a trade as win or loss so win-rate updates in /stats.

    Example body:  { "result": "win" }
    """
    if body.result not in ("win", "loss"):
        raise HTTPException(status_code=422, detail="result must be 'win' or 'loss'")
    updated = update_result(trade_id, body.result)  # type: ignore[arg-type]
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return updated
