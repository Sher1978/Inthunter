import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

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
    
    # 1. DB Init & Auto-Seeding for Nha Trang channels
    await init_db()
    try:
        from seed_nhatrang_channels import seed_nhatrang
        asyncio.create_task(seed_nhatrang())
    except Exception as e:
        logger.warning(f"Startup seeding notice: {e}")
    
    # 2. Init Bot & Dispatcher
    alert_bot.init_bot()
    bot_task = None
    if alert_bot.bot and alert_bot.dp:
        bot_task = asyncio.create_task(alert_bot.run_polling_safe())
    else:
        logger.warning("Bot polling task SKIPPED: alert_bot.bot or alert_bot.dp is None.")

    # 3. Init Ingestion Engine in background task to allow instant HTTP healthcheck response
    ingestor = TelegramIngestor()
    async def start_ingestor_bg():
        try:
            await ingestor.setup()
            await ingestor.start()
            logger.info("✅ Telegram Ingestion Engine started successfully.")
        except Exception as e:
            logger.warning(f"Ingestion Engine background startup notice: {e}")

    ingestor_task = asyncio.create_task(start_ingestor_bg())

    logger.info("✅ Intent Hunter CDP fully started on Web Service!")
    
    yield
    
    # Shutdown logic
    logger.info("Shutting down Intent Hunter CDP background tasks...")
    if ingestor_task:
        ingestor_task.cancel()
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

# Mount Static Assets
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(router, prefix="/api")

@app.get("/health")
@app.get("/api/health")
async def root_health_check():
    return {"status": "ok", "service": "Intent Hunter CDP API"}

@app.get("/")
@app.get("/dashboard")
async def serve_dashboard():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Intent Hunter CDP Active", "status": "running"}

@app.get("/tma")
@app.get("/app")
async def serve_tma_landing():
    tma_path = os.path.join(static_dir, "tma.html")
    if os.path.exists(tma_path):
        return FileResponse(tma_path)
    return {"message": "Intent Hunter TMA Active", "status": "running"}
