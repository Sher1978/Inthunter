import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.config import settings
from src.db.session import init_db
from src.ingestion.telegram import TelegramIngestor
from src.bot.alert_bot import init_bot, dp
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
    init_bot()
    bot_task = None
    from src.bot.alert_bot import bot, dp
    if bot and dp:
        logger.info("Starting Aiogram Bot polling task...")
        bot_task = asyncio.create_task(dp.start_polling(bot))

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
