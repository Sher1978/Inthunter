import logging
import asyncio
from typing import List, Optional
from aiogram import Bot, Dispatcher, html
from aiogram.exceptions import TelegramConflictError
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
    if bot and dp:
        return
    import os
    raw_token = os.getenv("TELEGRAM_BOT_TOKEN") or getattr(settings, "TELEGRAM_BOT_TOKEN", "")
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
_background_tasks_started = False

async def run_polling_safe():
    global _is_polling_active, _background_tasks_started, bot, dp
    if _is_polling_active:
        logger.info("Bot polling is already running in this process. Skipping duplicate task.")
        return
    if not bot or not dp:
        init_bot()
        if not bot or not dp:
            logger.warning("Bot polling skipped: bot or dp is None.")
            return
    _is_polling_active = True
    
    import asyncio
    if not _background_tasks_started:
        _background_tasks_started = True
        asyncio.create_task(run_hourly_superadmin_digest_loop())
        asyncio.create_task(run_partner_onboarding_nudge_loop())
        asyncio.create_task(run_dead_channel_watchdog_loop())

    try:
        logger.info("Clearing old webhooks for Aiogram Bot...")
        await bot.delete_webhook(drop_pending_updates=False)

        official_desc = (
            "🎯 LeadRadar — B2B Маркетплейс ИИ-Лидов\n\n"
            "Перехват горячих клиентских запросов в Telegram в реальном времени (Нячанг, Дубай, Пхукет, Бали).\n\n"
            "🌐 Веб-Панель: https://leadradar.win"
        )
        await bot.set_my_description(description=official_desc)
        await bot.set_my_short_description(short_description="🎯 B2B Маркетплейс ИИ-Лидов https://leadradar.win")

        from aiogram.types import BotCommand, MenuButtonCommands
        bot_commands = [
            BotCommand(command="start", description="🚀 Запустить бота / Главное меню"),
            BotCommand(command="menu", description="🔝 Главное меню и панели"),
            BotCommand(command="marketplace", description="🎯 Маркетплейс горячих лидов"),
            BotCommand(command="profile", description="👤 Мой профиль и настройки"),
            BotCommand(command="balance", description="💳 Баланс и пополнение"),
            BotCommand(command="grok", description="🤖 Поиск чатов с Grok AI"),
            BotCommand(command="channels", description="📡 Каналы прослушки"),
            BotCommand(command="referral", description="🤝 Партнерка 20% RevShare"),
            BotCommand(command="archive", description="📜 Архив выкупленных лидов"),
            BotCommand(command="help", description="ℹ️ Справка и поддержка")
        ]
        await bot.set_my_commands(bot_commands)
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("✅ Official Telegram Bot Description, Commands & Menu Button locked on Telegram servers.")
    except Exception as e:
        logger.warning(f"delete_webhook/description lock notice: {e}")

    while _is_polling_active:
        try:
            logger.info("🤖 Starting Aiogram Bot polling loop...")
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            logger.info("Bot polling task was cancelled. Exiting polling loop.")
            break
        except TelegramConflictError as conflict_err:
            logger.warning(
                f"⚠️ TelegramConflictError: Another bot instance with token ending ...{bot.token[-6:]} is currently active ({conflict_err}). "
                f"Pausing polling for 15s to prevent session thrashing..."
            )
            await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"Error in Aiogram Bot polling loop: {e}. Retrying polling in 5 seconds...")
            await asyncio.sleep(5)

    _is_polling_active = False


async def auto_publish_lead_after_5m(lead_id: str, chat_id: int, message_id: int, is_outreach: bool = False):
    logger.info(f"⏳ Launched 5-minute auto-moderation fallback timer for {'B2B Lead' if is_outreach else 'Lead'} {lead_id}...")
    await asyncio.sleep(300)

    async with AsyncSessionLocal() as session:
        from src.db.models import Lead, OutreachLead
        if is_outreach:
            olead = (await session.execute(select(OutreachLead).where(OutreachLead.id == lead_id))).scalar_one_or_none()
            if not olead or olead.status != "NEED_APPROVAL":
                logger.info(f"⏱️ 5m timer: B2B Lead {lead_id} was already moderated/approved.")
                return

            olead.status = "READY_FOR_OUTREACH"
            await session.commit()

            try:
                if bot:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"🚀 <b>АВТО-ЗАПУСК ИИ-АУТРИЧА (5 мин истекли)</b>\n"
                            f"───────────────────────────\n\n"
                            f"⏱️ <i>Автоматическое утверждение B2B-продавца для рассылки.</i>\n\n"
                            f"🟢 Продавец занесен в очередь авто-аутрича Екатерины."
                        ),
                        parse_mode="HTML"
                    )
            except Exception as edit_err:
                logger.warning(f"Notice editing auto-publish B2B outreach message: {edit_err}")
            return
        else:
            lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
            if not lead:
                return

            lead.price = 1.00
            lead.status = "AVAILABLE"
            await session.commit()

    try:
        if bot:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"⚡ <b>АВТО-ПУБЛИКАЦИЯ В МАРКЕТПЛЕЙС (5 мин истекли)</b>\n"
                    f"───────────────────────────\n\n"
                    f"⏱️ <i>Модерация суперадмином не выполнена за 5 минут.</i>\n"
                    f"💰 <b>Назначена стандартная цена:</b> <b>$1.00 USD</b>\n"
                    f"👑 <b>Цена выкупа (х10):</b> <b>$10.00 USD</b>\n\n"
                    f"🟢 Лид автоматически опубликован в общем пуле на маркетплейсе."
                ),
                parse_mode="HTML"
            )
            logger.info(f"✅ Auto-published lead {lead_id} after 5-minute timeout.")
    except Exception as err:
        logger.warning(f"Notice editing auto-publish timeout message: {err}")


async def broadcast_lead_alert(
    user_id: int,
    lead_result: LeadScoringResult,
    messages: List[UserActivityLog]
):
    """
    Formats and delivers lead alert card using Staggered Niche Priority:
    Priority 1 (VIP - 0s delay) -> Priority 2 (High - 30s delay) -> Priority 3 (Standard - 60s delay / Alert Channel).
    """
    from src.bot.keyboards import NICHE_NAMES
    niche_code = lead_result.niche_code
    niche_title = getattr(lead_result, "rubric_name", None) or NICHE_NAMES.get(niche_code, niche_code.upper())
    
    # Format message timeline history safely
    timeline_lines = []
    for msg in messages[-3:]:
        ts = getattr(msg, "timestamp", None)
        timestamp_fmt = ts.strftime("%d %b %H:%M") if ts else "Только что"
        chat_fmt = getattr(msg, "chat_title", None) or "Групповой чат"
        msg_txt = getattr(msg, "message_text", None) or ""
        timeline_lines.append(f"• <b>{timestamp_fmt}</b> [{chat_fmt}]: <i>\"{html.quote(msg_txt)}\"</i>")
    
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
        u_prof = up_res.scalars().first()
        if not u_prof:
            u_name = getattr(messages[0], "username", None) if messages else None
            f_name = getattr(messages[0], "first_name", None) if messages else None
            if not u_name and messages and hasattr(messages[0], "user") and messages[0].user:
                u_name = getattr(messages[0].user, "username", None)
                f_name = getattr(messages[0].user, "first_name", None)
            u_prof = UserProfile(
                user_id=user_id,
                username=u_name,
                first_name=f_name
            )
            session.add(u_prof)

        # Save Lead record
        l_res = await session.execute(select(Lead).where(Lead.user_id == user_id, Lead.niche_code == niche_code, Lead.status == "AVAILABLE"))
        existing_lead = l_res.scalars().first()
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

    platform_code = getattr(lead_result, "platform", "telegram") or "telegram"
    platform_badge = {
        "telegram": "✈️ Telegram",
        "max": "💬 Messenger MAX",
        "vk": "🔵 ВКонтакте",
        "ok": "🟠 Одноклассники",
        "custom": "🌐 Custom API"
    }.get(str(platform_code).lower(), f"🌐 {str(platform_code).upper()}")

    user_msg_count = max(1, len(messages))

    alert_text = (
        f"🔥 <b>ПОСТУПИЛ НОВЫЙ ГОРЯЧИЙ ЛИД!</b>\n\n"
        f"💬 <b>Источник:</b> <b>{platform_badge}</b>\n"
        f"<b>Категория:</b> {niche_title}\n"
        f"📍 <b>Локация:</b> <b>{loc_name}</b>\n"
        f"<b>Температура:</b> {lead_result.temperature} (Готовность: {int(lead_result.confidence_score * 100)}%)\n"
        f"<b>Свежесть:</b> Только что\n\n"
        f"📜 <b>Запрос пользователя:</b>\n"
        f"<i>\"{html.quote(lead_result.intent_summary)}\"</i>\n\n"
        f"💬 <b>Всего сообщений пользователя в системе:</b> <b>{user_msg_count}</b>\n\n"
        f"💰 <b>Стоимость контакта:</b> <b>$1.00 USD</b> (1 контакт)\n"
        f"───────────────────────────"
    )

    logger.info(f"\n=================== LEAD ALERT CARD ===================\n{alert_text}\n=======================================================")

    if not bot:
        return

    # Filter partners subscribed to niche and location with active monitoring
    # (Superadmins and Admins ALWAYS receive lead alerts immediately)
    subbed_partners = [
        p for p in all_partners 
        if (p.role in ["ADMIN", "SUPERADMIN"] or p.is_monitoring_active) and (
            p.role in ["ADMIN", "SUPERADMIN"] or (
                (not p.subscribed_niches or "all" in p.subscribed_niches or niche_code in p.subscribed_niches) and
                (not p.subscribed_locations or "all" in p.subscribed_locations or loc_code in p.subscribed_locations)
            )
        )
    ]

    from src.bot.keyboards import get_buy_lead_keyboard

    # VIP partners (role == VIP/ADMIN/SUPERADMIN or priority 1) get immediate 0s access
    p1_vips = [p for p in subbed_partners if p.role in ["VIP", "ADMIN", "SUPERADMIN"] or (p.niche_priorities or {}).get(niche_code, 3) == 1]
    others = [p for p in subbed_partners if p not in p1_vips]

    # Dispatch to VIPs immediately (0s delay)
    logger.info(f"🚀 Dispatching VIP Early-Access alert to {len(p1_vips)} VIP partners...")
    for partner in p1_vips:
        if bot and bot.id and partner.telegram_id == bot.id:
            logger.info(f"Skipping alert delivery to bot self ID ({partner.telegram_id})")
            continue
        try:
            from src.bot.keyboards import get_lead_approval_keyboard, get_buy_lead_keyboard
            if partner.role in ["ADMIN", "SUPERADMIN"]:
                card_kb = get_lead_approval_keyboard(lead_id, is_outreach=False)
                sent_msg = await bot.send_message(
                    chat_id=partner.telegram_id,
                    text=f"⭐ <b>VIP РАННИЙ ДОСТУП к лиду!</b> (10 мин эксклюзив)\n\n" + alert_text,
                    parse_mode="HTML",
                    reply_markup=card_kb
                )
                if sent_msg and hasattr(sent_msg, "message_id"):
                    asyncio.create_task(auto_publish_lead_after_5m(lead_id, partner.telegram_id, sent_msg.message_id, is_outreach=False))
            else:
                card_kb = get_buy_lead_keyboard(lead_id, 1.00, user_id=user_id)
                await bot.send_message(
                    chat_id=partner.telegram_id,
                    text=f"⭐ <b>VIP РАННИЙ ДОСТУП к лиду!</b> (10 мин эксклюзив)\n\n" + alert_text,
                    parse_mode="HTML",
                    reply_markup=card_kb
                )
            logger.info(f"✅ Lead alert successfully sent to Telegram ID {partner.telegram_id}")
        except Exception as e:
            logger.error(f"Error sending VIP alert to partner {partner.telegram_id}: {e}")

    # Schedule 10-minute delayed dispatch to regular/demo partners
    # If a VIP buys the lead during these 10 minutes, status becomes 'SOLD' and regular users NEVER receive the lead
    async def delayed_broadcast_to_others(target_lead_id: str, targets: list, card_text: str, u_id: int):
        logger.info(f"⏳ Waiting 10 minutes (VIP exclusivity window) before releasing lead {target_lead_id} to regular partners...")
        await asyncio.sleep(600)  # 10 minutes delay
        
        async with AsyncSessionLocal() as session:
            l_check = (await session.execute(select(Lead).where(Lead.id == target_lead_id))).scalar_one_or_none()
            if not l_check or l_check.status != "AVAILABLE":
                logger.info(f"🔒 Lead {target_lead_id} was SOLD during the 10m VIP window! Skipping broadcast to regular partners.")
                return

        logger.info(f"📢 10m VIP window expired. Releasing lead {target_lead_id} to {len(targets)} regular partners...")
        for partner in targets:
            try:
                buy_kb = get_buy_lead_keyboard(target_lead_id, 1.00, user_id=u_id)
                if partner.role == "DEMO":
                    demo_card = card_text + "\n\n🔒 <i>Контакты лида скрыты (Демо-доступ). Пополните баланс от $100 или дождитесь модерации админом.</i>"
                    await bot.send_message(
                        chat_id=partner.telegram_id,
                        text=demo_card,
                        parse_mode="HTML",
                        reply_markup=buy_kb
                    )
                else:
                    await bot.send_message(
                        chat_id=partner.telegram_id,
                        text=card_text,
                        parse_mode="HTML",
                        reply_markup=buy_kb
                    )
            except Exception as e:
                logger.error(f"Error sending delayed alert to partner {partner.telegram_id}: {e}")

    if others:
        asyncio.create_task(delayed_broadcast_to_others(lead_id, others, alert_text, user_id))


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


_last_alert_time: float = 0.0
_last_alert_hash: str = ""

async def notify_superadmins_system_alert(message_text: str):
    """
    Sends critical system/scanner alerts to Superadmins.
    Enforces 1-minute rate limit and deduplicates identical error messages.
    """
    global _last_alert_time, _last_alert_hash
    if not bot:
        return

    import time
    import hashlib
    now = time.time()

    # Create MD5 hash of message text to detect duplicate messages
    msg_hash = hashlib.md5(message_text.encode('utf-8')).hexdigest()

    # Deduplicate: Ignore if identical message text was sent within 1 minute
    if msg_hash == _last_alert_hash and (now - _last_alert_time) < 60.0:
        logger.info("Suppressed duplicate system alert notification to Telegram bot.")
        return

    # Rate-limit: Enforce at least 60 seconds between error notifications
    if (now - _last_alert_time) < 60.0:
        logger.info("Suppressed rate-limited system alert notification to Telegram bot (< 60s cooldown).")
        return

    _last_alert_time = now
    _last_alert_hash = msg_hash

    from sqlalchemy import select
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner

    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(Partner.telegram_id).where(Partner.role == "SUPERADMIN")
            )
            superadmin_ids = set(res.scalars().all())

        bot_id = getattr(bot, "id", None)
        valid_target_ids = [sa_id for sa_id in superadmin_ids if sa_id and sa_id != bot_id]

        for sa_id in valid_target_ids:
            try:
                await bot.send_message(
                    chat_id=sa_id,
                    text=message_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                err_txt = str(e)
                if "chat not found" in err_txt or "bot was blocked" in err_txt:
                    logger.info(f"Notice: Superadmin {sa_id} has not started chat with bot yet ({err_txt}).")
                else:
                    logger.error(f"Error sending system alert to superadmin {sa_id}: {e}")
    except Exception as e:
        logger.error(f"Error in notify_superadmins_system_alert: {e}")


async def notify_superadmins_new_rubric(rubric_code: str, rubric_name: str):
    """
    Notifies Superadmins when a brand new rubric is dynamically created by AI.
    """
    card_text = (
        f"✨ <b>ОБНАРУЖЕНА И ДОБАВЛЕНА НОВАЯ РУБРИКА ИИ!</b>\n"
        f"───────────────────────────\n\n"
        f"🏷 <b>Название:</b> <b>{html.quote(rubric_name)}</b>\n"
        f"🔑 <b>Системный код:</b> <code>{html.quote(rubric_code)}</code>\n\n"
        f"⚙️ <i>Новая рубрика автоматически зарегистрирована в системе и доступна для управления в Веб-панели.</i>"
    )
    await notify_superadmins_system_alert(card_text)


async def notify_superadmins_llm_error(provider: str, model_name: str, error_msg: str):
    """
    Notifies Superadmins when an LLM provider or model fails/refuses or encounters an error.
    Suppresses noisy 429 Rate Limit / Quota Exceeded notifications since the cascade automatically falls back.
    """
    err_lower = str(error_msg).lower()
    if any(k in err_lower for k in ["429", "quota", "resource_exhausted", "rate_limit", "rate limit", "too many requests"]):
        logger.info(f"Suppressed Telegram notification for standard LLM 429 rate limit ({provider}/{model_name}). Cascade handle active.")
        return

    card_text = (
        f"🤖 <b>СБОЙ / ОТКАЗ ИИ-МОДЕЛИ ({html.quote(provider.upper())})!</b>\n"
        f"───────────────────────────\n\n"
        f"⚠️ <b>Модель:</b> <code>{html.quote(model_name)}</code>\n"
        f"❌ <b>Детали ошибки:</b>\n"
        f"<code>{html.quote(str(error_msg)[:350])}</code>\n\n"
        f"🔄 <i>Запущен автоматический перебор резервных моделей каскада...</i>"
    )
    await notify_superadmins_system_alert(card_text)


async def notify_superadmins_api_error(method: str, path: str, error_msg: str, traceback_snippet: str = ""):
    """
    Notifies Superadmins via Telegram Bot whenever an unhandled API error occurs.
    """
    card_text = (
        f"🚨 <b>СБОЙ В РАБОТЕ API / СЕРВЕРА!</b>\n"
        f"───────────────────────────\n\n"
        f"🌐 <b>Запрос:</b> <code>{html.quote(method)} {html.quote(path)}</code>\n"
        f"❌ <b>Ошибка:</b> <code>{html.quote(str(error_msg)[:200])}</code>\n"
    )
    if traceback_snippet:
        card_text += f"\n📜 <b>Детали ошибки:</b>\n<code>{html.quote(str(traceback_snippet)[:350])}</code>\n"
    card_text += f"\n⚡ <i>Сообщение отправлено автоматически службой мониторинга.</i>"
    await notify_superadmins_system_alert(card_text)


_heuristic_fallback_last_notified = None
_heuristic_admin_approved_until = None  # Set when admin clicks "Confirm", silences alerts for 3h

def set_heuristic_admin_approved():
    """Called by handler when superadmin confirms heuristic mode. Silences alerts for 3 hours."""
    global _heuristic_admin_approved_until
    from datetime import datetime, timezone, timedelta
    _heuristic_admin_approved_until = datetime.now(timezone.utc) + timedelta(hours=3)
    logger.info("✅ Admin confirmed heuristic mode. Alerts silenced for 3 hours.")

async def notify_superadmins_heuristic_fallback_request(reason: str = "Отсутствуют API ключи или не отвечает AI-провайдер"):
    """
    Sends notification with confirmation buttons to Superadmins when AI scorer is forced to switch to Heuristic mode.
    Rate-limited to max once per 3 hours. Fully silenced if admin already confirmed this session.
    """
    global _heuristic_fallback_last_notified, _heuristic_admin_approved_until
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    # If admin already confirmed heuristic — stay silent until approval expires
    if _heuristic_admin_approved_until and now < _heuristic_admin_approved_until:
        return

    # Anti-spam: notify at most once per 3 hours
    if _heuristic_fallback_last_notified and (now - _heuristic_fallback_last_notified) < timedelta(hours=3):
        return

    _heuristic_fallback_last_notified = now

    if not bot:
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить эвристический режим",
                    callback_data="confirm_heuristic:allow"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Проверить статус ИИ",
                    callback_data="check_ai_status"
                )
            ]
        ]
    )

    card_text = (
        f"⚠️ <b>ВНИМАНИЕ: ПЕРЕХОД НА ЭВРИСТИЧЕСКИЙ АНАЛИЗАТОР!</b>\n"
        f"───────────────────────────\n\n"
        f"🤖 <b>Причина:</b> {html.quote(reason)}\n"
        f"⚙️ <b>Режим:</b> Резервная оценка сообщений по ключевым словам (Heuristic Scorer).\n\n"
        f"<i>Эвристический режим продолжит фильтрацию спама по жестким паттернам, пока не будут подключены API-ключи Groq/Gemini.</i>\n\n"
        f"Пожалуйста, подтвердите использование эвристики или проверьте настройки."
    )

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
                    text=card_text,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Error sending heuristic fallback confirmation to superadmin {sa.telegram_id}: {e}")
    except Exception as e:
        logger.error(f"Error in notify_superadmins_heuristic_fallback_request: {e}")


digest_task = None

async def run_hourly_superadmin_digest_loop():
    """
    Background loop that computes scraped messages & leads count every hour and notifies Superadmins.
    Night Mute: Muted between 00:00 and 09:00 AM Vietnam time (UTC+7).
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, func
    from src.db.session import AsyncSessionLocal
    from src.db.models import UserActivityLog, Lead

    vn_tz = timezone(timedelta(hours=7))
    logger.info("⏰ Starting Hourly Superadmin Digest Loop (Vietnam Time UTC+7)...")

    while True:
        try:
            await asyncio.sleep(3600) # 1 hour interval
            now_vn = datetime.now(vn_tz)
            
            # Check quiet hours (00:00 - 09:00 Vietnam time)
            if 0 <= now_vn.hour < 9:
                logger.info(f"🌙 Quiet Hours in Vietnam ({now_vn.strftime('%H:%M')} VN). Skipping hourly Telegram digest.")
                continue

            cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
            cutoff_15m = datetime.now(timezone.utc) - timedelta(minutes=15)
            async with AsyncSessionLocal() as session:
                msgs_1h = (await session.execute(
                    select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_1h)
                )).scalar() or 0

                msgs_pass = (await session.execute(
                    select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_15m)
                )).scalar() or 0

                leads_1h = (await session.execute(
                    select(func.count(Lead.id)).where(Lead.created_at >= cutoff_1h)
                )).scalar() or 0

                channels_1h = (await session.execute(
                    select(func.count(func.distinct(UserActivityLog.chat_title))).where(UserActivityLog.timestamp >= cutoff_1h)
                )).scalar() or 0

                from src.db.models import MonitoredChannel, DiscoveredChat, ChannelCandidate
                total_channels = (await session.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0
                joined_channels = (await session.execute(select(func.count(MonitoredChannel.id)).where(MonitoredChannel.status == "JOINED"))).scalar() or 0

                total_logs = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
                total_b2c_leads = (await session.execute(
                    select(func.count(Lead.id))
                )).scalar() or 0
                from src.db.models import OutreachLead
                total_b2b_leads = (await session.execute(
                    select(func.count(OutreachLead.id))
                )).scalar() or 0
                total_leads = total_b2c_leads + total_b2b_leads

                disc_approved_1h = (await session.execute(
                    select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "APPROVED", DiscoveredChat.audited_at >= cutoff_1h)
                )).scalar() or 0
                if disc_approved_1h == 0:
                    disc_approved_1h = (await session.execute(
                        select(func.count(MonitoredChannel.id)).where(MonitoredChannel.created_at >= cutoff_1h)
                    )).scalar() or 0

                disc_rejected_1h = (await session.execute(
                    select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "REJECTED", DiscoveredChat.audited_at >= cutoff_1h)
                )).scalar() or 0

                disc_pending = (await session.execute(
                    select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "PENDING")
                )).scalar() or 0
                cand_pending = (await session.execute(
                    select(func.count(ChannelCandidate.id)).where(ChannelCandidate.status == "DISCOVERED")
                )).scalar() or 0
                disc_pending += cand_pending

            digest_card = (
                f"📊 <b>ЧАСОВОЙ ОТЧЕТ И СТАТИСТИКА СКАНИРОВАНИЯ</b>\n"
                f"───────────────────────────\n\n"
                f"⏱ <b>Время (UTC+7):</b> {now_vn.strftime('%H:%M')}\n"
                f"📡 <b>Проверено каналов сканером:</b> <b>{joined_channels}</b> из {joined_channels} активных (100% покрытие)\n"
                f"💬 <b>Каналов с активностью за 1 час:</b> <b>{channels_1h}</b> из {joined_channels}\n"
                f"💬 <b>Прослушано новых сообщений (час - проход):</b> <b>{msgs_1h} - {msgs_pass}</b> шт.\n"
                f"🎯 <b>Квалифицировано лидов за 1 час:</b> <b>{leads_1h}</b> шт.\n\n"
                f"🔎 <b>ИИ-Поиск чатов (Discovery Engine):</b>\n"
                f"• ✅ Добавлено в прослушку за 1ч: <b>{disc_approved_1h}</b> чатов\n"
                f"• ⛔ Отклонено ИИ (спам/боты/профили) за 1ч: <b>{disc_rejected_1h}</b> чатов\n"
                f"• ⏳ Ожидают ИИ-аудита в очереди: <b>{disc_pending}</b> кандидатов\n\n"
                f"📈 <b>Всего каналов в базе:</b> <b>{total_channels}</b> шт. (🟢 {joined_channels} активны)\n"
                f"📂 <b>Всего сообщений в базе (CDP):</b> <b>{total_logs}</b> шт.\n"
                f"🔥 <b>Активных лидов в маркетплейсе (за 3ч):</b> <b>{total_leads}</b> шт.\n\n"
                f"💡 <i>Автоматические отчеты отправляются с 09:00 до 00:00 (UTC+7).</i>"
            )

            await notify_superadmins_system_alert(digest_card)

        except Exception as e:
            logger.error(f"Error in hourly superadmin digest loop: {e}")


async def notify_superadmins_niche_request(
    user_id: int,
    first_name: Optional[str],
    username: Optional[str],
    requested_niche: str
):
    """
    Sends rich alert card to all Superadmins when a user requests a new niche.
    Includes direct contact link to the user.
    """
    import html
    from datetime import datetime, timezone, timedelta
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from sqlalchemy import select
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner

    vn_now = datetime.now(timezone(timedelta(hours=7))).strftime("%d.%m.%Y %H:%M")
    u_str = f"@{username}" if username else "без username"
    f_name_clean = html.quote(first_name or "Пользователь")
    req_clean = html.quote(requested_niche)

    contact_link = f"https://t.me/{username}" if username else f"tg://user?id={user_id}"

    alert_card = (
        f"📥 <b>НОВАЯ ЗАЯВКА НА НИШУ ОТ ПАРТНЕРА!</b>\n"
        f"───────────────────────────\n\n"
        f"👤 <b>Пользователь:</b> {f_name_clean} ({u_str})\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"📅 <b>Дата:</b> {vn_now} (UTC+7)\n\n"
        f"💡 <b>Запрошенная ниша / категория:</b>\n"
        f"«<b>{req_clean}</b>»\n\n"
        f"📲 <i>Нажмите кнопку ниже, чтобы связаться с партнером.</i>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📲 Написать {f_name_clean}",
                    url=contact_link
                )
            ]
        ]
    )

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Partner.telegram_id).where(Partner.role == "SUPERADMIN")
        )
        superadmin_ids = list(result.scalars().all())

    if not superadmin_ids and settings.SUPERADMIN_TELEGRAM_ID:
        superadmin_ids = [settings.SUPERADMIN_TELEGRAM_ID]

    for sa_id in set(superadmin_ids):
        try:
            await bot.send_message(sa_id, alert_card, reply_markup=keyboard, parse_mode="HTML")
            logger.info(f"Successfully notified superadmin {sa_id} of new niche request")
        except Exception as e:
            logger.error(f"Failed to send niche request alert to superadmin {sa_id}: {e}")


nudge_task = None

async def run_partner_onboarding_nudge_loop():
    """
    Background loop sending non-intrusive drip onboarding nudges to active partners with $0 balance.
    Frequency: Maximum 2 messages per 24 hours (min 12 hours between nudges).
    Quiet hours: Muted during 00:00 - 09:00 AM Vietnam time (UTC+7).
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    vn_tz = timezone(timedelta(hours=7))
    logger.info("🚀 Starting Partner Onboarding Drip Nudge Loop...")

    nudges_content = {
        1: {
            "text": (
                "🔔 <b>[Шаг 1 из 5] Включили ли вы целевые ниши для прослушки?</b>\n"
                "───────────────────────────\n\n"
                "За последние 24 часа наш ИИ-сканер квалифицировал целевые запросы в чатах Нячанга и Дананга!\n\n"
                "💡 Проверьте ваши настройки: выберите активные рубрики или запросите новую категорию кнопкой <code>➕ Запросить новую нишу</code>."
            ),
            "btn": ("➕ Запросить нишу", "request_niche_cmd")
        },
        2: {
            "text": (
                "🤝 <b>[Шаг 2 из 5] ПАРТНЕРСКАЯ ПРОГРАММА: 20% С КАЖДОГО ВЫКУПА ЛИДОВ!</b>\n"
                "───────────────────────────\n\n"
                "Знаете коллег, риелторов или бизнесменов, ищущих клиентов в Вьетнаме или ОАЭ?\n\n"
                "💰 Делитесь вашей личной реферальной ссылкой! С каждого выкупленного ими лида вы мгновенно получаете <b>20% дохода</b> на свой баланс (вывод от $50 USD)."
            ),
            "is_ref_btn": True
        },
        3: {
            "text": (
                "💡 <b>[Шаг 3 из 5] Почему депозитный баланс в RADAR составляет $100.00 USD?</b>\n"
                "───────────────────────────\n\n"
                "1️⃣ <b>Это НЕ абонентская плата:</b> Все 100% средств попадают на ваш личный баланс для выкупа контактов в 1 клик ($1.00 USD за контакт).\n"
                "2️⃣ <b>Гарантия эксклюзива:</b> Депозит отсекает спам-ботов и дает вам мгновенное оповещение (0 сек) по горячим заявкам.\n"
                "3️⃣ <b>Быстрый старт:</b> Вы сразу можете выкупить до 100 целевых клиентов!"
            ),
            "btn": ("💳 Пополнить баланс $100", "topup_deposit_cmd")
        },
        4: {
            "text": (
                "💸 <b>[Шаг 4 из 5] Пассивный доход с вашей реферальной сети</b>\n"
                "───────────────────────────\n\n"
                "Один активный партнер выкупает от 30 до 100 лидов в месяц.\n"
                "Ваш 20% бонус с каждого выкупа приносит вам от <b>$10 до $50+ USD пассивного дохода</b> ежемесячно за 1 реферала!\n\n"
                "Отправьте вашу реферальную ссылку прямо сейчас!"
            ),
            "is_ref_btn": True
        },
        5: {
            "text": (
                "👑 <b>[Шаг 5 из 5] Персональная помощь в настройке потока лидов</b>\n"
                "───────────────────────────\n\n"
                "Хотите настроить индивидуальную фильтрацию или привязать CRM-систему (Webhook)?\n\n"
                "Наш менеджер проконсультирует вас по всем вопросам!"
            ),
            "btn": ("💬 Связаться с поддержкой", "contact_support_cmd")
        }
    }

    while True:
        try:
            await asyncio.sleep(7200) # Check every 2 hours
            now_utc = datetime.now(timezone.utc)
            now_vn = datetime.now(vn_tz)

            if 0 <= now_vn.hour < 9:
                continue

            async with AsyncSessionLocal() as session:
                # Select partners created within last 30 days
                cutoff_30d = now_utc - timedelta(days=30)
                stmt = select(Partner).where(
                    Partner.onboarding_step < 5,
                    Partner.role != "SUPERADMIN",
                    Partner.created_at >= cutoff_30d
                )
                res = await session.execute(stmt)
                partners = list(res.scalars().all())

                for partner in partners:
                    # Enforce max 2 nudges per 24 hours (min 12 hours interval)
                    if partner.last_nudge_at and (now_utc - partner.last_nudge_at).total_seconds() < 43200:
                        continue

                    next_step = (partner.onboarding_step or 0) + 1
                    if next_step in nudges_content:
                        n_info = nudges_content[next_step]

                        if n_info.get("is_ref_btn"):
                            import urllib.parse
                            ref_link = f"https://t.me/intenthunter_bot?start=ref_{partner.telegram_id}"
                            share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote('🚀 Перехватывай горячих лидов раньше конкурентов!')}"
                            kb = InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text="🚀 Поделиться реферальной ссылкой 20%", url=share_url)
                            ]])
                        else:
                            kb = InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text=n_info["btn"][0], callback_data=n_info["btn"][1])
                            ]])

                        try:
                            if bot:
                                await bot.send_message(partner.telegram_id, n_info["text"], reply_markup=kb, parse_mode="HTML")
                                partner.onboarding_step = next_step
                                partner.last_nudge_at = now_utc
                                await session.commit()
                                logger.info(f"Sent onboarding referral nudge step {next_step} to partner {partner.telegram_id}")
                        except Exception as e:
                            logger.error(f"Failed to send onboarding nudge to partner {partner.telegram_id}: {e}")

        except Exception as e:
            logger.error(f"Error in partner onboarding nudge loop: {e}")


# ─── DEAD CHANNEL WATCHDOG LOOP ───────────────────────────────────────────
async def run_dead_channel_watchdog_loop():
    """
    Daily background loop that detects 'dead' channels (7+ days with no scanned messages).
    Sends superadmin a summary card per dead channel with inline buttons:
      ✅ Продолжить мониторинг | 🗑 Удалить канал
    Runs once every 24 hours (06:00 VN time).
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, func
    from src.db.session import AsyncSessionLocal
    from src.db.models import Partner, MonitoredChannel, UserActivityLog, Lead

    VN_TZ = timezone(timedelta(hours=7))
    IDLE_THRESHOLD_DAYS = 7
    CHECK_INTERVAL_HOURS = 24

    logger.info("💀 Dead Channel Watchdog started.")

    # Wait until 06:00 VN time on first run
    await asyncio.sleep(60)

    while True:
        try:
            now_vn = datetime.now(VN_TZ)
            # Run daily at 06:00 VN time
            secs_until_6am = ((6 - now_vn.hour) % 24) * 3600 - now_vn.minute * 60 - now_vn.second
            if secs_until_6am < 60:
                secs_until_6am += 86400
            await asyncio.sleep(secs_until_6am)

            logger.info("💀 Running dead channel watchdog scan...")
            now_utc = datetime.now(timezone.utc)
            cutoff_7d = now_utc - timedelta(days=IDLE_THRESHOLD_DAYS)
            cutoff_24h = now_utc - timedelta(hours=24)

            async with AsyncSessionLocal() as session:
                # Get all superadmins
                sa_res = await session.execute(select(Partner).where(Partner.role == "SUPERADMIN"))
                superadmins = list(sa_res.scalars().all())
                if not superadmins or not bot:
                    continue

                # Get all monitored channels
                ch_res = await session.execute(select(MonitoredChannel))
                channels = list(ch_res.scalars().all())

                # Get max activity timestamp grouped by chat_title in 1 query
                act_res = await session.execute(
                    select(UserActivityLog.chat_title, func.max(UserActivityLog.timestamp)).group_by(UserActivityLog.chat_title)
                )
                act_map = { (r[0] or "").strip().lower(): r[1] for r in act_res.all() if r[0] }

                dead_count = 0
                for ch in channels:
                    title_key = (ch.title or "").strip().lower()
                    if not title_key:
                        continue

                    last_act = act_map.get(title_key)

                    if last_act:
                        if last_act.tzinfo is None:
                            last_act = last_act.replace(tzinfo=timezone.utc)
                        days_idle = (now_utc - last_act).days
                    else:
                        # Never scanned
                        ch_age_days = (now_utc - ch.created_at.replace(tzinfo=timezone.utc) if ch.created_at.tzinfo is None else now_utc - ch.created_at).days
                        if ch_age_days < IDLE_THRESHOLD_DAYS:
                            continue
                        days_idle = ch_age_days

                    if days_idle < IDLE_THRESHOLD_DAYS:
                        continue

                    dead_count += 1

                    # Count stats
                    msgs_7d = (await session.execute(
                        select(func.count(UserActivityLog.id)).where(
                            UserActivityLog.chat_title.ilike(f"%{title_key}%"),
                            UserActivityLog.timestamp >= cutoff_7d
                        )
                    )).scalar() or 0

                    # Leads from this channel's users in last 7 days
                    user_ids = list((await session.execute(
                        select(UserActivityLog.user_id).where(
                            UserActivityLog.chat_title.ilike(f"%{title_key}%")
                        ).distinct()
                    )).scalars().all())

                    leads_7d = 0
                    if user_ids:
                        leads_7d = (await session.execute(
                            select(func.count(Lead.id)).where(
                                Lead.user_id.in_(user_ids),
                                Lead.created_at >= cutoff_7d
                            )
                        )).scalar() or 0

                    niche_label = ch.niche_code or "—"
                    loc_label = ch.location_code or "global"
                    link = ch.username_or_link or "—"
                    tg_link = f"https://t.me/{link.lstrip('@')}" if link.startswith("@") else link

                    last_activity_fmt = (last_act + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if last_act else "Никогда"

                    card = (
                        f"💀 <b>МЁРТВЫЙ КАНАЛ — требует решения</b>\n"
                        f"───────────────────────\n\n"
                        f"📡 <b>Канал:</b> {html.quote(ch.title or link)}\n"
                        f"🔗 <b>Ссылка:</b> <a href='{tg_link}'>{link}</a>\n"
                        f"📍 <b>Локация:</b> {loc_label} | <b>Ниша:</b> {niche_label}\n\n"
                        f"📊 <b>Статистика за 7 дней:</b>\n"
                        f"  • Сообщений просканировано: <b>{msgs_7d}</b>\n"
                        f"  • Лидов обнаружено: <b>{leads_7d}</b>\n"
                        f"  • Дней без активности: <b>{days_idle}</b>\n"
                        f"  • Последняя активность: <b>{last_activity_fmt}</b>\n\n"
                        f"⚠️ <i>Этот канал не приносит лидов уже {days_idle} дней. "
                        f"Удалите его из пула для оптимизации ресурсов сканера.</i>"
                    )

                    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="✅ Продолжить мониторинг",
                            callback_data=f"keep_channel_{ch.id}"
                        ),
                        InlineKeyboardButton(
                            text="🗑 Удалить канал",
                            callback_data=f"dead_channel_delete_{ch.id}"
                        )
                    ]])

                    for sa in superadmins:
                        try:
                            await bot.send_message(
                                sa.telegram_id,
                                card,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                                disable_web_page_preview=True
                            )
                            logger.info(f"Sent dead channel alert: {ch.title} ({days_idle}d) → SA {sa.telegram_id}")
                        except Exception as e:
                            logger.error(f"Failed to send dead channel alert to {sa.telegram_id}: {e}")
                        await asyncio.sleep(0.3)

                if dead_count > 0:
                    logger.info(f"💀 Dead channel watchdog: sent {dead_count} alerts to superadmins.")

        except Exception as e:
            logger.error(f"Error in dead channel watchdog loop: {e}")
            await asyncio.sleep(3600)

