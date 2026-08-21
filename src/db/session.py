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
        "ALTER TABLE partners ADD COLUMN subscribed_locations JSON DEFAULT '[]'",
        "ALTER TABLE monitored_channels ADD COLUMN last_scraped_msg_id BIGINT DEFAULT 0",
        "ALTER TABLE monitored_channels ADD COLUMN chat_type VARCHAR(50) DEFAULT 'channel'",
        "ALTER TABLE monitored_channels ADD COLUMN location_code VARCHAR(100) DEFAULT 'nhatrang'",
        "ALTER TABLE leads ADD COLUMN location_code VARCHAR(100) DEFAULT 'global'",
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
        )"""
    ]

    for stmt in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass

    # Seed & ensure essential target channels exist
    from sqlalchemy import select
    from src.db.models import MonitoredChannel
    extra_channels = [

        # ══════════════════════════════════════════════════════════════════════
        # 🇦🇪 DUBAI / UAE — 40+ channels
        # ══════════════════════════════════════════════════════════════════════

        # Dubai — General Community
        {"username_or_link": "@UAE_chat",              "title": "Русскоязычные в ОАЭ | Дубай Чат",          "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@chat_dubai",            "title": "ДУБАЙ ЧАТ | РУССКИЕ В ДУБАЕ",              "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@chatdubai_OAE",         "title": "Дубай ЧАТ ОАЭ",                            "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@vDubai_rus",            "title": "Русские в Дубае",                           "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@rudubaichat",           "title": "Русский чат Дубай 🇦🇪",                   "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@chatrudubai",           "title": "Чат Русскоязычных в Дубае",                "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_chat_dubiru",     "title": "Dubai Chat Ru",                             "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@expats_dubai",          "title": "Expats Dubai | Эмигранты Дубай",            "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_ru",              "title": "Дубай RU — главный чат",                   "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@russiandubai",          "title": "Русские в Дубае — сообщество",             "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_market360",       "title": "Дубай Барахолка 360°",                     "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@buy_sell_dubai",        "title": "Дубай: куплю/продам",                      "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@uslugi_v_dubai",        "title": "Услуги и объявления Дубай",                "niche_code": "community",        "location_code": "dubai"},

        # Dubai — Real Estate
        {"username_or_link": "@rent_in_dubai",         "title": "Аренда Дубай: сдам/сниму",                "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@nedvizhimost_dubai_rent","title": "Недвижимость Дубай — аренда/покупка",     "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@dubai_nedvizhimost_oae","title": "Дубай Недвижимость ОАЭ",                   "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@dubaiNedvizhimost",     "title": "Дубай Недвижимость Чат",                   "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@uae_real_estate",       "title": "ОАЭ Недвижимость",                         "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@dubai_realty",          "title": "Dubai Realty | Недвижимость",               "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@dubairent",             "title": "Dubai Rent — аренда квартир",              "niche_code": "real_estate",      "location_code": "dubai"},
        {"username_or_link": "@emiratesrealestate",    "title": "Emirates Real Estate",                      "niche_code": "real_estate",      "location_code": "dubai"},

        # Dubai — Jobs
        {"username_or_link": "@jobs_in_dubai",         "title": "Jobs in Dubai",                             "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@job_in_dubai",          "title": "Вакансии Дубай",                            "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_hotel_jobs",      "title": "Dubai Hotel Jobs",                          "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@jobs_part_time",        "title": "Part Time Jobs Dubai",                      "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@jobs_in_dubai_uaee",    "title": "Работа в Дубае и Эмиратах",                "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@topjobdubai",           "title": "Работа Дубай | ОАЭ | JOB IN DUBAI",        "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_work24",          "title": "Дубай работа | Jobs in Dubai",              "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@dubai_rabota_vakansii", "title": "Вакансии Дубай от работодателей",           "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@workinuae",             "title": "Work in UAE | Работа в ОАЭ",                "niche_code": "community",        "location_code": "dubai"},

        # Dubai — Currency / Crypto / USDT
        {"username_or_link": "@dubai_crypto_chat",     "title": "Дубай Крипто / USDT ОАЭ",                  "niche_code": "currency_exchange","location_code": "dubai"},
        {"username_or_link": "@dubai_usdt",            "title": "USDT Дубай | Обмен крипты",                "niche_code": "currency_exchange","location_code": "dubai"},
        {"username_or_link": "@crypto_dubai",          "title": "Крипта Дубай — P2P обмен",                 "niche_code": "currency_exchange","location_code": "dubai"},

        # Dubai — Services & Visa
        {"username_or_link": "@uslugi_krasoty_dubai",  "title": "Услуги красоты Дубай",                     "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@beautyservicesdubai",   "title": "Beauty Services Dubai (Канал)",             "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@beauty_services_dubai", "title": "Beauty Services Dubai (Чат)",               "niche_code": "community",        "location_code": "dubai"},
        {"username_or_link": "@visa_uae",              "title": "Виза ОАЭ | Визовые услуги Дубай",          "niche_code": "services_visa",    "location_code": "dubai"},
        {"username_or_link": "@dubai_visa",            "title": "Дубай Виза — оформление",                  "niche_code": "services_visa",    "location_code": "dubai"},

        # Dubai — Auto & Transport
        {"username_or_link": "@cars_dubai",            "title": "Авто Дубай | Купить/Продать машину",       "niche_code": "bike_rent",        "location_code": "dubai"},
        {"username_or_link": "@auto_dubai_uae",        "title": "Auto Dubai UAE",                            "niche_code": "bike_rent",        "location_code": "dubai"},

        # ══════════════════════════════════════════════════════════════════════
        # 🇻🇳 NHA TRANG / VIETNAM — 40+ channels
        # ══════════════════════════════════════════════════════════════════════

        # Nha Trang — General Community & Screenshot Target Groups
        {"username_or_link": "@nhatrang_live",             "title": "Нячанг LIVE🔴",                            "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_baraholka_arenda",  "title": "Барахолка Нячанг 📌 Аренда",              "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_otdyh_rabota",      "title": "Нячанг 🏖 Отдых 🛠 Работа ✈️",            "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@vietnam_nhatrang_ads",       "title": "ВЬЕТНАМ НЯЧАНГ ОБЪЯВЛЕНИЯ",                "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrangafisha",            "title": "Нячанг афиша | квиз йога настолки мафия",  "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_shankar",          "title": "Нячанг Shankar - Услуги Товары Пати",      "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_chat_realty",       "title": "Нячанг Чат Барахолка Недвижимость",        "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@club110_nhatrang",          "title": "КЛУБ 110% | Нячанг | Вьетнам",             "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_obyavleniya",       "title": "Нячанг Объявления",                       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_chat_obshchenie",   "title": "НЯЧАНГ ЧАТ 💥 ОБЩЕНИЕ",                   "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_bizness_club",      "title": "Чат | Бизнес Клуб Нячанг",                 "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_bazaar",            "title": "Нячанг Чат Bazaar",                       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_rabota_vacancies",  "title": "Нячанг | Вьетнам - Работа Вакансии",       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_work_sales",        "title": "Нячанг Работа | Продажи и Услуги",         "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@NhaTrangchat",              "title": "НЯЧАНГ ЧАТ ВЬЕТНАМ",                       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@chat_Vietnam_ru",           "title": "Чат Нячанг Вьетнам RU",                    "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_chat",             "title": "Чат Нячанга | Вьетнам Общение",            "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_ru",               "title": "Нячанг 🇻🇳 Русские во Вьетнаме",          "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@vietnam_ru_chat",           "title": "Вьетнам 🇻🇳 Русские | Общий чат",         "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@NhaTrangCommunity",         "title": "Nha Trang Community",                       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@vietnam_news_bot",          "title": "Новости Вьетнама",                          "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@niachang_tusa",             "title": "Нячанг Тусовки и досуг",                   "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@beauty_nhatrang",           "title": "Красота и стиль Нячанг",                   "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@food_nhatrang",             "title": "Еда и рестораны Нячанг",                   "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@doctor_viet",               "title": "Медицина Вьетнам | Врачи и аптеки",        "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@visarun_nhatrang_laos",       "title": "Визаран Нячанг/Лаос/Камбоджа. Виза Вьетнам", "niche_code": "services_visa",    "location_code": "nhatrang"},
        {"username_or_link": "@podslushano_oceanus",       "title": "Подслушано Океанус | Нячанг, Вьетнам",     "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@chat_nhatrang_bg",          "title": "Чат Нячанг 🇻🇳 | B-G",                     "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_baraholka_telegrant","title": "НЯЧАНГ | БАРАХОЛКА | TELEGRANT",           "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@mastermind_nhatrang",       "title": "Main Mastermind Nha Trang | Мастермаинд",  "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@proshche_govorya_nhatrang",  "title": "🌎 Проще говоря. Нячанг",                 "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@pod_palmami_nhatranga",      "title": "Под пальмами Нячанга 🇻🇳 Вьетнам🌴",       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@ukrainians_in_nhatrang",     "title": "Ukrainians in Nha Trang, Vietnam",         "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_online_vietnam",   "title": "Нячанг онлайн 🇻🇳 Вьетнам живём",          "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@motohub_nhatrang",          "title": "Moto Hub . Мото Экипировка Нячанг",        "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_bachata_salsa",    "title": "Nha Trang | Bachata Salsa Kizomba",        "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@duhovny_nhatrang",          "title": "🕉️ Духовный Нячанг",                      "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@privet_nhatrang_chat",      "title": "🏖️ Привет, Нячанг! | Чат Нячанг",         "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_chatik",           "title": "Нячанг чатик 🇻🇳 | CHATIK",                 "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@scooter_party_nhatrang",    "title": "Скутер Пати. Нячанг. Вьетнам",             "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@podcast_studio_nhatrang",   "title": "Подкаст студия | Нячанг",                  "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@om_chanting_nhatrang",      "title": "Ом-чантинг. Нячанг",                       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@informal_networking_nhatrang","title": "Неформальный нетворкинг в Нячанге",     "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_dating",           "title": "Нячанг знакомства",                        "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@pvz_nhatrang",              "title": "ПВЗ Нячанг",                               "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@arenda_baikov_nhatrang",    "title": "Аренда байков Нячанг",                     "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@bizness_club_nhatrang",     "title": "Бизнес клуб Нячанг",                       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@mangalo_center_nhatrang",   "title": "МАНГАЛО | ЦЕНТР НЯЧАНГ",                   "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_scooter_bez_prav", "title": "НЯЧАНГ | СКУТЕР БЕЗ ПРАВ",                 "niche_code": "bike_rent",        "location_code": "nhatrang"},
        # Exact Telegram Usernames from Global Search Screenshots
        {"username_or_link": "@nyachang",                  "title": "Нячанг Чат Объявления Барахолка",          "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nyachang12",                "title": "Нячанг Чат Вьетнам",                       "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nyachang1",                 "title": "🇻🇳 Нячанг Чат Объявления Барахолка 🇻🇳",   "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nyachangs",                "title": "Нячанг Чат Объявления",                   "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@TeZeU0bxBKDtYoUKaObot",     "title": "НЯЧАНГ АРЕНДА | Жилья Квартиры",           "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@nvvprabot",                 "title": "ОБМЕН 💚 Чат Вьетнам Нячанг Дананг",        "niche_code": "currency_exchange","location_code": "nhatrang"},
        {"username_or_link": "@twhcrabot",                 "title": "№1 🇻🇳 ЧАТ по Вьетнаму 💚 ОБМЕН КОМЬЮНИТИ", "niche_code": "currency_exchange","location_code": "nhatrang"},
        {"username_or_link": "@Abqrxobot",                 "title": "Фукуок Чат Нячанг Дананг",                "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@occupation100bot",          "title": "Аренда Дананг Нячанг Вьетнам",             "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@VnXl7dYcncV7KGAP4Ebot",     "title": "ОБМЕННИК В ВЬЕТНАМЕ НЯЧАНГЕ ДАНАНГЕ",      "niche_code": "currency_exchange","location_code": "nhatrang"},
        {"username_or_link": "@shadow_crowd",              "title": "Shadow Crowd — Нячанг",                     "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@goa_people",                "title": "Goa People | Сообщество Нячанг",           "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@ResoNATION",                "title": "ResoNATION — Нячанг",                       "niche_code": "community",        "location_code": "nhatrang"},

        # Nha Trang — Real Estate
        {"username_or_link": "@nhatrang_nedvizhimost", "title": "Нячанг Недвижимость | Аренда",             "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_arenda",       "title": "Нячанг Аренда квартир и домов",            "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_house",        "title": "Нячанг — аренда апартаментов",             "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@vietnam_rent",          "title": "Аренда жилья Вьетнам",                     "niche_code": "real_estate",      "location_code": "nhatrang"},
        {"username_or_link": "@nha_trang_rent",        "title": "Нячанг прокат и аренда",                   "niche_code": "real_estate",      "location_code": "nhatrang"},

        # Nha Trang — Bikes & Transport
        {"username_or_link": "@nhatrang_bike",         "title": "Нячанг Байки | Мото Аренда",               "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@bike_nhatrang",         "title": "Bike Nha Trang | Аренда мото",             "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_transfers",    "title": "Нячанг Трансферы и такси",                 "niche_code": "bike_rent",        "location_code": "nhatrang"},
        {"username_or_link": "@taxi_nhatrang",         "title": "Такси Нячанг | Трансфер Камрань",          "niche_code": "bike_rent",        "location_code": "nhatrang"},

        # Nha Trang — Visa & Visa Runs
        {"username_or_link": "@nhatrang_visa",         "title": "Визаран Нячанг | Виза Вьетнам",            "niche_code": "services_visa",    "location_code": "nhatrang"},
        {"username_or_link": "@visarun_nhatrang",      "title": "Визаран из Нячанга | Лаос/Камбоджа",      "niche_code": "services_visa",    "location_code": "nhatrang"},
        {"username_or_link": "@vietnam_visa_run",      "title": "Visa Run Вьетнам",                         "niche_code": "services_visa",    "location_code": "nhatrang"},

        # Nha Trang — Currency Exchange
        {"username_or_link": "@exchange_nhatrang",     "title": "Обмен валют Нячанг",                       "niche_code": "currency_exchange","location_code": "nhatrang"},
        {"username_or_link": "@usdt_vietnam",          "title": "USDT Вьетнам | Обмен валюты",              "niche_code": "currency_exchange","location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_currency_exchange","title": "Нячанг обмен валют",                   "niche_code": "currency_exchange","location_code": "nhatrang"},
        {"username_or_link": "@vietnam_usdt",          "title": "USDT Вьетнам P2P",                         "niche_code": "currency_exchange","location_code": "nhatrang"},

        # Nha Trang — Barakholka / Marketplace
        {"username_or_link": "@Barakholka_NhaTrang",   "title": "Барахолка Нячанг 🛍️",                    "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@nhatrang_market",       "title": "Нячанг Маркет | Объявления",               "niche_code": "community",        "location_code": "nhatrang"},

        # Nha Trang — Jobs
        {"username_or_link": "@vietnam_job",           "title": "Работа Вьетнам | Вакансии",                "niche_code": "community",        "location_code": "nhatrang"},
        {"username_or_link": "@niachang_rabota",       "title": "Работа Нячанг | Вакансии",                 "niche_code": "community",        "location_code": "nhatrang"},

        # ══════════════════════════════════════════════════════════════════════
        # 🌐 GLOBAL / RUSSIA / OTHER
        # ══════════════════════════════════════════════════════════════════════
        {"username_or_link": "@mnogovacansii",         "title": "Удаленная работа & Фриланс",               "niche_code": "community",        "location_code": "global"},
        {"username_or_link": "@barcelona_alicante",    "title": "Барселона Аликанте Нячанг",                "niche_code": "community",        "location_code": "global"},
        {"username_or_link": "@alicante_torrevieja",   "title": "Барселона Аликанте Торревьеха",            "niche_code": "community",        "location_code": "global"},
        {"username_or_link": "@alicante_madrid",       "title": "Барселона Аликанте Мадрид",                "niche_code": "community",        "location_code": "global"},
        {"username_or_link": "@barcelona_ru",          "title": "Барселона | Русские в Испании",            "niche_code": "community",        "location_code": "global"},
    ]


    async with AsyncSessionLocal() as session:
        for item in extra_channels:
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == item["username_or_link"])
            ch = (await session.execute(stmt)).scalar_one_or_none()
            if not ch:
                session.add(MonitoredChannel(
                    username_or_link=item["username_or_link"],
                    title=item["title"],
                    niche_code=item["niche_code"],
                    location_code=item["location_code"],
                    status="JOINED"
                ))
        await session.commit()

    # Ensure Owner/Superadmin (ID: 113767) is present and elevated in Partner table
    from src.db.models import Partner
    from sqlalchemy import delete
    async with AsyncSessionLocal() as session:
        owner_ids = [113767, 268669598]
        for oid in owner_ids:
            p = (await session.execute(select(Partner).where(Partner.telegram_id == oid))).scalar_one_or_none()
            if not p:
                session.add(Partner(
                    telegram_id=oid,
                    company_name="Компания Ihor Sher",
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
        
        # Clean up bot self ID and mock test IDs
        bot_self_and_mocks = [8866001783, 260669598, 777000111, 999111222, 888777666]
        await session.execute(delete(Partner).where(Partner.telegram_id.in_(bot_self_and_mocks)))
        await session.commit()

    # ⚠️ DEMO SEED: Only insert placeholder leads on SQLite (local/dev).
    # On PostgreSQL (Railway production), NEVER overwrite real data with seed leads.
    is_sqlite = "sqlite" in db_url.lower()
    if is_sqlite:
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

    # Deduplicate existing leads in database
    async with AsyncSessionLocal() as session:
        try:
            leads_res = await session.execute(select(Lead).order_by(Lead.created_at.asc()))
            all_leads = list(leads_res.scalars().all())
            seen_lead_keys = set()
            for l in all_leads:
                key = f"{l.user_id}_{l.niche_code}_{l.location_code}_{l.intent_summary}"
                if key in seen_lead_keys:
                    await session.delete(l)
                else:
                    seen_lead_keys.add(key)
            await session.commit()
        except Exception as dedup_err:
            logger.warning(f"Lead deduplication error on init: {dedup_err}")

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
