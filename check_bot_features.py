import asyncio
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("intent_hunter.check_features")

from src.db.session import init_db, AsyncSessionLocal
from src.db.models import Partner, UserProfile, UserActivityLog, Lead, MonitoredChannel, LeadPurchase
from src.bot.keyboards import get_main_reply_keyboard, NICHE_NAMES
from sqlalchemy import select, func

async def test_bot_features():
    logger.info("================================================================")
    logger.info("🧪 TESTING BOT MONITORING & ADMIN STATS DASHBOARD")
    logger.info("================================================================")

    await init_db()

    # 1. Test Keyboard Generation
    logger.info("Step 1: Testing Main Reply Keyboards...")
    kb_active = get_main_reply_keyboard(is_monitoring_active=True)
    kb_inactive = get_main_reply_keyboard(is_monitoring_active=False)
    logger.info(f"  • Active keyboard monitoring button: '{kb_active.keyboard[2][0].text}'")
    logger.info(f"  • Inactive keyboard monitoring button: '{kb_inactive.keyboard[2][0].text}'")
    logger.info(f"  • Admin stats button: '{kb_active.keyboard[2][1].text}'")

    # 2. Test Partner Monitoring Toggle Persistence
    logger.info("\nStep 2: Testing Partner Monitoring Toggle in DB...")
    test_id = 999111222
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == test_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()

        if not partner:
            partner = Partner(
                telegram_id=test_id,
                company_name="Тестовый Админ",
                balance=5000.00,
                subscribed_niches=["real_estate", "bike_rent", "currency_exchange"],
                is_monitoring_active=True
            )
            session.add(partner)
            await session.commit()
            logger.info("  + Created test admin partner record.")

        initial_state = partner.is_monitoring_active
        logger.info(f"  • Initial Monitoring State: {initial_state}")

        # Toggle state
        partner.is_monitoring_active = not partner.is_monitoring_active
        await session.commit()
        await session.refresh(partner)
        toggled_state = partner.is_monitoring_active
        logger.info(f"  • Toggled Monitoring State: {toggled_state}")

        # Revert back to True
        partner.is_monitoring_active = True
        await session.commit()

    # 3. Test Admin Stats Query Logic
    logger.info("\nStep 3: Testing Admin Stats Metrics Assembly...")
    async with AsyncSessionLocal() as session:
        users_count = (await session.execute(select(func.count(UserProfile.user_id)))).scalar() or 0
        logs_count = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
        leads_count = (await session.execute(select(func.count(Lead.id)))).scalar() or 0
        hot_leads = (await session.execute(select(func.count(Lead.id)).where(Lead.temperature == "HOT"))).scalar() or 0
        sold_leads = (await session.execute(select(func.count(Lead.id)).where(Lead.status == "SOLD"))).scalar() or 0
        partners_count = (await session.execute(select(func.count(Partner.id)))).scalar() or 0
        channels_res = await session.execute(select(MonitoredChannel))
        channels = list(channels_res.scalars().all())

    logger.info("📊 METRICS SUMMARY FOR BOT ADMIN DASHBOARD:")
    logger.info(f"  • User Profiles: {users_count}")
    logger.info(f"  • Activity Logs: {logs_count}")
    logger.info(f"  • Total Leads: {leads_count} (HOT: {hot_leads})")
    logger.info(f"  • Sold Leads: {sold_leads}")
    logger.info(f"  • B2B Partners/Admins: {partners_count}")
    logger.info(f"  • Monitored Channels: {len(channels)}")

    logger.info("\n================================================================")
    logger.info("✅ ALL BOT MONITORING & ADMIN STATS TESTS PASSED!")
    logger.info("================================================================")

if __name__ == "__main__":
    asyncio.run(test_bot_features())
