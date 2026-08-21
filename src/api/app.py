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
from src.api.tma_auth import tma_router

logger = logging.getLogger("intent_hunter.app")

ingestor: TelegramIngestor = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan manager to run DB init, Telegram Bot, and Userbot background worker asynchronously."""
    global ingestor
    logger.info("Initializing Intent Hunter CDP Web & Bot Service...")
    
    async def bg_init():
        try:
            await init_db()
            from seed_nhatrang_channels import seed_nhatrang
            from seed_dubai_channels import seed_dubai
            await seed_nhatrang()
            await seed_dubai()
        except Exception as e:
            logger.warning(f"Background init notice: {e}")

    asyncio.create_task(bg_init())
    
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

import traceback
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Intent Hunter CDP REST API & Background Service",
    lifespan=lifespan
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb_str = traceback.format_exc()
    logger.error(f"Unhandled API Exception on {request.method} {request.url.path}: {exc}\n{tb_str}")
    
    # Notify Superadmins via Telegram Bot asynchronously
    try:
        from src.bot.alert_bot import notify_superadmins_api_error
        asyncio.create_task(notify_superadmins_api_error(
            method=request.method,
            path=request.url.path,
            error_msg=str(exc) or type(exc).__name__,
            traceback_snippet=tb_str
        ))
    except Exception as notify_err:
        logger.error(f"Failed to send exception notification to superadmins: {notify_err}")

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc) or type(exc).__name__}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Assets
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(router, prefix="/api")
app.include_router(tma_router, prefix="/api/tma", tags=["TMA Marketplace"])

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

@app.get("/marketplace")
@app.get("/tma")
@app.get("/app")
async def serve_marketplace():
    """Serves the TMA marketplace page. Auto-login via Telegram WebApp initData."""
    mp_path = os.path.join(static_dir, "marketplace.html")
    if os.path.exists(mp_path):
        return FileResponse(mp_path)
    return {"message": "Marketplace coming soon", "status": "running"}

