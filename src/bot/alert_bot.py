import logging
from typing import List
from aiogram import Bot, Dispatcher, html
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.config import settings
from src.ai.schemas import LeadScoringResult
from src.db.models import UserActivityLog
from src.bot.handlers import router

logger = logging.getLogger("intent_hunter.bot")

bot: Bot = None
dp: Dispatcher = None

def init_bot():
    global bot, dp
    raw_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    clean_token = raw_token.strip().strip('"').strip("'")

    if clean_token and clean_token != "mock_bot_token":
        try:
            bot = Bot(token=clean_token)
            dp = Dispatcher()
            dp.include_router(router)
            logger.info(f"Aiogram 3 Bot initialized for token ending in ...{clean_token[-6:]}")
        except Exception as e:
            logger.error(f"Failed to initialize Aiogram Bot: {e}")
    else:
        logger.warning(f"TELEGRAM_BOT_TOKEN empty or mock ('{raw_token}'). Bot initialization skipped.")


async def broadcast_lead_alert(
    user_id: int,
    lead_result: LeadScoringResult,
    messages: List[UserActivityLog]
):
    """
    Formats and delivers lead alert card using Staggered Niche Priority:
    Priority 1 (VIP - 0s delay) -> Priority 2 (High - 30s delay) -> Priority 3 (Standard - 60s delay / Alert Channel).
    """
    niche_labels = {
        "auto_kasko": "Автострахование (КАСКО / ОСАГО)",
        "real_estate": "Недвижимость / Аренда / Покупка",
        "auto_broker": "Автоброкер / Подбор автомобилей",
    }
    niche_code = lead_result.niche_code
    niche_title = niche_labels.get(niche_code, niche_code.upper())
    
    # Format message timeline history
    timeline_lines = []
    for msg in messages[-3:]:
        timestamp_fmt = msg.timestamp.strftime("%d %b %H:%M")
        chat_fmt = msg.chat_title or "Групповой чат"
        timeline_lines.append(f"• <b>{timestamp_fmt}</b> [{chat_fmt}]: <i>\"{html.quote(msg.message_text)}\"</i>")
    
    timeline_text = "\n".join(timeline_lines)
    
    alert_text = (
        f"🔥 <b>ПОСТУПИЛ НОВЫЙ ГОРЯЧИЙ ЛИД!</b>\n\n"
        f"<b>Категория:</b> {niche_title}\n"
        f"<b>Температура:</b> {lead_result.temperature} (Готовность: {int(lead_result.confidence_score * 100)}%)\n"
        f"<b>Свежесть:</b> Только что\n\n"
        f"📜 <b>История действий пользователя:</b>\n"
        f"{timeline_text}\n\n"
        f"💡 <b>Рекомендация ИИ по продажам (Sales Hook):</b>\n"
        f"«{html.quote(lead_result.sales_hook)}»\n\n"
        f"💰 <b>Стоимость контакта:</b> {800 if lead_result.temperature == 'HOT' else 500} ₽\n"
        f"───────────────────────────"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Выкупить лид и получить контакт",
                    callback_data=f"buy_lead:{user_id}"
                )
            ]
        ]
    )

    logger.info(f"\n=================== LEAD ALERT CARD ===================\n{alert_text}\n=======================================================")

    if not bot:
        return

    # Query subscribed partners from DB
    from sqlalchemy import select
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner, Lead

    async with AsyncSessionLocal() as session:
        stmt = select(Partner)
        res = await session.execute(stmt)
        all_partners = list(res.scalars().all())

    # Filter partners subscribed to niche
    subbed_partners = [
        p for p in all_partners 
        if p.subscribed_niches and niche_code in p.subscribed_niches
    ]

    # Group by priority for this niche (1=VIP 0s, 2=High 30s, 3=Standard 60s)
    p1_vips = [p for p in subbed_partners if (p.niche_priorities or {}).get(niche_code, 3) == 1]
    p2_high = [p for p in subbed_partners if (p.niche_priorities or {}).get(niche_code, 3) == 2]
    p3_standard = [p for p in subbed_partners if (p.niche_priorities or {}).get(niche_code, 3) >= 3]

    # 1. Immediate Dispatch to Priority 1 (VIP) Partners (0s delay)
    logger.info(f"🚀 Dispatching VIP Early-Access alert to {len(p1_vips)} Priority 1 partners...")
    for partner in p1_vips:
        try:
            await bot.send_message(
                chat_id=partner.telegram_id,
                text=f"⭐ <b>VIP РАННИЙ ДОСТУП к лиду!</b>\n\n" + alert_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Error sending VIP alert to partner {partner.telegram_id}: {e}")

    # 2. Async Staggered Delivery Task for Priority 2, Priority 3, and Channel
    import asyncio
    async def staggered_delivery():
        # Wait 30 seconds for Priority 2 (High)
        if p2_high:
            await asyncio.sleep(30)
            async with AsyncSessionLocal() as session:
                l_res = await session.execute(select(Lead).where(Lead.user_id == user_id, Lead.niche_code == niche_code))
                lead_obj = l_res.scalar_one_or_none()
                if lead_obj and lead_obj.status == "SOLD":
                    logger.info("Lead purchased during VIP window. Stopping staggered delivery.")
                    return

            logger.info(f"⚡ Dispatching Priority 2 alert to {len(p2_high)} High Priority partners...")
            for partner in p2_high:
                try:
                    await bot.send_message(
                        chat_id=partner.telegram_id,
                        text=alert_text,
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.error(f"Error sending P2 alert to partner {partner.telegram_id}: {e}")

        # Wait remaining 30 seconds for Priority 3 (Standard) & Alert Channel
        if p3_standard or (settings.ALERT_CHANNEL_ID and str(settings.ALERT_CHANNEL_ID) != "0"):
            await asyncio.sleep(30)
            async with AsyncSessionLocal() as session:
                l_res = await session.execute(select(Lead).where(Lead.user_id == user_id, Lead.niche_code == niche_code))
                lead_obj = l_res.scalar_one_or_none()
                if lead_obj and lead_obj.status == "SOLD":
                    logger.info("Lead purchased before general marketplace release.")
                    return

            logger.info(f"📢 Dispatching Standard alert to {len(p3_standard)} partners & alert channel...")
            for partner in p3_standard:
                try:
                    await bot.send_message(
                        chat_id=partner.telegram_id,
                        text=alert_text,
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.error(f"Error sending P3 alert to partner {partner.telegram_id}: {e}")

            if settings.ALERT_CHANNEL_ID and str(settings.ALERT_CHANNEL_ID) != "0":
                try:
                    await bot.send_message(
                        chat_id=settings.ALERT_CHANNEL_ID,
                        text=alert_text,
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.error(f"Error sending channel alert: {e}")

    asyncio.create_task(staggered_delivery())
