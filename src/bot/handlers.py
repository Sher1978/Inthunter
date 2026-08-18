import logging
from aiogram import Router, F, html
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.session import AsyncSessionLocal
from src.db.models import Partner, Lead, LeadPurchase, UserProfile, UserActivityLog
from src.bot.keyboards import (
    get_main_reply_keyboard,
    get_niche_inline_keyboard,
    get_topup_keyboard,
    NICHE_NAMES
)

logger = logging.getLogger("intent_hunter.bot_handlers")
router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Партнер"
    
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        if not partner:
            partner = Partner(
                telegram_id=telegram_id,
                company_name=f"Компания {first_name}",
                balance=1000.00, # 1,000 RUB bonus welcome balance for testing
                subscribed_niches=["auto_kasko", "real_estate", "auto_broker"]
            )
            session.add(partner)
            await session.commit()
            welcome_suffix = "\n\n🎁 Вам начислен приветственный баланс <b>1 000 ₽</b> для тестирования выкупа лидов!"
        else:
            welcome_suffix = ""

    await message.answer(
        f"👋 Добро пожаловать в <b>Intent Hunter CDP Marketplace</b>!\n\n"
        f"Здесь вы можете в реальном времени получать карточки горячих лидов с искусственным интеллектом, "
        f"оценивающим степень готовности к покупке и готовую стратегию диалога.{welcome_suffix}",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text == "👤 Мой Профиль")
@router.message(Command("profile"))
async def show_profile(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        if not partner:
            await message.answer("Пожалуйста, нажмите /start для регистрации.")
            return

        purchases_stmt = select(LeadPurchase).where(LeadPurchase.partner_id == partner.id)
        p_res = await session.execute(purchases_stmt)
        purchases = list(p_res.scalars().all())

        subbed_niches_str = ", ".join([NICHE_NAMES.get(n, n) for n in partner.subscribed_niches]) or "Нет подписок"

        await message.answer(
            f"<b>👤 Профиль B2B-Партнера:</b>\n\n"
            f"<b>Компания:</b> {html.quote(partner.company_name)}\n"
            f"<b>Telegram ID:</b> <code>{partner.telegram_id}</code>\n"
            f"<b>Баланс:</b> <b>{partner.balance:.2f} ₽</b>\n"
            f"<b>Выкуплено лидов:</b> {len(purchases)} шт.\n"
            f"<b>Активные ниши:</b> {subbed_niches_str}",
            parse_mode="HTML"
        )


@router.message(F.text == "💳 Баланс")
@router.message(Command("balance"))
async def show_balance(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        balance = partner.balance if partner else 0.0

    await message.answer(
        f"<b>💳 Ваш текущий баланс: {balance:.2f} ₽</b>\n\n"
        f"Выберите сумму пополнения баланса для мгновенного выкупа лидов:",
        reply_markup=get_topup_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text == "🎯 Мои Ниши")
@router.message(Command("niches"))
async def show_niches(message: Message):
    telegram_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        user_niches = partner.subscribed_niches if partner else []

    await message.answer(
        "<b>🎯 Настройка подписки на категории лидов:</b>\n"
        "Нажмите на категорию, чтобы включить или отключить получение уведомлений о новых горячих лидах:",
        reply_markup=get_niche_inline_keyboard(user_niches),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("toggle_niche:"))
async def toggle_niche_callback(callback: CallbackQuery):
    niche_code = callback.data.split(":")[1]
    telegram_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        if partner:
            current_niches = list(partner.subscribed_niches or [])
            if niche_code in current_niches:
                current_niches.remove(niche_code)
                msg_status = "отключена"
            else:
                current_niches.append(niche_code)
                msg_status = "подключена"

            partner.subscribed_niches = current_niches
            await session.commit()

            await callback.message.edit_reply_markup(
                reply_markup=get_niche_inline_keyboard(current_niches)
            )
            await callback.answer(f"Категория {niche_code} {msg_status}!")


@router.callback_query(F.data.startswith("topup:"))
async def topup_callback(callback: CallbackQuery):
    amount = float(callback.data.split(":")[1])
    telegram_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        if partner:
            partner.balance = float(partner.balance) + amount
            await session.commit()

            await callback.answer(f"🎉 Баланс пополнен на +{int(amount)} ₽!", show_alert=True)
            await callback.message.edit_text(
                f"<b>✅ Баланс успешно пополнен!</b>\n\nТекущий баланс: <b>{partner.balance:.2f} ₽</b>",
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("buy_lead:"))
async def buy_lead_callback(callback: CallbackQuery):
    lead_id = callback.data.split(":")[1]
    telegram_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        # 1. Fetch Partner
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        p_res = await session.execute(p_stmt)
        partner = p_res.scalar_one_or_none()

        if not partner:
            await callback.answer("Ошибка: Партнер не зарегистрирован. Нажмите /start", show_alert=True)
            return

        # 2. Fetch Lead
        l_stmt = select(Lead).where(Lead.id == lead_id)
        l_res = await session.execute(l_stmt)
        lead = l_res.scalar_one_or_none()

        if not lead:
            await callback.answer("Ошибка: Карточка лида не найдена.", show_alert=True)
            return

        if lead.status == "SOLD":
            await callback.answer("⚠️ Этот лид уже выкуплен другим партнером!", show_alert=True)
            return

        # 3. Check Balance
        price = float(lead.price)
        if float(partner.balance) < price:
            await callback.answer(
                f"❌ Недостаточно средств на балансе! Стоимость: {int(price)} ₽, у вас: {int(partner.balance)} ₽",
                show_alert=True
            )
            return

        # 4. Process Atomic Purchase
        partner.balance = float(partner.balance) - price
        lead.status = "SOLD"

        purchase = LeadPurchase(
            lead_id=lead.id,
            partner_id=partner.id,
            price_paid=price
        )
        session.add(purchase)

        # 5. Fetch User Profile Details
        u_stmt = select(UserProfile).where(UserProfile.user_id == lead.user_id)
        u_res = await session.execute(u_stmt)
        user_profile = u_res.scalar_one_or_none()

        await session.commit()

        # Format Contact Card Output
        username = f"@{user_profile.username}" if user_profile and user_profile.username else f"ID {lead.user_id}"
        tg_link = f"https://t.me/{user_profile.username}" if user_profile and user_profile.username else f"tg://user?id={lead.user_id}"
        full_name = f"{user_profile.first_name or ''} {user_profile.last_name or ''}".strip() or "Пользователь Telegram"

        purchase_success_text = (
            f"🎉 <b>ЛИД УСПЕШНО ВЫКУПЛЕН!</b>\n\n"
            f"<b>👤 Клиент:</b> {html.quote(full_name)}\n"
            f"<b>Username:</b> {username}\n"
            f"<b>Прямая ссылка:</b> <a href=\"{tg_link}\">Открыть диалог в Telegram</a>\n"
            f"<b>Telegram ID:</b> <code>{lead.user_id}</code>\n\n"
            f"📌 <b>Суть потребности:</b>\n{html.quote(lead.intent_summary)}\n\n"
            f"💡 <b>Рекомендация по продажам (Sales Hook):</b>\n«{html.quote(lead.sales_hook)}»\n\n"
            f"💰 Списано с баланса: {int(price)} ₽ (Остаток: {partner.balance:.2f} ₽)"
        )

        await callback.message.edit_text(purchase_success_text, parse_mode="HTML", disable_web_page_preview=True)
        await callback.answer("✅ Контакт лида выкуплен!", show_alert=True)
