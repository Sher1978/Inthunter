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
    """FastAPI lifespan manager ensuring instant HTTP port binding to prevent Railway 502 timeouts."""
    global ingestor
    logger.info("Initializing Intent Hunter CDP Web & Bot Service...")
    
    async def start_all_background_services():
        # Short sleep to allow uvicorn event loop to complete binding to $PORT first
        await asyncio.sleep(1)
        try:
            await init_db()
            from sqlalchemy import text
            from src.db.session import engine
            async with engine.begin() as conn:
                try:
                    await conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS reasoning TEXT;"))
                except Exception:
                    pass
            logger.info("✅ Database init & schema check completed.")
            
            # Run automated DB Guard enforcement pass on startup
            try:
                from src.services.db_guard import db_guard
                res_prune = await db_guard.run_enforcement_pass()
                logger.info(f"🧹 DB Guard startup pass complete: Initial {res_prune.get('initial_size_mb')} MB -> Final {res_prune.get('final_size_mb')} MB.")
            except Exception as prune_err:
                logger.warning(f"Disk volume auto-pruning notice: {prune_err}")
        except Exception as e:
            logger.warning(f"Background DB init notice: {e}")

        try:
            alert_bot.init_bot()
            if alert_bot.bot and alert_bot.dp:
                asyncio.create_task(alert_bot.run_polling_safe())
        except Exception as e:
            logger.warning(f"Bot startup notice: {e}")

        try:
            from src.bot.hr_bot import init_hr_bot, run_hr_polling_safe
            init_hr_bot()
            asyncio.create_task(run_hr_polling_safe())
        except Exception as e:
            logger.warning(f"HR Bot startup notice: {e}")

        try:
            from src.bot.vacancy_group_poster import run_group_poster_loop
            asyncio.create_task(run_group_poster_loop())
        except Exception as e:
            logger.warning(f"Vacancy group poster startup notice: {e}")

        try:
            from src.bot.hr_bot import run_vip_reminder_loop
            asyncio.create_task(run_vip_reminder_loop())
        except Exception as e:
            logger.warning(f"VIP reminder loop startup notice: {e}")

        try:
            global ingestor
            ingestor = TelegramIngestor()
            await ingestor.setup()
            await ingestor.start()
            logger.info("✅ Telegram Ingestion Engine started.")
        except Exception as e:
            logger.warning(f"Ingestion engine startup notice: {e}")

        try:
            from src.services.custom_chat_engine import run_custom_chats_billing_cycle
            from src.bot.alert_bot import bot
            from src.db.session import AsyncSessionLocal
            from src.ingestion.vendor_quality import refresh_dynamic_stopwords
            async def billing_loop():
                while True:
                    try:
                        async with AsyncSessionLocal() as session:
                            await run_custom_chats_billing_cycle(session, bot=bot)
                    except Exception as e:
                        logger.error(f"Billing loop notice: {e}")
                    
                    try:
                        async with AsyncSessionLocal() as session:
                            await refresh_dynamic_stopwords(session)
                    except Exception as e:
                        logger.error(f"Stopwords refresh notice: {e}")
                        
                    await asyncio.sleep(3600)  # every 1 hour (was 86400)
            asyncio.create_task(billing_loop())
        except Exception as e:
            logger.warning(f"Billing loop notice: {e}")

        try:
            from src.ingestion.multi_platform_poller import run_multi_platform_poller_loop
            asyncio.create_task(run_multi_platform_poller_loop(interval_seconds=180))
        except Exception as e:
            logger.warning(f"Multi-platform poller notice: {e}")

        try:
            from src.outreach.outreach_worker import outreach_worker_instance
            asyncio.create_task(outreach_worker_instance.run_loop())
        except Exception as e:
            logger.warning(f"Outreach worker notice: {e}")

        try:
            from src.discovery.chat_manager import run_discovery_background_loop
            asyncio.create_task(run_discovery_background_loop())
        except Exception as e:
            logger.warning(f"Discovery engine notice: {e}")

    bg_task = asyncio.create_task(start_all_background_services())
    logger.info("✅ Intent Hunter CDP HTTP Service online!")

    yield

    logger.info("Shutting down Intent Hunter CDP background tasks...")
    if bg_task:
        bg_task.cancel()
    if ingestor:
        await ingestor.stop()

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

from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        if request.url.path.startswith("/api"):
            return JSONResponse(status_code=404, content={"detail": exc.detail or "Not Found"})
        custom_404 = os.path.join(static_dir, "404.html")
        if os.path.exists(custom_404):
            return FileResponse(custom_404, status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

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
@app.get("/api/healthcheck")
async def root_health_check():
    import os
    from datetime import datetime, timezone
    from fastapi import status
    from fastapi.responses import JSONResponse
    from src.ingestion.telegram import get_last_message_time

    timeout_seconds = int(os.getenv("DEAD_MAN_TIMEOUT_SECONDS", "300"))
    last_msg_at = get_last_message_time()
    now = datetime.now(timezone.utc)
    seconds_since_last = (now - last_msg_at).total_seconds() if last_msg_at else 0.0
    is_stale = seconds_since_last > timeout_seconds

    scraped_count = getattr(ingestor, "scraped_count", 0) if ingestor else 0
    ub_info = ingestor.get_userbot_status() if (ingestor and hasattr(ingestor, "get_userbot_status")) else {
        "status": "NOT_CONFIGURED",
        "connected": False,
        "user_handle": None,
        "group_chats_302_count": 0
    }

    payload = {
        "status": "stale" if is_stale else "ok",
        "service": "Intent Hunter CDP / LeadRadar Listener",
        "last_message_time": last_msg_at.isoformat() if last_msg_at else None,
        "seconds_since_last_message": round(seconds_since_last, 1),
        "stale_threshold_seconds": timeout_seconds,
        "is_stale": is_stale,
        "scraped_count": scraped_count,
        "userbot": ub_info,
        "timestamp": now.isoformat()
    }

    http_code = status.HTTP_503_SERVICE_UNAVAILABLE if is_stale else status.HTTP_200_OK
    return JSONResponse(status_code=http_code, content=payload)


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/landing", methods=["GET", "HEAD"])
async def serve_landing():
    landing_path = os.path.join(static_dir, "landing.html")
    if os.path.exists(landing_path):
        return FileResponse(landing_path)
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Intent Hunter CDP Active", "status": "running"}

@app.api_route("/dashboard", methods=["GET", "HEAD"])
async def serve_dashboard():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Intent Hunter CDP Active", "status": "running"}

@app.api_route("/marketplace", methods=["GET", "HEAD"])
@app.api_route("/tma", methods=["GET", "HEAD"])
@app.api_route("/marketplace.html", methods=["GET", "HEAD"])
async def serve_marketplace():
    mkt_path = os.path.join(static_dir, "marketplace.html")
    if os.path.exists(mkt_path):
        return FileResponse(mkt_path)
    tma_path = os.path.join(static_dir, "tma.html")
    if os.path.exists(tma_path):
        return FileResponse(tma_path)
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Intent Hunter CDP Active", "status": "running"}

@app.api_route("/archive", methods=["GET", "HEAD"])
@app.api_route("/archive.html", methods=["GET", "HEAD"])
async def serve_archive():
    archive_path = os.path.join(static_dir, "archive.html")
    if os.path.exists(archive_path):
        return FileResponse(archive_path)
    return {"message": "Lead Archive Active", "status": "running"}

from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=500)

@app.get("/robots.txt", include_in_schema=False)
async def serve_robots():
    robots_path = os.path.join(static_dir, "robots.txt")
    if os.path.exists(robots_path):
        return FileResponse(robots_path, media_type="text/plain")
    return {"detail": "Not Found"}

@app.get("/sitemap.xml", include_in_schema=False)
async def serve_sitemap():
    sitemap_path = os.path.join(static_dir, "sitemap.xml")
    if os.path.exists(sitemap_path):
        return FileResponse(sitemap_path, media_type="application/xml")
    return {"detail": "Not Found"}

@app.get("/llms.txt", include_in_schema=False)
async def serve_llmstxt():
    llms_path = os.path.join(static_dir, "llms.txt")
    if os.path.exists(llms_path):
        return FileResponse(llms_path, media_type="text/plain; charset=utf-8")
    return {"detail": "Not Found"}

@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon():
    logo_path = os.path.join(static_dir, "images", "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/png")
    return {"detail": "Not Found"}


