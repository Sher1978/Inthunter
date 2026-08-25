import asyncio
import logging
import sys
from datetime import datetime, timezone
from sqlalchemy import select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("intent_hunter.seed_ekb_tourism")

from src.db.session import AsyncSessionLocal
from src.db.models import DiscoveryKeyword, MonitoredChannel, Partner

EKATERINBURG_KEYWORDS = [
    ("Екатеринбург туризм", "ekaterinburg"),
    ("Екатеринбург туры и горящие путевки", "ekaterinburg"),
    ("Екатеринбург медицинский туризм", "ekaterinburg"),
    ("Екатеринбург клиники и обследование", "ekaterinburg"),
    ("Екатеринбург санатории и оздоровление", "ekaterinburg"),
    ("Екатеринбург визовый центр", "ekaterinburg"),
    ("Екатеринбург экскурсии гиды", "ekaterinburg"),
    ("Медицинский туризм Урал", "ekaterinburg"),
    ("Туры из Екатеринбурга", "ekaterinburg"),
    ("Екатеринбург лечение и чек-ап", "ekaterinburg")
]

CURATED_EKB_CHANNELS = [
    {"username_or_link": "@ekb_turizm", "title": "Туризм и Отдых Екатеринбург", "niche_code": "tourism_travel", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@ekb_tury_travel", "title": "Горящие Туры Екатеринбург", "niche_code": "tourism_travel", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@ekaterinburg_travel_chat", "title": "Путешествия и Гиды Екатеринбург", "niche_code": "tourism_travel", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@med_turizm_ekb", "title": "Медицинский Туризм Екатеринбург & Урал", "niche_code": "medical_tourism", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@ekb_health_clinics", "title": "Клиники и Консультации Врачей Екб", "niche_code": "medical_tourism", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@sanatorii_ural_ekb", "title": "Санатории и Здравницы Урала", "niche_code": "medical_tourism", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@ekb_visa_travel", "title": "Визы и Оформление Документов Екб", "niche_code": "tourism_travel", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@ekb_doska_uslugi", "title": "Доска Объявлений и Услуги Екатеринбург", "niche_code": "community", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@ekaterinburg_chat_official", "title": "Главный Чат Екатеринбурга", "niche_code": "community", "location_code": "ekaterinburg", "platform": "telegram"},
    {"username_or_link": "@vk_ekb_tourism_group", "title": "VK: Туризм Екатеринбург", "niche_code": "tourism_travel", "location_code": "ekaterinburg", "platform": "vk"},
    {"username_or_link": "@vk_med_turizm_ural", "title": "VK: Медтуризм Урал и Екатеринбург", "niche_code": "medical_tourism", "location_code": "ekaterinburg", "platform": "vk"},
    {"username_or_link": "@ok_ekb_travel_health", "title": "OK: Путешествия и Здоровье Екатеринбург", "niche_code": "tourism_travel", "location_code": "ekaterinburg", "platform": "ok"},
    {"username_or_link": "@max_ekb_tours", "title": "MAX: Туры и Санатории Екб", "niche_code": "tourism_travel", "location_code": "ekaterinburg", "platform": "max"}
]

async def seed_ekb_tourism():
    logger.info("================================================================")
    logger.info("🏙 SEEDING EKATERINBURG TOURISM & MEDICAL TOURISM CONFIG & CHANNELS")
    logger.info("================================================================")

    async with AsyncSessionLocal() as session:
        # 1. Seed DiscoveryKeywords for Ekaterinburg
        added_kw = 0
        for kw, loc in EKATERINBURG_KEYWORDS:
            stmt = select(DiscoveryKeyword).where(DiscoveryKeyword.keyword.ilike(kw))
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                nk = DiscoveryKeyword(
                    keyword=kw,
                    location_code=loc,
                    is_active=True,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(nk)
                added_kw += 1
            else:
                existing.is_active = True
                existing.location_code = loc
        logger.info(f"✅ Registered/updated {len(EKATERINBURG_KEYWORDS)} discovery keywords ({added_kw} new).")

        # 2. Seed Target MonitoredChannels across TG, VK, OK, MAX
        added_ch = 0
        for ch in CURATED_EKB_CHANNELS:
            stmt = select(MonitoredChannel).where(
                MonitoredChannel.username_or_link.ilike(ch["username_or_link"]),
                MonitoredChannel.platform == ch["platform"]
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                new_ch = MonitoredChannel(
                    username_or_link=ch["username_or_link"],
                    title=ch["title"],
                    niche_code=ch["niche_code"],
                    location_code=ch["location_code"],
                    platform=ch["platform"],
                    chat_type="group",
                    status="JOINED"
                )
                session.add(new_ch)
                added_ch += 1
            else:
                existing.location_code = ch["location_code"]
                existing.niche_code = ch["niche_code"]
                existing.status = "JOINED"
        logger.info(f"✅ Registered/updated {len(CURATED_EKB_CHANNELS)} Ekaterinburg target channels ({added_ch} new).")

        # 3. Ensure partners have 'ekaterinburg', 'tourism_travel', 'medical_tourism' included in subscriptions
        partners_res = await session.execute(select(Partner))
        for p in partners_res.scalars().all():
            locs = list(p.subscribed_locations or [])
            niches = list(p.subscribed_niches or [])
            if "all" in locs or not locs:
                if "ekaterinburg" not in locs:
                    locs.append("ekaterinburg")
                    p.subscribed_locations = locs
            if "all" in niches or not niches:
                if "tourism_travel" not in niches:
                    niches.append("tourism_travel")
                if "medical_tourism" not in niches:
                    niches.append("medical_tourism")
                p.subscribed_niches = niches

        await session.commit()

    # 4. Trigger Instant Grok & MTProto discovery pass for Ekaterinburg
    logger.info("🚀 Triggering immediate Grok AI & Multi-Platform discovery pass for Ekaterinburg...")
    try:
        from src.discovery.chat_discovery import run_global_keyword_search
        async with AsyncSessionLocal() as session:
            found = await run_global_keyword_search(session)
            logger.info(f"✨ Discovery pass complete: Found {found} new candidate channels for Ekaterinburg!")
    except Exception as e:
        logger.error(f"Error executing immediate Ekaterinburg discovery pass: {e}")

if __name__ == "__main__":
    asyncio.run(seed_ekb_tourism())
