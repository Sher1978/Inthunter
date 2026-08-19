import logging
from aiogram import Router, F, html
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from src.db.session import AsyncSessionLocal
from src.db.models import Partner, Lead, LeadPurchase, UserProfile, UserActivityLog, MonitoredChannel
from src.bot.keyboards import (
    get_main_reply_keyboard,
    get_niche_inline_keyboard,
    get_topup_keyboard,
    get_channels_inline_keyboard,
    get_moderation_inline_keyboard,
    get_buy_lead_keyboard,
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

@router.message(CommandStart())
async def cmd_start(message: Message):
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    
    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        # Check total partners to auto-grant SUPERADMIN to the very first user
        p_count_res = await session.execute(select(func.count(Partner.id)))
        p_count = p_count_res.scalar() or 0

        is_new = False
        if not partner:
            is_new = True
            assigned_role = "SUPERADMIN"
            assigned_status = "APPROVED"

            partner = Partner(
                telegram_id=telegram_id,
                company_name=f"Компания {first_name}",
                role=assigned_role,
                moderation_status=assigned_status,
                balance=100.00,
                subscribed_niches=["real_estate", "bike_rent", "currency_exchange", "services_visa", "auto_kasko"],
                is_monitoring_active=True
            )
            session.add(partner)
            await session.commit()
            await session.refresh(partner)
        elif partner.role == "DEMO":
            # Auto-upgrade owner to SUPERADMIN
            partner.role = "SUPERADMIN"
            partner.moderation_status = "APPROVED"
            partner.balance = max(float(partner.balance), 100.00)
            await session.commit()
            await session.refresh(partner)

        role_str = ROLE_LABELS.get(partner.role, partner.role)
        is_monitoring = partner.is_monitoring_active

        # Broadcast moderation alert to admins if new user registered with DEMO/PENDING status
        if is_new and partner.role == "DEMO":
            admins_res = await session.execute(
                select(Partner).where(Partner.role.in_(["ADMIN", "SUPERADMIN"]))
            )
            admins = list(admins_res.scalars().all())

            from src.bot.alert_bot import bot
            if bot:
                mod_card = (
                    f"🆕 <b>НОВАЯ ЗАЯВКА НА РЕГИСТРАЦИЮ В СИСТЕМЕ!</b>\n\n"
                    f"<b>Имя:</b> {html.quote(first_name)}\n"
                    f"<b>Username:</b> @{message.from_user.username or 'отсутствует'}\n"
                    f"<b>Telegram ID:</b> <code>{telegram_id}</code>\n"
                    f"<b>Текущий статус:</b> DEMO (Ожидает модерации)\n\n"
                    f"Выберите статус для одобрения аккаунта:"
                )
                for admin in admins:
                    try:
                        await bot.send_message(
                            chat_id=admin.telegram_id,
                            text=mod_card,
                            reply_markup=get_moderation_inline_keyboard(telegram_id),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Error sending moderation card to admin {admin.telegram_id}: {e}")

    await message.answer(
        f"👋 Добро пожаловать в <b>Intent Hunter CDP Marketplace</b>!\n\n"
        f"<b>Ваш текущий статус:</b> {role_str}\n"
        f"<b>Баланс:</b> <b>${partner.balance:.2f} USD</b>\n\n"
        f"Здесь вы можете в реальном времени получать карточки горячих лидов с ИИ-скорингом по Нячангу (Вьетнам).",
        reply_markup=get_main_reply_keyboard(is_monitoring),
        parse_mode="HTML"
    )


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

    await message.answer(msg_text, reply_markup=get_main_reply_keyboard(is_active), parse_mode="HTML")


@router.message(F.text.startswith("📊 Статистика"))
@router.message(Command("stats"))
@router.message(Command("admin"))
async def show_admin_stats_handler(message: Message):
    from sqlalchemy import func
    async with AsyncSessionLocal() as session:
        users_count = (await session.execute(select(func.count(UserProfile.user_id)))).scalar() or 0
        logs_count = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
        leads_count = (await session.execute(select(func.count(Lead.id)))).scalar() or 0
        hot_leads_count = (await session.execute(select(func.count(Lead.id)).where(Lead.temperature == "HOT"))).scalar() or 0
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
        f"🎯 <b>Квалифицировано лидов:</b> {leads_count} лидов\n"
        f"🔥 <b>Горячие лиды (HOT):</b> {hot_leads_count} лидов\n"
        f"💰 <b>Выкуплено лидов:</b> {sold_leads_count} шт. (Доход: <b>{revenue:.2f} ₽</b>)\n"
        f"🤝 <b>B2B-Партнеров / Админов:</b> {partners_count} аккаунтов\n"
        f"📡 <b>Отслеживаемые чаты:</b> {len(channels)} каналов (🟢 {joined_ch_count} подключены)\n\n"
        f"🤖 <b>ИИ Модель:</b> Groq (qwen/qwen3.6-27b) / Gemini 2.5 Flash\n"
        f"⚡ <b>Статус системы:</b> Live Production Monitoring Active"
    )

    await message.answer(stats_text, parse_mode="HTML")


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
        role_str = ROLE_LABELS.get(partner.role, partner.role)
        status_str = "🟢 Одобрен" if partner.moderation_status == "APPROVED" else "⏳ Ожидает модерации"

        await message.answer(
            f"<b>👤 Профиль Пользователя / Партнера:</b>\n\n"
            f"<b>Компания / Имя:</b> {html.quote(partner.company_name)}\n"
            f"<b>Telegram ID:</b> <code>{partner.telegram_id}</code>\n"
            f"<b>Статус / Роль:</b> <b>{role_str}</b> ({status_str})\n"
            f"<b>Баланс:</b> <b>${partner.balance:.2f} USD</b> ({int(partner.balance)} контактов лидов)\n"
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
                provider_token="", # Empty provider token for Telegram Stars
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
                    reply_markup=get_main_reply_keyboard(partner.is_monitoring_active),
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
                    reply_markup=get_main_reply_keyboard(target_partner.is_monitoring_active),
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
                f"<b>Текущий роль:</b> {p.role}\n\n"
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
    async with AsyncSessionLocal() as session:
        # Fetch lead
        l_stmt = select(Lead).where(Lead.id == lead_id)
        lead = (await session.execute(l_stmt)).scalar_one_or_none()

        if not lead:
            await callback.answer("❌ Карточка лида не найдена.", show_alert=True)
            return

        # Fetch user profile & user activities
        u_stmt = select(UserProfile).where(UserProfile.user_id == lead.user_id)
        user_prof = (await session.execute(u_stmt)).scalar_one_or_none()

        act_stmt = select(UserActivityLog).where(UserActivityLog.user_id == lead.user_id).order_by(UserActivityLog.timestamp.desc()).limit(15)
        activities = list((await session.execute(act_stmt)).scalars().all())

    await callback.answer()

    # Build multi-chat activity breakdown
    chat_titles = list(set([a.chat_title for a in activities if a.chat_title]))
    chats_str = ", ".join([f"<b>{html.quote(c)}</b>" for c in chat_titles]) or "Групповые чаты"
    username_str = f"@{user_prof.username}" if user_prof and user_prof.username else f"ID {lead.user_id}"

    timeline_lines = []
    for act in reversed(activities):
        ts = act.timestamp.strftime("%d %b %H:%M")
        timeline_lines.append(f"• <b>{ts}</b> [{html.quote(act.chat_title)}]: <i>\"{html.quote(act.message_text)}\"</i>")

    timeline_fmt = "\n".join(timeline_lines) if timeline_lines else "• Сообщения сохранены в истории"

    analysis_card = (
        f"📊 <b>ПОЛНЫЙ ИИ-АНАЛИЗ АКТИВНОСТИ ЛИДА (Groq AI Engine)</b>\n"
        f"───────────────────────────\n"
        f"👤 <b>Клиент:</b> {username_str} (ID <code>{lead.user_id}</code>)\n"
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

    await callback.message.reply(analysis_card, parse_mode="HTML")


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

        # 6. Trigger Outbound CRM Webhook if configured for partner
        if partner.webhook_url and partner.webhook_url.startswith("http"):
            import httpx
            webhook_payload = {
                "event": "lead_purchased",
                "lead_id": lead.id,
                "niche_code": lead.niche_code,
                "temperature": lead.temperature,
                "client": {
                    "full_name": full_name,
                    "telegram_id": lead.user_id,
                    "username": user_profile.username if user_profile else None,
                    "telegram_link": tg_link
                },
                "intent_summary": lead.intent_summary,
                "sales_hook": lead.sales_hook,
                "price_paid": price,
                "purchased_at": datetime.now(timezone.utc).isoformat()
            }
            async def send_crm_webhook(url, data):
                try:
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        r = await client.post(url, json=data)
                        logger.info(f"Outbound CRM Webhook delivered to {url} (HTTP {r.status_code})")
                except Exception as e:
                    logger.error(f"Error delivering CRM webhook to {url}: {e}")

            asyncio.create_task(send_crm_webhook(partner.webhook_url, webhook_payload))

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


class AddChannelForm(StatesGroup):
    waiting_for_link = State()


@router.message(F.text == "📡 Каналы прослушки")
@router.message(Command("channels"))
async def show_channels_handler(message: Message, state: FSMContext):
    await state.clear()
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
        channels = list(res.scalars().all())

    if not channels:
        text = (
            "📡 <b>Мониторинг каналов и чатов:</b>\n\n"
            "В данный момент нет добавленных отслеживаемых чатов.\n"
            "Нажмите кнопку ниже, чтобы добавить публичную группу или канал для прослушки."
        )
    else:
        status_map = {
            "JOINED": "🟢 Подключен",
            "PENDING": "⏳ В процессе подключения...",
            "FAILED": "🔴 Ошибка подключения"
        }
        lines = []
        for idx, ch in enumerate(channels, 1):
            st = status_map.get(ch.status, ch.status)
            title_str = f"<b>{html.quote(ch.title)}</b> ({ch.username_or_link})" if ch.title else f"<b>{ch.username_or_link}</b>"
            err_str = f"\n   └ <i>Причина: {html.quote(ch.error_message)}</i>" if ch.error_message else ""
            lines.append(f"{idx}. {title_str}\n   Статус: {st}{err_str}")

        text = (
            f"📡 <b>Отслеживаемые чаты и каналы ({len(channels)}):</b>\n\n"
            + "\n\n".join(lines)
        )

    await message.answer(text, reply_markup=get_channels_inline_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "refresh_channels")
async def refresh_channels_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
        channels = list(res.scalars().all())

    status_map = {
        "JOINED": "🟢 Подключен",
        "PENDING": "⏳ В процессе подключения...",
        "FAILED": "🔴 Ошибка подключения"
    }
    lines = []
    for idx, ch in enumerate(channels, 1):
        st = status_map.get(ch.status, ch.status)
        title_str = f"<b>{html.quote(ch.title)}</b> ({ch.username_or_link})" if ch.title else f"<b>{ch.username_or_link}</b>"
        err_str = f"\n   └ <i>Причина: {html.quote(ch.error_message)}</i>" if ch.error_message else ""
        lines.append(f"{idx}. {title_str}\n   Статус: {st}{err_str}")

    text = (
        f"📡 <b>Отслеживаемые чаты и каналы ({len(channels)}):</b>\n\n"
        + ("\n\n".join(lines) if lines else "Пока нет добавленных чатов.")
    )

    await callback.message.edit_text(text, reply_markup=get_channels_inline_keyboard(), parse_mode="HTML")
    await callback.answer("🔄 Список обновлен")


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
    await state.clear()

    if not raw_input:
        await message.answer("❌ Ссылка не должна быть пустой. Попробуйте снова через меню.")
        return

    clean_target = raw_input.replace("https://t.me/", "@").replace("http://t.me/", "@")
    if not clean_target.startswith("@") and not clean_target.startswith("+"):
        clean_target = f"@{clean_target}"

    async with AsyncSessionLocal() as session:
        # Check duplicate
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

    # Attempt dynamic auto-join via ingestor
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
    
    # Delegate to process_add_channel_link logic
    message.text = parts[1]
    await process_add_channel_link(message, state)


class GrokSearchForm(StatesGroup):
    waiting_for_keywords = State()


@router.callback_query(F.data == "grok_search_prompt")
@router.message(F.text.contains("Grok") | F.text.contains("grok"))
@router.message(Command("find_channels"))
@router.message(Command("grok"))
async def start_grok_search(event, state: FSMContext):
    """Starts Grok channel & group discovery flow."""
    await state.set_state(GrokSearchForm.waiting_for_keywords)
    prompt_text = (
        "🤖 <b>ГРОК ИИ-ПАРСЕР И ПОИСК ТЕЛЕГРАМ ЧАТОВ И КАНАЛОВ</b>\n"
        "───────────────────────────\n\n"
        "Выберите готовое направление ниши ниже или просто введите ключевые слова сообщением (например: <code>нячанг жилье аренда</code>).\n\n"
        "Грок найдет релевантные <b>📢 каналы</b> и <b>👥 публичные группы/чаты</b> и пришлет их вам на поканальное утверждение!"
    )
    from src.bot.keyboards import get_grok_niche_preset_keyboard
    kb = get_grok_niche_preset_keyboard()

    if isinstance(event, CallbackQuery):
        await event.message.answer(prompt_text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(prompt_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("grok_preset:"))
async def grok_preset_callback(callback: CallbackQuery, state: FSMContext):
    preset_keywords = callback.data.split(":", 1)[1]
    await callback.answer(f"🔎 Ищем: {preset_keywords}...")

    # Create dummy message object to trigger search handler
    dummy_msg = callback.message
    dummy_msg.text = preset_keywords
    await process_grok_keywords_search(dummy_msg, state)


@router.message(GrokSearchForm.waiting_for_keywords)
async def process_grok_keywords_search(message: Message, state: FSMContext):
    keywords = message.text.strip()
    await state.clear()

    if not keywords:
        await message.answer("❌ Ключевые слова не должны быть пустыми.")
        return

    status_msg = await message.answer(
        f"🤖 <b>Grok AI ищет Telegram каналы и группы по запросу:</b>\n<i>«{html.quote(keywords)}»</i>...\n\n"
        f"⏳ Пожалуйста, подождите...",
        parse_mode="HTML"
    )

    from src.ai.grok_channel_finder import GrokChannelFinder
    finder = GrokChannelFinder()

    try:
        candidates = await finder.search_channels_and_groups(keywords=keywords, limit=6)
    except Exception as e:
        logger.error(f"Error in Grok discovery handler: {e}")
        await status_msg.edit_text(f"❌ Ошибка при поиске Grok: {html.quote(str(e))}")
        return

    if not candidates:
        await status_msg.edit_text(f"❌ Grok не нашел подходящих каналов или чатов по запросу «{html.quote(keywords)}». Попробуйте другие ключевые слова.")
        return

    await status_msg.edit_text(
        f"🎯 <b>Grok нашел {len(candidates)} потенциальных каналов и чатов!</b>\n"
        f"Утверждайте по очереди:",
        parse_mode="HTML"
    )

    for idx, item in enumerate(candidates, 1):
        type_str = "👥 <b>ГРУППА (ЧАТ)</b>" if item["chat_type"] == "group" else "📢 <b>КАНАЛ</b>"
        username = item["username"]
        title = item["title"]
        members = item.get("estimated_members", "N/A")
        desc = item.get("description", "")

        card_text = (
            f"🔍 <b>Кандидат #{idx} из {len(candidates)}</b> ({type_str})\n\n"
            f"📌 <b>Название:</b> {html.quote(title)}\n"
            f"🔗 <b>Ссылка:</b> {username}\n"
            f"👥 <b>Участники:</b> {members}\n"
            f"💡 <b>Описание Grok:</b> <i>\"{html.quote(desc)}\"</i>"
        )

        kb = get_grok_candidate_keyboard(username, idx, len(candidates))
        await message.answer(card_text, reply_markup=kb, parse_mode="HTML")


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

    # Attempt dynamic auto-join via ingestor
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
