from fastapi import FastAPI
from src.config import settings
from src.api.routes import router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Intent Hunter CDP REST API for platform metrics and B2B lead management."
)

app.include_router(router, prefix="/api")
