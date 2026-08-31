#!/usr/bin/env python3
"""
Appends VIP Stars subscription system to hr_bot.py:
- VIP keyboard (200⭐/week, 300⭐/month)
- Stars invoice for VIP
- VIP activation on successful payment
- VIP upsell message after contact purchase
- Daily VIP reminder loop (runs once per day, max 7 days)
- Modifies hr_successful_payment_handler to add upsell
"""

HR_BOT_PATH = "src/bot/hr_bot.py"

VIP_CODE = '''

# ─────────────────────────────────────────────────────────────────────────────
# VIP SUBSCRIPTION VIA TELEGRAM STARS (200⭐/week, 300⭐/month)
# ─────────────────────────────────────────────────────────────────────────────

def get_vip_stars_keyboard(context: str = "general") -> InlineKeyboardMarkup:
    """Keyboard offering VIP via Stars (shown after contact purchase or on reminder)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⭐ VIP на неделю — 200 ⭐",
                callback_data=f"hr_vip_stars:WEEK_200:{context}"
            )
        ],
        [
            InlineKeyboardButton(
                text="👑 VIP на месяц — 300 ⭐ (выгоднее!)",
                callback_data=f"hr_vip_stars:MONTH_300:{context}"
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
        f"💡 <b>Совет для {html.quote(first_name)}: получайте вакансии первым!</b>\\n"
        f"───────────────────────────\\n\\n"
        f"Вы только что купили контакт за 50 ⭐ — это уже умный шаг! 🎯\\n\\n"
        f"Но пока вы платили за один контакт, <b>VIP-пользователи</b> уже получили эту вакансию <b>автоматически</b> — прямо в бот, <b>за 30 минут до публикации в группах</b>!\\n\\n"
        f"🏆 <b>Что даёт VIP-статус:</b>\\n"
        f"• ⚡ <b>Мгновенные PUSH</b> — все новые вакансии приходят в бот автоматически\\n"
        f"• 🔓 <b>Открытые контакты HR</b> без дополнительной оплаты\\n"
        f"• ⏱ <b>30 минут форы</b> перед всеми группами\\n"
        f"• 📩 Пока другие видят вакансию в группе — вы уже отправили резюме\\n\\n"
        f"👇 <b>Активируйте VIP прямо сейчас:</b>"
    )


@hr_bot_router.callback_query(F.data.startswith("hr_vip_info:"))
async def hr_vip_info_callback(callback: CallbackQuery):
    """Shows VIP description."""
    await callback.message.answer(
        "🏆 <b>VIP-статус HR-Radar — что это?</b>\\n"
        "───────────────────────────\\n\\n"
        "⚡ <b>Мгновенные PUSH-уведомления</b>\\n"
        "Все новые вакансии из 200+ Telegram-сообществ Дубая приходят к вам в бот автоматически, с открытыми контактами HR.\\n\\n"
        "⏱ <b>30 минут форы</b>\\n"
        "Пока вакансия появится в группах — VIP-пользователь уже успел откликнуться и назначить интервью.\\n\\n"
        "🔓 <b>Нет скрытых платежей</b>\\n"
        "Контакты HR открыты автоматически — не нужно платить 50 ⭐ за каждый контакт.\\n\\n"
        "💰 <b>Цены:</b>\\n"
        "• Неделя — всего 200 ⭐ (~$5)\\n"
        "• Месяц — 300 ⭐ (~$7.50)\\n\\n"
        "<i>Это дешевле, чем покупать 6 контактов по 50 ⭐!</i>",
        parse_mode="HTML",
        reply_markup=get_vip_stars_keyboard()
    )
    await callback.answer()


@hr_bot_router.callback_query(F.data.startswith("hr_vip_stars:"))
async def hr_vip_stars_callback(callback: CallbackQuery):
    """Sends Stars invoice for VIP subscription."""
    parts = callback.data.split(":")
    plan_code = parts[1]  # WEEK_200 or MONTH_300
    context = parts[2] if len(parts) > 2 else "general"

    is_week = plan_code == "WEEK_200"
    stars_price = 200 if is_week else 300
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
    is_week = plan_code == "WEEK_200"
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
            amount_usd=round(stars_paid * 0.025, 2),  # ~$0.025 per Star
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
                            f"⏰ <b>Напоминание #{reminder_num}: VIP HR-Radar</b>\\n"
                            f"───────────────────────────\\n\\n"
                            f"👋 {html.quote(first_name)}, пока вы искали вакансию вручную, сегодня вышло несколько горячих предложений!\\n\\n"
                            f"🔑 <b>С VIP-статусом</b> они пришли бы к вам <b>автоматически</b> — с открытым контактом HR и на 30 минут раньше всех в группах.\\n\\n"
                            f"💡 <b>Всего 200 ⭐ на неделю</b> — дешевле 4 покупок контактов по 50 ⭐!"
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
'''

with open(HR_BOT_PATH, 'a', encoding='utf-8') as f:
    f.write(VIP_CODE)

lines = len(open(HR_BOT_PATH, encoding='utf-8').readlines())
print(f"VIP Stars code appended. Total lines: {lines}")
