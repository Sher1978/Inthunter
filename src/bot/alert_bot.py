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


_is_polling_active = False

async def run_polling_safe():
    global _is_polling_active, bot, dp
    if _is_polling_active:
        logger.info("Bot polling is already running in this process. Skipping duplicate task.")
        return
    if not bot or not dp:
        return
    _is_polling_active = True
    try:
        logger.info("Clearing old webhooks and starting Aiogram Bot polling loop...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        logger.error(f"Error in Aiogram Bot polling loop: {e}")
    finally:
        _is_polling_active = False


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

    # 1. Save or fetch Lead record in DB
    from sqlalchemy import select
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner, Lead, UserProfile

    lead_id = None
    async with AsyncSessionLocal() as session:
        # Save UserProfile if missing
        up_res = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        if not up_res.scalar_one_or_none():
            u_prof = UserProfile(
                user_id=user_id,
                username=messages[0].username if messages else None,
                first_name=messages[0].first_name if messages else None
            )
            session.add(u_prof)

        # Save Lead record
        l_res = await session.execute(select(Lead).where(Lead.user_id == user_id, Lead.niche_code == niche_code, Lead.status == "AVAILABLE"))
        existing_lead = l_res.scalar_one_or_none()
        if existing_lead:
            lead_id = existing_lead.id
        else:
            new_lead = Lead(
                user_id=user_id,
                niche_code=niche_code,
                temperature=lead_result.temperature,
                confidence_score=lead_result.confidence_score,
                intent_summary=lead_result.intent_summary,
                sales_hook=lead_result.sales_hook,
                price=1.00, # $1.00 USD
                status="AVAILABLE"
            )
            session.add(new_lead)
            await session.commit()
            await session.refresh(new_lead)
            lead_id = new_lead.id

        # Query subscribed partners
        all_partners = list((await session.execute(select(Partner))).scalars().all())

    alert_text = (
        f"🔥 <b>ПОСТУПИЛ НОВЫЙ ГОРЯЧИЙ ЛИД!</b>\n\n"
        f"<b>Категория:</b> {niche_title}\n"
        f"<b>Температура:</b> {lead_result.temperature} (Готовность: {int(lead_result.confidence_score * 100)}%)\n"
        f"<b>Свежесть:</b> Только что\n\n"
        f"📜 <b>История действий пользователя:</b>\n"
        f"{timeline_text}\n\n"
        f"💡 <b>Рекомендация ИИ по продажам (Sales Hook):</b>\n"
        f"«{html.quote(lead_result.sales_hook)}»\n\n"
        f"💰 <b>Стоимость контакта:</b> <b>$1.00 USD</b> (1 контакт)\n"
        f"───────────────────────────"
    )

    logger.info(f"\n=================== LEAD ALERT CARD ===================\n{alert_text}\n=======================================================")

    if not bot:
        return

    # Filter partners subscribed to niche with active monitoring
    subbed_partners = [
        p for p in all_partners 
        if p.is_monitoring_active and p.subscribed_niches and niche_code in p.subscribed_niches
    ]

    from src.bot.keyboards import get_buy_lead_keyboard

    # VIP partners (role == VIP or priority 1) get immediate 0s access
    p1_vips = [p for p in subbed_partners if p.role == "VIP" or (p.niche_priorities or {}).get(niche_code, 3) == 1]
    others = [p for p in subbed_partners if p not in p1_vips]

    # Dispatch to VIPs immediately
    logger.info(f"🚀 Dispatching VIP Early-Access alert to {len(p1_vips)} VIP partners...")
    for partner in p1_vips:
        try:
            buy_kb = get_buy_lead_keyboard(lead_id, 1.00)
            await bot.send_message(
                chat_id=partner.telegram_id,
                text=f"⭐ <b>VIP РАННИЙ ДОСТУП к лиду!</b>\n\n" + alert_text,
                parse_mode="HTML",
                reply_markup=buy_kb
            )
        except Exception as e:
            logger.error(f"Error sending VIP alert to partner {partner.telegram_id}: {e}")

    # Dispatch to other partners
    for partner in others:
        try:
            buy_kb = get_buy_lead_keyboard(lead_id, 1.00)
            if partner.role == "DEMO":
                demo_card = alert_text + "\n\n🔒 <i>Контакты лида скрыты (Демо-доступ). Пополните баланс от $100 или дождитесь модерации админом.</i>"
                await bot.send_message(
                    chat_id=partner.telegram_id,
                    text=demo_card,
                    parse_mode="HTML",
                    reply_markup=buy_kb
                )
            else:
                await bot.send_message(
                    chat_id=partner.telegram_id,
                    text=alert_text,
                    parse_mode="HTML",
                    reply_markup=buy_kb
                )
        except Exception as e:
            logger.error(f"Error sending alert to partner {partner.telegram_id}: {e}")


async def broadcast_debug_scan(
    chat_title: str,
    user_id: int,
    first_name: str,
    username: str,
    text: str,
    total_messages: int = 1
):
    """
    Sends live real-time scanning feed messages to Superadmins/Admins who enabled is_debug_monitoring.
    """
    if not bot:
        return

    from sqlalchemy import select
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner

    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(Partner).where(
                    Partner.role.in_(["SUPERADMIN", "ADMIN"]),
                    Partner.is_debug_monitoring == True
                )
            )
            debug_admins = list(res.scalars().all())

        if not debug_admins:
            return

        u_str = f"@{username}" if username else "без username"
        first_name_clean = html.quote(first_name or "Пользователь")
        chat_clean = html.quote(chat_title or "Групповой чат")
        text_snippet = html.quote(text[:350]) + ("..." if len(text) > 350 else "")

        debug_card = (
            f"🧪 <b>[ТЕСТ-МОНИТОР СКАНИРОВАНИЯ]</b>\n"
            f"───────────────────────────\n"
            f"📍 <b>Чат:</b> {chat_clean}\n"
            f"👤 <b>Автор:</b> {first_name_clean} ({u_str}) | ID: <code>{user_id}</code>\n"
            f"💬 <b>Текст сообщения:</b>\n<i>\"{text_snippet}\"</i>\n\n"
            f"⚙️ <b>Статус:</b> 🟢 Перехвачено ИИ-сканером ({total_messages} сообщений в истории)"
        )

        for admin in debug_admins:
            try:
                await bot.send_message(
                    chat_id=admin.telegram_id,
                    text=debug_card,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error sending debug scan card to admin {admin.telegram_id}: {e}")
    except Exception as e:
        logger.error(f"Error in broadcast_debug_scan: {e}")


async def notify_superadmins_system_alert(message_text: str):
    """
    Sends critical system/scanner alerts to Superadmins.
    """
    if not bot:
        return

    from sqlalchemy import select
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner

    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(Partner).where(Partner.role == "SUPERADMIN")
            )
            superadmins = list(res.scalars().all())

        for sa in superadmins:
            try:
                await bot.send_message(
                    chat_id=sa.telegram_id,
                    text=message_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error sending system alert to superadmin {sa.telegram_id}: {e}")
    except Exception as e:
        logger.error(f"Error in notify_superadmins_system_alert: {e}")


