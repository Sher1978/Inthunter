from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from src.config import settings
from src.db.models import Base

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    db_url,
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
    """Initializes the database schema automatically and performs schema migrations."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Safe column migrations for SQLite (separate transaction for each to prevent transaction aborts)
    migrations = [
        "ALTER TABLE partners ADD COLUMN niche_priorities JSON DEFAULT '{}'",
        "ALTER TABLE partners ADD COLUMN is_monitoring_active BOOLEAN DEFAULT 1",
        "ALTER TABLE partners ADD COLUMN balance NUMERIC(10,2) DEFAULT 0.00",
        "ALTER TABLE partners ADD COLUMN role VARCHAR(50) DEFAULT 'DEMO'",
        "ALTER TABLE partners ADD COLUMN moderation_status VARCHAR(50) DEFAULT 'PENDING'",
        "ALTER TABLE partners ADD COLUMN webhook_url VARCHAR(500)",
        "ALTER TABLE partners ADD COLUMN onboarding_step INTEGER DEFAULT 0",
        "ALTER TABLE partners ADD COLUMN last_nudge_at DATETIME",
        "ALTER TABLE partners ADD COLUMN referred_by_id VARCHAR(36)",
        "ALTER TABLE partners ADD COLUMN referral_code VARCHAR(50)",
        "ALTER TABLE partners ADD COLUMN referral_balance NUMERIC(10,2) DEFAULT 0.00",
        "ALTER TABLE partners ADD COLUMN total_referral_earned NUMERIC(10,2) DEFAULT 0.00",
        "ALTER TABLE monitored_channels ADD COLUMN last_scraped_msg_id BIGINT DEFAULT 0",
        "ALTER TABLE monitored_channels ADD COLUMN chat_type VARCHAR(50) DEFAULT 'channel'",
        "ALTER TABLE monitored_channels ADD COLUMN location_code VARCHAR(100) DEFAULT 'nhatrang'",
        "ALTER TABLE leads ADD COLUMN location_code VARCHAR(100) DEFAULT 'global'"
    ]

    for stmt in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass

    # Seed initial public channels if table is empty
    from sqlalchemy import select
    from src.db.models import MonitoredChannel
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(MonitoredChannel))
        channels = list(res.scalars().all())
        if not channels:
            seed_channels = [
                MonitoredChannel(username_or_link="@nhatrang_chat", title="Чат Нячанга | Вьетнам Общение", niche_code="community", location_code="nhatrang", status="JOINED"),
                MonitoredChannel(username_or_link="@nhatrang_realty", title="Аренда Недвижимости Нячанг", niche_code="real_estate", location_code="nhatrang", status="JOINED")
            ]
            session.add_all(seed_channels)
            await session.commit()

    # Seed 4 target real leads if marketplace table is empty
    from src.db.models import Lead, UserProfile
    async with AsyncSessionLocal() as session:
        lead_check = (await session.execute(select(Lead))).scalars().first()
        if not lead_check:
            seed_leads = [
                {
                    "user_id": 771001,
                    "username": "visarun_nhatrang_user",
                    "first_name": "Визаран Клиент",
                    "niche_code": "services_visa",
                    "intent_summary": "Запрос бордеррана/визарана в Лаос из Нячанга на 2 человек с комфортными спальными местами и поддержкой визы",
                    "sales_hook": "Организуем визаран в Лаос на комфортабельном минивэне со спальными местами и сопровождением"
                },
                {
                    "user_id": 771002,
                    "username": "bike_rent_nhatrang",
                    "first_name": "Алексей Байк",
                    "niche_code": "bike_rent",
                    "intent_summary": "Аренда байка Honda NVX 155/PCX на 1 месяц в районе Северного пляжа + трансфер из аэропорта Камрань на завтра 14:00",
                    "sales_hook": "В наличии обслуженные Honda NVX 155 и PCX с доставкой на Северный пляж и встречей в Камрани"
                },
                {
                    "user_id": 771003,
                    "username": "usdt_exchanger_nhatrang",
                    "first_name": "Дмитрий Обмен",
                    "niche_code": "currency_exchange",
                    "intent_summary": "Срочный обмен $1500 USDT на наличные донги (VND) с курьерской доставкой в центр Нячанга",
                    "sales_hook": "Обменяем $1500 USDT по лучшему курсу в Нячанге с бесплатной доставкой наличных в центр"
                },
                {
                    "user_id": 771004,
                    "username": "muongthanh_renter",
                    "first_name": "Екатерина Недвижимость",
                    "niche_code": "real_estate",
                    "intent_summary": "Сниму 1-к квартиру или студию в Muong Thanh Grand на 3 месяца (вид на море, бюджет до 8 млн VND)",
                    "sales_hook": "Есть готовые варианты студий в Muong Thanh Grand с видом на море до 8 млн VND от проверенных владельцев"
                }
            ]
            for item in seed_leads:
                u = (await session.execute(select(UserProfile).where(UserProfile.user_id == item["user_id"]))).scalar_one_or_none()
                if not u:
                    u = UserProfile(user_id=item["user_id"], username=item["username"], first_name=item["first_name"])
                    session.add(u)
                    await session.flush()
                session.add(Lead(
                    user_id=item["user_id"],
                    niche_code=item["niche_code"],
                    temperature="HOT",
                    confidence_score=0.98,
                    intent_summary=item["intent_summary"],
                    sales_hook=item["sales_hook"],
                    status="AVAILABLE",
                    price=1.00
                ))
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
