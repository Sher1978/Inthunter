import asyncio
import logging
import sys
from sqlalchemy import select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("intent_hunter.seed_nhatrang")

from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel, UserProfile, UserActivityLog, Lead
from src.ingestion.public_scraper import PublicTelegramScraper
from src.ingestion.telegram import TelegramIngestor

TARGET_CHANNELS = [
    {"username_or_link": "@nhatrang_chat", "niche_code": "community", "location_code": "nhatrang", "title": "Чат Нячанга | Вьетнам Общение"},
    {"username_or_link": "@nhatrang_realty", "niche_code": "real_estate", "location_code": "nhatrang", "title": "Аренда Недвижимости Нячанг"},
    {"username_or_link": "@nhatrang_doska", "niche_code": "general_market", "location_code": "nhatrang", "title": "Барахолка Нячанг Объявления"},
    {"username_or_link": "@nhatrang_services", "niche_code": "services_visa", "location_code": "nhatrang", "title": "Услуги и Визаран Нячанг"},
    {"username_or_link": "@nhatrang_moto", "niche_code": "bike_rent", "location_code": "nhatrang", "title": "Аренда Байков & Трансфер Нячанг"},
]

SEED_LEAD_MESSAGES = [
    {
        "user_id": 881001,
        "username": "maxim_nhatrang",
        "first_name": "Максим",
        "last_name": "Ковалев",
        "chat_id": -10099112233,
        "chat_title": "Аренда Недвижимости Нячанг",
        "messages": [
            "Срочно сниму 1-к квартиру или студию в Муонг Тхань (Muong Thanh Grand) на 3 месяца с видами на море. Бюджет до 8 млн донгов. Кто подскажет проверенного агента?"
        ]
    },
    {
        "user_id": 881002,
        "username": "olga_expat",
        "first_name": "Ольга",
        "last_name": "Морозова",
        "chat_id": -10099445566,
        "chat_title": "Чат Нячанга | Вьетнам Общение",
        "messages": [
            "Привет всем! Подскажите, где в центре Нячанга сейчас самый выгодный курс обмена USDT на наличные донги? Нужно поменять $1500 с доставкой."
        ]
    },
    {
        "user_id": 881003,
        "username": "andrey_rider",
        "first_name": "Андрей",
        "last_name": "Соколов",
        "chat_id": -10099778899,
        "chat_title": "Аренда Байков & Трансфер Нячанг",
        "messages": [
            "Нужен байк Honda NVX 155 или PCX в хорошем состоянии на месяц в районе Северного пляжа. Также нужен трансфер из аэропорта Камрань на завтра 14:00."
        ]
    },
    {
        "user_id": 881004,
        "username": "sveta_travel",
        "first_name": "Светлана",
        "last_name": "Романова",
        "chat_id": -10099223344,
        "chat_title": "Услуги и Визаран Нячанг",
        "messages": [
            "Ребята, кто делает бордерран/визаран в Лаос из Нячанга на этой неделе? Нас 2 человека, нужны комфортные спальные места и помощь с визой."
        ]
    }
]

async def seed_nhatrang():
    logger.info("================================================================")
    logger.info("🇻🇳 SEEDING CORE TELEGRAM CHANNELS & LEADS (NHATRANG)")
    logger.info("================================================================")

    await init_db()
    ingestor = TelegramIngestor()
    scraper = PublicTelegramScraper()

    # 1. Register monitored channels in DB
    logger.info("Step 1: Registering target Telegram channels in DB...")
    async with AsyncSessionLocal() as session:
        for ch in TARGET_CHANNELS:
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == ch["username_or_link"])
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                new_ch = MonitoredChannel(
                    username_or_link=ch["username_or_link"],
                    niche_code=ch["niche_code"],
                    location_code=ch.get("location_code", "nhatrang"),
                    title=ch["title"],
                    status="JOINED"
                )
                session.add(new_ch)
            else:
                existing.location_code = ch.get("location_code", "nhatrang")
                existing.status = "JOINED"
        await session.commit()
    logger.info("Core channels registration complete.")

if __name__ == "__main__":
    asyncio.run(seed_nhatrang())
