import asyncio
import logging
import sys

# Ensure UTF-8 stream output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.config import settings
from src.db.session import init_db
from src.ingestion.telegram import TelegramIngestor
import src.bot.alert_bot as alert_bot


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("intent_hunter.main")

async def main():
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION}...")
    
    # 1. Initialize Database Schema
    logger.info("Initializing database schema...")
    await init_db()
    logger.info("Database schema ready.")

    # 2. Initialize Telegram Bot
    alert_bot.init_bot()
    bot_task = None
    if alert_bot.bot and alert_bot.dp:
        bot_task = asyncio.create_task(alert_bot.run_polling_safe())

    # 3. Setup and Start Ingestion Listener
    ingestor = TelegramIngestor()
    await ingestor.setup()
    await ingestor.start()

    logger.info("⚡ Intent Hunter CDP Phase 1 (Pilot) running successfully.")
    
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION} on port {port}...")
    uvicorn.run("src.api.app:app", host="0.0.0.0", port=port)
