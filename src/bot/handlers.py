import io
import logging
import qrcode
from typing import Union
from aiogram import Router, F, html
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, BufferedInputFile
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

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


@router.message(CommandStart())
async def cmd_start(message: Message):
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    user_username = (message.from_user.username or "").lower()

    # Check deep link arguments
    cmd_parts = message.text.split()
    deep_link_arg = cmd_parts[1].lower() if len(cmd_parts) > 1 else ""
    is_staff_invite = deep_link_arg in ["staff_invite", "staff", "invite"]

    SUPERADMIN_IDS = [268669598, 260669598]
    is_superadmin_user = (user_username == settings.SUPERADMIN_USERNAME.lower()) or (telegram_id in SUPERADMIN_IDS)

    async with AsyncSessionLocal() as session:
        stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        res = await session.execute(stmt)
        partner = res.scalar_one_or_none()

        is_new = False
        if not partner:
            is_new = True
            if is_superadmin_user:
                assigned_role = "SUPERADMIN"
                assigned_status = "APPROVED"
                init_balance = 1000.00
            elif is_staff_invite:
                assigned_role = "DEMO"
                assigned_status = "PENDING"
                init_balance = 0.00
            else:
                assigned_role = "DEMO"
                assigned_status = "APPROVED"
                init_balance = 0.00

            partner = Partner(
                telegram_id=telegram_id,
                company_name=f"Компания {first_name}",
                role=assigned_role,
                moderation_status=assigned_status,
                balance=init_balance,
                subscribed_niches=["real_estate", "bike_rent", "currency_exchange", "services_visa", "auto_kasko"],
                is_monitoring_active=True,
                is_debug_monitoring=False
            )
            session.add(partner)
            await session.commit()
            await session.refresh(partner)
        else:
            # Upgrade superadmins if not already
            if is_superadmin_user and partner.role != "SUPERADMIN":
                partner.role = "SUPERADMIN"
                partner.moderation_status = "APPROVED"
                partner.balance = max(float(partner.balance), 1000.00)
                await session.commit()
                await session.refresh(partner)

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

        role_str = ROLE_LABELS.get(partner.role, partner.role)
        is_monitoring = partner.is_monitoring_active

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

    welcome_extra = ""
    if is_staff_invite:
        welcome_extra = "\n\n📲 <b>Заявка на добавление персонала отправлена Суперадминистратору!</b> Ожидайте назначения вашей роли."

    await message.answer(
        f"👋 Добро пожаловать в <b>Intent Hunter CDP Marketplace</b>!\n\n"
        f"<b>Ваш текущий статус:</b> {role_str}\n"
        f"<b>Баланс:</b> <b>${partner.balance:.2f} USD</b>\n\n"
        f"Здесь вы можете в реальном времени получать карточки горячих лидов с ИИ-скорингом по Нячангу (Вьетнам).{welcome_extra}",
        reply_markup=get_main_reply_keyboard(is_monitoring, partner.role),
        parse_mode="HTML"
    )


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

    if query.lower() in ["/cancel", "отмена", "стоп", "выход"]:
        await state.clear()
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


@router.message(F.text.contains("Здоровье сканера") | F.text.contains("Здоровье"))
@router.message(Command("health"))
@router.message(Command("scanner"))
@router.callback_query(F.data == "restart_scanner_cmd")
async def check_scanner_health_handler(event: Union[Message, CallbackQuery]):
    telegram_id = event.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        if not partner or partner.role not in ["ADMIN", "SUPERADMIN"]:
            msg = "❌ Проверка статуса доступна только для Администраторов и Суперадминистраторов."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

    from src.api.app import ingestor
    from datetime import datetime, timezone

    if isinstance(event, CallbackQuery) and event.data == "restart_scanner_cmd":
        if ingestor:
            await ingestor.restart_scraper_loop()
            await event.answer("✅ Сборщик сообщений перезапущен!", show_alert=True)
            await event.message.answer("🔄 <b>Сборщик сообщений был успешно перезапущен пользователем.</b>", parse_mode="HTML")
        else:
            await event.answer("❌ Ingestor не инициализирован.", show_alert=True)
        return

    if not ingestor or not ingestor._is_running:
        status_str = "🔴 ВЫКЛЮЧЕН / СБОЙ"
        check_str = "Не выполняется"
        last_msg_str = "Неизвестно"
        scraped_count = 0
    else:
        status_str = "🟢 АКТИВЕН"
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
        f"📡 <b>Состояние сборщика:</b> {status_str} (Проверка чатов: {check_str})\n"
        f"⏱ <b>Последнее НОВОЕ сообщение из чатов:</b> {last_msg_str}\n"
        f"📊 <b>Отсканировано сообщений за сессию:</b> <b>{scraped_count}</b> шт.\n"
        f"🛡 <b>Авто-проверщик (Watchdog):</b> 🟢 Активен (порог 5 мин.)\n\n"
        f"💡 <i>Ночью или в часы затишья сообщения в чатах появляются реже. Сканер непрерывно проверяет все подсоединенные чаты каждые 15 секунд.</i>"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Перезапустить сканер вручную", callback_data="restart_scanner_cmd")]
        ]
    )

    if isinstance(event, CallbackQuery):
        await event.message.answer(health_card, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(health_card, reply_markup=kb, parse_mode="HTML")



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
    async with AsyncSessionLocal() as session:
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
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()

        if not partner:
            await callback.answer("Ошибка: Партнер не зарегистрирован. Нажмите /start", show_alert=True)
            return

        l_stmt = select(Lead).where(Lead.id == lead_id)
        lead = (await session.execute(l_stmt)).scalar_one_or_none()

        if not lead:
            await callback.answer("Ошибка: Карточка лида не найдена.", show_alert=True)
            return

        if lead.status == "SOLD":
            await callback.answer("⚠️ Этот лид уже выкуплен другим партнером!", show_alert=True)
            return

        price = float(lead.price)
        if float(partner.balance) < price:
            await callback.answer(
                f"❌ Недостаточно средств на балансе! Стоимость: ${price:.2f} USD, у вас: ${partner.balance:.2f} USD",
                show_alert=True
            )
            return

        partner.balance = float(partner.balance) - price
        lead.status = "SOLD"

        purchase = LeadPurchase(
            lead_id=lead.id,
            partner_id=partner.id,
            price_paid=price
        )
        session.add(purchase)

        u_stmt = select(UserProfile).where(UserProfile.user_id == lead.user_id)
        user_profile = (await session.execute(u_stmt)).scalar_one_or_none()

        await session.commit()

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
            f"💰 Списано с баланса: ${price:.2f} USD (Остаток: ${partner.balance:.2f} USD)"
        )

        await callback.message.edit_text(purchase_success_text, parse_mode="HTML", disable_web_page_preview=True)
        await callback.answer("✅ Контакт лида выкуплен!", show_alert=True)


@router.message(F.text == "📡 Каналы прослушки")
@router.message(Command("channels"))
async def show_channels_handler(message: Message, state: FSMContext):
    await state.clear()
    telegram_id = message.from_user.id
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

    await message.answer(text, reply_markup=get_channels_inline_keyboard(is_admin), parse_mode="HTML")


@router.callback_query(F.data == "refresh_channels")
async def refresh_channels_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        p_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
        partner = (await session.execute(p_stmt)).scalar_one_or_none()
        is_admin = partner.role in ["ADMIN", "SUPERADMIN"] if partner else False

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

    await callback.message.edit_text(text, reply_markup=get_channels_inline_keyboard(is_admin), parse_mode="HTML")
    await callback.answer("🔄 Список обновлен")


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

    if user_input.lower() in ["стоп", "выход", "exit", "cancel", "отмена"]:
        await state.clear()
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

    try:
        chat_response = await finder.proactive_chat_dialog(
            messages_history=dialog_history,
            user_input=user_input
        )
    except Exception as e:
        logger.error(f"Error in Grok proactive dialog handler: {e}")
        await status_msg.edit_text(f"❌ Ошибка при обращении к Grok: {html.quote(str(e))}")
        return

    dialog_history.append({"role": "user", "content": user_input})
    dialog_history.append({"role": "assistant", "content": chat_response["reply_text"]})
    await state.update_data(
        dialog_history=dialog_history,
        suggested_questions=chat_response.get("suggested_questions", []),
        all_candidates=candidates,
        candidate_page=0
    )

    reply_text = chat_response["reply_text"]
    suggested_q = chat_response.get("suggested_questions", [])

    from src.bot.keyboards import get_grok_proactive_chat_keyboard
    proactive_kb = get_grok_proactive_chat_keyboard(suggested_q)

    await status_msg.edit_text(
        f"{reply_text}\n\n"
        f"💬 <i>Вы можете продолжить диалог с Grok, уточнить поиск или переключать пачки каналов ниже!</i>",
        reply_markup=proactive_kb,
        parse_mode="HTML"
    )

    await send_grok_candidate_batch(message, state)


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
            f"🔍 <b>Найдено Grok: #{global_idx} из {len(candidates)}</b> ({type_str})\n\n"
            f"📌 <b>Название:</b> {html.quote(title)}\n"
            f"🔗 <b>Ссылка:</b> {username}\n"
            f"👥 <b>Участники:</b> {members}\n"
            f"💡 <b>Рекомендация Grok:</b> <i>\"{html.quote(desc)}\"</i>"
        )

        kb = get_grok_candidate_keyboard(username, global_idx, len(candidates))
        await message.answer(card_text, reply_markup=kb, parse_mode="HTML")

    remaining = len(candidates) - end_idx
    next_kb = get_grok_next_batch_keyboard(remaining)

    if remaining > 0:
        batch_info = (
            f"📦 <b>Пачка {page + 1}: просмотрено {end_idx} из {len(candidates)} чатов.</b>\n"
            f"Осталось каналов: <b>{remaining} шт.</b> Нажмите кнопку ниже для просмотра следующих."
        )
    else:
        batch_info = f"✅ <b>Просмотрены все {len(candidates)} найденных каналов по текущему запросу!</b>"

    await message.answer(batch_info, reply_markup=next_kb, parse_mode="HTML")


@router.callback_query(F.data == "grok_next_batch")
async def grok_next_batch_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    candidates = data.get("all_candidates", [])
    page = data.get("candidate_page", 0) + 1
    batch_size = 3

    if page * batch_size >= len(candidates):
        await callback.answer("Все варианты по текущему запросу уже просмотрены!", show_alert=True)
        return

    await state.update_data(candidate_page=page)
    await callback.answer(f"📦 Загружаем следующие 3 канала...")
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
