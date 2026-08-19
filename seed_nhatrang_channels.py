import asyncio
import logging
import sys
from sqlalchemy import select

# Ensure UTF-8 stream output on Windows terminals
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

NHATRANG_CHANNELS = [
    {"username_or_link": "@nhatrang_chat", "niche_code": "community", "title": "Чат Нячанга | Вьетнам Общение"},
    {"username_or_link": "@nhatrang_realty", "niche_code": "real_estate", "title": "Аренда Недвижимости Нячанг"},
    {"username_or_link": "@nhatrang_doska", "niche_code": "general_market", "title": "Барахолка Нячанг Объявления"},
    {"username_or_link": "@nhatrang_services", "niche_code": "services_visa", "title": "Услуги и Визаран Нячанг"},
    {"username_or_link": "@nhatrang_moto", "niche_code": "bike_rent", "title": "Аренда Байков & Трансфер Нячанг"}
]

# Real-world realistic candidate messages for Nha Trang expat lead generation
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
    logger.info("🇻🇳 SEEDING NHATRANG (VIETNAM) TELEGRAM CHANNELS & LEADS")
    logger.info("================================================================")

    await init_db()
    ingestor = TelegramIngestor()
    scraper = PublicTelegramScraper()

    # 1. Register monitored channels in DB
    logger.info("Step 1: Registering Nha Trang target Telegram channels in DB...")
    async with AsyncSessionLocal() as session:
        for ch in NHATRANG_CHANNELS:
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == ch["username_or_link"])
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                new_ch = MonitoredChannel(
                    username_or_link=ch["username_or_link"],
                    niche_code=ch["niche_code"],
                    title=ch["title"],
                    status="JOINED"
                )
                session.add(new_ch)
                logger.info(f"  + Added monitored channel: {ch['title']} ({ch['username_or_link']})")
        await session.commit()

    # 2. Attempt Live Public Scrape
    logger.info("\nStep 2: Scraping public Telegram channel feeds...")
    scraped_total = 0
    for ch in NHATRANG_CHANNELS:
        target = ch["username_or_link"]
        logger.info(f"Scraping public preview for {target}...")
        posts = await scraper.fetch_latest_messages(target)
        if posts:
            scraped_total += len(posts)
            for post in posts:
                await ingestor.process_incoming_message(
                    user_id=post["user_id"],
                    username=post["username"],
                    first_name=post["first_name"],
                    last_name=post["last_name"],
                    chat_id=abs(hash(target)) % (10**9),
                    chat_title=post["chat_title"] or ch["title"],
                    message_id=post["message_id"],
                    text=post["text"]
                )

    logger.info(f"Live scraping completed. Fetched {scraped_total} public posts.")

    # 3. Ingest Seed Lead Messages to Ensure Hot Leads Baseline
    logger.info("\nStep 3: Processing Nha Trang expat lead dataset...")
    msg_counter = 5000
    for candidate in SEED_LEAD_MESSAGES:
        for text in candidate["messages"]:
            msg_counter += 1
            await ingestor.process_incoming_message(
                user_id=candidate["user_id"],
                username=candidate["username"],
                first_name=candidate["first_name"],
                last_name=candidate["last_name"],
                chat_id=candidate["chat_id"],
                chat_title=candidate["chat_title"],
                message_id=msg_counter,
                text=text
            )

    await asyncio.sleep(1)

    # 4. Display Results
    async with AsyncSessionLocal() as session:
        leads_res = await session.execute(select(Lead).order_by(Lead.created_at.desc()))
        leads = list(leads_res.scalars().all())

        profiles_res = await session.execute(select(UserProfile))
        profiles = list(profiles_res.scalars().all())

        logger.info(f"\n================================================================")
        logger.info(f"🔥 TOTAL QUALIFIED LEADS IN FEED: {len(leads)}")
        logger.info(f"================================================================")

        for idx, lead in enumerate(leads, 1):
            user_profile = next((p for p in profiles if p.user_id == lead.user_id), None)
            uname = f"@{user_profile.username}" if user_profile and user_profile.username else f"ID: {lead.user_id}"
            user_name = f"{user_profile.first_name or ''} {user_profile.last_name or ''}".strip() if user_profile else ""
            
            logger.info(f"LEAD #{idx}:")
            logger.info(f"  • User: {user_name} ({uname})")
            logger.info(f"  • Niche: {lead.niche_code}")
            logger.info(f"  • Temp: {lead.temperature} | Score: {lead.confidence_score}")
            logger.info(f"  • Summary: {lead.intent_summary}")
            logger.info(f"  • Hook: {lead.sales_hook}")
            logger.info(f"  • Status/Price: {lead.status} | {lead.price} ₽\n")

if __name__ == "__main__":
    asyncio.run(seed_nhatrang())
