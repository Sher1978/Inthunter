import html
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, ReplyKeyboardMarkup, KeyboardButton,
    SuccessfulPayment
)

from sqlalchemy import select, update
from src.config import settings
from src.db.session import AsyncSessionLocal
from src.db.models import HRVacancy, HRSubscriber, HRSubscriptionPayment, VacancyGroupTarget, VacancyContactPurchase

logger = logging.getLogger("intent_hunter.hr_bot")
hr_bot_router = Router(name="hr_bot_router")

hr_bot: Optional[Bot] = None
hr_dp: Optional[Dispatcher] = None
_hr_polling_active = False

def init_hr_bot():
    global hr_bot, hr_dp
    if hr_bot and hr_dp:
        return
    import os
    raw_token = os.getenv("HR_BOT_TOKEN") or getattr(settings, "HR_BOT_TOKEN", "8841353152:AAEnr4Tb5a5LqtdCi0GbJEI2sO6bzT7Xe3c")
    clean_token = raw_token.strip().strip('"').strip("'")

    if clean_token:
        try:
            hr_bot = Bot(token=clean_token)
            hr_dp = Dispatcher()
            hr_dp.include_router(hr_bot_router)
            logger.info(f"✅ HR-Radar B2C Bot initialized successfully for token ...{clean_token[-6:]}")
        except Exception as e:
            logger.error(f"Failed to initialize HR-Radar B2C Bot: {e}")


async def run_hr_polling_safe():
    global _hr_polling_active, hr_bot, hr_dp
    if _hr_polling_active:
        return
    if not hr_bot or not hr_dp:
        init_hr_bot()
        if not hr_bot or not hr_dp:
            logger.warning("HR Bot polling skipped: hr_bot or hr_dp is None.")
            return

    _hr_polling_active = True
    try:
        logger.info("⚡ Starting HR-Radar B2C Bot polling loop...")
        await hr_dp.start_polling(hr_bot, handle_signals=False)
    except TelegramConflictError:
        logger.warning("⚠️ Another HR Bot instance is running. Polling handles conflicting session gracefully.")
    except Exception as e:
        logger.error(f"Error in HR Bot polling loop: {e}")
    finally:
        _hr_polling_active = False

# Auto-initialize HR Bot at module level
init_hr_bot()


def blur_contact_string(contact: Optional[str]) -> str:
    """Blurs username or contact string for non-subscribers."""
    if not contact:
        return "🔒 [Скрыто. Доступно по подписке]"
    clean = contact.strip().replace("@", "")
    if len(clean) <= 3:
        return "🔒 @" + clean[0] + "*** [Доступно по подписке]"
    return f"🔒 @{clean[:2]}***{clean[-1]} [Доступно по подписке]"


def get_hr_subscription_keyboard(vacancy_id: Optional[str] = None) -> InlineKeyboardMarkup:
    """Generates subscription paywall keyboard."""
    buttons = [
        [
            InlineKeyboardButton(
                text=f"⚡ Trial Pass $7/неделя",
                callback_query_data=f"hr_pay:TRIAL_7D:{vacancy_id or 'none'}"
            ),
            InlineKeyboardButton(
                text=f"👑 VIP Pass $19/месяц",
                callback_query_data=f"hr_pay:VIP_30D:{vacancy_id or 'none'}"
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐ Оплатить Telegram Stars (3100 ⭐️)",
                callback_query_data=f"hr_stars:TRIAL_7D:{vacancy_id or 'none'}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🎯 Настройка тегов и ниш",
                callback_query_data="hr_menu:tags"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_hr_tags_keyboard(current_tags: List[str]) -> InlineKeyboardMarkup:
    """Generates interactive tag selection keyboard."""
    available_tags = [
        "#Все", "#Маркетинг", "#Недвижимость", "#Разработка",
        "#Логистика", "#Дизайн", "#Сервис", "#Прочее"
    ]
    buttons = []
    row = []
    for tag in available_tags:
        is_active = tag in current_tags
        text = f"✅ {tag}" if is_active else f"➕ {tag}"
        row.append(InlineKeyboardButton(text=text, callback_query_data=f"hr_tag_toggle:{tag}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="🏠 Главное меню HR-Radar", callback_query_data="hr_menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_hr_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Generates persistent bottom reply menu for B2C HR Bot."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💼 Архив Вакансий"), KeyboardButton(text="👤 Мой Профиль & VIP")],
            [KeyboardButton(text="🎯 Мои Теги Ниш"), KeyboardButton(text="💳 Оплатить Доступ")],
            [KeyboardButton(text="ℹ️ О сервисе HR-Radar")]
        ],
        resize_keyboard=True
    )


@hr_bot_router.message(CommandStart())
async def hr_start_command_handler(message: Message):
    """Handles /start command including deep-linked /start vac_{id} requests."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name or "Соискатель"

    # Get or create HRSubscriber
    async with AsyncSessionLocal() as session:
        stmt = select(HRSubscriber).where(HRSubscriber.telegram_id == user_id)
        sub = (await session.execute(stmt)).scalars().first()
        if not sub:
            sub = HRSubscriber(
                telegram_id=user_id,
                username=username,
                first_name=first_name,
                subscription_status="FREE",
                subscribed_tags=["#Все", "#Маркетинг", "#Недвижимость", "#Разработка"]
            )
            session.add(sub)
            await session.commit()

        # Check deep link parameter
        args = message.text.split(maxsplit=1)
        deep_param = args[1].strip() if len(args) > 1 else ""

        # Handle deep-link: buy_{vacancy_id}_{group_slug} (Stars paywall from group post)
        if deep_param.startswith("buy_"):
            buy_parts = deep_param[4:].split("_", 1)  # buy_{vacancy_id}_{group_slug}
            if len(buy_parts) >= 2:
                vac_id_raw, grp_slug = buy_parts[0], buy_parts[1]
            else:
                vac_id_raw, grp_slug = buy_parts[0], "unknown"
            await _handle_buy_contact_deeplink(message, vac_id_raw, grp_slug)
            return

        # Handle deep-link vacancy redirect (vac_{id} or test_{id})
        if deep_param:
            vacancy_id = deep_param.replace("vac_", "").replace("test_", "")
            v_stmt = select(HRVacancy).where((HRVacancy.id == vacancy_id) | (HRVacancy.id.like(f"%{vacancy_id}%")))
            vac = (await session.execute(v_stmt)).scalars().first()

            # If not found by exact ID, fallback to most recent vacancy
            if not vac:
                vac = (await session.execute(select(HRVacancy).order_by(HRVacancy.created_at.desc()).limit(1))).scalars().first()

            if vac:
                now_utc = datetime.now(timezone.utc)
                is_active_sub = (
                    sub.subscription_status in ("TRIAL", "VIP") and
                    sub.subscription_expires_at and
                    sub.subscription_expires_at > now_utc
                )

                if is_active_sub:
                    # Subscriber view with unblurred HR contact
                    contact_display = f"@{vac.author_username}" if vac.author_username else (vac.hr_contact or "Прямой контакт")
                    contact_link = f"https://t.me/{vac.author_username}" if vac.author_username else "#"

                    card = (
                        f"⚡ <b>ВЫБРАННАЯ ВАКАНСИЯ ИЗ ВИТРИНЫ</b>\n"
                        f"───────────────────────────\n\n"
                        f"💼 <b>{html.quote(vac.title)}</b>\n"
                        f"📍 <b>Локация:</b> {html.quote((vac.location_code or 'ДУБАЙ').upper())}\n"
                        f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договоренности')}\n"
                        f"🏢 <b>Компания:</b> {html.quote(vac.company_name or 'Прямой работодатель')}\n\n"
                        f"📝 <b>Описание:</b>\n«{html.quote((vac.description or '')[:450])}»\n\n"
                        f"✅ <b>ПРЯМОЙ КОНТАКТ HR (ОТКРЫТО):</b> <b>{html.quote(contact_display)}</b>"
                    )
                    kb_btn = []
                    if vac.author_username:
                        kb_btn.append([InlineKeyboardButton(text="💬 Написать HR прямо сейчас", url=contact_link)])
                    kb_btn.append([InlineKeyboardButton(text="💼 Все Вакансии", callback_query_data="hr_menu:archive")])

                    await message.answer(card, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_btn))
                    await message.answer("👇 <i>Постоянное меню бота активировано внизу экрана:</i>", reply_markup=get_hr_main_reply_keyboard())
                    return
                else:
                    # Non-subscriber Paywall View for this specific vacancy
                    blurred_contact = blur_contact_string(vac.author_username or vac.hr_contact)
                    card = (
                        f"🔒 <b>РАЗБЛОКИРОВКА ВАКАНСИИ ИЗ ВИТРИНЫ</b>\n"
                        f"───────────────────────────\n\n"
                        f"💼 <b>{html.quote(vac.title)}</b>\n"
                        f"📍 <b>Локация:</b> {html.quote((vac.location_code or 'ДУБАЙ').upper())}\n"
                        f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договоренности')}\n"
                        f"🏢 <b>Компания:</b> {html.quote(vac.company_name or 'Прямой работодатель')}\n\n"
                        f"📝 <b>Описание:</b>\n«{html.quote((vac.description or '')[:350])}»\n\n"
                        f"🔒 <b>Контакты HR:</b> <code>{blurred_contact}</code> <i>(скрыто)</i>\n\n"
                        f"⚡ <b>Активируйте подписку ниже, чтобы моментально открыть контакт этого HR и получать все новые вакансии без 30-минутной задержки:</b>"
                    )
                    await message.answer(
                        card,
                        parse_mode="HTML",
                        reply_markup=get_hr_subscription_keyboard(vacancy_id=vac.id)
                    )
                    await message.answer("👇 <i>Постоянное меню бота активировано внизу экрана:</i>", reply_markup=get_hr_main_reply_keyboard())
                    return

        # Regular /start Welcome & Onboarding Script (No deep link)
        welcome_text = (
            f"👋 <b>Добро пожаловать в HR-Radar, {html.quote(first_name)}!</b>\n"
            f"───────────────────────────\n\n"
            f"🎯 <b>Зачем подключать подписку в HR-Radar?</b>\n\n"
            f"1️⃣ <b>Мгновенные PUSH-уведомления:</b> Вы получаете свежие вакансии от прямых работодателей <b>первыми</b> — за 30 минут до их публикации в канале-витрине @jobsrdr!\n\n"
            f"2️⃣ <b>Открытые контакты HR:</b> Прямой доступ к Telegram-юзернеймам работодателей без скрытия и задержек.\n\n"
            f"3️⃣ <b>Персональный фильтр ниш:</b> Фильтруйте только интересующие теги (#Разработка, #Маркетинг, #Недвижимость и др.).\n\n"
            f"💳 <b>Ваш текущий статус:</b> <code>{sub.subscription_status}</code> (публичный доступ со скрытыми контактами)\n\n"
            f"👇 <i>Выберите тариф подписки, чтобы мгновенно разблокировать контакты HR:</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ Trial Pass ($7.00 / 7 дней)", callback_query_data="hr_pay:TRIAL_7D:none"),
            ],
            [
                InlineKeyboardButton(text="👑 VIP Pass ($19.00 / 30 дней)", callback_query_data="hr_pay:VIP_30D:none"),
            ],
            [
                InlineKeyboardButton(text="⭐ Telegram Stars (3100 ⭐️)", callback_query_data="hr_stars:TRIAL_7D:none"),
            ],
            [
                InlineKeyboardButton(text="💼 Архив Свежих Вакансий", callback_query_data="hr_menu:archive"),
                InlineKeyboardButton(text="🎯 Настроить Теги Ниш", callback_query_data="hr_menu:tags")
            ]
        ])
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=kb)
        await message.answer("👇 <i>Постоянное меню бота активировано внизу экрана:</i>", reply_markup=get_hr_main_reply_keyboard())


@hr_bot_router.message(F.text == "💼 Архив Вакансий")
async def hr_archive_handler(message: Message):
    """Lists available vacancies from DB."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == user_id))).scalars().first()
        now_utc = datetime.now(timezone.utc)
        is_active_sub = (
            sub and sub.subscription_status in ("TRIAL", "VIP") and
            sub.subscription_expires_at and sub.subscription_expires_at > now_utc
        )

        vacs = list((await session.execute(
            select(HRVacancy).where(HRVacancy.status == "PUBLISHED").order_by(HRVacancy.created_at.desc()).limit(5)
        )).scalars().all())

        if not vacs:
            await message.answer("💼 В архиве пока нет опубликованных вакансий.", reply_markup=get_hr_main_reply_keyboard())
            return

        for vac in vacs:
            if is_active_sub:
                contact_display = f"@{vac.author_username}" if vac.author_username else (vac.hr_contact or "Прямой контакт")
                contact_link = f"https://t.me/{vac.author_username}" if vac.author_username else "#"
                card = (
                    f"💼 <b>{html.quote(vac.title)}</b>\n"
                    f"───────────────────────────\n"
                    f"📍 <b>Локация:</b> {html.quote((vac.location_code or 'Дубай').upper())}\n"
                    f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договоренности')}\n\n"
                    f"📝 «{html.quote((vac.description or '')[:300])}»\n\n"
                    f"✅ <b>КОНТАКТ HR:</b> <b>{html.quote(contact_display)}</b>"
                )
                kb_btn = [InlineKeyboardButton(text="💬 Написать HR", url=contact_link)] if vac.author_username else []
                await message.answer(card, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[kb_btn]))
            else:
                blurred_contact = blur_contact_string(vac.author_username or vac.hr_contact)
                card = (
                    f"💼 <b>{html.quote(vac.title)}</b>\n"
                    f"───────────────────────────\n"
                    f"📍 <b>Локация:</b> {html.quote((vac.location_code or 'Дубай').upper())}\n"
                    f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договоренности')}\n\n"
                    f"📝 «{html.quote((vac.description or '')[:250])}»\n\n"
                    f"🔒 <b>Контакты HR:</b> {blurred_contact}"
                )
                await message.answer(card, parse_mode="HTML", reply_markup=get_hr_subscription_keyboard(vacancy_id=vac.id))


@hr_bot_router.message(F.text == "👤 Мой Профиль & VIP")
async def hr_profile_handler(message: Message):
    """Displays subscriber profile status."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == user_id))).scalars().first()
        status_str = sub.subscription_status if sub else "FREE"
        exp_str = sub.subscription_expires_at.strftime("%d.%m.%Y %H:%M") if (sub and sub.subscription_expires_at) else "Нет активности"
        tags_str = ", ".join(sub.subscribed_tags or ["#Все"]) if sub else "#Все"

    card = (
        f"👤 <b>ПРОФИЛЬ СОИСКАТЕЛЯ HR-RADAR</b>\n"
        f"───────────────────────────\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"💳 <b>Статус доступа:</b> <code>{status_str}</code>\n"
        f"⏳ <b>Действителен до:</b> {exp_str}\n"
        f"🏷 <b>Выбранные ниши:</b> {tags_str}\n\n"
        f"⚡ <i>Подписчики VIP получают мгновенные PUSH-уведомления с открытыми юзернеймами HR!</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ Активировать VIP ($7/нед)", callback_query_data="hr_pay:TRIAL_7D:none"),
            InlineKeyboardButton(text="👑 VIP Pass ($19/мес)", callback_query_data="hr_pay:VIP_30D:none")
        ],
        [InlineKeyboardButton(text="🎯 Настроить теги ниш", callback_query_data="hr_menu:tags")]
    ])
    await message.answer(card, parse_mode="HTML", reply_markup=kb)


@hr_bot_router.message(F.text == "🎯 Мои Теги Ниш")
async def hr_tags_menu_handler(message: Message):
    """Opens tag management menu."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == user_id))).scalars().first()
        current_tags = sub.subscribed_tags if sub else ["#Все"]

    await message.answer(
        "🎯 <b>ВЫБОР НИШ И ТЕГОВ ВАКАНСИЙ</b>\n"
        "───────────────────────────\n\n"
        "Отметьте интересующие категории. ИИ-сканер отправляет мгновенные PUSH-уведомления только по выбранным тегам:",
        parse_mode="HTML",
        reply_markup=get_hr_tags_keyboard(current_tags)
    )


@hr_bot_router.message(F.text == "💳 Оплатить Доступ")
async def hr_payment_menu_handler(message: Message):
    """Opens paywall menu."""
    card = (
        f"💳 <b>ТАРИФЫ И VIP-ДОСТУП HR-RADAR</b>\n"
        f"───────────────────────────\n\n"
        f"⚡ <b>Trial Pass ($7.00 / 7 дней)</b> — быстрый поиск работы на этой неделе.\n"
        f"👑 <b>VIP Pass ($19.00 / 30 дней)</b> — полный безлимитный доступ ко всем вакансиям и тегам.\n"
        f"⭐ <b>Telegram Stars (3100 ⭐️)</b> — оплата прямо в Telegram.\n\n"
        f"👇 <i>Выберите удобный вариант оплаты:</i>"
    )
    await message.answer(card, parse_mode="HTML", reply_markup=get_hr_subscription_keyboard())


@hr_bot_router.message(F.text == "ℹ️ О сервисе HR-Radar")
async def hr_info_handler(message: Message):
    """Displays information about HR-Radar."""
    card = (
        f"ℹ️ <b>О СЕРВИСЕ HR-RADAR</b>\n"
        f"───────────────────────────\n\n"
        f"🤖 <b>HR-Radar</b> — это нейросетевой поисковик работы и вакансий.\n\n"
        f"1. Наш ИИ посекундно читает 200+ Telegram-сообществ (Дубай, Вьетнам, Бали, РФ).\n"
        f"2. Автоматически извлекает предложения работодателей.\n"
        f"3. VIP-подписчики получают контакты мгновенно, а в публичный канал @jobsrdr посты выкладываются с задержкой 30 минут.\n\n"
        f"💬 <b>Поддержка:</b> @sherlockdxb"
    )
    await message.answer(card, parse_mode="HTML", reply_markup=get_hr_main_reply_keyboard())


@hr_bot_router.callback_query(F.data.startswith("hr_menu:"))
async def hr_menu_callback_handler(callback: CallbackQuery):
    """Handles main menu navigation."""
    action = callback.data.split(":")[1]
    user_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == user_id))).scalars().first()
        current_tags = sub.subscribed_tags if sub else ["#Все"]

    if action == "tags":
        await callback.message.edit_text(
            "🎯 <b>ВЫБОР НИШ И ТЕГОВ ВАКАНСИЙ</b>\n"
            "───────────────────────────\n\n"
            "Выберите интересующие вас категории. ИИ-сканер будет присылать мгновенные PUSH-уведомления только по отмеченным тегам:",
            parse_mode="HTML",
            reply_markup=get_hr_tags_keyboard(current_tags)
        )
    else:
        status_str = sub.subscription_status if sub else "FREE"
        welcome_text = (
            f"👋 <b>Главное меню HR-Radar</b>\n"
            f"───────────────────────────\n\n"
            f"💳 <b>Ваш статус:</b> <code>{status_str}</code>\n"
            f"🏷 <b>Активные теги:</b> {', '.join(current_tags)}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ Активировать доступ ($7/нед)", callback_query_data="hr_pay:TRIAL_7D:none"),
                InlineKeyboardButton(text="👑 VIP Pass ($19/мес)", callback_query_data="hr_pay:VIP_30D:none")
            ],
            [
                InlineKeyboardButton(text="🎯 Выбор ниш и тегов", callback_query_data="hr_menu:tags")
            ]
        ])
        await callback.message.edit_text(welcome_text, parse_mode="HTML", reply_markup=kb)


@hr_bot_router.callback_query(F.data.startswith("hr_tag_toggle:"))
async def hr_tag_toggle_callback_handler(callback: CallbackQuery):
    """Toggles niche tags for subscriber."""
    tag = callback.data.split(":")[1]
    user_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == user_id))).scalars().first()
        if sub:
            tags = list(sub.subscribed_tags or [])
            if tag in tags:
                tags.remove(tag)
            else:
                tags.append(tag)
            sub.subscribed_tags = tags
            await session.commit()
            current_tags = tags
        else:
            current_tags = ["#Все"]

    await callback.message.edit_reply_markup(reply_markup=get_hr_tags_keyboard(current_tags))
    await callback.answer(f"Тег {tag} обновлен!")


@hr_bot_router.callback_query(F.data.startswith("hr_pay:"))
async def hr_pay_callback_handler(callback: CallbackQuery):
    """Handles subscription plan selection (Trial $7 / VIP $19)."""
    parts = callback.data.split(":")
    plan_code = parts[1]
    vacancy_id = parts[2] if len(parts) > 2 else "none"

    user_id = callback.from_user.id
    duration_days = 7 if plan_code == "TRIAL_7D" else 30
    price_usd = 7.00 if plan_code == "TRIAL_7D" else 19.00

    # Auto-activate subscription in demo mode & grant VIP access
    async with AsyncSessionLocal() as session:
        exp_date = datetime.now(timezone.utc) + timedelta(days=duration_days)
        stmt = select(HRSubscriber).where(HRSubscriber.telegram_id == user_id)
        sub = (await session.execute(stmt)).scalars().first()
        if sub:
            sub.subscription_status = "VIP" if plan_code == "VIP_30D" else "TRIAL"
            sub.subscription_expires_at = exp_date
        else:
            sub = HRSubscriber(
                telegram_id=user_id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                subscription_status="TRIAL",
                subscription_expires_at=exp_date
            )
            session.add(sub)

        pmt = HRSubscriptionPayment(
            telegram_id=user_id,
            plan_code=plan_code,
            amount_usd=price_usd,
            payment_provider="DEMO_TEST",
            status="SUCCESS"
        )
        session.add(pmt)
        await session.commit()

    await callback.answer("🎉 Подписка успешно активирована!", show_alert=True)

    if vacancy_id and vacancy_id != "none":
        async with AsyncSessionLocal() as session:
            vac = (await session.execute(select(HRVacancy).where(HRVacancy.id == vacancy_id))).scalars().first()
            if vac:
                contact_display = f"@{vac.author_username}" if vac.author_username else (vac.hr_contact or "Прямой контакт")
                contact_link = f"https://t.me/{vac.author_username}" if vac.author_username else "#"
                card = (
                    f"🎉 <b>ПОДПИСКА АКТИВИРОВАНА! ПРЯМОЙ КОНТАКТ ОТКРЫТ:</b>\n"
                    f"───────────────────────────\n\n"
                    f"💼 <b>{html.quote(vac.title)}</b>\n"
                    f"📍 <b>Локация:</b> {html.quote(vac.location_code.upper())}\n"
                    f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договоренности')}\n\n"
                    f"👤 <b>Прямой контакт HR:</b> <b>{html.quote(contact_display)}</b>"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💬 Написать HR прямо сейчас", url=contact_link)] if vac.author_username else [],
                    [InlineKeyboardButton(text="🏠 Главное меню HR-Radar", callback_query_data="hr_menu:main")]
                ])
                await callback.message.edit_text(card, parse_mode="HTML", reply_markup=kb)
                return

    await callback.message.edit_text(
        f"✅ <b>ПОДПИСКА УСПЕШНО АКТИВИРОВАНА!</b>\n"
        f"───────────────────────────\n\n"
        f"👑 <b>Статус:</b> VIP Доступ активирован на {duration_days} дней!\n"
        f"⚡ <i>Теперь вы будете получать все свежие вакансии с прямыми юзернеймами HR без задержек.</i>",
        parse_mode="HTML"
    )


async def auto_post_vacancy_to_showcase_channel(vacancy: 'HRVacancy') -> bool:
    """
    Posts a formatted vacancy card to the Public Showcase Channel with a 30-minute delay.
    Contacts are COMPLETELY HIDDEN/BLURRED. Includes deep-link button leading to HR_Radar_Bot.
    """
    global hr_bot
    if not hr_bot or not settings.HR_PUBLIC_CHANNEL_ID:
        logger.debug(f"Showcase Channel posting skipped (channel/bot not configured). Vacancy ID: {vacancy.id}")
        return False

    try:
        bot_user = settings.HR_BOT_USERNAME or "HR_Radar_Bot"

        card_text = (
            f"💼 <b>{html.quote(vacancy.title)}</b>\n"
            f"📍 <b>Локация:</b> {html.quote((vacancy.location_code or 'Дубай').upper())}\n"
            f"💵 <b>Зарплата:</b> {html.quote(vacancy.salary_text or '$2,500 – $4,000')}\n"
            f"🏢 <b>Компания:</b> {html.quote(vacancy.company_name or 'Прямой работодатель')}\n\n"
            f"📝 <b>Задачи & Описание:</b>\n«{html.quote((vacancy.description or '')[:350])}»\n\n"
            f"🔒 <b>Контакты работодателя:</b> <i>🔒 Скрыты. Опубликовано с задержкой 30 мин. Прямой доступ открыт в VIP-боте.</i>"
        )
        button_url = f"https://t.me/{bot_user}?start=vac_{vacancy.id}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔓 Узнать контакт и откликнуться первым", url=button_url)]
        ])

        msg = await hr_bot.send_message(
            chat_id=settings.HR_PUBLIC_CHANNEL_ID,
            text=card_text,
            parse_mode="HTML",
            reply_markup=kb
        )
        if msg and hasattr(msg, "message_id"):
            vacancy.showcase_message_id = msg.message_id
            logger.info(f"✅ Posted Vacancy #{vacancy.id} to Showcase Channel ({settings.HR_PUBLIC_CHANNEL_ID}) with 30m delay!")
            return True
    except Exception as e:
        logger.warning(f"Notice posting vacancy to showcase channel: {e}")
    return False


async def schedule_showcase_delayed_posting(vacancy_id: str, delay_seconds: int = 1800):
    """
    Schedules publication of vacancy post to Public Showcase Channel with a 30-minute (1800s) delay.
    """
    logger.info(f"⏳ Vacancy #{vacancy_id} scheduled for Public Showcase posting in {delay_seconds // 60} minutes.")
    await asyncio.sleep(delay_seconds)
    async with AsyncSessionLocal() as session:
        vac = (await session.execute(select(HRVacancy).where(HRVacancy.id == vacancy_id))).scalars().first()
        if vac:
            await auto_post_vacancy_to_showcase_channel(vac)


async def notify_hr_vip_subscribers(vacancy: 'HRVacancy'):
    """
    Sends INSTANT PUSH notifications with UNBLURRED HR contacts to active VIP subscribers.
    """
    global hr_bot
    if not hr_bot:
        return

    try:
        now_utc = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            stmt = select(HRSubscriber).where(
                HRSubscriber.subscription_status.in_(["TRIAL", "VIP"]),
                HRSubscriber.subscription_expires_at > now_utc
            )
            subscribers = list((await session.execute(stmt)).scalars().all())

            if not subscribers:
                return

            contact_display = f"@{vacancy.author_username}" if vacancy.author_username else (vacancy.hr_contact or "Прямой контакт")
            contact_link = f"https://t.me/{vacancy.author_username}" if vacancy.author_username else "#"

            push_card = (
                f"⚡ <b>МГНОВЕННЫЙ PUSH: НОВАЯ ГОРЯЧАЯ ВАКАНСИЯ!</b>\n"
                f"───────────────────────────\n\n"
                f"💼 <b>{html.quote(vacancy.title)}</b>\n"
                f"📍 <b>Локация:</b> {html.quote((vacancy.location_code or 'Дубай').upper())}\n"
                f"💵 <b>Зарплата:</b> {html.quote(vacancy.salary_text or 'По договоренности')}\n"
                f"💬 <b>Текст:</b> «{html.quote((vacancy.description or '')[:300])}»\n\n"
                f"👤 <b>ПРЯМОЙ КОНТАКТ HR (VIP):</b> <b>{html.quote(contact_display)}</b>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать HR прямо сейчас", url=contact_link)] if vacancy.author_username else []
            ])

            for sub in subscribers:
                try:
                    await hr_bot.send_message(chat_id=sub.telegram_id, text=push_card, parse_mode="HTML", reply_markup=kb)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Notice sending VIP HR push notifications: {e}")


async def route_new_vacancy(vacancy: 'HRVacancy'):
    """
    Main routing handler for newly classified vacancies:
    1. INSTANT push to VIP/TRIAL subscribers with open contacts.
    2. Delayed (30-min) post to Public Showcase Channel with hidden contacts.
    3. Delayed (30-min) post to all external groups (Stars paywall).
    VIP subscribers always have a 30-minute head start over everyone else.
    """
    # 1. Instant VIP Push
    asyncio.create_task(notify_hr_vip_subscribers(vacancy))

    # 2. Delayed 30-min Showcase Post
    asyncio.create_task(schedule_showcase_delayed_posting(vacancy.id, delay_seconds=1800))

    # 3. Delayed 30-min group posting (VIP head start)
    try:
        from src.bot.vacancy_group_poster import post_new_vacancy_to_all_groups_delayed
        asyncio.create_task(post_new_vacancy_to_all_groups_delayed(vacancy, delay_seconds=1800))
    except Exception as e:
        logger.warning(f"Notice triggering group poster: {e}")



# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM STARS: BUY VACANCY CONTACT  (deep-link: /start buy_{id}_{group})
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_buy_contact_deeplink(message: Message, vacancy_id: str, group_slug: str):
    """Shows vacancy preview + Stars payment button when user arrives from group post."""
    user_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        vac = (await session.execute(
            select(HRVacancy).where(HRVacancy.id == vacancy_id)
        )).scalars().first()

        if not vac:
            await message.answer("❌ Вакансия не найдена или уже закрыта.")
            return

        # Check if already purchased
        already_bought = (await session.execute(
            select(VacancyContactPurchase).where(
                VacancyContactPurchase.vacancy_id == vacancy_id,
                VacancyContactPurchase.buyer_telegram_id == user_id
            )
        )).scalars().first()

        # Get stars price from group target
        group_username = "@" + group_slug.replace("x", "_") if "x" in group_slug else group_slug
        target = (await session.execute(
            select(VacancyGroupTarget).where(
                VacancyGroupTarget.group_username.ilike(f"%{group_slug.replace('x','_')}%")
            )
        )).scalars().first()
        stars_price = target.stars_price if target else 50

        if already_bought:
            # Show contact directly without re-charging
            contact_display = f"@{vac.author_username}" if vac.author_username else (vac.hr_contact or "Прямой контакт")
            contact_link = f"https://t.me/{vac.author_username}" if vac.author_username else None
            card = (
                f"✅ <b>Вы уже оплатили этот контакт!</b>\n"
                f"───────────────────────────\n\n"
                f"💼 <b>{html.quote(vac.title)}</b>\n"
                f"📍 <b>Локация:</b> {html.quote((vac.location_code or 'Dubai').upper())}\n"
                f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договорённости')}\n\n"
                f"👤 <b>Контакт HR:</b> <b>{html.quote(contact_display)}</b>"
            )
            kb_rows = []
            if contact_link:
                kb_rows.append([InlineKeyboardButton(text="💬 Написать HR", url=contact_link)])
            await message.answer(card, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None)
            return

        # Show preview + Stars payment button
        safe_desc = (vac.description or "")[:350]
        import re
        for pat in [r'https?://\S+', r't\.me/\S+', r'@[a-zA-Z0-9_]{4,}', r'[\+]?[0-9]{7,15}']:
            safe_desc = re.sub(pat, '🔒', safe_desc)

        preview_card = (
            f"💼 <b>{html.quote(vac.title)}</b>\n"
            f"📍 <b>Локация:</b> {html.quote((vac.location_code or 'Dubai').upper())}\n"
            f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договорённости')}\n"
            f"🏢 <b>Работодатель:</b> {html.quote(vac.company_name or 'прямой работодатель')}\n\n"
            f"📝 «{html.quote(safe_desc)}»\n\n"
            f"🔒 <b>Контакты работодателя скрыты.</b>\n"
            f"💫 Нажмите кнопку ниже, чтобы оплатить <b>{stars_price} ⭐</b> и мгновенно получить прямой контакт HR:"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"💫 Оплатить {stars_price} ⭐ и получить контакт",
                callback_data=f"hr_buy_contact:{vacancy_id}:{stars_price}:{group_slug}"
            )
        ]])
        await message.answer(preview_card, parse_mode="HTML", reply_markup=kb)


@hr_bot_router.callback_query(F.data.startswith("hr_buy_contact:"))
async def hr_buy_contact_callback(callback: CallbackQuery):
    """Sends Telegram Stars invoice for vacancy contact unlock."""
    parts = callback.data.split(":")
    vacancy_id = parts[1]
    stars_price = int(parts[2]) if len(parts) > 2 else 50
    group_slug = parts[3] if len(parts) > 3 else "unknown"

    async with AsyncSessionLocal() as session:
        vac = (await session.execute(
            select(HRVacancy).where(HRVacancy.id == vacancy_id)
        )).scalars().first()
        if not vac:
            await callback.answer("Вакансия не найдена.", show_alert=True)
            return

    try:
        await callback.message.answer_invoice(
            title=f"Контакт HR: {vac.title[:50]}",
            description=f"Прямой Telegram-контакт работодателя для вакансии '{vac.title}'. Одноразовая покупка — {stars_price} ⭐.",
            payload=f"vac_contact:{vacancy_id}:{group_slug}",
            currency="XTR",  # Telegram Stars currency
            prices=[LabeledPrice(label="Контакт HR", amount=stars_price)],
            # No provider_token needed for Stars
        )
        await callback.answer()
    except Exception as e:
        logger.warning(f"Error sending Stars invoice: {e}")
        await callback.answer("Ошибка выставления счёта. Попробуйте позже.", show_alert=True)


@hr_bot_router.pre_checkout_query()
async def hr_pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """Approve all Stars pre-checkout queries."""
    await pre_checkout_query.answer(ok=True)


@hr_bot_router.message(F.successful_payment)
async def hr_successful_payment_handler(message: Message):
    """Handles successful Stars payment — contact reveal OR VIP activation."""
    payment: SuccessfulPayment = message.successful_payment
    payload = payment.invoice_payload
    parts = payload.split(":")
    stars_paid = payment.total_amount
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name or "друг"

    # ── VIP SUBSCRIPTION PAYMENT ──
    if parts[0] == "vip_sub" and len(parts) >= 2:
        plan_code = parts[1]
        is_week = plan_code == "WEEK_500"
        duration_days = 7 if is_week else 30
        duration_label = "7 дней" if is_week else "30 дней"

        exp_date, _ = await _activate_vip_subscription(
            user_id, username, plan_code, stars_paid, payment.telegram_payment_charge_id
        )
        exp_str = exp_date.strftime("%d.%m.%Y")

        await message.answer(
            f"🎉 <b>VIP АКТИВИРОВАН! До статуса — незамедлительно!</b>\n"
            f"───────────────────────────\n\n"
            f"⚡ <b>VIP-доступ активирован</b> на <b>{duration_label}</b>!\n"
            f"📅 <b>Действует до:</b> {exp_str}\n"
            f"⭐ <b>Оплачено:</b> {stars_paid} ⭐ Telegram Stars\n\n"
            f"📡 <b>Теперь вы будете получать:</b>\n"
            f"• 🔔 Все новые вакансии автоматически в бот — <b>мгновенно</b>!\n"
            f"• 🔓 Контакты HR открыты — без дополнительной оплаты\n"
            f"• ⏱ На 30 минут раньше, чем в группах!\n\n"
            f"🚀 <i>HR-Radar будет сам отправлять ваш вакансии — никаких дополнительных действий не нужно!</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💼 Свежие вакансии с открытыми контактами", callback_data="hr_menu:archive")]
            ])
        )
        logger.info(f"⭐ VIP activated: user {user_id} paid {stars_paid}⭐ plan={plan_code}")
        return

    # ── CONTACT PURCHASE PAYMENT ──
    if len(parts) < 2 or parts[0] != "vac_contact":
        await message.answer("✅ Оплата получена! Спасибо.")
        return

    vacancy_id = parts[1]
    group_slug = parts[2] if len(parts) > 2 else "unknown"
    stars_paid = payment.total_amount  # in Stars (XTR)
    user_id = message.from_user.id
    username = message.from_user.username

    async with AsyncSessionLocal() as session:
        vac = (await session.execute(
            select(HRVacancy).where(HRVacancy.id == vacancy_id)
        )).scalars().first()

        if not vac:
            await message.answer("✅ Оплата получена! К сожалению, вакансия уже закрыта.")
            return

        # Record purchase
        purchase = VacancyContactPurchase(
            vacancy_id=vacancy_id,
            buyer_telegram_id=user_id,
            buyer_username=username,
            stars_paid=stars_paid,
            group_source=group_slug,
            telegram_charge_id=payment.telegram_payment_charge_id
        )
        session.add(purchase)

        # Update subscriber: track first purchase date (for VIP upsell reminders)
        sub = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == user_id))).scalars().first()
        now_utc = datetime.now(timezone.utc)
        if sub and not sub.first_contact_purchase_at:
            sub.first_contact_purchase_at = now_utc
            sub.last_vip_reminder_at = now_utc  # first upsell sent right now (below)
            sub.vip_upsell_sent_count = 1
        elif not sub:
            sub = HRSubscriber(
                telegram_id=user_id,
                username=username,
                first_name=first_name,
                first_contact_purchase_at=now_utc,
                last_vip_reminder_at=now_utc,
                vip_upsell_sent_count=1,
            )
            session.add(sub)

        await session.commit()

        # Reveal contact
        contact_display = f"@{vac.author_username}" if vac.author_username else (vac.hr_contact or "Прямой контакт")
        contact_link = f"https://t.me/{vac.author_username}" if vac.author_username else None

        reveal_card = (
            f"🎉 <b>ОПЛАТА ПРИНЯТА! Контакт HR открыт:</b>\n"
            f"───────────────────────────\n\n"
            f"💼 <b>{html.quote(vac.title)}</b>\n"
            f"📍 <b>Локация:</b> {html.quote((vac.location_code or 'Dubai').upper())}\n"
            f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договорённости')}\n"
            f"🏢 <b>Компания:</b> {html.quote(vac.company_name or 'прямой работодатель')}\n\n"
            f"👤 <b>ПРЯМОЙ КОНТАКТ HR:</b> <b>{html.quote(contact_display)}</b>\n\n"
            f"⚡ <i>Оплачено: {stars_paid} ⭐ Telegram Stars</i>"
        )
        kb_rows = []
        if contact_link:
            kb_rows.append([InlineKeyboardButton(text="💬 Написать HR прямо сейчас", url=contact_link)])
        kb_rows.append([InlineKeyboardButton(text="💼 Все вакансии", callback_data="hr_menu:archive")])

        await message.answer(
            reveal_card,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
        logger.info(f"✅ Stars purchase: user {user_id} paid {stars_paid}⭐ for vacancy {vacancy_id} from {group_slug}")

        # ── VIP UPSELL: send immediately after first contact purchase ──
        is_vip = sub and sub.subscription_status in ("TRIAL", "VIP") and sub.subscription_expires_at and sub.subscription_expires_at > now_utc
        if not is_vip:
            await asyncio.sleep(2)  # small pause so reveal message lands first
            await message.answer(
                _vip_upsell_text(first_name),
                parse_mode="HTML",
                reply_markup=get_vip_stars_keyboard(context="post_purchase")
            )



# ─────────────────────────────────────────────────────────────────────────────
# VIP SUBSCRIPTION VIA TELEGRAM STARS (200⭐/week, 300⭐/month)
# ─────────────────────────────────────────────────────────────────────────────

def get_vip_stars_keyboard(context: str = "general") -> InlineKeyboardMarkup:
    """Keyboard offering VIP via Stars (shown after contact purchase or on reminder)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⭐ VIP на неделю — 500 ⭐",
                callback_data=f"hr_vip_stars:WEEK_500:{context}"
            )
        ],
        [
            InlineKeyboardButton(
                text="👑 VIP на месяц — 1000 ⭐ (выгоднее!)",
                callback_data=f"hr_vip_stars:MONTH_1000:{context}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❓ Что даёт VIP?",
                callback_data="hr_vip_info:general"
            )
        ]
    ])


def _vip_upsell_text(first_name: str = "друг") -> str:
    """Returns VIP upsell message text."""
    return (
        f"💡 <b>Совет для {html.quote(first_name)}: получайте вакансии первым!</b>\n"
        f"───────────────────────────\n\n"
        f"Вы только что купили контакт за 100 ⭐ — это уже умный шаг! 🎯\n\n"
        f"Но пока вы платили за один контакт, <b>VIP-пользователи</b> уже получили эту вакансию <b>автоматически</b> — прямо в бот, <b>за 30 минут до публикации в группах</b>!\n\n"
        f"🏆 <b>Что даёт VIP-статус:</b>\n"
        f"• ⚡ <b>Мгновенные PUSH</b> — все новые вакансии приходят в бот автоматически\n"
        f"• 🔓 <b>Открытые контакты HR</b> без дополнительной оплаты\n"
        f"• ⏱ <b>30 минут форы</b> перед всеми группами\n"
        f"• 📩 Пока другие видят вакансию в группе — вы уже отправили резюме\n\n"
        f"👇 <b>Активируйте VIP прямо сейчас:</b>"
    )


@hr_bot_router.callback_query(F.data.startswith("hr_vip_info:"))
async def hr_vip_info_callback(callback: CallbackQuery):
    """Shows VIP description."""
    await callback.message.answer(
        "🏆 <b>VIP-статус HR-Radar — что это?</b>\n"
        "───────────────────────────\n\n"
        "⚡ <b>Мгновенные PUSH-уведомления</b>\n"
        "Все новые вакансии из 200+ Telegram-сообществ Дубая приходят к вам в бот автоматически, с открытыми контактами HR.\n\n"
        "⏱ <b>30 минут форы</b>\n"
        "Пока вакансия появится в группах — VIP-пользователь уже успел откликнуться и назначить интервью.\n\n"
        "🔓 <b>Нет скрытых платежей</b>\n"
        "Контакты HR открыты автоматически — не нужно платить 100 ⭐ за каждый контакт.\n\n"
        "💰 <b>Цены:</b>\n"
        "• Неделя — всего 500 ⭐ (~$5)\n"
        "• Месяц — 1000 ⭐ (~$7.50)\n\n"
        "<i>Это дешевле, чем покупать 6 контактов по 100 ⭐!</i>",
        parse_mode="HTML",
        reply_markup=get_vip_stars_keyboard()
    )
    await callback.answer()


@hr_bot_router.callback_query(F.data.startswith("hr_vip_stars:"))
async def hr_vip_stars_callback(callback: CallbackQuery):
    """Sends Stars invoice for VIP subscription."""
    parts = callback.data.split(":")
    plan_code = parts[1]  # WEEK_500 or MONTH_1000
    context = parts[2] if len(parts) > 2 else "general"

    is_week = plan_code == "WEEK_500"
    stars_price = 500 if is_week else 1000
    duration_label = "7 дней" if is_week else "30 дней"
    plan_label = f"VIP HR-Radar ({duration_label})"

    try:
        await callback.message.answer_invoice(
            title=plan_label,
            description=(
                f"VIP-доступ к HR-Radar на {duration_label}. "
                f"Все вакансии мгновенно в бот с открытыми контактами HR, "
                f"30 минут форы перед публикацией в группах."
            ),
            payload=f"vip_sub:{plan_code}",
            currency="XTR",
            prices=[LabeledPrice(label=plan_label, amount=stars_price)],
        )
        await callback.answer()
    except Exception as e:
        logger.warning(f"Error sending VIP Stars invoice: {e}")
        await callback.answer("Ошибка выставления счёта. Попробуйте позже.", show_alert=True)


async def _activate_vip_subscription(user_id: int, username: str, plan_code: str, stars_paid: int, charge_id: str):
    """Activates VIP subscription in DB and records payment."""
    is_week = plan_code == "WEEK_500"
    duration_days = 7 if is_week else 30
    status = "TRIAL" if is_week else "VIP"

    async with AsyncSessionLocal() as session:
        sub = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == user_id))).scalars().first()
        now_utc = datetime.now(timezone.utc)
        exp_date = now_utc + timedelta(days=duration_days)

        if sub:
            # Extend if already VIP/TRIAL
            if sub.subscription_expires_at and sub.subscription_expires_at > now_utc:
                exp_date = sub.subscription_expires_at + timedelta(days=duration_days)
            sub.subscription_status = status
            sub.subscription_expires_at = exp_date
            sub.last_vip_reminder_at = now_utc  # Reset reminder cycle
        else:
            sub = HRSubscriber(
                telegram_id=user_id,
                username=username,
                subscription_status=status,
                subscription_expires_at=exp_date,
                last_vip_reminder_at=now_utc,
            )
            session.add(sub)

        pmt = HRSubscriptionPayment(
            telegram_id=user_id,
            plan_code=plan_code,
            amount_usd=round(stars_paid * 0.02, 2),  # ~$0.025 per Star
            payment_provider="TELEGRAM_STARS",
            status="SUCCESS",
            payload=charge_id
        )
        session.add(pmt)
        await session.commit()

    return exp_date, duration_days


# ─────────────────────────────────────────────────────────────────────────────
# DAILY VIP REMINDER BACKGROUND LOOP
# ─────────────────────────────────────────────────────────────────────────────

async def run_vip_reminder_loop():
    """
    Background task: runs every 4 hours.
    Finds users who bought contacts but are NOT VIP, and sends them
    a daily VIP upsell reminder (max 7 reminders, once per 24h).
    """
    logger.info("⏰ VIP Reminder Loop started.")
    await asyncio.sleep(600)  # 10 min grace on startup

    while True:
        try:
            global hr_bot
            if not hr_bot:
                await asyncio.sleep(3600)
                continue

            now_utc = datetime.now(timezone.utc)
            cutoff_reminder = now_utc - timedelta(hours=23)  # send once per ~24h

            async with AsyncSessionLocal() as session:
                # Users who: bought contacts, are NOT active VIP, and need a reminder
                subs = list((await session.execute(
                    select(HRSubscriber).where(
                        HRSubscriber.first_contact_purchase_at.isnot(None),
                        HRSubscriber.subscription_status == "FREE",
                        HRSubscriber.vip_upsell_sent_count < 7,
                        (
                            HRSubscriber.last_vip_reminder_at.is_(None) |
                            (HRSubscriber.last_vip_reminder_at < cutoff_reminder)
                        )
                    )
                )).scalars().all())

            for sub in subs:
                try:
                    first_name = sub.first_name or "друг"
                    reminder_num = (sub.vip_upsell_sent_count or 0) + 1

                    if reminder_num == 1:
                        reminder_text = _vip_upsell_text(first_name)
                    else:
                        reminder_text = (
                            f"⏰ <b>Напоминание #{reminder_num}: VIP HR-Radar</b>\n"
                            f"───────────────────────────\n\n"
                            f"👋 {html.quote(first_name)}, пока вы искали вакансию вручную, сегодня вышло несколько горячих предложений!\n\n"
                            f"🔑 <b>С VIP-статусом</b> они пришли бы к вам <b>автоматически</b> — с открытым контактом HR и на 30 минут раньше всех в группах.\n\n"
                            f"💡 <b>Всего 500 ⭐ на неделю</b> — дешевле 4 покупок контактов по 100 ⭐!"
                        )

                    await hr_bot.send_message(
                        chat_id=sub.telegram_id,
                        text=reminder_text,
                        parse_mode="HTML",
                        reply_markup=get_vip_stars_keyboard(context=f"reminder_{reminder_num}")
                    )

                    # Update reminder counters
                    async with AsyncSessionLocal() as session:
                        s = (await session.execute(select(HRSubscriber).where(HRSubscriber.telegram_id == sub.telegram_id))).scalars().first()
                        if s:
                            s.last_vip_reminder_at = now_utc
                            s.vip_upsell_sent_count = reminder_num
                            await session.commit()

                    logger.info(f"📩 VIP reminder #{reminder_num} sent to user {sub.telegram_id}")
                    await asyncio.sleep(2)  # pacing

                except Exception as e:
                    logger.debug(f"VIP reminder notice for user {sub.telegram_id}: {e}")

        except Exception as e:
            logger.error(f"VIP reminder loop error: {e}")

        await asyncio.sleep(4 * 3600)  # Check every 4 hours
