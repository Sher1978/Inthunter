import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select

from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel
from src.ingestion.vk_ok_scrapers import VKPublicScraper, OKPublicScraper, MAXPublicScraper
from src.ingestion.multichannel_adapter import MultiChannelAdapter

logger = logging.getLogger("intent_hunter.multi_platform_poller")

poller_task = None


class MultiPlatformPoller:
    @staticmethod
    async def run_poller_cycle() -> int:
        """
        Polls active VK, OK, and MAX groups from MonitoredChannel table.
        Ingests newly scraped posts into MultiChannelAdapter for AI Lead Scoring.
        """
        async with AsyncSessionLocal() as session:
            stmt = select(MonitoredChannel).where(
                MonitoredChannel.status == "JOINED",
                MonitoredChannel.platform.in_(["vk", "ok", "max"])
            )
            res = await session.execute(stmt)
            channels = list(res.scalars().all())

            if not channels:
                logger.debug("No active non-Telegram channels (VK/OK/MAX) registered for polling.")
                return 0

            logger.info(f"📡 Multi-Platform Poller: Checking {len(channels)} active VK/OK/MAX channels...")
            total_ingested = 0

            for ch in channels:
                try:
                    messages = []
                    if ch.platform == "vk":
                        messages = await VKPublicScraper.fetch_latest_messages(ch.username_or_link)
                    elif ch.platform == "ok":
                        messages = await OKPublicScraper.fetch_latest_messages(ch.username_or_link)
                    elif ch.platform == "max":
                        messages = await MAXPublicScraper.fetch_latest_messages(ch.username_or_link)

                    if messages:
                        for msg in messages:
                            ingest_res = await MultiChannelAdapter.process_inbound_message(
                                session=session,
                                platform=ch.platform,
                                chat_title=msg.get("chat_title") or ch.title or ch.username_or_link,
                                message_text=msg.get("message_text"),
                                user_id_raw=msg.get("user_id"),
                                username=msg.get("username"),
                                first_name=msg.get("first_name"),
                                location_code=ch.location_code or "global"
                            )
                            if ingest_res.get("status") == "success":
                                total_ingested += 1

                    ch.last_scraped_at = datetime.now(timezone.utc)
                    await session.commit()
                except Exception as e:
                    logger.warning(f"Poller error for {ch.platform} channel {ch.username_or_link}: {e}")

            logger.info(f"✅ Multi-Platform Poller cycle finished. Ingested {total_ingested} messages across VK/OK/MAX.")
            return total_ingested


async def run_multi_platform_poller_loop(interval_seconds: int = 180):
    """Background task loop executing multi-platform poller every 3 minutes."""
    logger.info("🚀 Starting Multi-Platform Ingestion Loop (VK, OK, MAX Messenger)...")
    while True:
        try:
            await MultiPlatformPoller.run_poller_cycle()
        except Exception as e:
            logger.error(f"Error in Multi-Platform Poller Loop: {e}")
        await asyncio.sleep(interval_seconds)
