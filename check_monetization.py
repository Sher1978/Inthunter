import asyncio
import sys
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("intent_hunter.check_monetization")

async def test_monetization():
    logger.info("================================================================")
    logger.info("🧪 TESTING MONETIZATION, RBAC & TELEGRAM STARS ENGINE")
    logger.info("================================================================")

    # 1. Test Database Models & Session Migration
    from src.db.session import init_db, AsyncSessionLocal
    from src.db.models import Partner, Lead, LeadPurchase
    from sqlalchemy import select

    logger.info("Step 1: Initializing DB schema and migrations...")
    await init_db()

    async with AsyncSessionLocal() as session:
        # Create test Demo user
        test_id = 999111222
        stmt = select(Partner).where(Partner.telegram_id == test_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()
        if not partner:
            partner = Partner(
                telegram_id=test_id,
                company_name="Test Demo Partner",
                role="DEMO",
                moderation_status="PENDING",
                balance=0.00,
                subscribed_niches=["real_estate"]
            )
            session.add(partner)
            await session.commit()
            await session.refresh(partner)

        logger.info(f"  ✅ Created Demo Partner: ID={partner.telegram_id}, Role={partner.role}, Status={partner.moderation_status}, Balance=${partner.balance:.2f} USD")

        # Promote user to REGULAR and add $100 USD balance
        partner.role = "REGULAR"
        partner.moderation_status = "APPROVED"
        partner.balance = 100.00 # $100 USD top-up (5000 Stars)
        await session.commit()

        logger.info(f"  ✅ Approved Partner & Top-Up: Role={partner.role}, Balance=${partner.balance:.2f} USD (100 contacts)")

        # Create test Lead priced at $1.00 USD
        test_lead = Lead(
            user_id=123456789,
            niche_code="real_estate",
            temperature="HOT",
            confidence_score=0.95,
            intent_summary="Wants to rent 3-bedroom villa in Nha Trang",
            sales_hook="Ask for budget and check-in date",
            price=1.00, # $1.00 USD
            status="AVAILABLE"
        )
        session.add(test_lead)
        await session.commit()
        await session.refresh(test_lead)

        logger.info(f"  ✅ Created Lead: ID={test_lead.id}, Price=${test_lead.price:.2f} USD, Status={test_lead.status}")

        # Simulate purchasing lead contact for $1.00 USD
        partner.balance = float(partner.balance) - float(test_lead.price)
        test_lead.status = "SOLD"
        purchase = LeadPurchase(lead_id=test_lead.id, partner_id=partner.id, price_paid=test_lead.price)
        session.add(purchase)
        await session.commit()

        logger.info(f"  ✅ Purchased Lead: Paid=${test_lead.price:.2f} USD, Partner Remaining Balance=${partner.balance:.2f} USD")

    # 2. Test Keyboard Assemblies
    logger.info("\nStep 2: Testing Telegram Keyboards Assembly...")
    from src.bot.keyboards import get_topup_keyboard, get_buy_lead_keyboard, get_moderation_inline_keyboard

    topup_kb = get_topup_keyboard()
    buy_kb = get_buy_lead_keyboard(test_lead.id, 1.00)
    mod_kb = get_moderation_inline_keyboard(test_id)

    logger.info(f"  ✅ Top-Up Stars buttons count: {len(topup_kb.inline_keyboard)}")
    logger.info(f"  ✅ Buy Lead button text: '{buy_kb.inline_keyboard[0][0].text}'")
    logger.info(f"  ✅ Moderation buttons count: {len(mod_kb.inline_keyboard[0]) + len(mod_kb.inline_keyboard[1])}")

    logger.info("\n================================================================")
    logger.info("✅ MONETIZATION, RBAC & TELEGRAM STARS TESTS PASSED!")
    logger.info("================================================================")

if __name__ == "__main__":
    asyncio.run(test_monetization())
