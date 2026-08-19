import asyncio
import sys
import logging
from sqlalchemy import select

# UTF-8 stream reconfig for Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel
from src.ai.grok_channel_finder import GrokChannelFinder

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("test_grok")

async def test_grok_finder_and_approval():
    logger.info("================================================================")
    logger.info("🧪 RUNNING GROK TELEGRAM CHANNEL & GROUP DISCOVERY TEST SUITE")
    logger.info("================================================================")

    # 1. Init DB
    await init_db()

    # 2. Instantiate Grok Finder & Execute Search
    finder = GrokChannelFinder()
    test_query = "нячанг жилье аренда квартиры"
    logger.info(f"Querying Grok Channel Finder for: '{test_query}'...")

    candidates = await finder.search_channels_and_groups(keywords=test_query, limit=5)

    assert len(candidates) > 0, "❌ Candidate list is empty!"
    logger.info(f"✅ Received {len(candidates)} candidates from Grok discovery engine.")

    groups_count = len([c for c in candidates if c["chat_type"] == "group"])
    channels_count = len([c for c in candidates if c["chat_type"] == "channel"])
    logger.info(f"   • 👥 Groups/Chats found: {groups_count}")
    logger.info(f"   • 📢 Channels found: {channels_count}")

    for idx, c in enumerate(candidates, 1):
        logger.info(f"   [{idx}] [{c['chat_type'].upper()}] {c['title']} ({c['username']}) - {c['description'][:50]}...")
        assert c["username"].startswith("@") or c["username"].startswith("http"), f"Invalid username: {c['username']}"
        assert c["chat_type"] in ["group", "channel"], f"Invalid chat_type: {c['chat_type']}"

    # 3. Simulate per-channel approval flow into Database
    target_candidate = candidates[0]
    target_username = target_candidate["username"]
    logger.info(f"\nSimulating approval for candidate: {target_username} ({target_candidate['title']})...")

    async with AsyncSessionLocal() as session:
        # Clear previous test entry if exists
        stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == target_username)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            await session.delete(existing)
            await session.commit()

        # Add approved channel
        approved_ch = MonitoredChannel(
            username_or_link=target_username,
            title=target_candidate["title"],
            chat_type=target_candidate["chat_type"],
            niche_code="real_estate",
            status="PENDING"
        )
        session.add(approved_ch)
        await session.commit()

        # Verify DB insertion
        verify_stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == target_username)
        saved = (await session.execute(verify_stmt)).scalar_one_or_none()

        assert saved is not None, f"❌ Failed to find saved channel {target_username} in DB!"
        assert saved.chat_type == target_candidate["chat_type"], f"Chat type mismatch: {saved.chat_type}"
        logger.info(f"✅ Successfully verified DB record: ID={saved.id}, username={saved.username_or_link}, type={saved.chat_type}, status={saved.status}")

    from src.db.session import engine
    await engine.dispose()

    logger.info("================================================================")
    logger.info("🎉 ALL GROK CHANNEL FINDER TESTS PASSED SUCCESSFULLY!")
    logger.info("================================================================")

if __name__ == "__main__":
    asyncio.run(test_grok_finder_and_approval())
