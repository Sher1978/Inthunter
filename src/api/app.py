import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.config import settings
from src.db.session import init_db
from src.ingestion.telegram import TelegramIngestor
import src.bot.alert_bot as alert_bot
from src.api.routes import router

logger = logging.getLogger("intent_hunter.app")

ingestor: TelegramIngestor = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan manager to run DB init, Telegram Bot, and Userbot background worker."""
    global ingestor
    logger.info("Initializing Intent Hunter CDP Web & Bot Service...")
    
    # 1. DB Init
    await init_db()
    
    # 2. Init Bot & Dispatcher
    alert_bot.init_bot()
    bot_task = None
    if alert_bot.bot and alert_bot.dp:
        async def run_bot_polling():
            try:
                logger.info("Clearing old webhooks and starting Aiogram Bot polling task...")
                await alert_bot.bot.delete_webhook(drop_pending_updates=True)
                await alert_bot.dp.start_polling(alert_bot.bot, handle_signals=False)
            except Exception as e:
                logger.error(f"Error in Aiogram Bot polling loop: {e}", exc_info=True)

        bot_task = asyncio.create_task(run_bot_polling())

    # 3. Init Ingestion Engine
    ingestor = TelegramIngestor()
    await ingestor.setup()
    await ingestor.start()

    logger.info("✅ Intent Hunter CDP fully started on Render Web Service!")
    
    yield
    
    # Shutdown logic
    logger.info("Shutting down Intent Hunter CDP background tasks...")
    if ingestor:
        await ingestor.stop()
    if bot_task:
        bot_task.cancel()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Intent Hunter CDP REST API & Background Service",
    lifespan=lifespan
)

app.include_router(router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Intent Hunter CDP Active", "status": "running"}
