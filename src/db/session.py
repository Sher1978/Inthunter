from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from src.config import settings
from src.db.models import Base

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

is_sqlite = "sqlite" in db_url.lower()

engine_kwargs = {
    "echo": False,
    "future": True,
}

if not is_sqlite:
    engine_kwargs.update({
        "pool_size": 25,
        "max_overflow": 50,
        "pool_timeout": 30.0,
        "pool_pre_ping": True,
        "pool_recycle": 1800
    })

engine = create_async_engine(db_url, **engine_kwargs)

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

    # Safe column migrations (separate transaction for each to prevent transaction aborts)
    migrations = [
        "ALTER TABLE partners ADD COLUMN niche_priorities JSON DEFAULT '{}'",
        "ALTER TABLE partners ADD COLUMN is_monitoring_active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE partners ADD COLUMN balance NUMERIC(10,2) DEFAULT 0.00",
        "ALTER TABLE partners ADD COLUMN role VARCHAR(50) DEFAULT 'DEMO'",
        "ALTER TABLE partners ADD COLUMN moderation_status VARCHAR(50) DEFAULT 'PENDING'",
        "ALTER TABLE partners ADD COLUMN webhook_url VARCHAR(500)",
        "ALTER TABLE partners ADD COLUMN onboarding_step INTEGER DEFAULT 0",
        "ALTER TABLE partners ADD COLUMN last_nudge_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE partners ADD COLUMN referred_by_id VARCHAR(36)",
        "ALTER TABLE partners ADD COLUMN referral_code VARCHAR(50)",
        "ALTER TABLE partners ADD COLUMN referral_balance NUMERIC(10,2) DEFAULT 0.00",
        "ALTER TABLE partners ADD COLUMN total_referral_earned NUMERIC(10,2) DEFAULT 0.00",
        "ALTER TABLE partners ADD COLUMN subscribed_locations JSON DEFAULT '[]'",
        "ALTER TABLE monitored_channels ADD COLUMN last_scraped_msg_id BIGINT DEFAULT 0",
        "ALTER TABLE monitored_channels ADD COLUMN last_scraped_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE monitored_channels ADD COLUMN chat_type VARCHAR(50) DEFAULT 'channel'",
        "ALTER TABLE monitored_channels ADD COLUMN location_code VARCHAR(100) DEFAULT 'nhatrang'",
        "ALTER TABLE monitored_channels ADD COLUMN platform VARCHAR(50) DEFAULT 'telegram'",
        "ALTER TABLE leads ADD COLUMN location_code VARCHAR(100) DEFAULT 'global'",
        "ALTER TABLE leads ADD COLUMN platform VARCHAR(50) DEFAULT 'telegram'",
        "ALTER TABLE leads ADD COLUMN reasoning TEXT",
        "ALTER TABLE user_activity_logs ADD COLUMN platform VARCHAR(50) DEFAULT 'telegram'",
        "ALTER TABLE outreach_leads ADD COLUMN platform VARCHAR(50) DEFAULT 'telegram'",
        "ALTER TABLE discovered_chats ADD COLUMN location_code VARCHAR(100) DEFAULT 'global'",
        "ALTER TABLE discovered_chats ADD COLUMN platform VARCHAR(50) DEFAULT 'telegram'",
        "ALTER TABLE collector_logs ADD COLUMN total_fetched_count INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS ai_evaluation_logs (
            id VARCHAR(36) PRIMARY KEY,
            user_id BIGINT NOT NULL,
            username VARCHAR(255),
            first_name VARCHAR(255),
            chat_title VARCHAR(255),
            message_text TEXT NOT NULL,
            is_lead BOOLEAN DEFAULT FALSE,
            reasoning TEXT NOT NULL,
            niche_code VARCHAR(100),
            temperature VARCHAR(20),
            confidence_score FLOAT DEFAULT 0.0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS collector_logs (
            id VARCHAR(36) PRIMARY KEY,
            chat_title VARCHAR(255) NOT NULL,
            username_or_link VARCHAR(255),
            new_messages_count INTEGER DEFAULT 0,
            new_leads_count INTEGER DEFAULT 0,
            status VARCHAR(50) DEFAULT 'OK',
            details TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS channel_candidates (
            id VARCHAR(36) PRIMARY KEY,
            username_or_link VARCHAR(255) NOT NULL UNIQUE,
            title VARCHAR(255),
            source VARCHAR(50) DEFAULT 'RECURSIVE_MENTION',
            niche_code VARCHAR(100) DEFAULT 'community',
            location_code VARCHAR(100) DEFAULT 'nhatrang',
            status VARCHAR(50) DEFAULT 'DISCOVERED',
            member_count INTEGER DEFAULT 0,
            discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE b2b_prospects ADD COLUMN dialogue_history JSON DEFAULT '[]'",
        "ALTER TABLE b2b_prospects ADD COLUMN ai_enabled BOOLEAN DEFAULT TRUE",
        "ALTER TABLE outreach_accounts ADD COLUMN manager_name VARCHAR(255) DEFAULT 'Екатерина'",
        "ALTER TABLE outreach_accounts ADD COLUMN manager_role VARCHAR(255) DEFAULT 'Руководитель отдела B2B развития LeadRadar'",
        "ALTER TABLE outreach_accounts ADD COLUMN persona_prompt TEXT"
    ]

    for stmt in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass

    # Seed & ensure essential target channels, superadmins, and seed leads exist in a single session
    from sqlalchemy import select
    from src.db.models import MonitoredChannel, Partner, CustomChatSubscription, LeadPurchase, WithdrawalRequest, ReferralAccrual, Lead, UserProfile
    from sqlalchemy import update, delete

    extra_channels = [
        {"username_or_link": "@nhatrang_realty",             "title": "📍«NhaTrang Real Estate» | Нячанг. Недвижимость.", "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_services",           "title": "Услуги Вьетнам",                           "niche_code": "services_visa",    "location_code": "nhatrang"},
        {"username_or_link": "@jobs_in_dubai",               "title": "Jobs in Dubai , UAE",                      "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@chatrudubai",                 "title": "Дубай чат ОАЭ, Dubai chat UAE",            "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@jobs_part_time",              "title": "Dubai",                                    "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@beautyservicesdubai",         "title": "Сфера красоты ОАЭ",                        "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@oae_visa",                    "title": "Оформление визы | EasyVisa World",         "niche_code": "services_visa",    "location_code": "dubai"},
        {"username_or_link": "@dubai_usdt_cash",             "title": "Dubai Cash | Обмен USDT/USD/AED/RUB",       "niche_code": "currency_exchange","location_code": "dubai"},
        {"username_or_link": "@dubai_hotel_jobs",            "title": "Dubai hotel Jobs",                         "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@mnogovacansii",               "title": "Работа в Дубае",                           "niche_code": "community",        "location_code": "global"},
        {"username_or_link": "@jobs_in_dubai_uaee",          "title": "Работа в Дубае и Эмиратах - Jobs in Dubai", "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_work24",                "title": "Дубай работа | Jobs in Dubai OAE",         "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_nedvizhimost_oae",      "title": "Дубай недвижимость | ОАЭ",                 "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@dubaiNedvizhimost",           "title": "Дубай Недвижимость",                       "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@dubai_realty",                "title": "Dubai Realty",                             "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@emiratesrealestate",          "title": "Emirates Real Estate",                     "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@workinuae",                   "title": "Jobs in UAE / Работа в ОАЭ",               "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_usdt",                  "title": "USDT Дубай",                               "niche_code": "currency_exchange","location_code": "dubai"},
        {"username_or_link": "@cars_dubai",                  "title": "Cars Dubai Distress Deal",                 "niche_code": "bike_rent",        "location_code": "dubai"},
        {"username_or_link": "@auto_dubai_uae",              "title": "Dubai Auto",                               "niche_code": "bike_rent",        "location_code": "dubai"},
        {"username_or_link": "@nhatrang_ru",                 "title": "Нячанг | Вьетнам Общение",                 "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@motohub_nhatrang",            "title": "MotoHub Нячанг (Moto&Car RENT)",           "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@ResoNATION",                  "title": "ResoNATION",                               "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_arenda",             "title": "Нячанг Аренда Жилья",                      "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@nha_trang_rent",              "title": "Нячанг Прокат Аренда",                     "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_bike",               "title": "АРЕНДА БАЙКОВ НЯЧАНГ/ВЬЕТНАМ",             "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@bike_nhatrang",               "title": "АРЕНДА БАЙКА НЯЧАНГ",                      "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@taxi_nhatrang",               "title": "ТАКСИ - ТРАНСФЕР ВЬЕТНАМ",                 "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@exchange_nhatrang",           "title": "Обмен Валюты Нячанг",                      "niche_code": "currency_exchange","location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_currency_exchange",  "title": "НЯЧАНГ - ОБМЕН ВАЛЮТ",                     "niche_code": "currency_exchange","location_code": "nhatrang"}
    ]

    async with AsyncSessionLocal() as session:
        try:
            existing_targets = set((await session.execute(select(MonitoredChannel.username_or_link))).scalars().all())
            to_add = [
                MonitoredChannel(
                    username_or_link=item["username_or_link"],
                    title=item["title"],
                    niche_code=item["niche_code"],
                    location_code=item["location_code"],
                    status="JOINED"
                )
                for item in extra_channels if item["username_or_link"] not in existing_targets
            ]
            if to_add:
                session.add_all(to_add)

            owner_ids = [260669598, 8866001783]
            for oid in owner_ids:
                p = (await session.execute(select(Partner).where(Partner.telegram_id == oid))).scalar_one_or_none()
                if not p:
                    session.add(Partner(
                        telegram_id=oid,
                        company_name="Компания Ihor Sher" if oid == 260669598 else "RADAR HQ Admin",
                        role="SUPERADMIN",
                        moderation_status="APPROVED",
                        balance=1000.00,
                        subscribed_niches=["real_estate", "bike_rent", "currency_exchange", "services_visa", "auto_kasko"],
                        is_monitoring_active=True
                    ))
                else:
                    p.role = "SUPERADMIN"
                    p.moderation_status = "APPROVED"
                    p.balance = max(float(p.balance or 0), 1000.00)

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
        except Exception as seed_err:
            logger.warning(f"Database init seeding notice: {seed_err}")

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency helper for database session retrieval."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
