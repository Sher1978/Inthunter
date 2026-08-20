import asyncio
import os
import re
import logging
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel

logger = logging.getLogger("intent_hunter.seed_dubai")

async def seed_dubai():
    try:
        base_dir = os.path.dirname(__file__)
        tech_dir = os.path.join(base_dir, "Tech")
        files = []
        if os.path.exists(tech_dir):
            for fn in os.listdir(tech_dir):
                if fn.endswith(".md"):
                    files.append(os.path.join(tech_dir, fn))

        matches = []
        pattern = r"\|\s*\d+\s*\|\s*([^|]+)\s*\|\s*`(@[^`]+)`\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|"

        for file_path in files:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    matches.extend(re.findall(pattern, content))

        if not matches:
            logger.info("Dubai markdown files not found, skipping startup dubai seeding.")
            return

        def map_niche(category_str: str) -> str:
            cat = category_str.lower()
            if any(k in cat for k in ["real estate", "rental", "realty", "off-plan", "housing", "sublet"]):
                return "real_estate"
            elif any(k in cat for k in ["auto", "car", "driver", "motor", "racer", "towing"]):
                return "auto_kasko"
            elif any(k in cat for k in ["crypto", "currency", "exchange", "cash", "p2p", "swift", "bank", "finance"]):
                return "currency_exchange"
            elif any(k in cat for k in ["visa", "legal", "business", "tax", "corporate", "trade", "b2b", "consulting"]):
                return "services_visa"
            else:
                return "community"

        async with AsyncSessionLocal() as session:
            dubai_stmt = select(MonitoredChannel).where(MonitoredChannel.location_code == "dubai")
            existing_dubai = list((await session.execute(dubai_stmt)).scalars().all())

            if len(existing_dubai) >= 350:
                logger.info(f"Dubai channels already seeded ({len(existing_dubai)} channels in DB).")
                return

            added_count = 0
            for title, username, category, intents in matches:
                username = username.strip()
                title = title.strip()
                niche = map_niche(category)

                ch_stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(f"%{username.replace('@','')}%"))
                existing = (await session.execute(ch_stmt)).scalar_one_or_none()

                if not existing:
                    ch = MonitoredChannel(
                        username_or_link=username,
                        title=title,
                        niche_code=niche,
                        location_code="dubai",
                        status="JOINED",
                        chat_type="group"
                    )
                    session.add(ch)
                    added_count += 1

            await session.commit()
            logger.info(f"✅ Startup Dubai channels seeding completed! Added {added_count} channels.")

    except Exception as e:
        logger.warning(f"Error during startup Dubai channel seeding: {e}")
