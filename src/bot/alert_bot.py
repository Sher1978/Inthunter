import logging
from typing import List, Optional
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
        import asyncio
        asyncio.create_task(run_hourly_superadmin_digest_loop())
        asyncio.create_task(run_partner_onboarding_nudge_loop())
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
        u_prof = up_res.scalar_one_or_none()
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
            async with AsyncSessionLocal() as session:
                msgs_1h = (await session.execute(
                    select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_1h)
                )).scalar() or 0

                leads_1h = (await session.execute(
                    select(func.count(Lead.id)).where(Lead.created_at >= cutoff_1h)
                )).scalar() or 0

                total_logs = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
                total_leads = (await session.execute(select(func.count(Lead.id)))).scalar() or 0

            digest_card = (
                f"📊 <b>ЧАСОВОЙ ОТЧЕТ И СТАТИСТИКА СКАНИРОВАНИЯ</b>\n"
                f"───────────────────────────\n\n"
                f"⏱ <b>Время (Вьетнам UTC+7):</b> {now_vn.strftime('%H:%M')}\n"
                f"💬 <b>Прослушано сообщений за 1 час:</b> <b>{msgs_1h}</b> шт.\n"
                f"🎯 <b>Квалифицировано лидов за 1 час:</b> <b>{leads_1h}</b> шт.\n\n"
                f"📈 <b>Всего сообщений в базе (CDP):</b> <b>{total_logs}</b> шт.\n"
                f"🔥 <b>Всего лидов в маркетплейсе:</b> <b>{total_leads}</b> шт.\n\n"
                f"💡 <i>Автоматические уведомления активны с 09:00 до 00:00 (по Вьетнаму).</i>"
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
