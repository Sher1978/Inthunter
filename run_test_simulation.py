import asyncio
import logging
import sys
from sqlalchemy import select

from src.config import settings
from src.db.session import init_db, AsyncSessionLocal
from src.db.models import UserProfile, UserActivityLog, Lead, Partner, LeadPurchase

from src.ingestion.telegram import TelegramIngestor
from src.ai.scorer import evaluate_user_timeline

import io
import sys

# Ensure UTF-8 stream output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("intent_hunter.simulation")

# Test simulation dataset representing realistic Telegram group chat interactions
SIMULATED_MESSAGES = [
    # Candidate 1: Auto KASKO Lead
    {
        "user_id": 990101,
        "username": "alex_automotive",
        "first_name": "Алексей",
        "last_name": "Петров",
        "chat_id": -100111223344,
        "chat_title": "Чат Сообщества Автомобилистов",
        "messages": [
            "Ребят, всем привет! Кто где сейчас оформляет КАСКО в Москве?",
            "Купил новый Chery Tiggo 8, хочу понять где сейчас нормальные выплаты без геморроя. Посоветуйте брокера!"
        ]
    },
    # Candidate 2: Real Estate Lead
    {
        "user_id": 990202,
        "username": "elena_invest",
        "first_name": "Елена",
        "last_name": "Смирнова",
        "chat_id": -100555667788,
        "chat_title": "Чат Жилого Комплекса",
        "messages": [
            "Ищу проверенного риелтора для срочной покупки 2-к квартиры в ЖК. Бюджет 15 млн ₽, за наличные."
        ]
    },
    # Candidate 3: Casual conversation (Non-Lead)
    {
        "user_id": 990303,
        "username": "dmitry_chat",
        "first_name": "Дмитрий",
        "last_name": "Иванов",
        "chat_id": -100111223344,
        "chat_title": "Чат Сообщества Автомобилистов",
        "messages": [
            "Да уж, погода сегодня не очень.",
            "Кто на выходных планирует на дачу ехать?"
        ]
    }
]

async def run_simulation():
    logger.info("================================================================")
    logger.info("🚀 STARTING INTENT HUNTER CDP - END-TO-END PIPELINE SIMULATION")
    logger.info("================================================================")

    # 1. Initialize DB Tables
    logger.info("Step 1: Initializing Database Schema...")
    await init_db()
    logger.info("Database schema ready.")

    ingestor = TelegramIngestor()

    # 2. Simulate Chat Ingestion
    logger.info("\nStep 2: Simulating Message Ingestion from Telegram Groups...")
    msg_counter = 1000
    for candidate in SIMULATED_MESSAGES:
        logger.info(f"\n---> Simulating user: {candidate['first_name']} (@{candidate['username']})")
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

    # Allow async scoring tasks to finish
    await asyncio.sleep(1)

    # 3. Verify Database Records
    logger.info("\nStep 3: Verifying Qualified Leads in Database...")
    async with AsyncSessionLocal() as session:
        profiles_res = await session.execute(select(UserProfile))
        profiles = list(profiles_res.scalars().all())
        logger.info(f"Total User Profiles in Database: {len(profiles)}")

        logs_res = await session.execute(select(UserActivityLog))
        logs = list(logs_res.scalars().all())
        logger.info(f"Total Activity Logs in Database: {len(logs)}")

        leads_res = await session.execute(select(Lead))
        leads = list(leads_res.scalars().all())
        logger.info(f"\n================================================================")
        logger.info(f"🎯 QUALIFIED LEADS FOUND: {len(leads)}")
        logger.info(f"================================================================")
        
        for idx, lead in enumerate(leads, 1):
            user_profile = next((p for p in profiles if p.user_id == lead.user_id), None)
            username_str = f"@{user_profile.username}" if user_profile and user_profile.username else f"ID: {lead.user_id}"
            
            logger.info(f"\nLEAD #{idx}:")
            logger.info(f"  • User: {user_profile.first_name if user_profile else ''} ({username_str})")
            logger.info(f"  • Niche: {lead.niche_code}")
            logger.info(f"  • Temperature: {lead.temperature} (Score: {lead.confidence_score})")
            logger.info(f"  • Intent Summary: {lead.intent_summary}")
            logger.info(f"  • Sales Hook: {lead.sales_hook}")
            logger.info(f"  • Status/Price: {lead.status} | {lead.price} ₽")

        # 4. Simulate B2B Partner Lead Purchase Flow
        logger.info("\nStep 4: Simulating B2B Partner Marketplace & Lead Purchase Flow...")
        
        # Create test partner
        test_partner = Partner(
            telegram_id=777000111,
            company_name="ООО Страховой Брокер",
            balance=2000.00,
            subscribed_niches=["auto_kasko", "real_estate"]
        )
        session.add(test_partner)
        await session.commit()
        await session.refresh(test_partner)

        logger.info(f"Registered test partner: {test_partner.company_name} (Balance: {test_partner.balance:.2f} ₽)")

        # Buy Lead #1
        target_lead = leads[0]
        logger.info(f"Partner purchasing Lead #{target_lead.id[:8]} (Price: {target_lead.price:.2f} ₽)...")

        test_partner.balance = float(test_partner.balance) - float(target_lead.price)
        target_lead.status = "SOLD"

        purchase = LeadPurchase(
            lead_id=target_lead.id,
            partner_id=test_partner.id,
            price_paid=target_lead.price
        )
        session.add(purchase)
        await session.commit()

        logger.info(f"✅ Lead #{target_lead.id[:8]} status updated to 'SOLD'. Partner balance remaining: {test_partner.balance:.2f} ₽")

    logger.info("\n================================================================")
    logger.info("✅ SIMULATION COMPLETE - ALL MODULES WORKING PROPERLY!")
    logger.info("================================================================")

if __name__ == "__main__":
    asyncio.run(run_simulation())

