from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from src.config import settings
from src.db.models import Base

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def init_db():
    """Initializes the database schema automatically and seeds initial public channels."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed initial public channels if table is empty
    from sqlalchemy import select
    from src.db.models import MonitoredChannel
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(MonitoredChannel))
        channels = list(res.scalars().all())
        if not channels:
            seed_channels = [
                MonitoredChannel(username_or_link="@telegram", title="Telegram News", niche_code="auto_kasko", status="PENDING"),
                MonitoredChannel(username_or_link="@durov", title="Pavel Durov Channel", niche_code="real_estate", status="PENDING")
            ]
            session.add_all(seed_channels)
            await session.commit()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency helper for database session retrieval."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
