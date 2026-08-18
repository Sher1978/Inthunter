import asyncio
import logging
import sys

# Ensure UTF-8 stream output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.config import settings
from src.db.session import init_db
from src.ingestion.telegram import TelegramIngestor
from src.bot.alert_bot import init_bot


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
    init_bot()
    from src.bot.alert_bot import bot, dp
    bot_task = None
    if bot and dp:
        logger.info("Clearing old webhooks and starting Aiogram Bot polling task...")
        await bot.delete_webhook(drop_pending_updates=True)
        bot_task = asyncio.create_task(dp.start_polling(bot))

    # 3. Setup and Start Ingestion Listener
    ingestor = TelegramIngestor()
    await ingestor.setup()
    await ingestor.start()

    logger.info("⚡ Intent Hunter CDP Phase 1 (Pilot) running successfully.")
    
    try:
        # Keep application running
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down Intent Hunter CDP...")
    finally:
        await ingestor.stop()

if __name__ == "__main__":
    asyncio.run(main())
