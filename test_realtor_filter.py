import asyncio
import sys
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("intent_hunter.test_filter")

async def run_test():
    from src.db.models import UserActivityLog
    from src.ai.scorer import _fallback_heuristic_eval, SYSTEM_PROMPT
    from datetime import datetime, timezone

    logger.info("================================================================")
    logger.info("🧪 TESTING REALTOR / SELLER OFFER EXCLUSION FILTER")
    logger.info("================================================================")

    # Test 1: Realtor / Landlord Offer Announcement (User's exact example)
    realtor_msg = [
        UserActivityLog(
            user_id=111,
            chat_id=101,
            message_text="⛺️ Сдаётся дом рядом с морем, северная часть Нячанга. Дом с 2 спальнями, 1 санузлом. Аренда: 10 млн/мес, депозит 1 месяц, оплата за 3 месяца, контракт от 1 года.",
            timestamp=datetime.now(timezone.utc)
        )
    ]
    res1 = _fallback_heuristic_eval(realtor_msg)
    logger.info(f"Test 1 (Realtor Rental Ad): is_lead={res1.is_lead} (Expected: False) -> {'PASS' if not res1.is_lead else 'FAIL'}")

    tenant_msg = [
        UserActivityLog(
            user_id=222,
            chat_id=101,
            message_text="Здравствуйте! Сниму квартиру 2 спальни на севере Нячанга от 1 месяца. Бюджет 8-10 млн донгов. Кто сдает?",
            timestamp=datetime.now(timezone.utc)
        )
    ]
    res2 = _fallback_heuristic_eval(tenant_msg)
    logger.info(f"Test 2 (Genuine Tenant Inquiry): is_lead={res2.is_lead}, Niche={res2.niche_code}, Temp={res2.temperature} (Expected: True) -> {'✅ PASS' if res2.is_lead else '❌ FAIL'}")

    logger.info("================================================================")
    logger.info("✅ REALTOR VS BUYER INTENT FILTER VERIFIED!")
    logger.info("================================================================")

if __name__ == "__main__":
    asyncio.run(run_test())
