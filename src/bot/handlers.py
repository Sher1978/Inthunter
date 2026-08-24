import io
import logging
import qrcode
from typing import Union
from aiogram import Router, F, html
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, BufferedInputFile
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from src.config import settings
from src.db.session import AsyncSessionLocal
from src.db.models import Partner, Lead, LeadPurchase, UserProfile, UserActivityLog, MonitoredChannel
from src.bot.keyboards import (
    get_main_reply_keyboard,
    get_niche_inline_keyboard,
    get_topup_keyboard,
    get_channels_inline_keyboard,
    get_delete_channels_keyboard,
    get_moderation_inline_keyboard,
    get_buy_lead_keyboard,
    get_superadmin_role_menu_keyboard,
    get_user_role_edit_keyboard,
    get_staff_request_keyboard,
    get_grok_candidate_keyboard,
    get_grok_next_batch_keyboard,
    NICHE_NAMES
)

logger = logging.getLogger("intent_hunter.bot_handlers")
router = Router()

ROLE_LABELS = {
    "DEMO": "🆕 DEMO (Демо)",
    "REGULAR": "🔵 REGULAR (Регулярный)",
    "VIP": "⭐ VIP (ВИП)",
    "ADMIN": "🔑 ADMIN (Администратор)",
    "SUPERADMIN": "👑 SUPERADMIN (Суперадминистратор)"
}

class RoleSearchForm(StatesGroup):
    waiting_for_query = State()

class AddChannelForm(StatesGroup):
    waiting_for_link = State()

class GrokSearchForm(StatesGroup):
    active_dialog = State()

class DiscoveryForm(StatesGroup):
    waiting_for_keyword = State()

class ReferralWithdrawForm(StatesGroup):
    waiting_for_details = State()

class ConsultForm(StatesGroup):
    waiting_for_niche = State()
    waiting_for_budget = State()
    waiting_for_phone = State()


SUPERADMIN_IDS = [8866001783, 260669598]

async def get_or_create_partner(session: AsyncSession, telegram_id: int, first_name: str = "", username: str = "") -> Partner:
    """
    Safely retrieves Partner from DB. If missing, auto-creates record.
    If telegram_id or username matches Superadmin config, auto-assigns SUPERADMIN role with $1000 balance!
    """
    user_username = (username or "").lower()
    is_superadmin = (telegram_id in SUPERADMIN_IDS) or any(k in user_username for k in ["sherlockdxb", "sher1978", "sherlock_cars_uae", "sher"])

    stmt = select(Partner).where(Partner.telegram_id == telegram_id)
    partner = (await session.execute(stmt)).scalar_one_or_none()

    if not partner:
        partner = Partner(
            telegram_id=telegram_id,
            company_name=f"Компания {first_name or 'Ihor Sher'}",
            role="SUPERADMIN" if is_superadmin else "DEMO",
            moderation_status="APPROVED",
            balance=1000.00 if is_superadmin else 10.00,
            subscribed_niches=["real_estate", "bike_rent", "currency_exchange", "services_visa", "auto_kasko"],
            is_monitoring_active=True
        )
        session.add(partner)
        await session.commit()
        await session.refresh(partner)
    elif is_superadmin and partner.role != "SUPERADMIN":
        partner.role = "SUPERADMIN"
        partner.moderation_status = "APPROVED"
        partner.balance = max(float(partner.balance or 0), 1000.00)
        await session.commit()
        await session.refresh(partner)

    return partner


@router.message(Command("rescan_hour"))
async def cmd_rescan_hour(message: Message):
    """Superadmin command to manually trigger 1-hour forced rescan across all monitored channels."""
    telegram_id = message.from_user.id
    user_username = (message.from_user.username or "").lower()
    is_superadmin = (telegram_id in SUPERADMIN_IDS) or any(k in user_username for k in ["sherlockdxb", "sher1978", "sherlock_cars_uae", "sher"])

    if not is_superadmin:
        await message.answer("⚠️ Эта команда доступна только суперадминистраторам.")
        return

    await message.answer("⚡ <b>Запуск принудительного пересканирования...</b>\n\nСбрасываем указатели чтения и перечитываем все сообщения за последний 1 час по всем каналам и группам.", parse_mode="HTML")

    try:
        from src.api.app import ingestor
        if ingestor:
            ch_count = await ingestor.force_rescan_past_hour()
            await message.answer(f"✅ <b>Пересканирование успешно запущено!</b>\n\nПриоритетный опрос выполняется для <b>{ch_count}</b> каналов/групп. Данные в реальном времени поступают в «Онлайн Мониторинг».", parse_mode="HTML")
        else:
            await message.answer("⚠️ Модуль сборщика (Ingestor) в данный момент не запущен.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка при запуске пересканирования: <code>{html.quote(str(e))}</code>", parse_mode="HTML")


@router.message(Command("menu"))
@router.message(F.text == "🔝 Главное меню")
@router.message(F.text == "⏩ Пропустить")
@router.message(F.text == "⏩ Пропустить и открыть Главное меню")
@router.message(F.text == "Пропустить")
async def cmd_menu_handler(message: Message, state: FSMContext = None):
    if state:
        await state.clear()
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    user_username = (message.from_user.username or "").lower()
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, first_name, user_username)
        role = partner.role if partner else "DEMO"
        is_mon = partner.is_monitoring_active if partner else True

    await message.answer(
        "🎯 <b>Главное меню панели управления LeadRADAR восстановлено:</b>",
        reply_markup=get_main_reply_keyboard(is_mon, role),
        parse_mode="HTML"
    )

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext = None):
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    user_username = (message.from_user.username or "").lower()

    # Check deep link arguments
    cmd_parts = message.text.split()
    deep_link_arg = cmd_parts[1].lower() if len(cmd_parts) > 1 else ""
    is_staff_invite = deep_link_arg in ["staff_invite", "staff", "invite"]

    if deep_link_arg.startswith("consult") or deep_link_arg.startswith("onboarding") or deep_link_arg.startswith("bonus") or deep_link_arg in ["demo", "start"]:
        if state:
            await start_consult_form(message, state)
            return
    elif state:
        await state.clear()

    # 1. WEB LOGIN FLOW: browser sent user to bot to confirm login
    if deep_link_arg.startswith("weblogin_"):
        token = cmd_parts[1][len("weblogin_"):]  # preserve original case
        import time, os
        from src.api.tma_auth import _WEB_LOGIN_TOKENS, create_jwt
        async with AsyncSessionLocal() as session:
            partner = await get_or_create_partner(session, telegram_id, first_name, user_username)
            jwt = create_jwt(telegram_id, partner.role, partner.id)

        # Pre-confirm token on server so instant click or browser poll works seamlessly!
        entry = _WEB_LOGIN_TOKENS.get(token)
        if not entry:
            _WEB_LOGIN_TOKENS[token] = {
                "telegram_id": telegram_id,
                "expires_at": time.time() + 300,
                "confirmed": True,
                "jwt": jwt
            }
        else:
            entry["confirmed"] = True
            entry["telegram_id"] = telegram_id
            entry["jwt"] = jwt

        redirect_url = f"https://inthunter-production.up.railway.app/api/tma/web-login-redirect?token={token}"

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Вход подтверждён! Открыть Маркетплейс",
                    url=redirect_url
                )
            ]
        ])
        await message.answer(
            f"🔑 <b>Подтверждение входа в Веб-Маркетплейс</b>\n\n"
            f"Вход с вашего аккаунта <b>@{html.quote(user_username or str(telegram_id))}</b> подтверждён!\n\n"
            f"✅ Нажмите кнопку ниже для моментального перехода в Маркетплейс:",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    # ──────────────────────────────────────────────────────────────────────

    # Always ensure partner profile exists
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, first_name, user_username)

        # Process Referral link (e.g. /start ref_12345 or /start 12345)
        if (deep_link_arg.startswith("ref_") or deep_link_arg.isdigit()) and not partner.referred_by_id:
            ref_raw = deep_link_arg.replace("ref_", "").strip()
            if ref_raw.isdigit():
                ref_tg_id = int(ref_raw)
                if ref_tg_id != telegram_id:
                    ref_stmt = select(Partner).where(Partner.telegram_id == ref_tg_id)
                    ref_partner = (await session.execute(ref_stmt)).scalar_one_or_none()
                    if ref_partner and ref_partner.id != partner.id:
                        partner.referred_by_id = ref_partner.id
                        await session.commit()
                        logger.info(f"Partner {telegram_id} linked to referrer {ref_partner.telegram_id}")

        # Save/update UserProfile
        up_stmt = select(UserProfile).where(UserProfile.user_id == telegram_id)
        u_prof = (await session.execute(up_stmt)).scalar_one_or_none()
        if not u_prof:
            u_prof = UserProfile(
                user_id=telegram_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            session.add(u_prof)
            await session.commit()
        elif message.from_user.username and u_prof.username != message.from_user.username:
            u_prof.username = message.from_user.username
            await session.commit()

        # If user joined via staff QR code invite, broadcast staff application to Superadmins
        if is_staff_invite:
            superadmins_res = await session.execute(
                select(Partner).where(Partner.role == "SUPERADMIN")
            )
            superadmins = list(superadmins_res.scalars().all())

            from src.bot.alert_bot import bot
            if bot:
                staff_card = (
                    f"📲 <b>НОВАЯ ЗАЯВКА НА ДОБАВЛЕНИЕ ПЕРСОНАЛА (по QR-коду)!</b>\n\n"
                    f"<b>Имя:</b> {html.quote(first_name)}\n"
                    f"<b>Username:</b> @{message.from_user.username or 'отсутствует'}\n"
                    f"<b>Telegram ID:</b> <code>{telegram_id}</code>\n"
                    f"<b>Текущий статус:</b> {partner.role} (Ожидает назначения роли)\n\n"
                    f"Выберите роль для нового сотрудника:"
                )
                for sa in superadmins:
                    try:
                        await bot.send_message(
                            chat_id=sa.telegram_id,
                            text=staff_card,
                            reply_markup=get_staff_request_keyboard(telegram_id),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Error sending staff request card to superadmin {sa.telegram_id}: {e}")

        sn = partner.subscribed_niches or []
        if not sn:
            partner.subscribed_niches = ["all"]
            await session.commit()

        sl = partner.subscribed_locations or []
        if not sl:
            partner.subscribed_locations = ["all"]
            await session.commit()

    # 2. DEEP LINK ROUTING (Direct feature entry via /start argument)
    if deep_link_arg in ["deposit", "topup", "balance", "pay"]:
        await show_balance(message)
        return

    if deep_link_arg in ["leads", "marketplace", "shop", "market"]:
        await show_leads_marketplace_handler(message)
        return

    if deep_link_arg in ["profile", "me", "settings"]:
        await show_profile(message)
        return

    if deep_link_arg in ["stats", "analytics", "health", "scanner"]:
        await show_analytics_menu_handler(message)
        return

    if deep_link_arg.startswith("lead_") or deep_link_arg.startswith("buy_"):
        lead_id_str = deep_link_arg.replace("lead_", "").replace("buy_", "").strip()
        if lead_id_str.isdigit():
            lead_id = int(lead_id_str)
            async with AsyncSessionLocal() as session:
                lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
            if lead:
                rubric_label = NICHE_NAMES.get(lead.niche_code, lead.niche_code)
                conf_pct = int((lead.confidence_score or 0.85) * 100)
                lead_card = (
                    f"🏷️ <b>{html.quote(rubric_label)}</b> | 🔥 <b>{lead.temperature} ({conf_pct}%)</b>\n\n"
                    f"💬 <i>\"{html.quote(lead.intent_summary)}\"</i>\n\n"
                    f"💡 <b>Sales Hook:</b> «{html.quote(lead.sales_hook)}»\n"
                    f"💰 Стоимость контакта: <b>$1.00 USD</b>"
                )
                kb = get_buy_lead_keyboard(lead.id, float(lead.price or 1.00))
                await message.answer(lead_card, reply_markup=kb, parse_mode="HTML")
                return

    if deep_link_arg in ["grok", "search", "find"]:
        await start_grok_search(message, None)
        return

    # 3. ONBOARDING & MAIN CONTROL PANEL
    role_str = ROLE_LABELS.get(partner.role, partner.role)
    is_monitoring = partner.is_monitoring_active

    if partner.onboarding_step == 0:
        onboarding_card = (
            f"🎯 <b>Добро пожаловать в RADAR — B2B Маркетплейс ИИ-Лидов!</b>\n"
            f"───────────────────────────\n\n"
            f"👋 Здравствуйте, <b>{html.quote(first_name)}</b>!\n\n"
            f"Мы в реальном времени перехватываем горячие запросы клиентов из 700+ целевых соообществ (Нячанг, Дубай, Пхукет, Бали).\n\n"
            f"📋 <b>Шаг 1 из 2: Выберите Ниши и Рубрики</b>\n"
            f"Отметьте галочками категории клиентов, которые вас интересуют (можно выбрать все или несколько):"
        )
        kb = get_niche_inline_keyboard(partner.subscribed_niches, is_onboarding=True)
        await message.answer(
            "🎯 <b>Главное меню панели управления LeadRADAR активировано</b>",
            reply_markup=get_main_reply_keyboard(is_monitoring, partner.role),
            parse_mode="HTML"
        )
        await message.answer(onboarding_card, reply_markup=kb, parse_mode="HTML")
        return

    welcome_extra = ""
    if is_staff_invite:
        welcome_extra = "\n\n📲 <b>Заявка на добавление персонала отправлена Суперадминистратору!</b> Ожидайте назначения вашей роли."

    onboarding_card = (
        f"🎯 <b>RADAR AI Lead Engine — Панель Управления</b>\n"
        f"───────────────────────────\n\n"
        f"👋 С возвращением, <b>{html.quote(first_name)}</b>!\n"
        f"<b>Статус:</b> {role_str} | <b>Баланс:</b> <b>${partner.balance:.2f} USD</b>{welcome_extra}\n\n"
        f"💡 Используйте меню ниже для выкупа лидов и настройки подписок."
    )

    await message.answer(
        onboarding_card,
        reply_markup=get_main_reply_keyboard(is_monitoring, partner.role),
        parse_mode="HTML"
    )


# ────────────────────────────────────────────────────────────────────────────
# LANDING PAGE CONSULTATION APPLICATION FLOW & ONBOARDING
# ────────────────────────────────────────────────────────────────────────────
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

async def start_consult_form(message: Message, state: FSMContext):
    """Starts the 3-step consultation application flow from landing page."""
    await state.set_state(ConsultForm.waiting_for_niche)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ВСЕ НИШИ И РУБРИКИ", callback_data="cs_niche:all")],
        [InlineKeyboardButton(text="🏠 Недвижимость", callback_data="cs_niche:real_estate"), InlineKeyboardButton(text="🛵 Аренда авто & байков", callback_data="cs_niche:bike_rent")],
        [InlineKeyboardButton(text="💱 Обмен валюты & Финтех", callback_data="cs_niche:currency_exchange"), InlineKeyboardButton(text="🛂 Визы & ВНЖ", callback_data="cs_niche:services_visa")],
        [InlineKeyboardButton(text="🚗 КАСКО / ОСАГО Страхование", callback_data="cs_niche:auto_kasko"), InlineKeyboardButton(text="🩺 Медицина & Beauty", callback_data="cs_niche:medical")],
        [InlineKeyboardButton(text="💻 B2B & IT Разработка", callback_data="cs_niche:b2b"), InlineKeyboardButton(text="🚚 Логистика & Доставка", callback_data="cs_niche:logistics")],
        [InlineKeyboardButton(text="🔮 ДРУГОЕ (Индивидуально)", callback_data="cs_niche:other")]
    ])
    await message.answer(
        "🚀 <b>Запуск ИИ-Перехватчика LeadRaDaR</b>\n"
        "───────────────────────────\n\n"
        "🎁 <b>Вам автоматически начислено $10.00 на баланс</b> для бесплатного тестирования перехвата лидов!\n\n"
        "📋 <b>Шаг 1 из 3: Выберите вашу нишу бизнеса:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(ConsultForm.waiting_for_niche, F.data.startswith("cs_niche:"))
async def process_consult_niche(callback: CallbackQuery, state: FSMContext):
    niche_code = callback.data.split(":", 1)[1]
    niche_labels = {
        "all": "🌐 ВСЕ НИШИ И РУБРИКИ",
        "other": "🔮 ДРУГОЕ (Индивидуально)",
        "real_estate": "🏠 Недвижимость",
        "bike_rent": "🛵 Аренда авто & байков",
        "currency_exchange": "💱 Обмен валюты & Финтех",
        "services_visa": "🛂 Визы & ВНЖ",
        "auto_kasko": "🚗 КАСКО / ОСАГО Страхование",
        "medical": "🩺 Медицина & Beauty",
        "b2b": "💻 B2B & IT Разработка",
        "logistics": "🚚 Логистика & Доставка"
    }
    niche_name = niche_labels.get(niche_code, NICHE_NAMES.get(niche_code, niche_code))
    await state.update_data(niche_code=niche_code, niche_name=niche_name)
    await state.set_state(ConsultForm.waiting_for_budget)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 До $1,000 / мес", callback_data="cs_budget:up_to_1k")],
        [InlineKeyboardButton(text="💰 $1,000 – $5,000 / мес", callback_data="cs_budget:1k_5k")],
        [InlineKeyboardButton(text="💰 $5,000+ / мес", callback_data="cs_budget:5k_plus")]
    ])
    await callback.message.edit_text(
        f"✅ <b>Ниша:</b> {niche_name}\n"
        f"───────────────────────────\n\n"
        f"📊 <b>Шаг 2 из 3: Укажите ваш текущий рекламный бюджет в месяц:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(ConsultForm.waiting_for_budget, F.data.startswith("cs_budget:"))
async def process_consult_budget(callback: CallbackQuery, state: FSMContext):
    budget_code = callback.data.split(":", 1)[1]
    budget_map = {
        "up_to_1k": "До $1,000 / мес",
        "1k_5k": "$1,000 – $5,000 / мес",
        "5k_plus": "$5,000+ / мес"
    }
    budget_str = budget_map.get(budget_code, budget_code)
    await state.update_data(budget_str=budget_str)
    await state.set_state(ConsultForm.waiting_for_phone)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться контактом", request_contact=True)],
            [KeyboardButton(text="⏩ Пропустить и открыть Главное меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    kb_skip = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Пропустить и открыть Главное меню ➔", callback_data="skip_consult_phone")]
    ])
    await callback.message.answer(
        f"✅ <b>Бюджет:</b> {budget_str}\n"
        f"───────────────────────────\n\n"
        f"📞 <b>Шаг 3 из 3: Нажмите кнопку ниже или введите номер телефона / контакт для связи:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.message.answer(
        "💡 Или нажмите «Пропустить», чтобы сразу открыть Главное меню:",
        reply_markup=kb_skip
    )
    await callback.answer()

@router.callback_query(F.data == "skip_consult_phone")
async def skip_consult_phone_callback(callback: CallbackQuery, state: FSMContext):
    if state:
        await state.clear()
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")
        role = partner.role if partner else "DEMO"
        is_mon = partner.is_monitoring_active if partner else True

    await callback.answer("Главное меню восстановлено", show_alert=False)
    await callback.message.answer(
        "🎯 <b>Главное меню панели управления LeadRADAR восстановлено:</b>",
        reply_markup=get_main_reply_keyboard(is_mon, role),
        parse_mode="HTML"
    )

@router.message(ConsultForm.waiting_for_phone)
async def process_consult_phone(message: Message, state: FSMContext):
    text_val = (message.text or "").strip()
    if text_val in ["/start", "/menu", "🔝 Главное меню", "⏩ Пропустить", "⏩ Пропустить и открыть Главное меню", "Пропустить", "Отмена", "отмена", "пропустить"]:
        await cmd_menu_handler(message, state)
        return

    phone = message.contact.phone_number if message.contact else text_val
    data = await state.get_data()
    await state.clear()

    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Клиент"
    username_str = f"@{message.from_user.username}" if message.from_user.username else f"ID {telegram_id}"
    niche_name = data.get("niche_name", "Не указана")
    budget_str = data.get("budget_str", "Не указан")

    partner_role = "DEMO"
    is_monitoring = True

    # Send Notification to Superadmins
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, first_name, message.from_user.username or "")
        if partner:
            partner_role = partner.role
            is_monitoring = partner.is_monitoring_active

        superadmins_res = await session.execute(select(Partner).where(Partner.role == "SUPERADMIN"))
        superadmins = list(superadmins_res.scalars().all())

        from src.bot.alert_bot import bot
        if bot:
            lead_card = (
                f"🔥 <b>НОВАЯ ЗАЯВКА НА КОНСУЛЬТАЦИЮ LEADRADAR</b>\n"
                f"───────────────────────────\n\n"
                f"👤 <b>Имя:</b> {html.quote(first_name)}\n"
                f"💬 <b>Контакт:</b> {username_str}\n"
                f"🏷 <b>Ниша бизнеса:</b> {html.quote(niche_name)}\n"
                f"💰 <b>Рекламный бюджет:</b> {html.quote(budget_str)}\n"
                f"📞 <b>Телефон/Связь:</b> <code>{html.quote(phone)}</code>\n"
                f"🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>\n\n"
                f"⚡ Свяжитесь с клиентом для проведения персональной демонстрации!"
            )
            for sa in superadmins:
                try:
                    await bot.send_message(sa.telegram_id, lead_card, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Error sending consult alert to superadmin {sa.telegram_id}: {e}")

    from src.bot.keyboards import get_main_reply_keyboard, WebAppInfo
    main_kb = get_main_reply_keyboard(is_monitoring, partner_role)

    # Reply to User with Onboarding confirmation and RESTORE main reply keyboard to clear "Share contact" button
    await message.answer(
        f"✅ <b>Ваша заявка на консультацию успешно принята!</b>\n"
        f"───────────────────────────\n\n"
        f"Наш старший специалист свяжется с вами по указанному контакту (<b>{html.quote(phone)}</b>) в течение 15 минут.\n\n"
        f"💰 <b>Напоминаем:</b> в сервисе действует <b>20% Партнерская программа</b>! Вы получаете 20% пассивного дохода с каждой оплаты приглашенных вами клиентов.",
        reply_markup=main_kb,
        parse_mode="HTML"
    )

    mkt_url = os.getenv("MARKETPLACE_APP_URL", "https://leadradar.win/marketplace")
    kb_demo = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть Маркетплейс Лидов в Mini App", web_app=WebAppInfo(url=mkt_url))],
        [InlineKeyboardButton(text="⚡ Продемонстрировать работу прямо сейчас", callback_data="run_live_demo_scan")],
        [InlineKeyboardButton(text="💼 Мой партнерский QR-код (20%)", callback_data="show_partner_referral_info")]
    ])

    await message.answer(
        f"⚡ <b>Хотите прямо сейчас посмотреть, как LeadRaDaR перехватывает лиды в вашей нише ({html.quote(niche_name)}) в реальном времени?</b>",
        reply_markup=kb_demo,
        parse_mode="HTML"
    )

@router.message(F.contact | (F.text == "📱 Поделиться контактом") | F.text.contains("Поделиться контактом"))
async def fallback_contact_handler(message: Message, state: FSMContext):
    await state.clear()
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Клиент"
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, first_name, message.from_user.username or "")
        partner_role = partner.role if partner else "DEMO"
        is_monitoring = partner.is_monitoring_active if partner else True
    
    from src.bot.keyboards import get_main_reply_keyboard
    await message.answer(
        "✅ Ваш контакт принят. Главное меню панели управления восстановлено:",
        reply_markup=get_main_reply_keyboard(is_monitoring, partner_role)
    )

@router.callback_query(F.data == "run_live_demo_scan")
async def run_live_demo_scan_callback(callback: CallbackQuery):
    await callback.answer("⚡ Запуск демо-сканирования в эфире...", show_alert=False)
    await callback.message.answer(
        "📡 <b>ИИ-СКАНИРОВАНИЕ В ПРЯМОМ ЭФИРЕ ЗАПУЩЕНО!</b>\n"
        "───────────────────────────\n\n"
        "Нейросеть считывает поступающие сообщения из целевых чатов...\n"
        "Вы можете отслеживать поток лидов в реальном времени на веб-панели:\n"
        "https://inthunter-production.up.railway.app/dashboard",
        parse_mode="HTML"
    )

@router.callback_query(F.data == "show_partner_referral_info")
async def show_partner_referral_info_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    ref_link = f"https://t.me/intenthunter_bot?start=ref_{telegram_id}"
    await callback.answer()
    await callback.message.answer(
        f"💼 <b>ВАША ПАРТНЕРСКАЯ ПРОГРАММА (20%)</b>\n"
        f"───────────────────────────\n\n"
        f"Зарабатывайте <b>20% пожизненных отчислений</b> с каждой покупки подписки или лидов в системе!\n\n"
        f"🔗 <b>Ваша уникальная реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"💳 <b>Выплаты:</b> От $50 USD на USDT TRC20 / TON.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("weblogin_confirm"))
async def weblogin_confirm_callback(callback: CallbackQuery):
    try:
        await callback.answer("⏳ Проверка авторизации...", show_alert=False)
    except Exception:
        pass

    data_str = callback.data
    if ":" in data_str:
        token = data_str.split(":", 1)[1]
    elif "weblogin_confirm_" in data_str:
        token = data_str.replace("weblogin_confirm_", "")
    else:
        token = data_str

    telegram_id = callback.from_user.id
    import time
    from src.api.tma_auth import _WEB_LOGIN_TOKENS, create_jwt

    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")
        jwt = create_jwt(telegram_id, partner.role, partner.id)

    entry = _WEB_LOGIN_TOKENS.get(token)
    if not entry:
        _WEB_LOGIN_TOKENS[token] = {
            "telegram_id": telegram_id,
            "expires_at": time.time() + 300,
            "confirmed": True,
            "jwt": jwt
        }
    else:
        entry["confirmed"] = True
        entry["telegram_id"] = telegram_id
        entry["jwt"] = jwt

    try:
        mp_url = os.getenv("MARKETPLACE_APP_URL", "https://inthunter-production.up.railway.app/marketplace")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎯 Открыть RADAR Маркетплейс", url=mp_url)
        ]])
        await callback.message.edit_text(
            "✅ <b>Вход в Веб-Маркетплейс успешно подтверждён!</b>\n\n"
            "Вернитесь в окно браузера или нажмите кнопку ниже для быстрого перехода в Маркетплейс:",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Failed to edit message for web login confirm: {e}")


@router.message(F.text.contains("t.me/") | F.text.startswith("@"))
async def auto_add_channel_link_handler(message: Message):
    text = message.text.strip()
    lines = text.split()
    target_link = None
    for item in lines:
        if "t.me/" in item or item.startswith("@"):
            target_link = item.strip()
            break

    if not target_link:
        return

    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    user_username = (message.from_user.username or "").lower()

    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, first_name, user_username)

        from src.services.custom_chat_engine import process_custom_chat_addition
        success, response_msg, sub = await process_custom_chat_addition(session, partner, target_link)

        # If live and active, trigger bot join check
        if success and sub and sub.status == "ACTIVE":
            from src.api.app import ingestor
            if ingestor:
                try:
                    await ingestor.join_channel(sub.username_or_link)
                except Exception:
                    pass

        await message.answer(response_msg, parse_mode="HTML")


@router.message(F.text == "📱 QR-код персонала")
@router.message(Command("qr"))
@router.callback_query(F.data == "get_staff_qr")
async def show_staff_qr_handler(event: Union[Message, CallbackQuery]):
    telegram_id = event.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            msg = "❌ Недостаточно прав. QR-код персонала доступен только для Администраторов и Суперадминистраторов."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

    from src.bot.alert_bot import bot
    bot_obj = event.bot or bot
    bot_info = await bot_obj.get_me()
    invite_url = f"https://t.me/{bot_info.username}?start=staff_invite"

    # Generate PNG QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(invite_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)

    qr_file = BufferedInputFile(bio.getvalue(), filename="staff_invite_qr.png")

    caption = (
        f"📱 <b>QR-КОД ДЛЯ ДОБАВЛЕНИЯ ПЕРСОНАЛА</b>\n"
        f"───────────────────────────\n\n"
        f"Дайте этот QR-код новому сотруднику для сканирования камерой Telegram.\n\n"
        f"🔗 <b>Прямая ссылка для перехода:</b>\n<code>{invite_url}</code>\n\n"
        f"⚡ <b>Как это работает:</b>\n"
        f"1. Сотрудник сканирует QR-код и запускает бота.\n"
        f"2. Вам в бот сразу приходит push-заявка.\n"
        f"3. Вы в 1 клик назначаете роль: <b>VIP</b>, <b>ADMIN</b> или <b>SUPERADMIN</b>."
    )

    if isinstance(event, CallbackQuery):
        await event.message.answer_photo(photo=qr_file, caption=caption, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer_photo(photo=qr_file, caption=caption, parse_mode="HTML")


@router.message(F.text == "👑 Управление ролями")
@router.message(Command("roles"))
@router.callback_query(F.data == "open_role_menu")
async def show_role_management_panel(event: Union[Message, CallbackQuery], state: FSMContext = None):
    if state:
        await state.clear()

    telegram_id = event.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            msg = "❌ Отказано в доступе. Доступно только для Администрации."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

        all_p = list((await session.execute(select(Partner))).scalars().all())
        counts = {
            "SUPERADMIN": len([p for p in all_p if p.role == "SUPERADMIN"]),
            "ADMIN": len([p for p in all_p if p.role == "ADMIN"]),
            "VIP": len([p for p in all_p if p.role == "VIP"]),
            "REGULAR": len([p for p in all_p if p.role == "REGULAR"]),
            "DEMO": len([p for p in all_p if p.role == "DEMO"])
        }

    panel_text = (
        f"👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ РОЛЯМИ ПЕРСОНАЛА И ПОЛЬЗОВАТЕЛЕЙ</b>\n"
        f"───────────────────────────\n\n"
        f"👥 <b>Текущее распределение ролей в системе:</b>\n"
        f"   • 👑 SUPERADMIN (Суперадмины): <b>{counts['SUPERADMIN']}</b>\n"
        f"   • 🔑 ADMIN (Администраторы): <b>{counts['ADMIN']}</b>\n"
        f"   • ⭐ VIP (ВИП-клиенты): <b>{counts['VIP']}</b>\n"
        f"   • 🔵 REGULAR (Обычные юзеры): <b>{counts['REGULAR']}</b>\n"
        f"   • 🆕 DEMO (Демо-пользователи): <b>{counts['DEMO']}</b>\n\n"
        f"💡 Воспользуйтесь кнопками ниже для поиска пользователя по <b>@username</b>, <b>ID</b> или <b>имени</b>."
    )

    kb = get_superadmin_role_menu_keyboard()

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(panel_text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(panel_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "role_search_start")
async def start_role_search_callback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RoleSearchForm.waiting_for_query)
    await callback.message.answer(
        "🔍 <b>ПОИСК ПОЛЬЗОВАТЕЛЯ ДЛЯ УПРАВЛЕНИЯ РОЛЯМИ</b>\n"
        "───────────────────────────\n\n"
        "Пришлите <b>@username</b>, <b>Telegram ID</b> или <b>имя/компанию</b> пользователя:\n"
        "<i>(Пример: <code>@sherlockdxb</code>, <code>260669598</code> или <code>Иван</code>)</i>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(RoleSearchForm.waiting_for_query)
async def process_role_search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    telegram_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        admin_partner = (await session.execute(p_stmt)).scalar_one_or_none()
        admin_role = admin_partner.role if admin_partner else "DEMO"

    if query.lower().startswith("/start") or query.lower() in ["/cancel", "отмена", "стоп", "выход"]:
        await state.clear()
        if query.lower().startswith("/start"):
            await cmd_start(message)
            return
        await message.answer("🛑 Поиск отменен.", reply_markup=get_main_reply_keyboard(True, admin_role))
        return

    clean_query = query.replace("@", "").strip()
    async with AsyncSessionLocal() as session:
        stmt = select(Partner)
        if clean_query.isdigit():
            stmt = stmt.where(Partner.telegram_id == int(clean_query))
        else:
            stmt = stmt.where(Partner.company_name.ilike(f"%{clean_query}%"))

        partners = list((await session.execute(stmt)).scalars().all())

        if not partners:
            u_stmt = select(UserProfile).where(
                (UserProfile.username.ilike(f"%{clean_query}%")) | 
                (UserProfile.first_name.ilike(f"%{clean_query}%")) |
                (UserProfile.last_name.ilike(f"%{clean_query}%"))
            )
            u_profs = list((await session.execute(u_stmt)).scalars().all())
            if u_profs:
                u_ids = [u.user_id for u in u_profs]
                partners = list((await session.execute(select(Partner).where(Partner.telegram_id.in_(u_ids)))).scalars().all())

    await state.clear()

    if not partners:
        await message.answer(
            f"❌ Пользователи по запросу «<b>{html.quote(query)}</b>» не найдены в базе данных.",
            reply_markup=get_superadmin_role_menu_keyboard(),
            parse_mode="HTML"
        )
        return

    await message.answer(f"🔍 <b>Найдено совпадений: {len(partners)}</b>", parse_mode="HTML")

    for p in partners:
        async with AsyncSessionLocal() as session:
            u_prof = (await session.execute(select(UserProfile).where(UserProfile.user_id == p.telegram_id))).scalar_one_or_none()
            u_str = f"@{u_prof.username}" if u_prof and u_prof.username else "нет username"

        is_blocked = p.moderation_status == "BLOCKED"
        status_label = "⛔ Заблокирован" if is_blocked else ("🟢 Активен" if p.moderation_status == "APPROVED" else "⏳ Модерация")
        role_label = ROLE_LABELS.get(p.role, p.role)
        card_text = (
            f"👤 <b>Карточка пользователя:</b>\n"
            f"<b>Имя / Компания:</b> {html.quote(p.company_name)}\n"
            f"<b>Username:</b> {u_str}\n"
            f"<b>Telegram ID:</b> <code>{p.telegram_id}</code>\n"
            f"<b>Текущая роль:</b> {role_label}\n"
            f"<b>Статус блокировки:</b> {status_label}\n"
            f"<b>Баланс:</b> ${p.balance:.2f} USD\n\n"
            f"Выберите действие с аккаунтом:"
        )
        await message.answer(
            card_text,
            reply_markup=get_user_role_edit_keyboard(p.telegram_id, is_blocked),
            parse_mode="HTML"
        )


# ────────────────────────────────────────────────────────────────────────────
# SUPERADMIN FREEZING CHAMBER CONTROL PANEL ($2/MO CUSTOM CHATS)
# ────────────────────────────────────────────────────────────────────────────
from sqlalchemy import func

@router.callback_query(F.data.startswith("superadmin_frozen_chats:"))
async def superadmin_frozen_chats_handler(callback: CallbackQuery):
    admin_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("⛔ Нет доступа! Только для Администраторов.", show_alert=True)
            return

        page = int(callback.data.split(":")[1])
        stmt = select(CustomChatSubscription).where(
            (CustomChatSubscription.status == "FREEZING") | 
            (CustomChatSubscription.paid_until < datetime.now(timezone.utc))
        ).order_by(CustomChatSubscription.created_at.desc())
        frozen_subs = list((await session.execute(stmt)).scalars().all())

        if not frozen_subs:
            await callback.message.edit_text(
                "🥶 <b>МОРОЗИЛЬНАЯ КАМЕРА ЧАТОВ ПУСТА</b>\n"
                "───────────────────────────\n\n"
                "Все кастомные чаты клиентов оплачены и активно сканируются в эфире!",
                reply_markup=get_superadmin_role_menu_keyboard(),
                parse_mode="HTML"
            )
            await callback.answer()
            return

        sub = frozen_subs[page % len(frozen_subs)]
        
        # Calculate Chat Analytics
        created_str = sub.created_at.strftime("%d.%m.%Y") if sub.created_at else "Неизвестно"
        paid_until_str = sub.paid_until.strftime("%d.%m.%Y") if sub.paid_until else "Не оплачен"
        active_days = max(1, (sub.paid_until - sub.created_at).days) if (sub.paid_until and sub.created_at) else 0

        # Total leads extracted
        lead_stmt = select(func.count(Lead.id)).where(Lead.intent_summary.ilike(f"%{sub.username_or_link.replace('@','')}%"))
        total_leads = (await session.execute(lead_stmt)).scalar() or 0

        # Owner info
        owner_stmt = select(Partner).where(Partner.id == sub.partner_id)
        owner = (await session.execute(owner_stmt)).scalar_one_or_none()
        owner_name = owner.company_name if owner else f"ID {sub.telegram_id}"

        kb_buttons = [
            [
                InlineKeyboardButton(text="🟢 Сохранить в поиске", callback_data=f"freezing_keep:{sub.id}"),
                InlineKeyboardButton(text="🗑 Удалить из системы", callback_data=f"freezing_del:{sub.id}")
            ]
        ]
        if len(frozen_subs) > 1:
            next_p = (page + 1) % len(frozen_subs)
            prev_p = (page - 1) % len(frozen_subs)
            kb_buttons.append([
                InlineKeyboardButton(text="◀️ Пред.", callback_data=f"superadmin_frozen_chats:{prev_p}"),
                InlineKeyboardButton(text=f"📄 {page + 1} / {len(frozen_subs)}", callback_data="noop"),
                InlineKeyboardButton(text="След. ▶️", callback_data=f"superadmin_frozen_chats:{next_p}")
            ])
        kb_buttons.append([InlineKeyboardButton(text="🔙 Назад к управлению ролями", callback_data="open_role_menu")])

        card_text = (
            f"🥶 <b>МОРОЗИЛЬНАЯ КАМЕРА ЧАТОВ — УПРАВЛЕНИЕ АДМИНА</b>\n"
            f"───────────────────────────\n\n"
            f"📌 <b>Чат:</b> <code>{sub.username_or_link}</code>\n"
            f"👤 <b>Владелец чата:</b> {html.quote(owner_name)} (<code>{sub.telegram_id}</code>)\n"
            f"💰 <b>Абонентская плата:</b> ${sub.monthly_price:.2f} USD / мес\n"
            f"⛔ <b>Причина заморозки:</b> Оплата прекращена ({paid_until_str})\n\n"
            f"📊 <b>Базовая Аналитика Канала:</b>\n"
            f"⏱ <b>Срок активной работы:</b> ~{active_days} дней (с {created_str})\n"
            f"🎯 <b>Сгенерировано лидов:</b> {total_leads} шт.\n"
            f"🏷️ <b>Ниша:</b> 💬 Общий пул сообщества\n\n"
            f"⚖️ <b>Решение Суперадминистратора:</b>\n"
            f"Сохранять чат в бесплатном поиске маркетплейса или окончательно удалять:"
        )

        await callback.message.edit_text(card_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons), parse_mode="HTML")
        await callback.answer()


@router.callback_query(F.data.startswith("freezing_keep:"))
async def freezing_keep_callback(callback: CallbackQuery):
    sub_id = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(CustomChatSubscription).where(CustomChatSubscription.id == sub_id))).scalar_one_or_none()
        if sub:
            sub.status = "GLOBAL_SEARCH_ACTIVE"
            await session.commit()
            await callback.answer("🟢 Чат сохранен в глобальном поиске маркетплейса!", show_alert=True)
            await superadmin_frozen_chats_handler(callback)


@router.callback_query(F.data.startswith("freezing_del:"))
async def freezing_del_callback(callback: CallbackQuery):
    sub_id = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(CustomChatSubscription).where(CustomChatSubscription.id == sub_id))).scalar_one_or_none()
        if sub:
            ch_stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(f"%{sub.username_or_link.replace('@','')}%"))
            ch = (await session.execute(ch_stmt)).scalar_one_or_none()
            if ch:
                await session.delete(ch)
            await session.delete(sub)
            await session.commit()
            await callback.answer("🗑 Чат полностью удален из системы!", show_alert=True)
            await superadmin_frozen_chats_handler(callback)


@router.callback_query(F.data.startswith("set_role_btn:") | F.data.startswith("staff_approve:"))
async def process_set_role_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    action_type = parts[0]
    target_id = int(parts[1])
    new_role = parts[2]
    admin_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Недостаточно прав для выполнения операции.", show_alert=True)
            return

        target_stmt = select(Partner).where(Partner.telegram_id == target_id)
        target_partner = (await session.execute(target_stmt)).scalar_one_or_none()

        if not target_partner:
            await callback.answer("❌ Пользователь не найден в базе данных.", show_alert=True)
            return

        if new_role == "REJECT":
            target_partner.moderation_status = "REJECTED"
            msg_status = "отклонена ❌"
        else:
            target_partner.role = new_role
            if target_partner.moderation_status != "BLOCKED":
                target_partner.moderation_status = "APPROVED"
            msg_status = f"изменена на <b>{ROLE_LABELS.get(new_role, new_role)}</b> ✅"

        await session.commit()

        from src.bot.alert_bot import bot
        if bot:
            try:
                if new_role == "REJECT":
                    notify_text = "❌ Ваша заявка на доступ к системе была отклонена модератором."
                else:
                    notify_text = (
                        f"🎉 <b>ВАМ НАЗНАЧЕНА НОВАЯ РОЛЬ В СИСТЕМЕ!</b>\n\n"
                        f"<b>Ваш текущий статус:</b> {ROLE_LABELS.get(new_role, new_role)}\n"
                        f"Вам открыты все возможности согласно присвоенным правам."
                    )
                await bot.send_message(
                    chat_id=target_id,
                    text=notify_text,
                    reply_markup=get_main_reply_keyboard(target_partner.is_monitoring_active, target_partner.role, getattr(target_partner, "is_debug_monitoring", False)),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error notifying user {target_id} of role update: {e}")

        await callback.answer(f"Операция завершена: {msg_status}!", show_alert=True)
        await callback.message.edit_text(
            f"✅ <b>Операция завершена:</b> Роль пользователя <code>{target_id}</code> {msg_status}.",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("toggle_block_user:"))
async def toggle_block_user_callback(callback: CallbackQuery):
    target_id = int(callback.data.split(":", 1)[1])
    admin_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Недостаточно прав для блокировки пользователей.", show_alert=True)
            return

        target_stmt = select(Partner).where(Partner.telegram_id == target_id)
        target_partner = (await session.execute(target_stmt)).scalar_one_or_none()

        if not target_partner:
            await callback.answer("❌ Пользователь не найден в базе данных.", show_alert=True)
            return

        will_block = target_partner.moderation_status != "BLOCKED"
        target_partner.moderation_status = "BLOCKED" if will_block else "APPROVED"
        await session.commit()

        from src.bot.alert_bot import bot
        if bot:
            try:
                if will_block:
                    notify_text = "⛔ <b>Ваш аккаунт был заблокирован Администрацией системы.</b>\nДоступ к функциям бота приостановлен."
                else:
                    notify_text = "🟢 <b>Ваш аккаунт разблокирован!</b>\nДоступ к системе успешно восстановлен."
                await bot.send_message(
                    chat_id=target_id,
                    text=notify_text,
                    reply_markup=get_main_reply_keyboard(target_partner.is_monitoring_active, target_partner.role, getattr(target_partner, "is_debug_monitoring", False)),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error notifying user {target_id} of block status: {e}")

        msg = "заблокирован ⛔" if will_block else "разблокирован 🟢"
        await callback.answer(f"Пользователь {target_id} {msg}!", show_alert=True)
        await callback.message.edit_text(
            f"✅ <b>Операция завершена:</b> Пользователь <code>{target_id}</code> {msg}.",
            parse_mode="HTML"
        )


@router.callback_query(F.data == "list_blocked_users")
async def list_blocked_users_callback(callback: CallbackQuery):
    admin_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Отказано в доступе.", show_alert=True)
            return

        blocked_partners = list((await session.execute(select(Partner).where(Partner.moderation_status == "BLOCKED"))).scalars().all())

    if not blocked_partners:
        await callback.answer("⛔ Заблокированных пользователей нет.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(f"⛔ <b>Заблокированные пользователи ({len(blocked_partners)}):</b>", parse_mode="HTML")
    for p in blocked_partners:
        async with AsyncSessionLocal() as session:
            u_prof = (await session.execute(select(UserProfile).where(UserProfile.user_id == p.telegram_id))).scalar_one_or_none()
            u_str = f"@{u_prof.username}" if u_prof and u_prof.username else "нет username"

        role_label = ROLE_LABELS.get(p.role, p.role)
        card_text = (
            f"⛔ <b>Заблокированный аккаунт:</b>\n"
            f"<b>Имя / Компания:</b> {html.quote(p.company_name)}\n"
            f"<b>Username:</b> {u_str}\n"
            f"<b>Telegram ID:</b> <code>{p.telegram_id}</code>\n"
            f"<b>Роль:</b> {role_label}\n"
            f"<b>Баланс:</b> ${p.balance:.2f} USD"
        )
        await callback.message.answer(
            card_text,
            reply_markup=get_user_role_edit_keyboard(p.telegram_id, is_blocked=True),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("role_list_all:"))
async def list_all_users_by_role_callback(callback: CallbackQuery):
    admin_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Отказано в доступе.", show_alert=True)
            return

        all_partners = list((await session.execute(select(Partner).order_by(Partner.created_at.desc()))).scalars().all())

    if not all_partners:
        await callback.answer("Список пользователей пуст.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(f"👥 <b>Список зарегистрированных пользователей ({len(all_partners)}):</b>", parse_mode="HTML")
    for p in all_partners[:15]:
        async with AsyncSessionLocal() as session:
            u_prof = (await session.execute(select(UserProfile).where(UserProfile.user_id == p.telegram_id))).scalar_one_or_none()
            u_str = f"@{u_prof.username}" if u_prof and u_prof.username else "нет username"

        is_blocked = p.moderation_status == "BLOCKED"
        status_label = "⛔ Заблокирован" if is_blocked else ("🟢 Активен" if p.moderation_status == "APPROVED" else "⏳ Модерация")
        role_label = ROLE_LABELS.get(p.role, p.role)
        card_text = (
            f"👤 <b>Пользователь (ID <code>{p.telegram_id}</code>):</b>\n"
            f"<b>Имя / Компания:</b> {html.quote(p.company_name)}\n"
            f"<b>Username:</b> {u_str}\n"
            f"<b>Текущая роль:</b> {role_label}\n"
            f"<b>Статус:</b> {status_label}\n"
            f"<b>Баланс:</b> ${p.balance:.2f} USD"
        )
        await callback.message.answer(
            card_text,
            reply_markup=get_user_role_edit_keyboard(p.telegram_id, is_blocked=is_blocked),
            parse_mode="HTML"
        )


@router.message(Command("block"))
async def cmd_block_user(message: Message):
    """Admin command: /block <telegram_id>"""
    admin_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Отказано в доступе.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("⚠️ Использование: <code>/block &lt;telegram_id&gt;</code>", parse_mode="HTML")
            return

        try:
            target_id = int(parts[1])
            t_stmt = select(Partner).where(Partner.telegram_id == target_id)
            target_p = (await session.execute(t_stmt)).scalar_one_or_none()
            if not target_p:
                await message.answer(f"❌ Пользователь с Telegram ID {target_id} не найден.")
                return

            target_p.moderation_status = "BLOCKED"
            await session.commit()

            await message.answer(f"⛔ Пользователь ID <code>{target_id}</code> заблокирован!", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("unblock"))
async def cmd_unblock_user(message: Message):
    """Admin command: /unblock <telegram_id>"""
    admin_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Отказано в доступе.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("⚠️ Использование: <code>/unblock &lt;telegram_id&gt;</code>", parse_mode="HTML")
            return

        try:
            target_id = int(parts[1])
            t_stmt = select(Partner).where(Partner.telegram_id == target_id)
            target_p = (await session.execute(t_stmt)).scalar_one_or_none()
            if not target_p:
                await message.answer(f"❌ Пользователь с Telegram ID {target_id} не найден.")
                return

            target_p.moderation_status = "APPROVED"
            await session.commit()

            await message.answer(f"🟢 Пользователь ID <code>{target_id}</code> разблокирован!", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")


@router.message(F.text.contains("мониторинг") | F.text.contains("Мониторинг"))
@router.message(Command("monitoring"))
async def toggle_monitoring_handler(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        if not partner:
            partner = Partner(
                telegram_id=telegram_id,
                company_name=f"Компания {message.from_user.first_name or 'Партнер'}",
                balance=1000.00,
                subscribed_niches=["real_estate", "bike_rent", "currency_exchange", "services_visa", "auto_kasko"],
                is_monitoring_active=True
            )
            session.add(partner)
        else:
            msg_input = message.text or ""
            if "Выключить" in msg_input:
                partner.is_monitoring_active = False
            elif "Включить" in msg_input:
                partner.is_monitoring_active = True
            else:
                partner.is_monitoring_active = not partner.is_monitoring_active

        await session.commit()
        await session.refresh(partner)
        is_active = partner.is_monitoring_active

    if is_active:
        msg_text = (
            "🔔 <b>РЕЖИМ МОНИТОРИНГА ВКЛЮЧЕН!</b>\n\n"
            "⚡ Теперь вы будете мгновенно получать в этот бот уведомления о каждом новом горячем лиде из чатов Нячанга."
        )
    else:
        msg_text = (
            "🔕 <b>РЕЖИМ МОНИТОРИНГА ВЫКЛЮЧЕН.</b>\n\n"
            "Уведомления о новых лидах приостановлены. Нажмите кнопку снова, чтобы возобновить мониторинг."
        )

    await message.answer(msg_text, reply_markup=get_main_reply_keyboard(is_active, partner.role, getattr(partner, "is_debug_monitoring", False)), parse_mode="HTML")


@router.message(F.text.contains("Веб-Панель") | F.text.contains("Веб Панель"))
@router.message(Command("webadmin"))
@router.message(Command("dashboard"))
async def show_webadmin_panel_handler(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Доступ к Веб-панели разрешен только для Администраторов и Суперадминистраторов.")
            return

    web_url = os.getenv("WEB_APP_URL", "https://inthunter-production.up.railway.app/dashboard")
    card_text = (
        f"🌐 <b>ВЕБ-ПАНЕЛЬ УПРАВЛЕНИЯ (ADMIN)</b>\n"
        f"───────────────────────────\n\n"
        f"Вам открыт доступ к интерактивной панели суперадмина:\n\n"
        f"📡 <b>Список отслеживаемых чатов:</b> с фильтром по локациям, нишам и поиску.\n"
        f"⚡ <b>Онлайн Мониторинг Прослушки:</b> живой поток сообщений в реальном времени.\n"
        f"👥 <b>Пользователи & Статистика:</b> депозиты и таймлайн выкупов с метками времени.\n"
        f"🏷 <b>Управление рубриками:</b> создание, редактирование и удаление категорий.\n\n"
        f"🔗 <b>Ссылка для входа:</b> {web_url}"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть Веб-панель RADAR (Telegram Auth)", web_app=WebAppInfo(url=web_url))]
        ]
    )
    await message.answer(card_text, reply_markup=kb, parse_mode="HTML")

_user_screenshot_candidates = {}

async def show_screenshot_candidate_card(chat_id: int, telegram_id: int, index: int, bot_inst):
    candidates = _user_screenshot_candidates.get(telegram_id, [])
    if not candidates or index >= len(candidates):
        await bot_inst.send_message(
            chat_id,
            "🎉 <b>Все распознанные каналы со скриншота обработаны!</b>\n"
            "Утвержденные каналы добавлены в прослушку и отображаются в вашей Веб-панели.",
            parse_mode="HTML"
        )
        _user_screenshot_candidates.pop(telegram_id, None)
        return

    c = candidates[index]
    total = len(candidates)
    chat_icon = "👥 ГРУППА" if c.get("chat_type") == "group" else "📢 КАНАЛ"
    members_info = f" • 👥 {c.get('estimated_members')}" if c.get('estimated_members') else ""

    text = (
        f"📸 <b>РАСПОЗНАННЫЙ РЕСУРС СО СКРИНШОТА #{index + 1} из {total}</b>\n"
        f"───────────────────────────\n\n"
        f"📌 <b>{c.get('title')}</b>\n"
        f"🔗 Юзернейм: <b>{c.get('username')}</b>\n"
        f"Тип: <b>{chat_icon}</b>{members_info}\n\n"
        f"Добавить этот ресурс в сканирование ИИ?"
    )

    from src.bot.keyboards import get_screenshot_candidate_keyboard
    kb = get_screenshot_candidate_keyboard(index, total)
    await bot_inst.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")

@router.message(F.photo)
async def handle_photo_screenshot_handler(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, message.from_user.first_name or "", message.from_user.username or "")
        if partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Добавление каналов по скриншоту доступно только для Администраторов.")
            return

    status_msg = await message.answer("📸 <b>Скриншот получен!</b> ИИ Vision считывает названия и юзернеймы групп...", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        downloaded_file = await message.bot.download_file(file_info.file_path)
        img_bytes = downloaded_file.read() if hasattr(downloaded_file, 'read') else downloaded_file.getvalue()

        from src.ai.vision_ocr import extract_telegram_channels_from_image
        candidates = await extract_telegram_channels_from_image(img_bytes)

        if not candidates:
            await status_msg.edit_text("⚠️ <b>ИИ Vision не смог распознать названия каналов на скриншоте.</b>\nПопробуйте сделать более четкий скриншот списка чатов Telegram.", parse_mode="HTML")
            return

        _user_screenshot_candidates[telegram_id] = candidates

        await status_msg.delete()
        await show_screenshot_candidate_card(message.chat.id, telegram_id, 0, message.bot)
    except Exception as e:
        logger.error(f"Error processing screenshot photo: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ошибка при распознавании скриншота: {e}")

@router.callback_query(F.data.startswith("ocr_appr:"))
async def handle_ocr_approve(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    parts = callback.data.split(":")
    idx = int(parts[1])

    candidates = _user_screenshot_candidates.get(telegram_id, [])
    if idx < len(candidates):
        c = candidates[idx]
        target = c.get("username", "").strip()

        async with AsyncSessionLocal() as session:
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == target)
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if not existing:
                loc_code = "dubai" if "dubai" in target.lower() else ("nhatrang" if "nhatrang" in target.lower() else "global")
                niche_code = "real_estate" if "realty" in target.lower() or "недвиж" in c.get("title", "").lower() else "community"

                ch = MonitoredChannel(
                    username_or_link=target,
                    title=c.get("title"),
                    niche_code=niche_code,
                    location_code=loc_code,
                    chat_type=c.get("chat_type", "channel"),
                    status="JOINED"
                )
                session.add(ch)
                await session.commit()

        await callback.answer(f"✅ {c.get('title')} добавлен!", show_alert=False)
        await callback.message.edit_text(f"✅ <b>Утвержден и добавлен:</b> {c.get('title')} ({target})", parse_mode="HTML")

    await show_screenshot_candidate_card(callback.message.chat.id, telegram_id, idx + 1, callback.bot)

@router.callback_query(F.data.startswith("ocr_skip:"))
async def handle_ocr_skip(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    parts = callback.data.split(":")
    idx = int(parts[1])

    candidates = _user_screenshot_candidates.get(telegram_id, [])
    if idx < len(candidates):
        c = candidates[idx]
        await callback.answer("Пропущено", show_alert=False)
        await callback.message.edit_text(f"❌ <b>Пропущен:</b> {c.get('title')}", parse_mode="HTML")

    await show_screenshot_candidate_card(callback.message.chat.id, telegram_id, idx + 1, callback.bot)

@router.callback_query(F.data.startswith("ocr_appr_all:"))
async def handle_ocr_approve_all(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    parts = callback.data.split(":")
    start_idx = int(parts[1])

    candidates = _user_screenshot_candidates.get(telegram_id, [])
    added_count = 0

    async with AsyncSessionLocal() as session:
        for idx in range(start_idx, len(candidates)):
            c = candidates[idx]
            target = c.get("username", "").strip()
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == target)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing and target:
                loc_code = "dubai" if "dubai" in target.lower() else ("nhatrang" if "nhatrang" in target.lower() else "global")
                ch = MonitoredChannel(
                    username_or_link=target,
                    title=c.get("title"),
                    niche_code="community",
                    location_code=loc_code,
                    chat_type=c.get("chat_type", "channel"),
                    status="JOINED"
                )
                session.add(ch)
                added_count += 1
        await session.commit()

    await callback.answer(f"🚀 Утверждено {added_count} каналов!", show_alert=True)
    await callback.message.edit_text(
        f"🚀 <b>Все {added_count} каналов со скриншота утверждены и добавлены в прослушку!</b>\n"
        f"Вы можете отслеживать их статус в Веб-Панели.",
        parse_mode="HTML"
    )
    _user_screenshot_candidates.pop(telegram_id, None)


@router.message(F.text.contains("Тестовый") | F.text.contains("Тест-мониторинг") | F.text.contains("Тестовый режим"))
@router.message(Command("testmode"))
@router.message(Command("debug"))
async def toggle_debug_monitoring_handler(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Тестовый режим доступен только для Администраторов и Суперадминистраторов.")
            return

        partner.is_debug_monitoring = not getattr(partner, "is_debug_monitoring", False)
        await session.commit()
        await session.refresh(partner)
        is_debug = partner.is_debug_monitoring

        recent_logs = []
        if is_debug:
            log_stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(5)
            log_res = await session.execute(log_stmt)
            recent_logs = list(log_res.scalars().all())

    if is_debug:
        msg_text = (
            "🧪 <b>ТЕСТОВЫЙ РЕЖИМ СКАНИРОВАНИЯ ВКЛЮЧЕН!</b>\n\n"
            "⚡ Теперь вы будете в реальном времени получать push-уведомления о <b>ВСЕХ</b> отсканированных сообщениях из каналов и чатов.\n"
            "Это позволяет отслеживать работу ИИ-сканера и поток перехватываемого контента."
        )
        await message.answer(msg_text, reply_markup=get_main_reply_keyboard(partner.is_monitoring_active, partner.role, is_debug), parse_mode="HTML")

        if recent_logs:
            await message.answer("📡 <b>[РЕАЛЬНОЕ ВРЕМЯ] Последние перехваченные сообщения из чатов:</b>", parse_mode="HTML")
            for log in reversed(recent_logs):
                async with AsyncSessionLocal() as session:
                    u_prof = (await session.execute(select(UserProfile).where(UserProfile.user_id == log.user_id))).scalar_one_or_none()
                u_str = f"@{u_prof.username}" if u_prof and u_prof.username else "без username"
                first_name_clean = html.quote((u_prof.first_name if u_prof else None) or "Пользователь")
                chat_clean = html.quote(log.chat_title or "Групповой чат")
                text_snippet = html.quote((log.message_text or "")[:350]) + ("..." if len(log.message_text or "") > 350 else "")

                debug_card = (
                    f"🧪 <b>[ТЕСТ-МОНИТОР СКАНИРОВАНИЯ]</b>\n"
                    f"───────────────────────────\n"
                    f"📍 <b>Чат:</b> {chat_clean}\n"
                    f"👤 <b>Автор:</b> {first_name_clean} ({u_str}) | ID: <code>{log.user_id}</code>\n"
                    f"💬 <b>Текст сообщения:</b>\n<i>\"{text_snippet}\"</i>\n\n"
                    f"⚙️ <b>Статус:</b> 🟢 Перехвачено ИИ-сканером"
                )
                await message.answer(debug_card, parse_mode="HTML")
        else:
            await message.answer("ℹ️ <i>Отсканированных сообщений пока нет в базе данных. Ожидание поступления новых сообщений...</i>", parse_mode="HTML")
    else:
        msg_text = (
            "⏹️ <b>ТЕСТОВЫЙ РЕЖИМ СКАНИРОВАНИЯ ВЫКЛЮЧЕН.</b>\n\n"
            "Отладочный поток сообщений приостановлен. Вы будете получать только целевые ИИ-карточки горячих лидов."
        )
        await message.answer(msg_text, reply_markup=get_main_reply_keyboard(partner.is_monitoring_active, partner.role, is_debug), parse_mode="HTML")


@router.message(Command("study"))
@router.message(Command("study_bad"))
async def study_ai_exemplar_handler(message: Message):
    """Superadmin command: /study (positive lead) or /study_bad (hard negative / spam)."""
    telegram_id = message.from_user.id
    is_negative = message.text.startswith("/study_bad")

    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(
            session,
            telegram_id,
            message.from_user.first_name or "",
            message.from_user.username or ""
        )
        if partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Команда обучения ИИ доступна только Администраторам.")
            return

        target_text = ""
        if message.reply_to_message and message.reply_to_message.text:
            target_text = message.reply_to_message.text.strip()
        else:
            parts = message.text.split(maxsplit=1)
            if len(parts) > 1:
                target_text = parts[1].strip().strip('"').strip("'").strip()

        if not target_text:
            cmd_name = "/study_bad" if is_negative else "/study"
            type_desc = "Hard Negative (СПАМ/ФЛУД)" if is_negative else "Положительный ЛИД"
            await message.answer(
                f"🎓 <b>Дообучение ИИ-сканера ({type_desc}):</b>\n\n"
                f"Использование:\n"
                f"• Ответьте командой <code>{cmd_name}</code> на любое сообщение в чате\n"
                f"• Или введите: <code>{cmd_name} Текст примера</code>\n\n"
                f"ИИ автоматически внесет пример в Базу Знаний Few-Shot!",
                parse_mode="HTML"
            )
            return

        try:
            from datetime import datetime, timezone
            from src.ai.scorer import evaluate_user_timeline
            fake_log = UserActivityLog(
                user_id=telegram_id,
                chat_id=123,
                chat_title="Учебный пример",
                message_id=999,
                message_text=target_text,
                timestamp=datetime.now(timezone.utc)
            )
            scoring_res = await evaluate_user_timeline(telegram_id, session, [fake_log])

            # Override parameters if /study_bad was used
            final_is_lead = False if is_negative else (scoring_res.is_lead if scoring_res else True)
            final_temp = None if is_negative else (scoring_res.temperature if scoring_res else "HOT")
            final_summary = "Обученный Hard Negative (Спам/Флуд)" if is_negative else (scoring_res.intent_summary if scoring_res else "Учебный лид")

            from src.db.models import AIStudyExemplar
            exemplar = AIStudyExemplar(
                raw_message_text=target_text,
                niche_code=scoring_res.niche_code if (scoring_res and scoring_res.niche_code) else "other",
                temperature=final_temp,
                is_lead=final_is_lead,
                intent_summary=final_summary,
                sales_hook=scoring_res.sales_hook if (scoring_res and not is_negative) else ""
            )
            session.add(exemplar)
            await session.commit()
            await session.refresh(exemplar)

            status_badge = "⛔ HARD NEGATIVE (СПАМ)" if is_negative else ("🔥 HOT LEAD" if scoring_res and scoring_res.is_lead else "⛔ NOT_A_LEAD")
            niche_str = scoring_res.niche_code if scoring_res else "прочее"
            reason_str = html.quote(scoring_res.reasoning) if (scoring_res and scoring_res.reasoning) else "Анализ завершен"

            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🗑️ Удалить пример из базы", callback_data=f"del_study_ex:{exemplar.id}")
            ]])

            await message.answer(
                f"🎓 <b>ИИ УСПЕШНО ДООБУЧЕН НА ПРИМЕРЕ!</b>\n"
                f"───────────────────────────\n\n"
                f"💬 <b>Текст:</b> <i>\"{html.quote(target_text)}\"</i>\n"
                f"⚙️ <b>Статус ИИ:</b> {status_badge}\n"
                f"🏷 <b>Категория:</b> {niche_str}\n"
                f"💡 <b>Логика (Reasoning):</b> {reason_str}\n\n"
                f"✅ Пример (ID: <code>{exemplar.id[:8]}</code>) внесен в Базу Знаний Few-Shot и учитывается при квалификации 100% входящих сообщений!",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in study_ai_exemplar_handler: {e}")
            await message.answer(f"❌ Ошибка дообучения ИИ: {e}")


@router.message(Command("study_list"))
async def list_study_exemplars_handler(message: Message):
    """Admin command: /study_list - lists active dynamic exemplars."""
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, message.from_user.first_name or "", message.from_user.username or "")
        if partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Доступно только Администраторам.")
            return

        from src.db.models import AIStudyExemplar
        res = await session.execute(select(AIStudyExemplar).order_by(AIStudyExemplar.created_at.desc()).limit(10))
        exemplars = list(res.scalars().all())

    if not exemplars:
        await message.answer("ℹ️ <b>База обучающих примеров пока пуста.</b>\nИспользуйте <code>/study</code> или <code>/study_bad</code> для добавления примеров.", parse_mode="HTML")
        return

    cards = ["🎓 <b>АКТИВНАЯ БАЗА ЗНАНИЙ FEW-SHOT (ТОП-10):</b>\n───────────────────────────"]
    for idx, ex in enumerate(exemplars, 1):
        tag = "🔥 LEAD" if ex.is_lead else "⛔ SPAM"
        snippet = html.quote(ex.raw_message_text[:80]) + ("..." if len(ex.raw_message_text) > 80 else "")
        cards.append(f"{idx}. {tag} [{ex.niche_code or 'other'}]: <i>\"{snippet}\"</i>\n   └ ID: <code>{ex.id}</code> (Удалить: /study_del {ex.id[:8]})\n")

    await message.answer("\n".join(cards), parse_mode="HTML")


@router.message(Command("study_del"))
async def delete_study_exemplar_cmd(message: Message):
    """Admin command: /study_del <id>"""
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, message.from_user.first_name or "", message.from_user.username or "")
        if partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Доступно только Администраторам.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("⚠️ Использование: <code>/study_del &lt;ID_примера&gt;</code>", parse_mode="HTML")
            return

        target_id = parts[1].strip()
        from src.db.models import AIStudyExemplar
        from sqlalchemy import delete
        res = await session.execute(delete(AIStudyExemplar).where(AIStudyExemplar.id.ilike(f"{target_id}%")))
        await session.commit()

        if res.rowcount > 0:
            await message.answer(f"✅ Обучающий пример <code>{target_id}</code> удален из Базы Знаний ИИ!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Пример с ID <code>{target_id}</code> не найден.", parse_mode="HTML")


@router.callback_query(F.data.startswith("del_study_ex:"))
async def delete_study_exemplar_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    ex_id = callback.data.split(":", 1)[1]

    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")
        if partner.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Доступно только Администраторам.", show_alert=True)
            return

        from src.db.models import AIStudyExemplar
        from sqlalchemy import delete
        await session.execute(delete(AIStudyExemplar).where(AIStudyExemplar.id == ex_id))
        await session.commit()

    await callback.answer("🗑️ Пример удален из базы ИИ!", show_alert=True)
    await callback.message.edit_text("🗑️ <b>Обучающий пример удален из Базы Знаний ИИ.</b>", parse_mode="HTML")


@router.message(F.text.contains("Запросить новую нишу") | F.text.contains("Запросить нишу"))
@router.message(Command("request_niche"))
@router.message(Command("niche"))
async def request_niche_handler(message: Message):
    """Handler for user niche request button or /request_niche command."""
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and not parts[1].startswith("Запросить"):
        requested_text = parts[1].strip()
    else:
        await message.answer(
            "💡 <b>Запрос новой ниши / категории для прослушки:</b>\n"
            "───────────────────────────\n\n"
            "Введите название или описание ниши, которую вы хотите отслеживать.\n"
            "Например:\n"
            "• <code>/request_niche Аренда авто в Нячанге</code>\n"
            "• <code>/request_niche Клининг апартаментов</code>\n"
            "• <code>/request_niche Экскурсии и гиды</code>\n\n"
            "💬 Или просто отправьте сообщение с текстом: <b>Запрос ниши: [Ваше название]</b>",
            parse_mode="HTML"
        )
        return

    # Save to DB and notify Superadmins
    async with AsyncSessionLocal() as session:
        from src.db.models import NicheRequest
        n_req = NicheRequest(
            user_id=telegram_id,
            first_name=first_name,
            username=username,
            requested_niche=requested_text,
            status="PENDING"
        )
        session.add(n_req)
        await session.commit()

    # Send Alert Card to Superadmin
    from src.bot.alert_bot import notify_superadmins_niche_request
    await notify_superadmins_niche_request(
        user_id=telegram_id,
        first_name=first_name,
        username=username,
        requested_niche=requested_text
    )

    await message.answer(
        f"✅ <b>Заявка на добавление ниши успешно отправлена Администраторам!</b>\n"
        f"───────────────────────────\n\n"
        f"💡 <b>Запрошенная ниша:</b> «<b>{html.quote(requested_text)}</b>»\n\n"
        f"Администратор свяжется с вами в Telegram после подключения источников и настройки ИИ-рубрики.",
        parse_mode="HTML"
    )


@router.message(F.text.startswith("Запрос ниши:"))
async def request_niche_prefix_handler(message: Message):
    """Handles messages starting with 'Запрос ниши:'"""
    requested_text = message.text.replace("Запрос ниши:", "").strip()
    if not requested_text:
        await message.answer("⚠️ Пожалуйста, укажите название ниши после двоеточия.")
        return

    telegram_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username

    async with AsyncSessionLocal() as session:
        from src.db.models import NicheRequest
        n_req = NicheRequest(
            user_id=telegram_id,
            first_name=first_name,
            username=username,
            requested_niche=requested_text,
            status="PENDING"
        )
        session.add(n_req)
        await session.commit()

    from src.bot.alert_bot import notify_superadmins_niche_request
    await notify_superadmins_niche_request(
        user_id=telegram_id,
        first_name=first_name,
        username=username,
        requested_niche=requested_text
    )

    await message.answer(
        f"✅ <b>Заявка на добавление ниши успешно отправлена Администраторам!</b>\n"
        f"───────────────────────────\n\n"
        f"💡 <b>Запрошенная ниша:</b> «<b>{html.quote(requested_text)}</b>»\n\n"
        f"Администратор свяжется с вами в Telegram после подключения источников и настройки ИИ-рубрики.",
        parse_mode="HTML"
    )


async def get_db_size_mb(session: AsyncSession) -> str:
    try:
        from sqlalchemy import text
        res = await session.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
        val = res.scalar()
        if val:
            return str(val)
    except Exception:
        pass

    try:
        from sqlalchemy import text
        res = await session.execute(text("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"))
        bytes_val = res.scalar()
        if bytes_val:
            mb = bytes_val / (1024 * 1024)
            return f"{mb:.2f} MB"
    except Exception:
        pass

    import os
    if os.path.exists("intent_hunter.db"):
        sz = os.path.getsize("intent_hunter.db") / (1024 * 1024)
        return f"{sz:.2f} MB"

    return "Н/Д"


@router.message(F.text == "📊 Аналитика")
@router.message(F.text.contains("Аналитика") | F.text.contains("аналитика"))
@router.message(Command("analytics"))
@router.message(Command("stats"))
async def show_analytics_menu_handler(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, message.from_user.first_name or "", message.from_user.username or "")
        if partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Раздел аналитики доступен только Администраторам.")
            return

        db_size_str = await get_db_size_mb(session)

    text = (
        "📊 <b>ЦЕНТР АНАЛИТИКИ И УПРАВЛЕНИЯ (Superadmin)</b>\n"
        "───────────────────────────\n\n"
        f"💾 <b>Размер базы данных (CDP):</b> <b>{db_size_str}</b>\n\n"
        "Выберите интересующий раздел аналитики или отчётов:\n\n"
        "⏱ <b>Ежечасный отчёт:</b> Трафик, каналы и лиды за 1 час / проход\n"
        "📅 <b>Архив отчётов за день:</b> Сводка и конверсия за 24 часа\n"
        "📈 <b>Эффективность каналов:</b> Цветовая карта простоя каналов (7 уровней)\n"
        "⚙️ <b>Здоровье сканера:</b> Прослушка, статус юзербота, экспорты (.csv)"
    )
    kb = get_analytics_inline_keyboard()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "analytics_hourly")
async def analytics_hourly_callback(callback: CallbackQuery):
    now_utc = datetime.now(timezone.utc)
    now_vn = now_utc + timedelta(hours=7)
    cutoff_1h = now_utc - timedelta(hours=1)
    cutoff_15m = now_utc - timedelta(minutes=15)

    async with AsyncSessionLocal() as session:
        db_size_str = await get_db_size_mb(session)
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

        total_channels = (await session.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0
        joined_channels = (await session.execute(select(func.count(MonitoredChannel.id)).where(MonitoredChannel.status == "JOINED"))).scalar() or 0

        from src.db.models import DiscoveredChat
        disc_approved_1h = (await session.execute(
            select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "APPROVED", DiscoveredChat.audited_at >= cutoff_1h)
        )).scalar() or 0

        disc_rejected_1h = (await session.execute(
            select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "REJECTED", DiscoveredChat.audited_at >= cutoff_1h)
        )).scalar() or 0

        disc_pending = (await session.execute(
            select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "PENDING")
        )).scalar() or 0

    digest_card = (
        f"📊 <b>ЕЖЕЧАСНЫЙ ОТЧЁТ СКАНИРОВАНИЯ И ТРАФИКА</b>\n"
        f"───────────────────────────\n\n"
        f"⏱ <b>Время (UTC+7):</b> {now_vn.strftime('%H:%M')}\n"
        f"📡 <b>Отсканировано каналов за 1 час:</b> <b>{channels_1h}</b> из {joined_channels} (всего {total_channels})\n"
        f"💬 <b>Новых сообщений (час - проход):</b> <b>{msgs_1h} - {msgs_pass}</b> шт.\n"
        f"🎯 <b>Квалифицировано лидов за 1 час:</b> <b>{leads_1h}</b> шт.\n\n"
        f"🔎 <b>ИИ-Поиск чатов (Discovery Engine):</b>\n"
        f"• ✅ Добавлено в прослушку за 1ч: <b>{disc_approved_1h}</b> чатов\n"
        f"• ⛔ Отклонено ИИ (спам/боты/профили) за 1ч: <b>{disc_rejected_1h}</b> чатов\n"
        f"• ⏳ Ожидают ИИ-аудита в очереди: <b>{disc_pending}</b> кандидатов\n\n"
        f"💾 <b>Текущий размер БД:</b> <b>{db_size_str}</b>\n\n"
        f"💡 <i>Отчёт генерируется в реальном времени.</i>"
    )
    await callback.message.answer(digest_card, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "analytics_daily_archive")
async def analytics_daily_archive_callback(callback: CallbackQuery):
    now_utc = datetime.now(timezone.utc)
    now_vn = now_utc + timedelta(hours=7)
    cutoff_24h = now_utc - timedelta(hours=24)

    async with AsyncSessionLocal() as session:
        db_size_str = await get_db_size_mb(session)
        msgs_24h = (await session.execute(
            select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_24h)
        )).scalar() or 0

        leads_24h = (await session.execute(
            select(func.count(Lead.id)).where(Lead.created_at >= cutoff_24h)
        )).scalar() or 0

        sold_24h = (await session.execute(
            select(func.count(Lead.id)).where(Lead.created_at >= cutoff_24h, Lead.status == "SOLD")
        )).scalar() or 0

        ch_24h = (await session.execute(
            select(func.count(func.distinct(UserActivityLog.chat_title))).where(UserActivityLog.timestamp >= cutoff_24h)
        )).scalar() or 0

    text = (
        "📅 <b>АРХИВ И СВОДКА СТАТИСТИКИ ЗА 24 ЧАСА</b>\n"
        "───────────────────────────\n\n"
        f"⏱ <b>Дата отчёта:</b> {now_vn.strftime('%d.%m.%Y %H:%M')}\n"
        f"💬 <b>Обработано сообщений за 24ч:</b> <b>{msgs_24h}</b> шт.\n"
        f"📡 <b>Активных источников (чатов) за 24ч:</b> <b>{ch_24h}</b>\n"
        f"🎯 <b>Квалифицировано лидов за 24ч:</b> <b>{leads_24h}</b> шт.\n"
        f"💰 <b>Выкуплено лидов за 24ч:</b> <b>{sold_24h}</b> шт.\n"
        f"💾 <b>Размер БД на данный момент:</b> <b>{db_size_str}</b>"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "analytics_channels_heat")
async def analytics_channels_heat_callback(callback: CallbackQuery):
    await callback.message.answer("📈 <b>Загрузка карты эффективности каналов...</b>", parse_mode="HTML")
    await render_channels_view(callback, page=0)


@router.callback_query(F.data == "analytics_scanner_health")
async def analytics_scanner_health_callback(callback: CallbackQuery):
    await check_scanner_health_handler(callback)


@router.message(F.text.contains("Здоровье сканера") | F.text.contains("Здоровье"))
@router.message(Command("health"))
@router.message(Command("scanner"))
@router.callback_query(F.data == "restart_scanner_cmd")
async def check_scanner_health_handler(event: Union[Message, CallbackQuery]):
    telegram_id = event.from_user.id
    from datetime import datetime, timezone, timedelta
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, event.from_user.first_name or "", event.from_user.username or "")
        if partner.role not in ["ADMIN", "SUPERADMIN"]:
            msg = "❌ Проверка статуса доступна только для Администраторов и Суперадминистраторов."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

        cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
        cutoff_15m = datetime.now(timezone.utc) - timedelta(minutes=15)
        total_db_logs = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
        logs_24h_count = (await session.execute(select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_24h))).scalar() or 0
        logs_1h_count = (await session.execute(select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_1h))).scalar() or 0
        logs_pass_count = (await session.execute(select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_15m))).scalar() or 0

        ch_1h_count = (await session.execute(select(func.count(func.distinct(UserActivityLog.chat_title))).where(UserActivityLog.timestamp >= cutoff_1h))).scalar() or 0
        ch_24h_count = (await session.execute(select(func.count(func.distinct(UserActivityLog.chat_title))).where(UserActivityLog.timestamp >= cutoff_24h))).scalar() or 0

        db_size_str = await get_db_size_mb(session)
        total_channels = (await session.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0
        joined_channels = (await session.execute(select(func.count(MonitoredChannel.id)).where(MonitoredChannel.status == "JOINED"))).scalar() or 0

    from src.api.app import ingestor

    if isinstance(event, CallbackQuery) and event.data == "restart_scanner_cmd":
        await event.answer("🔄 Выполняется перезапуск сканера...", show_alert=False)
        if ingestor:
            import asyncio
            asyncio.create_task(ingestor.restart_scraper_loop())
            await event.message.answer("🔄 <b>Сборщик сообщений и прослушка чатов успешно перезапущены!</b>", parse_mode="HTML")
        else:
            await event.message.answer("❌ <b>Ошибка:</b> Сканер прослушки не инициализирован.", parse_mode="HTML")
        return

    if not ingestor or not ingestor._is_running:
        status_str = "🔴 ВЫКЛЮЧЕН / СБОЙ"
        check_str = "Не выполняется"
        last_msg_str = "Неизвестно"
        scraped_count = 0
    else:
        userbot_active = ingestor.app is not None and ingestor._is_running
        mode_str = "🟢 Юзербот (перехват групп в реальном времени)" if userbot_active else "🌐 Публичный скрапер (веб-превью)"
        status_str = f"🟢 АКТИВЕН [{mode_str}]"
        if getattr(ingestor, "last_check_at", None):
            check_sec = int((datetime.now(timezone.utc) - ingestor.last_check_at).total_seconds())
            check_str = f"<b>{check_sec}</b> сек. назад" if check_sec < 60 else f"<b>{check_sec // 60}</b> мин. назад"
        else:
            check_str = "Только что"

        if getattr(ingestor, "last_scraped_at", None):
            msg_sec = int((datetime.now(timezone.utc) - ingestor.last_scraped_at).total_seconds())
            if msg_sec < 60:
                last_msg_str = f"<b>{msg_sec}</b> сек. назад"
            elif msg_sec < 3600:
                last_msg_str = f"<b>{msg_sec // 60}</b> мин. назад"
            else:
                last_msg_str = f"<b>{msg_sec // 3600}</b> ч. <b>{(msg_sec % 3600) // 60}</b> мин. назад"
        else:
            last_msg_str = "Ещё не было в этой сессии"
        scraped_count = getattr(ingestor, "scraped_count", 0)

    health_card = (
        f"🩺 <b>СТАТУС И МОНИТОРИНГ ЗДОРОВЬЯ СКАНИРОВАНИЯ</b>\n"
        f"───────────────────────────\n\n"
        f"📡 <b>Состояние сборщика:</b> {status_str}\n"
        f"⏱ <b>Проверка чатов:</b> {check_str}\n"
        f"⏱ <b>Последнее НОВОЕ сообщение из чатов:</b> {last_msg_str}\n\n"
        f"📡 <b>Активно отсканировано каналов за 1 час:</b> <b>{ch_1h_count}</b> из {joined_channels} (всего {total_channels})\n"
        f"📡 <b>Активно отсканировано каналов за 24 часа:</b> <b>{ch_24h_count}</b> из {joined_channels}\n"
        f"💬 <b>Новых сообщений (час - проход):</b> <b>{logs_1h_count} - {logs_pass_count}</b> шт.\n"
        f"💬 <b>Новых сообщений за 24 часа:</b> <b>{logs_24h_count}</b> шт.\n"
        f"📊 <b>Всего сообщений в базе (CDP):</b> <b>{total_db_logs}</b> шт.\n"
        f"💾 <b>Размер базы данных (CDP):</b> <b>{db_size_str}</b>\n"
        f"🛡 <b>Авто-проверщик (Watchdog):</b> 🟢 Активен (порог 5 мин.)\n\n"
        f"💡 <i>Юзербот и скрапер опрашивают все {total_channels} отслеживаемых чатов в непрерывном цикле.</i>"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Перезапустить сканер вручную", callback_data="restart_scanner_cmd")],
            [InlineKeyboardButton(text="📥 Выгрузить логи сообщений (.csv)", callback_data="export_scanned_logs")]
        ]
    )

    if isinstance(event, CallbackQuery):
        await event.message.answer(health_card, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(health_card, reply_markup=kb, parse_mode="HTML")


@router.message(Command("export_logs"))
@router.message(Command("export"))
@router.callback_query(F.data == "export_scanned_logs")
async def export_scanned_logs_handler(event: Union[Message, CallbackQuery]):
    telegram_id = event.from_user.id
    from datetime import datetime
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            msg = "❌ Экспорт доступен только для Администраторов и Суперадминистраторов."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

        stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(5000)
        logs = list((await session.execute(stmt)).scalars().all())

    if not logs:
        msg = "ℹ️ В базе данных пока нет отсканированных сообщений."
        if isinstance(event, CallbackQuery):
            await event.answer(msg, show_alert=True)
        else:
            await event.answer(msg)
        return

    if isinstance(event, CallbackQuery):
        await event.answer("⏳ Формирование CSV файла...")

    import csv
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Timestamp (UTC)", "Chat Title", "User ID", "Message ID", "Message Text"])

    for log in logs:
        ts_str = log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else ""
        writer.writerow([
            ts_str,
            log.chat_title or "",
            log.user_id,
            log.message_id,
            log.message_text or ""
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"scanned_messages_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    input_file = BufferedInputFile(csv_bytes, filename=filename)

    caption = f"📊 <b>Выгрузка отсканированных сообщений</b>\nСохранено сообщений из базы: <b>{len(logs)}</b> шт."
    if isinstance(event, CallbackQuery):
        await event.message.answer_document(document=input_file, caption=caption, parse_mode="HTML")
    else:
        await event.answer_document(document=input_file, caption=caption, parse_mode="HTML")




@router.callback_query(F.data == "open_management_panel")
@router.message(F.text == "⚙️ Управление проектом")
@router.message(Command("management"))
@router.message(Command("control"))
async def open_superadmin_management_panel(event):
    telegram_id = event.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["SUPERADMIN", "ADMIN"]:
            msg = "⚠️ Эта панель доступна только Администраторам проекта."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

    from src.bot.keyboards import get_superadmin_management_keyboard
    kb = get_superadmin_management_keyboard()

    panel_text = (
        "⚙️ <b>ЕДИНЫЙ ЦЕНТР УПРАВЛЕНИЯ ПРОЕКТОМ (LEADRADAR)</b>\n"
        "───────────────────────────\n\n"
        "👑 <b>Добро пожаловать в единую панель администрирования!</b>\n"
        "Выберите интересующий раздел управления ниже:\n\n"
        "🎯 <b>Автопоиск чатов & ГЕО</b> — ключевые слова, ГЕО-теги (Дубай, Вьетнам, Пхукет, Бали) и ручной запуск ИИ-сканера.\n"
        "👑 <b>Роли & Блокировки</b> — поиск пользователей, изменение ролей (VIP, Admin), заморозка и баны.\n"
        "📡 <b>Каналы прослушки</b> — прослушиваемые чаты (78+), добавление/удаление каналов.\n"
        "🤖 <b>Скаутинг с Grok AI</b> — диалоговый ИИ-скаутинг целевых сообществ.\n"
        "📊 <b>Метрики & Здоровье</b> — системная аналитика, поток лидов и лог ошибок.\n"
        "🧠 <b>Обучение ИИ</b> — управление обучающими примерами скорера (/study).\n"
        "💸 <b>Заявки на модерацию</b> — проверка новых регистраций и выплат."
    )

    if isinstance(event, CallbackQuery):
        await event.answer()
        try:
            await event.message.edit_text(panel_text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await event.message.answer(panel_text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(panel_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin_open_discovery")
async def admin_open_discovery_callback(callback: CallbackQuery):
    await callback.answer()
    dummy_msg = callback.message
    dummy_msg.from_user = callback.from_user
    await cmd_discovery_keywords(dummy_msg)


@router.callback_query(F.data == "admin_open_study")
async def admin_open_study_callback(callback: CallbackQuery):
    await callback.answer()
    dummy_msg = callback.message
    dummy_msg.from_user = callback.from_user
    await list_study_exemplars_handler(dummy_msg)


@router.callback_query(F.data == "admin_open_pending")
async def admin_open_pending_callback(callback: CallbackQuery):
    await callback.answer()
    dummy_msg = callback.message
    dummy_msg.from_user = callback.from_user
    await list_pending_users_cmd(dummy_msg)


@router.callback_query(F.data == "open_analytics_menu")
async def open_analytics_menu_callback(callback: CallbackQuery):
    await callback.answer()
    from src.bot.keyboards import get_analytics_inline_keyboard
    kb = get_analytics_inline_keyboard()
    txt = (
        "📊 <b>СИСТЕМНАЯ АНАЛИТИКА И МОНИТОРИНГ</b>\n"
        "───────────────────────────\n\n"
        "Выберите раздел отчетности:"
    )
    try:
        await callback.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(txt, reply_markup=kb, parse_mode="HTML")


@router.message(F.text.startswith("📊 Статистика"))
@router.message(Command("stats"))
@router.message(Command("admin"))
async def show_admin_stats_handler(message: Message):
    from sqlalchemy import func, update
    from datetime import datetime, timezone, timedelta
    cutoff_3h = datetime.now(timezone.utc) - timedelta(hours=3)

    async with AsyncSessionLocal() as session:
        # Auto-expire AVAILABLE leads created > 3h ago
        await session.execute(
            update(Lead)
            .where(Lead.status == "AVAILABLE", Lead.created_at < cutoff_3h)
            .values(status="EXPIRED")
        )
        await session.commit()

        users_count = (await session.execute(select(func.count(UserProfile.user_id)))).scalar() or 0
        logs_count = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
        leads_count = (await session.execute(
            select(func.count(Lead.id)).where(Lead.status == "AVAILABLE", Lead.created_at >= cutoff_3h)
        )).scalar() or 0
        hot_leads_count = (await session.execute(
            select(func.count(Lead.id)).where(Lead.status == "AVAILABLE", Lead.temperature == "HOT", Lead.created_at >= cutoff_3h)
        )).scalar() or 0
        sold_leads_count = (await session.execute(select(func.count(Lead.id)).where(Lead.status == "SOLD"))).scalar() or 0
        partners_count = (await session.execute(select(func.count(Partner.id)))).scalar() or 0

        channels_res = await session.execute(select(MonitoredChannel))
        channels = list(channels_res.scalars().all())
        joined_ch_count = len([c for c in channels if c.status == "JOINED"])

        purchases_res = await session.execute(select(LeadPurchase))
        purchases = list(purchases_res.scalars().all())
        revenue = sum(float(p.price_paid) for p in purchases)

    stats_text = (
        "📊 <b>СИСТЕМНАЯ СТАТИСТИКА И МЕТРИКИ (ADMIN)</b>\n"
        "─────────── Intent Hunter CDP ───────────\n\n"
        f"👥 <b>Профилей пользователей (CDP):</b> {users_count} пользователей\n"
        f"💬 <b>Перехвачено сообщений:</b> {logs_count} логов активности\n"
        f"🎯 <b>Активных лидов (за 3 часа):</b> {leads_count} лидов\n"
        f"🔥 <b>Горячие лиды (HOT):</b> {hot_leads_count} лидов\n"
        f"💰 <b>Выкуплено лидов:</b> {sold_leads_count} шт. (Доход: <b>{revenue:.2f} ₽</b>)\n"
        f"🤝 <b>B2B-Партнеров / Админов:</b> {partners_count} аккаунтов\n"
        f"📡 <b>Отслеживаемые чаты:</b> {len(channels)} каналов (🟢 {joined_ch_count} подключены)\n\n"
        f"🤖 <b>ИИ Модель:</b> Groq (qwen/qwen3.6-27b) / Gemini 2.5 Flash\n"
        f"⚡ <b>Статус системы:</b> Live Production Monitoring Active"
    )

    await message.answer(stats_text, parse_mode="HTML")


@router.message(F.text == "👤 Мой Профиль")
@router.message(F.text.contains("Мой Профиль") | F.text.contains("Профиль") | F.text.contains("профиль"))
@router.message(Command("profile"))
@router.message(Command("me"))
async def show_profile(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, message.from_user.first_name or "", message.from_user.username or "")

        purchases_stmt = select(LeadPurchase).where(LeadPurchase.partner_id == partner.id)
        p_res = await session.execute(purchases_stmt)
        purchases = list(p_res.scalars().all())

        sn = partner.subscribed_niches or []
        if not sn or "all" in sn or len(sn) >= len(NICHE_NAMES):
            subbed_niches_str = "🔥 Все ниши и рубрики"
        else:
            subbed_niches_str = ", ".join([NICHE_NAMES.get(n, n) for n in sn])

        sl = partner.subscribed_locations or []
        if not sl or "all" in sl or len(sl) >= len(LOCATION_NAMES):
            subbed_locs_str = "📍 Все гео и локации"
        else:
            subbed_locs_str = ", ".join([LOCATION_NAMES.get(l, l) for l in sl])

        role_str = ROLE_LABELS.get(partner.role, partner.role)
        status_str = "🟢 Одобрен" if partner.moderation_status == "APPROVED" else "⏳ Ожидает модерации"

        profile_text = (
            f"<b>👤 Профиль Пользователя / Партнера:</b>\n"
            f"───────────────────────────\n\n"
            f"<b>Компания / Имя:</b> {html.quote(partner.company_name)}\n"
            f"<b>Telegram ID:</b> <code>{partner.telegram_id}</code>\n"
            f"<b>Статус / Роль:</b> <b>{role_str}</b> ({status_str})\n"
            f"<b>Баланс:</b> <b>${partner.balance:.2f} USD</b> ({int(partner.balance)} контактов)\n"
            f"<b>Всего выкуплено лидов:</b> {len(purchases)} шт.\n\n"
            f"🏷️ <b>Подписка на ниши:</b> {subbed_niches_str}\n"
            f"📍 <b>Подписка на гео:</b> {subbed_locs_str}\n\n"
            f"💡 <i>Используйте кнопки ниже для изменения подписок или пополнения баланса:</i>"
        )

        await message.answer(
            profile_text,
            reply_markup=get_profile_inline_keyboard(),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "profile_view")
async def profile_view_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")

        purchases_stmt = select(LeadPurchase).where(LeadPurchase.partner_id == partner.id)
        purchases = list((await session.execute(purchases_stmt)).scalars().all())

        sn = partner.subscribed_niches or []
        if not sn or "all" in sn or len(sn) >= len(NICHE_NAMES):
            subbed_niches_str = "🔥 Все ниши и рубрики"
        else:
            subbed_niches_str = ", ".join([NICHE_NAMES.get(n, n) for n in sn])

        sl = partner.subscribed_locations or []
        if not sl or "all" in sl or len(sl) >= len(LOCATION_NAMES):
            subbed_locs_str = "📍 Все гео и локации"
        else:
            subbed_locs_str = ", ".join([LOCATION_NAMES.get(l, l) for l in sl])

        role_str = ROLE_LABELS.get(partner.role, partner.role)
        status_str = "🟢 Одобрен" if partner.moderation_status == "APPROVED" else "⏳ Ожидает модерации"

        profile_text = (
            f"<b>👤 Профиль Пользователя / Партнера:</b>\n"
            f"───────────────────────────\n\n"
            f"<b>Компания / Имя:</b> {html.quote(partner.company_name)}\n"
            f"<b>Telegram ID:</b> <code>{partner.telegram_id}</code>\n"
            f"<b>Статус / Роль:</b> <b>{role_str}</b> ({status_str})\n"
            f"<b>Баланс:</b> <b>${partner.balance:.2f} USD</b> ({int(partner.balance)} контактов)\n"
            f"<b>Всего выкуплено лидов:</b> {len(purchases)} шт.\n\n"
            f"🏷️ <b>Подписка на ниши:</b> {subbed_niches_str}\n"
            f"📍 <b>Подписка на гео:</b> {subbed_locs_str}\n\n"
            f"💡 <i>Используйте кнопки ниже для изменения подписок или пополнения баланса:</i>"
        )
        try:
            await callback.message.edit_text(profile_text, reply_markup=get_profile_inline_keyboard(), parse_mode="HTML")
        except Exception:
            await callback.message.answer(profile_text, reply_markup=get_profile_inline_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "edit_user_niches")
async def edit_user_niches_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()
        niches = partner.subscribed_niches if partner else ["all"]

    is_onb = (partner.onboarding_step == 0) if partner else False
    text = (
        f"🏷️ <b>Настройка подписки на Ниши и Рубрики:</b>\n"
        f"───────────────────────────\n\n"
        f"Отметьте галочками категории клиентов, от которых вы хотите получать лиды:"
    )
    kb = get_niche_inline_keyboard(niches or ["all"], is_onboarding=is_onb)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "edit_user_locations")
async def edit_user_locations_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()
        locs = partner.subscribed_locations if partner else ["all"]

    is_onb = (partner.onboarding_step == 0) if partner else False
    text = (
        f"📍 <b>Настройка подписки на Гео и Локации:</b>\n"
        f"───────────────────────────\n\n"
        f"Отметьте галочками регионы, от которых вы хотите получать лиды:"
    )
    kb = get_location_inline_keyboard(locs or ["all"], is_onboarding=is_onb)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "onb_step:locations")
async def onboarding_locations_step(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()
        locs = partner.subscribed_locations if partner else ["all"]

    text = (
        f"📍 <b>Шаг 2 из 2: Выберите Локации и Страны</b>\n"
        f"───────────────────────────\n\n"
        f"Отметьте галочками гео-локации, откуда вы хотите получать заявки от клиентов (можно выбрать все или несколько):"
    )
    kb = get_location_inline_keyboard(locs or ["all"], is_onboarding=True)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "onb_finish")
async def onboarding_finish_step(callback: CallbackQuery, state: FSMContext = None):
    if state:
        await state.clear()
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()
        if partner:
            partner.onboarding_step = 2
            await session.commit()

        sn = partner.subscribed_niches or []
        if not sn or "all" in sn or len(sn) >= len(NICHE_NAMES):
            subbed_niches_str = "🔥 Все ниши и рубрики"
        else:
            subbed_niches_str = ", ".join([NICHE_NAMES.get(n, n) for n in sn])

        sl = partner.subscribed_locations or []
        if not sl or "all" in sl or len(sl) >= len(LOCATION_NAMES):
            subbed_locs_str = "📍 Все гео и локации"
        else:
            subbed_locs_str = ", ".join([LOCATION_NAMES.get(l, l) for l in sl])

        role_str = ROLE_LABELS.get(partner.role, partner.role)
        is_mon = partner.is_monitoring_active

    text = (
        f"✅ <b>НАСТРОЙКА УСПЕШНО ЗАВЕРШЕНА!</b>\n"
        f"───────────────────────────\n\n"
        f"🏷️ <b>Выбранные ниши:</b> {subbed_niches_str}\n"
        f"📍 <b>Выбранные локации:</b> {subbed_locs_str}\n"
        f"💳 <b>Ваш текущий баланс:</b> <b>${partner.balance:.2f} USD</b>\n\n"
        f"🚀 ИИ-сканер переведен в активный режим. Вы будете получать уведомления по выбранным критериям!"
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.message.answer(
        "💡 Используйте главное меню ниже для управления подписками и выкупа лидов:",
        reply_markup=get_main_reply_keyboard(is_mon, partner.role),
        parse_mode="HTML"
    )
    await callback.answer("🚀 Настройка завершена!")


@router.callback_query(F.data.startswith("toggle_niche:"))
async def toggle_niche_callback(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")

        current_niches = list(partner.subscribed_niches or [])
        all_codes = list(NICHE_NAMES.keys())

        if code == "all":
            if "all" in current_niches or len(current_niches) >= len(all_codes):
                current_niches = []
            else:
                current_niches = ["all"] + all_codes
        else:
            if "all" in current_niches:
                current_niches = list(all_codes)

            if code in current_niches:
                current_niches.remove(code)
            else:
                current_niches.append(code)

        partner.subscribed_niches = current_niches
        await session.commit()
        await session.refresh(partner)

        is_onb = (partner.onboarding_step < 2)
        kb = get_niche_inline_keyboard(partner.subscribed_niches, is_onboarding=is_onb)
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer("Ниши обновлены")


@router.callback_query(F.data.startswith("toggle_loc:"))
async def toggle_loc_callback(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")

        current_locs = list(partner.subscribed_locations or [])
        all_loc_codes = list(LOCATION_NAMES.keys())

        if code == "all":
            if "all" in current_locs or len(current_locs) >= len(all_loc_codes):
                current_locs = []
            else:
                current_locs = ["all"] + all_loc_codes
        else:
            if "all" in current_locs:
                current_locs = list(all_loc_codes)

            if code in current_locs:
                current_locs.remove(code)
            else:
                current_locs.append(code)

        partner.subscribed_locations = current_locs
        await session.commit()
        await session.refresh(partner)

        is_onb = (partner.onboarding_step < 2)
        kb = get_location_inline_keyboard(partner.subscribed_locations, is_onboarding=is_onb)
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer("Локации обновлены")


@router.callback_query(F.data.startswith("onb_step:"))
async def onboarding_step_callback(callback: CallbackQuery):
    step_target = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")

        if step_target == "locations":
            partner.onboarding_step = 1
            await session.commit()

            kb = get_location_inline_keyboard(partner.subscribed_locations or ["all"], is_onboarding=True)
            text = (
                f"🌍 <b>Шаг 2 из 2: Выберите Географию и Локации</b>\n"
                f"───────────────────────────\n\n"
                f"Отметьте галочками регионы, откуда вас интересуют горячие целевые клиенты (можно выбрать все или несколько):"
            )
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            await callback.answer("🌍 Переход к выбору локаций")

        elif step_target == "niches":
            partner.onboarding_step = 0
            await session.commit()

            kb = get_niche_inline_keyboard(partner.subscribed_niches or ["all"], is_onboarding=True)
            text = (
                f"📋 <b>Шаг 1 из 2: Выберите Ниши и Рубрики</b>\n"
                f"───────────────────────────\n\n"
                f"Отметьте галочками категории клиентов, которые вас интересуют (можно выбрать все или несколько):"
            )
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            await callback.answer("📋 Переход к выбору ниш")



@router.callback_query(F.data == "open_deposit_menu")
async def open_deposit_menu_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(stmt)).scalar_one_or_none()
        balance = partner.balance if partner else 0.0

    await callback.message.answer(
        f"<b>💳 Ваш текущий баланс: ${balance:.2f} USD</b>\n"
        f"─────────── Тарифы и Оплата ───────────\n\n"
        f"📌 <b>Стоимость 1 контакта лида:</b> <b>$1.00 USD</b>\n"
        f"📌 <b>Минимальная сумма пополнения:</b> <b>от $100.00 USD</b>\n"
        f"📌 <b>Способ оплаты:</b> Нативные 🌟 <b>Telegram Stars (XTR)</b>\n\n"
        f"Выберите пакет пополнения баланса:",
        reply_markup=get_topup_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(F.text == "🎯 Маркетплейс лидов")
@router.message(F.text.contains("Маркетплейс") | F.text.contains("маркетплейс") | F.text.contains("Маркет лидов"))
@router.message(Command("leads"))
@router.message(Command("marketplace"))
@router.message(Command("shop"))
async def show_leads_marketplace_handler(message: Message):
    """Displays active available leads directly in Telegram chat with instant 1-click buy buttons."""
    telegram_id = message.from_user.id
    cutoff_3h = datetime.now(timezone.utc) - timedelta(hours=3)
    async with AsyncSessionLocal() as session:
        # Auto-expire AVAILABLE leads created > 3h ago
        await session.execute(
            update(Lead)
            .where(Lead.status == "AVAILABLE", Lead.created_at < cutoff_3h)
            .values(status="EXPIRED")
        )
        await session.commit()

        partner_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(partner_stmt)).scalar_one_or_none()
        user_balance = partner.balance if partner else 0.0

        leads_stmt = select(Lead).where(Lead.status == "AVAILABLE", Lead.created_at >= cutoff_3h).order_by(Lead.created_at.desc()).limit(10)
        leads = list((await session.execute(leads_stmt)).scalars().all())

    mp_url = os.getenv("MARKETPLACE_APP_URL", "https://inthunter-production.up.railway.app/marketplace")
    tma_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть Веб-Маркетплейс (TMA)", web_app=WebAppInfo(url=mp_url))]
        ]
    )

    if not leads:
        await message.answer(
            "🎯 <b>Маркетплейс лидов</b>\n\n"
            "В данный момент все свежие лиды выкуплены или подготавливаются ИИ-сканером.\n"
            "Вы можете открыть веб-версию маркетплейса с фильтрами по кнопке ниже:",
            reply_markup=tma_kb,
            parse_mode="HTML"
        )
        return

    await message.answer(
        f"🎯 <b>МАРКЕТПЛЕЙС ГОРЯЧИХ ЛИДОВ (Доступно: {len(leads)}):</b>\n"
        f"💳 Ваш текущий баланс: <b>${user_balance:.2f} USD</b>\n"
        f"───────────\n"
        f"Выкупите контакт любого лида в 1 клик прямо здесь или откройте полноэкранную веб-версию:",
        reply_markup=tma_kb,
        parse_mode="HTML"
    )

    for lead in leads:
        rubric_label = NICHE_NAMES.get(lead.niche_code, lead.niche_code)
        conf_pct = int((lead.confidence_score or 0.85) * 100)
        lead_card = (
            f"🏷️ <b>{html.quote(rubric_label)}</b> | 🔥 <b>{lead.temperature} ({conf_pct}%)</b>\n\n"
            f"💬 <i>\"{html.quote(lead.intent_summary)}\"</i>\n\n"
            f"💡 <b>Sales Hook:</b> «{html.quote(lead.sales_hook)}»\n"
            f"💰 Стоимость контакта: <b>$1.00 USD</b>"
        )
        kb = get_buy_lead_keyboard(lead.id, float(lead.price or 1.00))
        await message.answer(lead_card, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "💳 Баланс")
@router.message(F.text.contains("Баланс") | F.text.contains("баланс"))
@router.message(Command("balance"))
@router.message(Command("topup"))
async def show_balance(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        balance = partner.balance if partner else 0.0

    await message.answer(
        f"<b>💳 Ваш текущий баланс: ${balance:.2f} USD</b>\n"
        f"─────────── Тарифы и Оплата ───────────\n\n"
        f"📌 <b>Стоимость 1 контакта лида:</b> <b>$1.00 USD</b>\n"
        f"📌 <b>Минимальная сумма пополнения:</b> <b>от $100.00 USD</b>\n"
        f"📌 <b>Способ оплаты:</b> Нативные 🌟 <b>Telegram Stars (XTR)</b>\n\n"
        f"Выберите пакет пополнения баланса:",
        reply_markup=get_topup_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("stars_invoice:"))
async def send_stars_invoice_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    usd_amount = int(parts[1])
    stars_amount = int(parts[2])

    from src.bot.alert_bot import bot
    if bot:
        try:
            await bot.send_invoice(
                chat_id=callback.from_user.id,
                title=f"Пополнение баланса на ${usd_amount} USD",
                description=f"Пакет пополнения баланса на {usd_amount} контактов горячих лидов (${usd_amount} USD = {stars_amount} Stars)",
                payload=f"stars_topup:{usd_amount}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label="Telegram Stars", amount=stars_amount)]
            )
            await callback.answer("🌟 Счет на оплату Telegram Stars отправлен!")
        except Exception as e:
            logger.error(f"Error sending Stars invoice: {e}")
            await callback.answer("❌ Ошибка отправки счета. Попробуйте еще раз.", show_alert=True)


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    telegram_id = message.from_user.id

    if payload.startswith("stars_topup:"):
        usd_amount = float(payload.split(":")[1])

        async with AsyncSessionLocal() as session:
            stmt = select(Partner).where(Partner.telegram_id == telegram_id)
            partner = (await session.execute(stmt)).scalar_one_or_none()
            if partner:
                partner.balance = float(partner.balance) + usd_amount
                if partner.role == "DEMO" and partner.moderation_status == "APPROVED":
                    partner.role = "REGULAR"
                await session.commit()
                await session.refresh(partner)

                await message.answer(
                    f"🎉 <b>ОПЛАТА УСПЕШНО ПОЛУЧЕНА!</b>\n\n"
                    f"Ваш баланс пополнен на: <b>+${usd_amount:.2f} USD</b>!\n"
                    f"Текущий баланс: <b>${partner.balance:.2f} USD</b> ({int(partner.balance)} контактов лидов)",
                    reply_markup=get_main_reply_keyboard(partner.is_monitoring_active, partner.role),
                    parse_mode="HTML"
                )


@router.callback_query(F.data.startswith("mod:"))
async def moderate_user_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    target_id = int(parts[1])
    new_role = parts[2]
    admin_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Недостаточно прав для модерации пользователей.", show_alert=True)
            return

        target_stmt = select(Partner).where(Partner.telegram_id == target_id)
        target_partner = (await session.execute(target_stmt)).scalar_one_or_none()

        if not target_partner:
            await callback.answer("❌ Пользователь не найден в базе данных.", show_alert=True)
            return

        if new_role == "REJECT":
            target_partner.moderation_status = "REJECTED"
            msg_status = "отклонен ❌"
        else:
            target_partner.role = new_role
            target_partner.moderation_status = "APPROVED"
            msg_status = f"одобрен как <b>{ROLE_LABELS.get(new_role, new_role)}</b> ✅"

        await session.commit()

        from src.bot.alert_bot import bot
        if bot and new_role != "REJECT":
            try:
                await bot.send_message(
                    chat_id=target_id,
                    text=(
                        f"🎉 <b>ВАШ АККАУНТ УСПЕШНО ОДОБРЕН МОДЕРАТОРОМ!</b>\n\n"
                        f"<b>Присвоенный статус:</b> {ROLE_LABELS.get(new_role, new_role)}\n"
                        f"Теперь вам доступен функционал системы."
                    ),
                    reply_markup=get_main_reply_keyboard(target_partner.is_monitoring_active, target_partner.role),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error notifying moderated user {target_id}: {e}")

        await callback.answer(f"Аккаунт {target_id} {msg_status}!", show_alert=True)
        await callback.message.edit_text(
            f"✅ <b>Модерация завершена:</b> Пользователь <code>{target_id}</code> {msg_status}.",
            parse_mode="HTML"
        )


@router.message(Command("pending"))
async def list_pending_users_cmd(message: Message):
    """Admin command: /pending - List all pending user registrations"""
    admin_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Отказано в доступе.")
            return

        pending_res = await session.execute(
            select(Partner).where(Partner.moderation_status == "PENDING")
        )
        pending_users = list(pending_res.scalars().all())

    if not pending_users:
        await message.answer("✅ Нет новых пользователей, ожидающих модерации.")
        return

    await message.answer(f"⏳ <b>Пользователи на модерации ({len(pending_users)}):</b>", parse_mode="HTML")
    from src.bot.alert_bot import bot
    if bot:
        for p in pending_users:
            mod_card = (
                f"👤 <b>Заявка от пользователя:</b>\n"
                f"<b>Имя:</b> {html.quote(p.company_name)}\n"
                f"<b>Telegram ID:</b> <code>{p.telegram_id}</code>\n"
                f"<b>Текущая роль:</b> {p.role}\n\n"
                f"Выберите статус:"
            )
            await message.answer(
                mod_card,
                reply_markup=get_moderation_inline_keyboard(p.telegram_id),
                parse_mode="HTML"
            )


@router.message(Command("setrole"))
async def set_role_cmd(message: Message):
    """Admin command: /setrole <telegram_id> <DEMO|REGULAR|VIP|ADMIN|SUPERADMIN>"""
    admin_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Отказано в доступе.")
            return

        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("⚠️ Использование: <code>/setrole &lt;telegram_id&gt; &lt;DEMO|REGULAR|VIP|ADMIN|SUPERADMIN&gt;</code>", parse_mode="HTML")
            return

        try:
            target_id = int(parts[1])
            new_role = parts[2].upper()
            if new_role not in ROLE_LABELS:
                await message.answer(f"❌ Неверная роль. Допустимые: {', '.join(ROLE_LABELS.keys())}")
                return

            t_stmt = select(Partner).where(Partner.telegram_id == target_id)
            target_p = (await session.execute(t_stmt)).scalar_one_or_none()
            if not target_p:
                await message.answer(f"❌ Пользователь с Telegram ID {target_id} не найден.")
                return

            target_p.role = new_role
            target_p.moderation_status = "APPROVED"
            await session.commit()

            await message.answer(f"✅ Назначена роль <b>{ROLE_LABELS[new_role]}</b> для ID {target_id}!", parse_mode="HTML")

        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("setbalance"))
async def set_balance_cmd(message: Message):
    """Admin command: /setbalance <telegram_id> <usd_amount>"""
    admin_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Отказано в доступе.")
            return

        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("⚠️ Использование: <code>/setbalance &lt;telegram_id&gt; &lt;usd_amount&gt;</code>\nПример: <code>/setbalance 777000111 100</code>", parse_mode="HTML")
            return

        try:
            target_id = int(parts[1])
            usd_amount = float(parts[2])

            t_stmt = select(Partner).where(Partner.telegram_id == target_id)
            target_p = (await session.execute(t_stmt)).scalar_one_or_none()
            if not target_p:
                await message.answer(f"❌ Пользователь с Telegram ID {target_id} не найден.")
                return

            target_p.balance = usd_amount
            await session.commit()

            await message.answer(f"✅ Баланс пользователя ID {target_id} установлен на <b>${usd_amount:.2f} USD</b>!", parse_mode="HTML")

        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("timeline"))
@router.message(Command("user"))
async def show_user_timeline_cmd(message: Message):
    """Admin command: /timeline <user_id> - Display accumulated multi-chat activity timeline"""
    admin_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        admin_stmt = select(Partner).where(Partner.telegram_id == admin_id)
        admin_obj = (await session.execute(admin_stmt)).scalar_one_or_none()
        if not admin_obj or admin_obj.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Отказано в доступе.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("⚠️ Использование: <code>/timeline &lt;user_id&gt;</code>\nПример: <code>/timeline 676797576</code>", parse_mode="HTML")
            return

        try:
            target_user_id = int(parts[1])

            # Fetch user profile
            u_stmt = select(UserProfile).where(UserProfile.user_id == target_user_id)
            user_prof = (await session.execute(u_stmt)).scalar_one_or_none()

            # Fetch activity logs across all chats
            act_stmt = select(UserActivityLog).where(UserActivityLog.user_id == target_user_id).order_by(UserActivityLog.timestamp.desc()).limit(20)
            activities = list((await session.execute(act_stmt)).scalars().all())

            # Fetch leads
            lead_stmt = select(Lead).where(Lead.user_id == target_user_id)
            leads = list((await session.execute(lead_stmt)).scalars().all())

            if not activities:
                await message.answer(f"❌ Совокупная активность пользователя ID {target_user_id} не найдена в базе данных.")
                return

            username_str = f"@{user_prof.username}" if user_prof and user_prof.username else f"ID {target_user_id}"
            lines = []
            for act in reversed(activities):
                ts = act.timestamp.strftime("%d %b %H:%M")
                lines.append(f"• <b>{ts}</b> [{html.quote(act.chat_title)}]: <i>\"{html.quote(act.message_text)}\"</i>")

            timeline_text = "\n".join(lines)
            lead_summary = f"🔥 <b>Найдено лидов:</b> {len(leads)} шт." if leads else "❄️ Лидов не найдено"

            await message.answer(
                f"👤 <b>СОВОКУПНАЯ АКТИВНОСТЬ ПОЛЬЗОВАТЕЛЯ {username_str}:</b>\n\n"
                f"<b>Всего записей в базе:</b> {len(activities)} сообщений в разных чатах\n"
                f"<b>Статус квалификации ИИ:</b> {lead_summary}\n\n"
                f"📜 <b>Хронология активности по всем чатам:</b>\n"
                f"{timeline_text}",
                parse_mode="HTML"
            )

        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")


@router.callback_query(F.data.startswith("analyze_lead:"))
async def analyze_lead_callback(callback: CallbackQuery):
    lead_id = callback.data.split(":")[1]
    telegram_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        is_sa = partner.role == "SUPERADMIN" if partner else False

        l_stmt = select(Lead).where(Lead.id == lead_id)
        lead = (await session.execute(l_stmt)).scalar_one_or_none()

        if not lead:
            await callback.answer("❌ Карточка лида не найдена.", show_alert=True)
            return

        u_stmt = select(UserProfile).where(UserProfile.user_id == lead.user_id)
        user_prof = (await session.execute(u_stmt)).scalar_one_or_none()

        act_stmt = select(UserActivityLog).where(UserActivityLog.user_id == lead.user_id).order_by(UserActivityLog.timestamp.desc()).limit(15)
        activities = list((await session.execute(act_stmt)).scalars().all())

    await callback.answer()

    chat_titles = list(set([a.chat_title for a in activities if a.chat_title]))
    chats_str = ", ".join([f"<b>{html.quote(c)}</b>" for c in chat_titles]) or "Групповые чаты"
    
    # Hide contact details for public/regular users in analysis text
    client_display = "🔒 Скрыт (доступен после выкупа $1.00 USD)"

    timeline_lines = []
    for act in reversed(activities):
        ts = act.timestamp.strftime("%d %b %H:%M")
        timeline_lines.append(f"• <b>{ts}</b> [{html.quote(act.chat_title)}]: <i>\"{html.quote(act.message_text)}\"</i>")

    timeline_fmt = "\n".join(timeline_lines) if timeline_lines else "• Сообщения сохранены в истории"

    loc_code = getattr(lead, "location_code", "global") or "global"
    loc_name = {"dubai": "🇦🇪 Дубай (ОАЭ)", "nhatrang": "🇻🇳 Нячанг (Вьетнам)", "phuket": "🇹🇭 Пхукет (Таиланд)", "bali": "🇮🇩 Бали (Индонезия)"}.get(loc_code, "🌐 Международный / Глобал")

    analysis_card = (
        f"📊 <b>ПОЛНЫЙ ИИ-АНАЛИЗ АКТИВНОСТИ ЛИДА (Groq AI Engine)</b>\n"
        f"───────────────────────────\n"
        f"👤 <b>Клиент:</b> {client_display}\n"
        f"📍 <b>Локация:</b> <b>{loc_name}</b>\n"
        f"🌐 <b>Зафиксирован в чатах ({len(chat_titles)}):</b> {chats_str}\n\n"
        f"🎯 <b>Оценка намерения ИИ:</b>\n"
        f"   • Категория ниши: <b>{lead.niche_code.upper()}</b>\n"
        f"   • Температура лида: <b>{lead.temperature} ({int(lead.confidence_score * 100)}% готовность)</b>\n\n"
        f"📝 <b>Анализ потребности (Intent Summary):</b>\n"
        f"«{html.quote(lead.intent_summary)}»\n\n"
        f"📜 <b>Накопленная история сообщений пользователя по всем чатам:</b>\n"
        f"{timeline_fmt}\n\n"
        f"💡 <b>Рекомендованная стратегия диалога (Sales Hook):</b>\n"
        f"«{html.quote(lead.sales_hook)}»\n"
        f"───────────────────────────"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    buttons = []
    if is_sa:
        buttons.append([InlineKeyboardButton(text="🔑 Показать контакт (Superadmin)", callback_data=f"sa_show_contact:{lead.id}")])
    buttons.append([InlineKeyboardButton(text="💳 Выкупить контакт лида ($1.00 USD)", callback_data=f"buy_lead:{lead.id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.reply(analysis_card, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("sa_show_contact:"))
async def sa_show_contact_callback(callback: CallbackQuery):
    lead_id = callback.data.split(":")[1]
    telegram_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()

        # Strict Superadmin verification check
        if not partner or partner.role != "SUPERADMIN":
            await callback.answer("🔒 Данный контакт доступен только для выкупивших партнеров или Суперадминистратора!", show_alert=True)
            return

        l_stmt = select(Lead).where(Lead.id == lead_id)
        lead = (await session.execute(l_stmt)).scalar_one_or_none()
        if not lead:
            await callback.answer("❌ Лид не найден.", show_alert=True)
            return

        u_stmt = select(UserProfile).where(UserProfile.user_id == lead.user_id)
        user_prof = (await session.execute(u_stmt)).scalar_one_or_none()

    username = f"@{user_prof.username}" if user_prof and user_prof.username else f"ID {lead.user_id}"
    tg_link = f"https://t.me/{user_prof.username}" if user_prof and user_prof.username else f"tg://user?id={lead.user_id}"
    full_name = f"{user_prof.first_name or ''} {user_prof.last_name or ''}".strip() or "Пользователь Telegram"

    contact_card = (
        f"🔑 <b>КОНТАКТНЫЕ ДАННЫЕ ЛИДА (SUPERADMIN ACCESS)</b>\n"
        f"───────────────────────────\n\n"
        f"<b>👤 Клиент:</b> {html.quote(full_name)}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>Прямая ссылка:</b> <a href=\"{tg_link}\">{tg_link}</a>\n"
        f"<b>Telegram ID:</b> <code>{lead.user_id}</code>"
    )

    await callback.answer()
    await callback.message.reply(contact_card, parse_mode="HTML", disable_web_page_preview=True)


# ─── CONFIRM BUY DIALOG ───────────────────────────────────────────────────
@router.callback_query(F.data.startswith("ask_buy:"))
async def ask_buy_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    lead_id = parts[1]
    is_exclusive = (len(parts) > 2 and parts[2] == "excl")
    price = 10.00 if is_exclusive else 1.00

    async with AsyncSessionLocal() as session:
        l_stmt = select(Lead).where(Lead.id == lead_id)
        lead = (await session.execute(l_stmt)).scalar_one_or_none()
        if not lead:
            await callback.answer("❌ Карточка лида не найдена.", show_alert=True)
            return

    from src.bot.keyboards import get_confirm_buy_keyboard
    buy_type = "excl" if is_exclusive else "std"
    confirm_kb = get_confirm_buy_keyboard(lead_id, buy_type=buy_type, price=price)

    type_title = "👑 ЭКСКЛЮЗИВНЫЙ ВЫКУП" if is_exclusive else "🛒 ПОКУПКА ЛИДА"
    confirm_text = (
        f"⚠️ <b>ПОДТВЕРЖДЕНИЕ ПОКУПКИ ({type_title})</b>\n"
        f"───────────────────────────\n\n"
        f"Вы покупаете контакт данного лида за <b>${price:.2f} USD</b>.\n\n"
        f"📌 <b>Запрос:</b> <i>\"{html.quote(lead.intent_summary)}\"</i>\n\n"
        f"<b>Вы подтверждаете списание средств с баланса?</b>"
    )

    await callback.message.reply(confirm_text, reply_markup=confirm_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_buy:"))
async def cancel_buy_callback(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("❌ Покупка отменена")


@router.callback_query(F.data.startswith("do_buy:"))
@router.callback_query(F.data.startswith("buy_lead:"))
async def buy_lead_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    lead_id = parts[1]
    is_exclusive = (len(parts) > 2 and parts[2] == "excl")
    telegram_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(
            session,
            telegram_id,
            callback.from_user.first_name or "",
            callback.from_user.username or ""
        )

        from src.services.purchase_engine import process_lead_purchase
        res = await process_lead_purchase(session, partner.id, lead_id, is_exclusive=is_exclusive)

        if res.get("status") == "error":
            await callback.answer(f"Ошибка: {res.get('message')}", show_alert=True)
            return

        if res.get("status") == "insufficient_balance":
            req_price = res.get("required", 1.0)
            cur_bal = res.get("balance", 0.0)
            await callback.answer(
                f"❌ Недостаточно средств на балансе! Стоимость: ${req_price:.2f} USD, у вас: ${cur_bal:.2f} USD. Пополните баланс кнопкой 💳 Баланс",
                show_alert=True
            )
            return

        lead_info = res["lead"]
        contact_info = res["contact"]
        price = res["price_paid"]
        new_bal = res["new_balance"]

        type_title = "👑 ЭКСКЛЮЗИВНЫЙ ВЫКУП ЛИДА" if is_exclusive else "🛒 ВЫКУП КОНТАКТА ЛИДА"

        purchase_success_text = (
            f"🎉 <b>{type_title}!</b>\n\n"
            f"<b>👤 Клиент:</b> {html.quote(contact_info['full_name'])}\n"
            f"<b>Username:</b> {contact_info['username']}\n"
            f"<b>Прямая ссылка:</b> <a href=\"{contact_info['tg_link']}\">Открыть диалог в Telegram</a>\n"
            f"<b>Telegram ID:</b> <code>{lead_info['user_id']}</code>\n\n"
            f"📌 <b>Суть потребности:</b>\n{html.quote(lead_info['intent_summary'])}\n\n"
            f"💬 <b>Всего сообщений пользователя в системе:</b> <b>{lead_info['user_msg_count']}</b>\n\n"
            f"💰 Списано с баланса: ${price:.2f} USD (Остаток: ${new_bal:.2f} USD)"
        )

        await callback.message.edit_text(purchase_success_text, parse_mode="HTML", disable_web_page_preview=True)
        await callback.answer("✅ Контакт лида успешно выкуплен!", show_alert=True)


# ─── DECRYPT USER MESSAGES CALLBACK ───────────────────────────────────────
@router.callback_query(F.data.startswith("decrypt_msgs:"))
async def decrypt_msgs_callback(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        logs = list((await session.execute(
            select(UserActivityLog)
            .where(UserActivityLog.user_id == user_id)
            .order_by(UserActivityLog.timestamp.desc())
            .limit(20)
        )).scalars().all())

    if not logs:
        await callback.answer("Сообщения пользователя не найдены в базе.", show_alert=True)
        return

    lines = [f"🔍 <b>РАСШИФРОВКА СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЯ (ID {user_id})</b>\n"]
    for i, log in enumerate(reversed(logs), 1):
        ts = (log.timestamp + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if log.timestamp else "—"
        lines.append(f"{i}. <b>[{ts}] {html.quote(log.chat_title or 'Чат')}:</b>\n<i>\"{html.quote(log.message_text)}\"</i>\n")

    msg_text = "\n".join(lines)
    if len(msg_text) > 4000:
        msg_text = msg_text[:3990] + "\n..."
    await callback.message.reply(msg_text, parse_mode="HTML")
    await callback.answer("✅ Расшифровка загружена")


# ─── LEAD ARCHIVE (АРХИВ ЛИДОВ) ───────────────────────────────────────────
@router.message(F.text == "📦 Архив лидов")
@router.message(F.text.contains("Архив") | F.text.contains("архив"))
@router.message(Command("archive"))
async def show_lead_archive_handler(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        partner = (await session.execute(select(Partner).where(Partner.telegram_id == telegram_id))).scalar_one_or_none()
        if not partner:
            await message.answer("❌ Профиль партнера не найден.")
            return

        stmt = (
            select(LeadPurchase, Lead)
            .join(Lead, LeadPurchase.lead_id == Lead.id)
            .where(LeadPurchase.partner_id == partner.id)
            .order_by(LeadPurchase.purchased_at.desc())
        )
        rows = list((await session.execute(stmt)).all())

        if not rows:
            await message.answer("📦 <b>Ваш архив выкупленных лидов пуст.</b>\n\nВыкупите лид в маркетплейсе, чтобы здесь появились контакты клиентов.", parse_mode="HTML")
            return

        archive_lines = [f"📦 <b>АРХИВ ВЫКУПЛЕННЫХ ЛИДОВ (Всего: {len(rows)})</b>\n"]
        for pur, lead in rows:
            dt_str = (pur.purchased_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if pur.purchased_at else "—"
            price_val = float(pur.price_paid or 1.0)
            is_vip = price_val >= 9.0 or lead.status == "SOLD"
            vip_badge = " [⭐ V.I.P. Выкуп]" if is_vip else ""

            up = (await session.execute(select(UserProfile).where(UserProfile.user_id == lead.user_id))).scalar_one_or_none()
            contact_str = f"@{up.username}" if up and up.username else f"ID {lead.user_id}"
            tg_link = f"https://t.me/{up.username}" if up and up.username else f"tg://user?id={lead.user_id}"

            archive_lines.append(
                f"• <b>{dt_str}</b>{vip_badge}\n"
                f"  🏷 <b>Ниша:</b> {lead.niche_code}\n"
                f"  💬 <b>Запрос:</b> <i>\"{html.quote(lead.intent_summary)}\"</i>\n"
                f"  👤 <b>Контакт:</b> <a href=\"{tg_link}\">{contact_str}</a> | 💳 <b>${price_val:.2f} USD</b>\n"
            )

        text = "\n".join(archive_lines)
        if len(text) > 4000:
            chunks = [text[i:i+3900] for i in range(0, len(text), 3900)]
            for chunk in chunks:
                await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


async def render_channels_view(event, page: int = 0):
    telegram_id = event.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        is_admin = partner.role in ["ADMIN", "SUPERADMIN"] if partner else False

        res = await session.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
        channels = list(res.scalars().all())

    if not channels:
        text = (
            "📡 <b>Мониторинг каналов и чатов:</b>\n\n"
            "В данный момент нет добавленных отслеживаемых чатов.\n"
            "Нажмите кнопку ниже, чтобы добавить публичную группу или канал для прослушки."
        )
        kb = get_channels_inline_keyboard(is_admin, 0, 1)
    else:
        joined_count = sum(1 for c in channels if c.status == "JOINED")
        pending_count = sum(1 for c in channels if c.status == "PENDING")
        failed_count = sum(1 for c in channels if c.status == "FAILED")

        PAGE_SIZE = 8
        total_pages = max(1, (len(channels) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * PAGE_SIZE
        page_channels = channels[start_idx : start_idx + PAGE_SIZE]

        status_map = {
            "JOINED": "🟢 Подключен",
            "PENDING": "⏳ Подключение...",
            "FAILED": "🔴 Ошибка"
        }
        lines = []
        for idx, ch in enumerate(page_channels, start_idx + 1):
            st = status_map.get(ch.status, ch.status)
            title_str = f"<b>{html.quote(ch.title)}</b> (<code>{ch.username_or_link}</code>)" if ch.title else f"<b>{ch.username_or_link}</b>"
            err_str = f"\n   └ <i>Ошибка: {html.quote(ch.error_message)}</i>" if ch.error_message else ""
            lines.append(f"{idx}. {title_str}\n   Статус: {st}{err_str}")

        text = (
            f"📡 <b>ОТСЛЕЖИВАЕМЫЕ ЧАТЫ И КАНАЛЫ (Всего: {len(channels)}):</b>\n"
            f"🟢 Активно в работе: <b>{joined_count}</b> | ⏳ Подключаются: <b>{pending_count}</b> | 🔴 Ошибки: <b>{failed_count}</b>\n"
            f"───────────\n"
            + "\n\n".join(lines) + "\n\n"
            f"💡 <i>Полную базу из {len(channels)} чатов с поисками и фильтрами смотрите в Веб-Панели.</i>"
        )
        kb = get_channels_inline_keyboard(is_admin, page, total_pages)

    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "📡 Каналы прослушки")
@router.message(F.text.contains("Каналы") | F.text.contains("прослушки") | F.text.contains("Прослушка"))
@router.message(Command("channels"))
async def show_channels_handler(message: Message, state: FSMContext):
    await state.clear()
    await render_channels_view(message, page=0)


@router.callback_query(F.data.startswith("channels_page:"))
@router.callback_query(F.data == "refresh_channels")
async def refresh_channels_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    page = 0
    if callback.data.startswith("channels_page:"):
        try:
            page = int(callback.data.split(":")[1])
        except ValueError:
            page = 0
    await render_channels_view(callback, page=page)


@router.callback_query(F.data == "open_delete_channels_menu")
async def open_delete_channels_menu_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Удаление каналов доступно только для Администрации.", show_alert=True)
            return

        res = await session.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
        channels = list(res.scalars().all())

    if not channels:
        await callback.answer("Список каналов пуст.", show_alert=True)
        return

    text = "🗑️ <b>Выберите канал или чат для удаления из списка прослушки:</b>"
    kb = get_delete_channels_keyboard(channels)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("del_ch:"))
async def delete_channel_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    channel_id = callback.data.split(":", 1)[1]

    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            await callback.answer("❌ Отказано в доступе.", show_alert=True)
            return

        ch_stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
        channel = (await session.execute(ch_stmt)).scalar_one_or_none()

        if not channel:
            await callback.answer("❌ Канал уже удален или не найден.", show_alert=True)
            return

        target_name = channel.title or channel.username_or_link
        await session.delete(channel)
        await session.commit()

        # Refresh remaining channels
        res = await session.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
        remaining = list(res.scalars().all())

    await callback.answer(f"✅ Канал {target_name} удален!")

    status_map = {
        "JOINED": "🟢 Подключен",
        "PENDING": "⏳ В процессе подключения...",
        "FAILED": "🔴 Ошибка подключения"
    }
    lines = []
    for idx, ch in enumerate(remaining, 1):
        st = status_map.get(ch.status, ch.status)
        title_str = f"<b>{html.quote(ch.title)}</b> ({ch.username_or_link})" if ch.title else f"<b>{ch.username_or_link}</b>"
        err_str = f"\n   └ <i>Причина: {html.quote(ch.error_message)}</i>" if ch.error_message else ""
        lines.append(f"{idx}. {title_str}\n   Статус: {st}{err_str}")

    text = (
        f"🗑️ <b>Канал «{html.quote(target_name)}» успешно удален!</b>\n\n"
        f"📡 <b>Отслеживаемые чаты и каналы ({len(remaining)}):</b>\n\n"
        + ("\n\n".join(lines) if lines else "Пока нет добавленных чатов.")
    )

    await callback.message.edit_text(text, reply_markup=get_channels_inline_keyboard(True), parse_mode="HTML")


@router.message(Command("delchannel"))
@router.message(Command("del"))
async def cmd_delete_channel(message: Message):
    """Admin command: /delchannel <@username_or_id>"""
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            await message.answer("❌ Отказано в доступе.")
            return

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("⚠️ Использование: <code>/delchannel @username_чата</code> или ID канала", parse_mode="HTML")
            return

        target = parts[1].strip()
        clean_target = target.replace("https://t.me/", "@").replace("http://t.me/", "@")
        if not clean_target.startswith("@") and not clean_target.startswith("+") and not clean_target.isdigit() and len(clean_target) < 30:
            clean_target = f"@{clean_target}"

        ch_stmt = select(MonitoredChannel).where(
            (MonitoredChannel.username_or_link == clean_target) |
            (MonitoredChannel.id == target)
        )
        channel = (await session.execute(ch_stmt)).scalar_one_or_none()

        if not channel:
            await message.answer(f"❌ Чат <b>{html.quote(target)}</b> не найден в списке отслеживаемых.", parse_mode="HTML")
            return

        name = channel.title or channel.username_or_link
        await session.delete(channel)
        await session.commit()

        await message.answer(f"✅ Чат <b>{html.quote(name)}</b> удален из списка прослушки!", parse_mode="HTML")


@router.callback_query(F.data == "add_channel")
async def add_channel_callback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannelForm.waiting_for_link)
    await callback.message.answer(
        "➕ <b>Добавление нового чата или канала для прослушки:</b>\n\n"
        "Пришлите ссылку на публичный чат/канал в формате:\n"
        "• <code>@chat_name</code>\n"
        "• <code>https://t.me/chat_name</code>\n\n"
        "ИИ-Юзербот автоматически вступит в данный чат и начнет отслеживать сообщения в реальном времени.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AddChannelForm.waiting_for_link)
async def process_add_channel_link(message: Message, state: FSMContext):
    raw_input = message.text.strip()
    telegram_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        role = partner.role if partner else "DEMO"

    if raw_input.lower() in ["/cancel", "отмена", "стоп", "выход"]:
        await state.clear()
        await message.answer("🛑 Добавление чата отменено.", reply_markup=get_main_reply_keyboard(True, role))
        return

    lower_input = raw_input.lower()
    if " " in raw_input or lower_input.startswith(("найди", "ищи", "поиск", "хочу", "grok", "грок", "/grok", "/start")):
        await state.clear()
        if lower_input.startswith("/start"):
            await cmd_start(message)
            return
        await start_grok_search(message, state)
        if len(raw_input) > 3 and not raw_input.startswith("/"):
            await process_grok_keywords_search(message, state)
        return

    await state.clear()

    if not raw_input:
        await message.answer("❌ Ссылка не должна быть пустой. Попробуйте снова через меню.")
        return

    clean_target = raw_input.replace("https://t.me/", "@").replace("http://t.me/", "@")
    if not clean_target.startswith("@") and not clean_target.startswith("+"):
        clean_target = f"@{clean_target}"

    async with AsyncSessionLocal() as session:
        stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == clean_target)
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            await message.answer(f"⚠️ Чат <b>{clean_target}</b> уже есть в списке (Статус: {existing.status}).", parse_mode="HTML")
            return

        channel = MonitoredChannel(
            username_or_link=clean_target,
            status="PENDING"
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)

    status_msg = await message.answer(f"⏳ Сохранено! Юзербот пытается вступить в <b>{clean_target}</b>...", parse_mode="HTML")

    try:
        from src.api.app import ingestor
        if ingestor and ingestor._is_running:
            success, title, error = await ingestor.join_channel(clean_target)
            async with AsyncSessionLocal() as session:
                stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel.id)
                ch_record = (await session.execute(stmt)).scalar_one()

                if success:
                    ch_record.status = "JOINED"
                    ch_record.title = title
                    ch_record.error_message = None
                    await session.commit()
                    await status_msg.edit_text(f"✅ <b>Успешно!</b> Юзербот вступил в чат <b>{html.quote(title or clean_target)}</b> и начал прослушку.", parse_mode="HTML")
                else:
                    ch_record.status = "FAILED"
                    ch_record.error_message = error
                    await session.commit()
                    await status_msg.edit_text(f"⚠️ Чат добавлен, но авто-вступление завершилось ошибкой:\n<i>{html.quote(error)}</i>", parse_mode="HTML")
        else:
            await status_msg.edit_text(f"📥 Чат <b>{clean_target}</b> сохранен в базу в статусе ⏳ <b>PENDING</b>.\nАвто-вступление выполнится при старте Юзербота.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in process_add_channel_link: {e}")
        await message.answer(f"📥 Чат <b>{clean_target}</b> сохранен в систему.", parse_mode="HTML")


@router.message(Command("addchannel"))
@router.message(Command("add"))
async def cmd_add_channel(message: Message, state: FSMContext):
    """Admin command: /addchannel <@chat_username> or /add <https://t.me/chat>"""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Использование: <code>/addchannel @username_чата</code> или <code>/add https://t.me/chat_name</code>", parse_mode="HTML")
        return
    
    message.text = parts[1]
    await process_add_channel_link(message, state)


@router.callback_query(F.data == "grok_search_prompt")
@router.message(F.text.contains("Grok") | F.text.contains("grok") | F.text.contains("Грок") | F.text.contains("грок") | F.text.contains("Поиск чатов") | F.text.contains("поиск чатов"))
@router.message(Command("find_channels"))
@router.message(Command("grok"))
async def start_grok_search(event, state: FSMContext):
    """Starts proactive multi-turn Grok channel & group discovery flow."""
    await state.set_state(GrokSearchForm.active_dialog)
    await state.update_data(dialog_history=[], suggested_questions=[])

    prompt_text = (
        "🤖 <b>ГРОК ИИ-СКАТУИНГ ТЕЛЕГРАМ ГРУПП И КАНАЛОВ</b>\n"
        "───────────────────────────\n\n"
        "👋 <b>Привет! Я Grok AI — ваш ИИ-ассистент.</b>\n"
        "Я помогу найти наиболее активные Telegram-чаты и каналы с потенциальными клиентами.\n\n"
        "💬 <b>Напишите мне в свободной форме:</b> какую категорию услуг, товары или город вы хотите проанализировать?\n"
        "<i>(Например: «Ищи чаты по аренде недвижимости в Нячанге» или «Нужны группы автострахования КАСКО»)</i>\n\n"
        "Или выберите пресет ниже:"
    )
    from src.bot.keyboards import get_grok_niche_preset_keyboard
    kb = get_grok_niche_preset_keyboard()

    if isinstance(event, CallbackQuery):
        await event.message.answer(prompt_text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(prompt_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "grok_exit_dialog")
async def grok_exit_dialog_callback(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        role = partner.role if partner else "DEMO"

    await state.clear()
    await callback.answer("🛑 Диалог с Grok завершен.")
    await callback.message.answer(
        "👋 Диалог с Grok AI завершен. Вы вернулись в главное меню.",
        reply_markup=get_main_reply_keyboard(True, role)
    )


@router.callback_query(F.data.startswith("grok_preset:"))
async def grok_preset_callback(callback: CallbackQuery, state: FSMContext):
    preset_keywords = callback.data.split(":", 1)[1]
    await callback.answer(f"🔎 Запрос к Grok: {preset_keywords}...")

    dummy_msg = callback.message
    dummy_msg.text = preset_keywords
    await process_grok_keywords_search(dummy_msg, state)


@router.callback_query(F.data.startswith("grok_q:"))
async def grok_suggested_question_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    suggested = data.get("suggested_questions", [])
    idx = int(callback.data.split(":")[1])

    question_text = suggested[idx] if idx < len(suggested) else "Показать еще чаты"
    await callback.answer(f"💬 Запрос: {question_text}")

    dummy_msg = callback.message
    dummy_msg.text = question_text
    await process_grok_keywords_search(dummy_msg, state)


@router.message(GrokSearchForm.active_dialog)
async def process_grok_keywords_search(message: Message, state: FSMContext):
    user_input = message.text.strip()
    telegram_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        role = partner.role if partner else "DEMO"

    if user_input.lower().startswith("/start") or user_input.lower() in ["стоп", "выход", "exit", "cancel", "отмена", "/cancel"]:
        await state.clear()
        if user_input.lower().startswith("/start"):
            await cmd_start(message)
            return
        await message.answer("🛑 Диалог с Grok завершен.", reply_markup=get_main_reply_keyboard(True, role))
        return

    data = await state.get_data()
    dialog_history = data.get("dialog_history", [])

    status_msg = await message.answer(
        f"🤖 <b>Grok AI анализирует запрос и подбирает целевые группы...</b>\n"
        f"<i>«{html.quote(user_input)}»</i>\n\n"
        f"⏳ Секунду...",
        parse_mode="HTML"
    )

    from src.ai.grok_channel_finder import GrokChannelFinder
    finder = GrokChannelFinder()

    # Fast Instant Candidates (< 0.05 seconds)
    semantic_kw = finder._extract_semantic_keywords(user_input)
    fast_candidates = finder._heuristic_fallback(semantic_kw or user_input, "general")
    shown_u = [c.get("username", "").strip() for c in fast_candidates]

    dialog_history.append({"role": "user", "content": user_input})
    reply_text = f"🤖 <b>Grok AI</b>: Мгновенная подборка по запросу «<b>{html.quote(semantic_kw or user_input)}</b>»:"
    dialog_history.append({"role": "assistant", "content": reply_text})

    await state.update_data(
        dialog_history=dialog_history,
        suggested_questions=["Искать ещё", "Уточнить поиск", "Завершить"],
        search_query=user_input,
        all_candidates=fast_candidates,
        shown_usernames=shown_u,
        candidate_page=0
    )

    from src.bot.keyboards import get_grok_proactive_chat_keyboard
    proactive_kb = get_grok_proactive_chat_keyboard(["Искать ещё", "Уточнить поиск", "Завершить"])

    try:
        await status_msg.edit_text(
            f"{reply_text}\n\n"
            f"⚡ <i>Первая пачка готова мгновенно! Глубокий ИИ-поиск подгружает дополнительные каналы в фоне.</i>",
            reply_markup=proactive_kb,
            parse_mode="HTML"
        )
    except Exception:
        await message.answer(
            f"{reply_text}\n\n"
            f"⚡ <i>Первая пачка готова мгновенно! Глубокий ИИ-поиск подгружает дополнительные каналы в фоне.</i>",
            reply_markup=proactive_kb,
            parse_mode="HTML"
        )

    # 1. Output first batch INSTANTLY
    await send_grok_candidate_batch(message, state)

    # 2. Launch Deep AI Search in BACKGROUND
    asyncio.create_task(background_prefetch_grok_channels(state, finder, semantic_kw or user_input, shown_u))


async def background_prefetch_grok_channels(state: FSMContext, finder, search_query: str, shown_usernames: list):
    """Background task that fetches deep Grok AI candidates without blocking the user response."""
    try:
        logger.info(f"🔄 Background Grok Prefetch started for query: '{search_query}'...")
        deep_candidates = await finder.search_channels_and_groups(
            keywords=search_query,
            limit=20,
            exclude_usernames=shown_usernames
        )
        if deep_candidates:
            data = await state.get_data()
            current_candidates = data.get("all_candidates", [])
            existing_u = {c.get("username", "").strip().lower() for c in current_candidates}

            added = 0
            for dc in deep_candidates:
                u = dc.get("username", "").strip().lower()
                if u and u not in existing_u:
                    current_candidates.append(dc)
                    existing_u.add(u)
                    added += 1

            if added > 0:
                await state.update_data(all_candidates=current_candidates)
                logger.info(f"✅ Background Grok Prefetch completed: added {added} deep channels to state pool.")
    except Exception as e:
        logger.error(f"Error in background Grok prefetch task: {e}")


async def send_grok_candidate_batch(message: Message, state: FSMContext):
    data = await state.get_data()
    candidates = data.get("all_candidates", [])
    page = data.get("candidate_page", 0)
    batch_size = 3

    if not candidates:
        return

    start_idx = page * batch_size
    end_idx = min(start_idx + batch_size, len(candidates))
    current_batch = candidates[start_idx:end_idx]

    for idx_in_batch, item in enumerate(current_batch, 1):
        global_idx = start_idx + idx_in_batch
        type_str = "👥 <b>ГРУППА (ЧАТ)</b>" if item.get("chat_type") == "group" else "📢 <b>КАНАЛ</b>"
        username = item.get("username", "")
        title = item.get("title", username)
        members = item.get("estimated_members", "N/A")
        desc = item.get("description", "")

        card_text = (
            f"🔍 <b>Найдено Grok: #{global_idx}</b> ({type_str})\n\n"
            f"📌 <b>Название:</b> {html.quote(title)}\n"
            f"🔗 <b>Ссылка:</b> {username}\n"
            f"👥 <b>Участники:</b> {members}\n"
            f"💡 <b>Рекомендация Grok:</b> <i>\"{html.quote(desc)}\"</i>"
        )

        kb = get_grok_candidate_keyboard(username, global_idx, len(candidates))
        await message.answer(card_text, reply_markup=kb, parse_mode="HTML")

    remaining = max(0, len(candidates) - end_idx)
    batch_count = len(current_batch)
    total_pool_count = len(candidates)
    next_kb = get_grok_next_batch_keyboard(batch_count=batch_count, remaining_count=remaining, total_pool_count=total_pool_count)

    batch_info = (
        f"📦 <b>Пачка {page + 1}: показано {end_idx} из {total_pool_count} чатов.</b>\n"
        f"<i>Нажмите «🚀 Добавить ВСЕ {total_pool_count} каналов из пула» для масс-добавления всего найденного списка или кнопки ниже.</i>"
    )

    await message.answer(batch_info, reply_markup=next_kb, parse_mode="HTML")


async def async_background_join_all_pool(chat_id: int, candidates: list):
    """Background task to join all Grok candidates asynchronously without blocking Telegram UI."""
    from src.api.app import ingestor
    added_names = []
    already_in_db = 0

    try:
        async with AsyncSessionLocal() as session:
            for item in candidates:
                raw_u = item.get("username", "").strip()
                if not raw_u:
                    continue
                username = raw_u if raw_u.startswith("@") or raw_u.startswith("+") else f"@{raw_u}"

                stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == username)
                existing = (await session.execute(stmt)).scalar_one_or_none()

                if existing:
                    existing.status = "JOINED"
                    already_in_db += 1
                    continue

                title = item.get("title", username)
                niche = item.get("niche_code", "community")
                channel = MonitoredChannel(
                    username_or_link=username,
                    title=title,
                    niche_code=niche,
                    status="JOINED"
                )
                session.add(channel)
                added_names.append(title or username)

                if ingestor and ingestor._is_running:
                    try:
                        success, real_title, _ = await ingestor.join_channel(username)
                        if success and real_title:
                            channel.title = real_title
                    except Exception as e:
                        logger.error(f"Auto join error for {username}: {e}")

            await session.commit()

        from src.bot.alert_bot import bot
        if bot and chat_id:
            res_card = (
                f"✅ <b>ФОНОВОЕ МАССОВОЕ ДОБАВЛЕНИЕ ЗАВЕРШЕНО!</b>\n\n"
                f"📥 Успешно обработано и добавлено: <b>{len(candidates)} источников</b>.\n"
                f"🟢 <b>Новых подключено:</b> {len(added_names)}\n"
                f"ℹ️ <b>Уже находились в БД:</b> {already_in_db}\n\n"
                f"Все добавленные чаты и каналы активны и сканируются ИИ-анализатором!"
            )
            await bot.send_message(chat_id=chat_id, text=res_card, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in async_background_join_all_pool: {e}")


@router.callback_query(F.data == "grok_approve_all_pool")
async def grok_approve_all_pool_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    candidates = data.get("all_candidates", [])

    if not candidates:
        await callback.answer("❌ Пул найденных каналов пуст.", show_alert=True)
        return

    try:
        await callback.answer("⚡ Запущено фоновое добавление!", show_alert=False)
    except Exception:
        pass

    asyncio.create_task(async_background_join_all_pool(callback.message.chat.id, candidates))

    await callback.message.edit_text(
        f"⚡ <b>ЗАПУЩЕНО АСИНХРОННОЕ ФОНОВОЕ ДОБАВЛЕНИЕ!</b>\n\n"
        f"Grok AI асинхронно подключает все <b>{len(candidates)} каналов и чатов</b> из пула к сканеру ИИ.\n\n"
        f"<i>Вы получите отчётное уведомление сразу по завершении подключения!</i>",
        reply_markup=get_grok_next_batch_keyboard(batch_count=0, remaining_count=0, total_pool_count=len(candidates)),
        parse_mode="HTML"
    )


# ── DISCOVERY KEYWORDS & GEO MANAGEMENT HANDLERS ──────────────────────────

@router.message(Command("discovery"))
@router.message(Command("discovery_keywords"))
async def cmd_discovery_keywords(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role != "SUPERADMIN":
            await message.answer("⚠️ Эта команда доступна только Superadmin.")
            return

        from src.db.models import DiscoveryKeyword
        kw_res = await session.execute(select(DiscoveryKeyword).order_by(DiscoveryKeyword.created_at.desc()))
        keywords = list(kw_res.scalars().all())

    geo_flags = {"dubai": "🇦🇪 Дубай", "nhatrang": "🇻🇳 Вьетнам", "phuket": "🇹🇭 Пхукет", "bali": "🇮🇩 Бали", "global": "🌐 Глобал"}

    lines = [
        "🎯 <b>НАСТРОЙКА КЛЮЧЕВЫХ СЛОВ И ГЕО АВТОПОИСКА ЧАТОВ</b>",
        "───────────────────────────\n",
        f"Всего ключевых слов в базе: <b>{len(keywords)}</b> шт.\n"
    ]

    for item in keywords[:20]:
        flag = geo_flags.get(item.location_code, "🌐 Глобал")
        status_icon = "✅" if item.is_active else "⏸️"
        lines.append(f"{status_icon} <b>{html.quote(item.keyword)}</b> ({flag})")

    if len(keywords) > 20:
        lines.append(f"\n<i>...и еще {len(keywords) - 20} ключевых слов</i>")

    txt = "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ключевик/ГЕО", callback_data="disc_kw_add")],
        [InlineKeyboardButton(text="⚡ Запустить ИИ-поиск сейчас", callback_data="disc_kw_trigger")],
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="disc_kw_refresh")]
    ])

    await message.answer(txt, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "disc_kw_refresh")
async def disc_kw_refresh_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role != "SUPERADMIN":
            await callback.answer("⚠️ Только для Superadmin", show_alert=True)
            return

        from src.db.models import DiscoveryKeyword
        kw_res = await session.execute(select(DiscoveryKeyword).order_by(DiscoveryKeyword.created_at.desc()))
        keywords = list(kw_res.scalars().all())

    geo_flags = {"dubai": "🇦🇪 Дубай", "nhatrang": "🇻🇳 Вьетнам", "phuket": "🇹🇭 Пхукет", "bali": "🇮🇩 Бали", "global": "🌐 Глобал"}

    lines = [
        "🎯 <b>НАСТРОЙКА КЛЮЧЕВЫХ СЛОВ И ГЕО АВТОПОИСКА ЧАТОВ</b>",
        "───────────────────────────\n",
        f"Всего ключевых слов в базе: <b>{len(keywords)}</b> шт.\n"
    ]

    for item in keywords[:20]:
        flag = geo_flags.get(item.location_code, "🌐 Глобал")
        status_icon = "✅" if item.is_active else "⏸️"
        lines.append(f"{status_icon} <b>{html.quote(item.keyword)}</b> ({flag})")

    if len(keywords) > 20:
        lines.append(f"\n<i>...и еще {len(keywords) - 20} ключевых слов</i>")

    txt = "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ключевик/ГЕО", callback_data="disc_kw_add")],
        [InlineKeyboardButton(text="⚡ Запустить ИИ-поиск сейчас", callback_data="disc_kw_trigger")],
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="disc_kw_refresh")]
    ])

    await callback.answer("🔄 Список обновлен")
    try:
        await callback.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data == "disc_kw_add")
async def disc_kw_add_callback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DiscoveryForm.waiting_for_keyword)
    await callback.answer()
    await callback.message.answer(
        "➕ <b>ДОБАВЛЕНИЕ КЛЮЧЕВОГО СЛОВА ДЛЯ ПОИСКА ЧАТОВ</b>\n"
        "───────────────────────────\n\n"
        "Введите поисковую фразу и ГЕО-код через разделитель <code>|</code>:\n\n"
        "💬 <b>Примеры:</b>\n"
        "• <code>Дубай жилье | dubai</code>\n"
        "• <code>Нячанг аренда | nhatrang</code>\n"
        "• <code>Пхукет байки | phuket</code>\n"
        "• <code>Бали виллы | bali</code>\n\n"
        "<i>(Если ГЕО не указать, по умолчанию присвоится 'global')</i>",
        parse_mode="HTML"
    )


@router.message(DiscoveryForm.waiting_for_keyword)
async def process_add_discovery_keyword(message: Message, state: FSMContext):
    user_input = message.text.strip()
    if user_input.lower() in ["/cancel", "отмена", "cancel"]:
        await state.clear()
        await message.answer("🛑 Добавление ключевого слова отменено.")
        return

    if "|" in user_input:
        kw_part, geo_part = user_input.split("|", 1)
        kw = kw_part.strip()
        geo = geo_part.strip().lower()
    else:
        kw = user_input
        geo = "global"

    if len(kw) < 3:
        await message.answer("⚠️ Ключевое слово слишком короткое. Введите заново (например: <code>Дубай жилье | dubai</code>):", parse_mode="HTML")
        return

    async with AsyncSessionLocal() as session:
        from src.db.models import DiscoveryKeyword
        existing = (await session.execute(
            select(DiscoveryKeyword).where(DiscoveryKeyword.keyword.ilike(kw))
        )).scalar_one_or_none()

        if existing:
            existing.is_active = True
            existing.location_code = geo
            await session.commit()
            await message.answer(f"✅ Ключевое слово <b>{html.quote(kw)}</b> обновлено! ГЕО: <code>{geo}</code>", parse_mode="HTML")
        else:
            nk = DiscoveryKeyword(keyword=kw, location_code=geo, is_active=True)
            session.add(nk)
            await session.commit()
            await message.answer(f"🎯 Новое ключевое слово <b>{html.quote(kw)}</b> успешно добавлено в автопоиск! ГЕО: <code>{geo}</code>", parse_mode="HTML")

    await state.clear()


@router.callback_query(F.data == "disc_kw_trigger")
async def disc_kw_trigger_callback(callback: CallbackQuery):
    await callback.answer("⚡ Запуск ИИ-поиска и аудита чатов...", show_alert=False)
    status_msg = await callback.message.answer(
        "🔎 <b>ИИ-СКАНИРОВАНИЕ И АУДИТ ТЕЛЕГРАМ-ЧАТОВ ЗАПУЩЕНЫ!</b>\n"
        "───────────────────────────\n\n"
        "Движок выполняет пассивный сбор, рекурсивный майнинг ссылок и поиск по ключам...\n"
        "⏳ Пожалуйста, подождите...",
        parse_mode="HTML"
    )

    try:
        from src.discovery.chat_manager import ChatDiscoveryManager
        res = await ChatDiscoveryManager.run_full_discovery_cycle()
        aud = res.get("audited_stats", {})

        report_txt = (
            "✅ <b>ИИ-ПОИСК И АУДИТ ЧАТОВ УСПЕШНО ЗАВЕРШЕН!</b>\n"
            "───────────────────────────\n\n"
            f"🔍 <b>Пассивный сбор:</b> {res.get('passive_discovered', 0)} чатов\n"
            f"🔗 <b>Рекурсивный майнинг:</b> {res.get('mined_discovered', 0)} чатов\n"
            f"🌐 <b>Поиск по ГЕО-ключам:</b> {res.get('active_discovered', 0)} чатов\n\n"
            f"📊 <b>Результаты ИИ-Аудита:</b>\n"
            f"• Проверено: <b>{aud.get('processed', 0)}</b> чатов\n"
            f"• ✅ Одобрено и добавлено в прослушку: <b>{aud.get('approved', 0)}</b> чатов\n"
            f"• ⛔ Отклонено (спам/боты): <b>{aud.get('rejected', 0)}</b> чатов"
        )
        await status_msg.edit_text(report_txt, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error executing manual discovery trigger: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка выполнения поиска: {str(e)[:200]}", parse_mode="HTML")


@router.callback_query(F.data == "grok_approve_batch")
async def grok_approve_batch_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    candidates = data.get("all_candidates", [])
    page = data.get("candidate_page", 0)
    batch_size = 3

    start_idx = page * batch_size
    end_idx = min(start_idx + batch_size, len(candidates))
    current_batch = candidates[start_idx:end_idx]

    if not current_batch:
        await callback.answer("❌ Текущая пачка пуста.", show_alert=True)
        return

    added_names = []
    already_in_db = 0

    from src.api.app import ingestor

    async with AsyncSessionLocal() as session:
        for item in current_batch:
            raw_u = item.get("username", "").strip()
            if not raw_u:
                continue
            username = raw_u if raw_u.startswith("@") or raw_u.startswith("+") else f"@{raw_u}"

            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == username)
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                already_in_db += 1
                continue

            title = item.get("title", username)
            channel = MonitoredChannel(
                username_or_link=username,
                title=title,
                status="PENDING"
            )
            session.add(channel)
            added_names.append(title or username)

            # Auto-join if ingestor active
            if ingestor and ingestor._is_running:
                try:
                    success, real_title, _ = await ingestor.join_channel(username)
                    if success:
                        channel.status = "JOINED"
                        channel.title = real_title or title
                except Exception as e:
                    logger.error(f"Auto join batch error for {username}: {e}")

        await session.commit()

    if added_names:
        names_str = ", ".join([f"«{html.quote(n)}»" for n in added_names[:3]])
        msg = f"🎉 <b>Успешно добавлено {len(added_names)} каналов!</b>\n\nВ базу добавлены: {names_str}.\nИИ-Сканер задействован в прослушке."
    else:
        msg = f"ℹ️ Все каналы текущей пачки уже находились в базе данных."

    await callback.answer(f"✅ Масс-добавление выполнено ({len(added_names)} добавлено)!", show_alert=True)
    await callback.message.edit_text(
        f"{callback.message.html_text}\n\n{msg}",
        reply_markup=get_grok_next_batch_keyboard(batch_count=0, remaining_count=max(0, len(candidates) - end_idx)),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "grok_next_batch")
async def grok_next_batch_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    candidates = data.get("all_candidates", [])
    page = data.get("candidate_page", 0) + 1
    shown_usernames = data.get("shown_usernames", [])
    search_query = data.get("search_query", "Telegram чаты")
    batch_size = 3

    # If remaining candidates in local buffer is less than batch_size, dynamically generate/fetch new ones!
    if page * batch_size >= len(candidates):
        await callback.answer("🤖 Grok сканирует сеть и подбирает новые варианты...", show_alert=False)
        loading_msg = await callback.message.answer("🤖 <b>Grok ищет новые целевые группы и каналы в реальном времени...</b>", parse_mode="HTML")

        # Exclude all existing candidates
        for c in candidates:
            u = c.get("username", "").strip().lower()
            if u and u not in shown_usernames:
                shown_usernames.append(u)

        from src.ai.grok_channel_finder import GrokChannelFinder
        finder = GrokChannelFinder()
        new_candidates = await finder.search_channels_and_groups(
            keywords=search_query,
            limit=10,
            exclude_usernames=shown_usernames
        )

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if new_candidates:
            candidates.extend(new_candidates)
            for nc in new_candidates:
                nu = nc.get("username", "").strip().lower()
                if nu and nu not in shown_usernames:
                    shown_usernames.append(nu)
            await state.update_data(all_candidates=candidates, shown_usernames=shown_usernames)

    if page * batch_size >= len(candidates):
        await callback.answer("Варианты по данному запросу временно исчерпаны.", show_alert=True)
        return

    await state.update_data(candidate_page=page)
    await callback.answer(f"📦 Показываем следующие 3 канала...")
    await send_grok_candidate_batch(callback.message, state)


@router.callback_query(F.data.startswith("grok_appr:"))
async def grok_approve_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    clean_username = parts[1]
    username = f"@{clean_username}"

    async with AsyncSessionLocal() as session:
        stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == username)
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            await callback.answer(f"⚠️ {username} уже есть в базе данных!", show_alert=True)
            await callback.message.edit_text(
                callback.message.html_text + f"\n\nℹ️ <i>Уже находится в мониторинге (Статус: {existing.status}).</i>",
                parse_mode="HTML"
            )
            return

        channel = MonitoredChannel(
            username_or_link=username,
            status="PENDING"
        )
        session.add(channel)
        await session.commit()

    await callback.answer(f"✅ {username} утвержден и добавлен!")

    try:
        from src.api.app import ingestor
        if ingestor and ingestor._is_running:
            success, title, error = await ingestor.join_channel(username)
            if success:
                async with AsyncSessionLocal() as session:
                    ch_record = (await session.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == username))).scalar_one_or_none()
                    if ch_record:
                        ch_record.status = "JOINED"
                        ch_record.title = title
                        await session.commit()

                await callback.message.edit_text(
                    callback.message.html_text + f"\n\n✅ <b>УТВЕРЖДЕНО:</b> {username} добавлен и юзербот мгновенно подключился!",
                    parse_mode="HTML"
                )
                return
    except Exception as e:
        logger.error(f"Auto-join in grok_approve error: {e}")

    await callback.message.edit_text(
        callback.message.html_text + f"\n\n✅ <b>УТВЕРЖДЕНО:</b> {username} сохранен в лист прослушки (PENDING).",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("grok_skip:"))
async def grok_skip_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    clean_username = parts[1]
    username = f"@{clean_username}"

    await callback.answer(f"❌ {username} пропущен")
    await callback.message.edit_text(
        callback.message.html_text + f"\n\n❌ <i>Пропущено пользователем.</i>",
        parse_mode="HTML"
    )


# ==========================================
# 🤝 20% RevShare Referral System Handlers
# ==========================================

@router.message(F.text.contains("Партнерка") | F.text.contains("Партнёрка"))
@router.message(Command("referral"))
@router.message(Command("ref"))
async def show_referral_program_handler(message_or_callback):
    is_callback = isinstance(message_or_callback, CallbackQuery)
    message = message_or_callback.message if is_callback else message_or_callback
    telegram_id = message_or_callback.from_user.id
    first_name = message_or_callback.from_user.first_name or "Пользователь"

    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, first_name, message_or_callback.from_user.username or "")

        from sqlalchemy import func
        ref_count_stmt = select(func.count(Partner.id)).where(Partner.referred_by_id == partner.id)
        invited_count = (await session.execute(ref_count_stmt)).scalar() or 0

        ref_link = f"https://t.me/intenthunter_bot?start=ref_{telegram_id}"
        can_withdraw = float(partner.referral_balance) >= 50.0

        ref_text = (
            f"🤝 <b>ПАРТНЕРСКАЯ ПРОГРАММА 20% RevShare</b>\n"
            f"───────────────────────────\n\n"
            f"💰 <b>Зарабатывайте 20% с каждого выкупа лидов вашими рефералами!</b>\n\n"
            f"Делитесь вашей личной реферальной ссылкой. Каждые $1.00 - $10.00+, потраченные вашими рефералами на выкуп контактов лидов, мгновенно приносят вам <b>20% дохода</b>.\n\n"
            f"📊 <b>Ваша реферальная статистика:</b>\n"
            f"• 👥 Приглашено рефералов: <b>{invited_count} пользователей</b>\n"
            f"• 💸 Доступно к выводу: <b>${float(partner.referral_balance):.2f} USD</b>\n"
            f"• 📈 Заработано за всё время: <b>${float(partner.total_referral_earned):.2f} USD</b>\n\n"
            f"🔗 <b>Ваша реферальная ссылка:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"ℹ️ <i>Минимальная сумма вывода средств: <b>$50.00 USD</b> (на кошелек USDT TRC20 / TON).</i>"
        )

        from src.bot.keyboards import get_referral_inline_keyboard
        kb = get_referral_inline_keyboard(ref_link, can_withdraw)

        if is_callback:
            await message.answer(ref_text, reply_markup=kb, parse_mode="HTML")
            await message_or_callback.answer()
        else:
            await message.answer(ref_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "ref_qr_code")
async def send_referral_qr_callback(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    ref_link = f"https://t.me/intenthunter_bot?start=ref_{telegram_id}"

    from src.services.referral_engine import generate_referral_qr_base64
    from aiogram.types import BufferedInputFile
    import base64

    b64_qr = generate_referral_qr_base64(ref_link)
    if "base64," in b64_qr:
        raw_b64 = b64_qr.split("base64,")[1]
        img_bytes = base64.b64decode(raw_b64)
        input_file = BufferedInputFile(img_bytes, filename=f"referral_qr_{telegram_id}.png")

        caption = (
            f"📱 <b>ВАШ ПЕРСОНАЛЬНЫЙ QR-КОД ДЛЯ ПРИГЛАШЕНИЙ</b>\n\n"
            f"Покажите этот QR-код клиентам или коллегам. При сканировании они перейдут по вашей реферальной ссылке:\n"
            f"<code>{ref_link}</code>"
        )
        await callback.message.answer_photo(photo=input_file, caption=caption, parse_mode="HTML")
    else:
        await callback.message.answer(f"📱 <b>Ваш QR-код:</b>\n{b64_qr}\n\nРеферальная ссылка:\n<code>{ref_link}</code>", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "ref_info_withdraw")
async def ref_info_withdraw_callback(callback: CallbackQuery):
    await callback.answer("ℹ️ Кнопка подачи заявки на вывод активируется автоматически при накоплении от $50.00 USD.", show_alert=True)


@router.callback_query(F.data == "ref_withdraw_start")
async def start_referral_withdrawal_callback(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, callback.from_user.first_name or "", callback.from_user.username or "")
        bal = float(partner.referral_balance)
        if bal < 50.0:
            await callback.answer("❌ Минимальная сумма вывода составляет $50.00 USD.", show_alert=True)
            return

    await state.set_state(ReferralWithdrawForm.waiting_for_details)
    await callback.message.answer(
        f"💸 <b>ЗАПРОС ВЫВОДА СРЕДСТВ (${bal:.2f} USD)</b>\n"
        f"───────────────────────────\n\n"
        f"Пришлите реквизиты для получения средств в ответном сообщении.\n\n"
        f"Например:\n"
        f"• <code>USDT TRC20: TKhg9...</code>\n"
        f"• <code>TON Wallet: EQD...</code>\n"
        f"• <code>Карта RUB/USD</code>\n\n"
        f"Или отправьте <b>Отмена</b> для выхода.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(ReferralWithdrawForm.waiting_for_details)
async def process_referral_withdraw_details(message: Message, state: FSMContext):
    details = message.text.strip()
    if details.lower().startswith("/start") or details.lower() in ["отмена", "/cancel", "выход"]:
        await state.clear()
        if details.lower().startswith("/start"):
            await cmd_start(message)
            return
        await message.answer("🛑 Запрос вывода отменен.")
        return

    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        partner = await get_or_create_partner(session, telegram_id, message.from_user.first_name or "", message.from_user.username or "")
        bal = float(partner.referral_balance)
        if bal < 50.0:
            await state.clear()
            await message.answer("❌ Недостаточно средств для вывода (мин. $50.00 USD).")
            return

        from src.db.models import WithdrawalRequest
        req = WithdrawalRequest(
            partner_id=partner.id,
            amount=bal,
            payment_details=details,
            status="PENDING"
        )
        session.add(req)
        # Deduct balance so duplicate withdrawals cannot occur
        partner.referral_balance = 0.00
        await session.commit()

        await state.clear()
        await message.answer(
            f"✅ <b>ЗАЯВКА НА ВЫВОД ${bal:.2f} USD УСПЕШНО СОЗДАНА!</b>\n\n"
            f"Реквизиты: <code>{html.quote(details)}</code>\n"
            f"Заявка передана Суперадминистраторам. Выплата будет произведена в течение 24 часов.",
            parse_mode="HTML"
        )

        # Notify Superadmins
        superadmins_res = await session.execute(select(Partner).where(Partner.role == "SUPERADMIN"))
        superadmins = list(superadmins_res.scalars().all())

        from src.bot.alert_bot import bot
        if bot:
            admin_msg = (
                f"🚨 <b>НОВАЯ ЗАЯВКА НА ВЫВОД РЕФЕРАЛЬНЫХ НАЧИСЛЕНИЙ!</b>\n\n"
                f"<b>Партнер:</b> {html.quote(partner.company_name)} (@{message.from_user.username or 'no'})\n"
                f"<b>Telegram ID:</b> <code>{telegram_id}</code>\n"
                f"<b>Сумма к выплате:</b> <b>${bal:.2f} USD</b>\n"
                f"<b>Реквизиты:</b> <code>{html.quote(details)}</code>"
            )
            for sa in superadmins:
                try:
                    await bot.send_message(sa.telegram_id, admin_msg, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Failed to notify superadmin of withdrawal: {e}")


# ─── WEB LOGIN CONFIRM CALLBACK ───────────────────────────────────────────
@router.callback_query(F.data.startswith("weblogin_confirm_"))
async def weblogin_confirm_callback(callback: CallbackQuery):
    """
    User clicked '✅ Подтвердить вход в Маркетплейс' in bot.
    Calls /api/tma/web-login-confirm to mark token as approved.
    """
    token = callback.data[len("weblogin_confirm_"):]
    telegram_id = callback.from_user.id

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "http://localhost:8000/api/tma/web-login-confirm",
                json={"token": token, "telegram_id": telegram_id}
            )
            data = resp.json()
    except Exception as e:
        logger.error(f"weblogin confirm API error: {e}")
        await callback.answer("❌ Ошибка. Попробуйте ещё раз.", show_alert=True)
        return

    if data.get("status") == "ok":
        await callback.message.edit_text(
            "✅ <b>Вход подтверждён!</b>\n\n"
            "Вернитесь в браузер — маркетплейс откроется автоматически.",
            parse_mode="HTML"
        )
        await callback.answer("✅ Авторизован!")
    elif data.get("status") == "expired":
        await callback.answer("⏰ Ссылка истекла. Запросите новую в браузере.", show_alert=True)
    else:
        await callback.answer("❌ Токен не найден. Попробуйте ещё раз.", show_alert=True)


# ─── DEAD CHANNEL: KEEP / DELETE CALLBACKS ────────────────────────────────
@router.callback_query(F.data.startswith("keep_channel_"))
async def keep_channel_callback(callback: CallbackQuery):
    """Superadmin chose to keep monitoring this channel."""
    channel_id = callback.data[len("keep_channel_"):]
    async with AsyncSessionLocal() as session:
        ch = (await session.execute(
            select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
        )).scalar_one_or_none()
        ch_name = (ch.title or ch.username_or_link) if ch else channel_id

    await callback.message.edit_text(
        f"✅ <b>Канал сохранён в мониторинге</b>\n\n"
        f"📡 <b>{ch_name}</b> будет продолжать сканироваться.\n"
        f"Следующая проверка эффективности — через 7 дней.",
        parse_mode="HTML"
    )
    await callback.answer("✅ Мониторинг продолжён")


@router.callback_query(F.data.startswith("dead_channel_delete_"))
async def dead_channel_delete_callback(callback: CallbackQuery):
    """Superadmin chose to delete dead channel from monitoring pool."""
    channel_id = callback.data[len("dead_channel_delete_"):]
    async with AsyncSessionLocal() as session:
        ch = (await session.execute(
            select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
        )).scalar_one_or_none()

        if not ch:
            await callback.answer("❌ Канал не найден или уже удалён", show_alert=True)
            return

        ch_name = ch.title or ch.username_or_link
        await session.delete(ch)
        await session.commit()
        logger.info(f"Dead channel deleted by superadmin {callback.from_user.id}: {ch_name}")

    await callback.message.edit_text(
        f"🗑 <b>Канал удалён из пула мониторинга</b>\n\n"
        f"📡 <b>{ch_name}</b> больше не будет сканироваться.\n"
        f"Ресурсы сканера перераспределены на активные каналы.",
        parse_mode="HTML"
    )
    await callback.answer("🗑 Канал удалён")


@router.callback_query(F.data.startswith("confirm_heuristic:"))
async def confirm_heuristic_callback(callback: CallbackQuery):
    from src.bot.alert_bot import set_heuristic_admin_approved
    set_heuristic_admin_approved()  # Silence all heuristic alerts for next 3 hours
    await callback.answer("✅ Эвристический режим подтверждён на 3 часа!")
    await callback.message.edit_text(
        callback.message.html_text + "\n\n✅ <b>ПОДТВЕРЖДЕНО СУПЕРАДМИНОМ:</b> Эвристический режим активен (уведомления заглушены на 3 часа).",
        parse_mode="HTML"
    )

@router.callback_query(F.data == "check_ai_status")
async def check_ai_status_callback(callback: CallbackQuery):
    status_text = (
        f"📊 <b>СТАТУС ИИ-АНАЛИЗАТОРА:</b>\n"
        f"───────────────────────────\n"
        f"⚙️ <b>Провайдер:</b> <code>{settings.AI_PROVIDER}</code>\n"
        f"🔑 <b>Groq API:</b> {'✅ Подключен' if settings.GROQ_API_KEY else '❌ Отсутствует'}\n"
        f"🔑 <b>Gemini API:</b> {'✅ Подключен' if settings.GEMINI_API_KEY else '❌ Отсутствует'}\n"
        f"🎯 <b>Порог сообщений для скоринга:</b> {settings.MIN_MESSAGES_FOR_SCORING}\n\n"
        f"💡 <i>Если API-ключи отсутствуют, система использует резервный эвристический фильтр по ключевым словам.</i>"
    )
    await callback.answer()
    await callback.message.answer(status_text, parse_mode="HTML")

