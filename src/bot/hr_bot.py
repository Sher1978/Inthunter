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
    LabeledPrice, PreCheckoutQuery, ReplyKeyboardMarkup, KeyboardButton
)

from sqlalchemy import select, update
from src.config import settings
from src.db.session import AsyncSessionLocal
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
                text="⭐ Оплатить Telegram Stars (350 ⭐️)",
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

        if deep_param.startswith("vac_"):
            vacancy_id = deep_param.replace("vac_", "")
            v_stmt = select(HRVacancy).where(HRVacancy.id == vacancy_id)
            vac = (await session.execute(v_stmt)).scalars().first()

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
                        f"💼 <b>{html.quote(vac.title)}</b>\n"
                        f"───────────────────────────\n\n"
                        f"📍 <b>Локация:</b> {html.quote(vac.location_code.upper())}\n"
                        f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договоренности')}\n"
                        f"🏢 <b>Компания:</b> {html.quote(vac.company_name or 'Прямой работодатель')}\n\n"
                        f"📝 <b>Описание:</b>\n«{html.quote((vac.description or '')[:400])}»\n\n"
                        f"✅ <b>ПРЯМОЙ КОНТАКТ HR (ОТКРЫТО):</b> <b>{html.quote(contact_display)}</b>"
                    )
                    kb_btn = []
                    if vac.author_username:
                        kb_btn.append([InlineKeyboardButton(text="💬 Написать HR прямо сейчас", url=contact_link)])
                    kb_btn.append([InlineKeyboardButton(text="🏠 Главное меню", callback_query_data="hr_menu:main")])

                    await message.answer(card, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_btn))
                    return
                else:
                    # Non-subscriber Paywall View
                    blurred_contact = blur_contact_string(vac.author_username or vac.hr_contact)
                    card = (
                        f"💼 <b>{html.quote(vac.title)}</b>\n"
                        f"───────────────────────────\n\n"
                        f"📍 <b>Локация:</b> {html.quote(vac.location_code.upper())}\n"
                        f"💵 <b>Зарплата:</b> {html.quote(vac.salary_text or 'По договоренности')}\n"
                        f"🏢 <b>Компания:</b> {html.quote(vac.company_name or 'Прямой работодатель')}\n\n"
                        f"📝 <b>Описание:</b>\n«{html.quote((vac.description or '')[:300])}»\n\n"
                        f"🔒 <b>Контакты HR:</b> {blurred_contact}\n\n"
                        f"⚡ <i>Чтобы открыть контакты этого HR и получать свежие вакансии без задержек, активируйте подписку:</i>"
                    )
                    await message.answer(
                        card,
                        parse_mode="HTML",
                        reply_markup=get_hr_subscription_keyboard(vacancy_id=vac.id)
                    )
                    return

        # Regular /start Welcome Menu
        welcome_text = (
            f"👋 <b>Приветствуем в HR-Radar, {html.quote(first_name)}!</b>\n"
            f"───────────────────────────\n\n"
            f"🎯 <b>HR-Radar</b> — это интеллектуальный сервис прямого поиска вакансий и работы в Дубае, Нячанге, Бали и РФ без посредников.\n\n"
            f"⚡ <b>Как это работает:</b>\n"
            f"1. Наш ИИ посекундно сканирует 200+ бизнес-сообществ и выхватывает свежие предложения работодателей.\n"
            f"2. Подписчики получают мгновенные PUSH-уведомления с прямыми контактами HR в Telegram.\n\n"
            f"💳 <b>Ваш статус:</b> <code>{sub.subscription_status}</code>\n"
            f"🏷 <b>Активные теги:</b> {', '.join(sub.subscribed_tags or ['#Все'])}"
        )
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_hr_main_reply_keyboard())


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
        f"⭐ <b>Telegram Stars (350 ⭐️)</b> — оплата прямо в Telegram.\n\n"
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


async def notify_hr_vip_subscribers(vacancy: HRVacancy):
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


async def route_new_vacancy(vacancy: HRVacancy):
    """
    Main routing handler for newly classified vacancies:
    1. Delivers INSTANT push to paid VIP subscribers with direct contacts.
    2. Schedules delayed (30-min) post to Public Showcase Channel with hidden contacts.
    """
    # 1. Instant VIP Push
    asyncio.create_task(notify_hr_vip_subscribers(vacancy))

    # 2. Delayed 30-min Showcase Post
    asyncio.create_task(schedule_showcase_delayed_posting(vacancy.id, delay_seconds=1800))
