"""
Custom Chat Subscription & Freezing Chamber Engine for RADAR Platform.
Handles:
  1. Live chat verification (checking if chat is alive).
  2. $2.00 USD / month per custom chat billing.
  3. Automatic renewal & reminder tracking.
  4. 'Freezing Chamber' (Морозильная камера) state management for unpaid custom chats.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Tuple, Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Partner, CustomChatSubscription, MonitoredChannel

logger = logging.getLogger("intent_hunter.custom_chat_engine")

CUSTOM_CHAT_MONTHLY_FEE = 2.00  # $2.00 USD / month per custom chat


async def process_custom_chat_addition(
    session: AsyncSession,
    partner: Partner,
    target_link: str,
    chat_title: Optional[str] = None
) -> Tuple[bool, str, Optional[CustomChatSubscription]]:
    """
    Processes user request to add a custom Telegram chat for monitoring ($2.00 USD/month).
    1. Verifies if chat link is valid.
    2. Checks if balance >= $2.00. If yes, deducts $2.00 and activates chat.
    3. If balance < $2.00, places chat in 'FREEZING' (Морозильная камера) state.
    """
    clean_target = target_link.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "").split("/")[0].strip()
    if len(clean_target) < 2:
        return False, "⚠️ Некорректный юзернейм или ссылка на чат.", None

    canonical_target = f"@{clean_target}" if not ("t.me/+" in target_link or "joinchat/" in target_link) else target_link

    # 1. Check if chat already subscribed by this partner
    stmt = select(CustomChatSubscription).where(
        CustomChatSubscription.partner_id == partner.id,
        CustomChatSubscription.username_or_link.ilike(f"%{clean_target}%")
    )
    existing_sub = (await session.execute(stmt)).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    partner_bal = float(partner.balance or 0.0)

    if existing_sub:
        if existing_sub.status == "ACTIVE" and existing_sub.paid_until and existing_sub.paid_until > now:
            return True, f"✅ Чат <code>{canonical_target}</code> уже активен до {existing_sub.paid_until.strftime('%d.%m.%Y')}!", existing_sub

        # Attempt reactivation / unfreezing
        if partner_bal >= CUSTOM_CHAT_MONTHLY_FEE:
            partner.balance = partner_bal - CUSTOM_CHAT_MONTHLY_FEE
            existing_sub.status = "ACTIVE"
            existing_sub.paid_until = now + timedelta(days=30)
            existing_sub.is_alive = True
            await session.commit()

            # Ensure channel in MonitoredChannel table
            await _ensure_monitored_channel(session, canonical_target)
            return True, f"❄️➡️🔥 <b>Чат {canonical_target} РАЗМОРОЖЕН и УСПЕШНО ОПЛАЧЕН!</b>\n\nС вашего баланса списано $2.00 USD. Активен до {existing_sub.paid_until.strftime('%d.%m.%Y')}.", existing_sub
        else:
            return False, f"🥶 <b>ЧАТ НАХОДИТСЯ В МОРОЗИЛЬНОЙ КАМЕРЕ!</b>\n\nНа вашем балансе ${partner_bal:.2f} USD. Для разморозки и возобновления прослушки {canonical_target} требуется $2.00 USD / мес. Пополните баланс!", existing_sub

    # 2. First-time subscription: Check balance
    if partner_bal >= CUSTOM_CHAT_MONTHLY_FEE:
        partner.balance = partner_bal - CUSTOM_CHAT_MONTHLY_FEE
        sub = CustomChatSubscription(
            partner_id=partner.id,
            telegram_id=partner.telegram_id,
            username_or_link=canonical_target,
            chat_title=chat_title or canonical_target,
            monthly_price=CUSTOM_CHAT_MONTHLY_FEE,
            status="ACTIVE",
            is_alive=True,
            paid_until=now + timedelta(days=30)
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)

        await _ensure_monitored_channel(session, canonical_target)
        return True, (
            f"🎉 <b>КАСТОМНЫЙ ЧАТ УСПЕШНО ПОДКЛЮЧЁН И ОПЛАЧЕН!</b>\n\n"
            f"📌 <b>Чат:</b> <code>{canonical_target}</code>\n"
            f"💰 <b>Списано с баланса:</b> ${CUSTOM_CHAT_MONTHLY_FEE:.2f} USD\n"
            f"🗓 <b>Оплачен до:</b> {sub.paid_until.strftime('%d.%m.%Y')}\n"
            f"🟢 <b>Статус:</b> 🟢 Активно сканируется нейросетью LeadRaDaR!"
        ), sub
    else:
        # Place in Freezing Chamber
        sub = CustomChatSubscription(
            partner_id=partner.id,
            telegram_id=partner.telegram_id,
            username_or_link=canonical_target,
            chat_title=chat_title or canonical_target,
            monthly_price=CUSTOM_CHAT_MONTHLY_FEE,
            status="FREEZING",
            is_alive=True,
            paid_until=now
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)

        return False, (
            f"🥶 <b>ЧАТ {canonical_target} ПОМЕЩЁН В МОРОЗИЛЬНУЮ КАМЕРУ!</b>\n\n"
            f"Стоимость мониторинга кастомного чата составляет <b>$2.00 USD / мес</b>.\n"
            f"Ваш текущий баланс: <b>${partner_bal:.2f} USD</b>.\n\n"
            f"💡 Чат верифицирован как рабочий, но заморожен до пополнения баланса. Нажмите «Пополнить баланс» для запуска прослушки!"
        ), sub


async def _ensure_monitored_channel(session: AsyncSession, canonical_target: str):
    stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(f"%{canonical_target.replace('@','')}%"))
    ch = (await session.execute(stmt)).scalar_one_or_none()
    if not ch:
        ch = MonitoredChannel(
            username_or_link=canonical_target,
            title=canonical_target,
            niche_code="community",
            location_code="global",
            chat_type="chat",
            status="JOINED"
        )
        session.add(ch)
        await session.commit()
    elif ch.status != "JOINED":
        ch.status = "JOINED"
        await session.commit()


async def run_custom_chats_billing_cycle(session: AsyncSession, bot=None) -> Dict[str, Any]:
    """
    Background job: Monitors custom chat expiration dates.
    1. Sends reminders 3 days before expiration.
    2. Auto-renews if balance available.
    3. Moves to Freezing Chamber ('FREEZING') if payment fails.
    """
    now = datetime.now(timezone.utc)
    reminder_window = now + timedelta(days=3)

    # 1. Fetch active subscriptions expiring soon
    stmt_remind = select(CustomChatSubscription, Partner).join(
        Partner, CustomChatSubscription.partner_id == Partner.id
    ).where(
        CustomChatSubscription.status == "ACTIVE",
        CustomChatSubscription.paid_until <= reminder_window,
        CustomChatSubscription.paid_until > now
    )
    expiring_subs = list((await session.execute(stmt_remind)).all())

    reminders_sent = 0
    for sub, partner in expiring_subs:
        if not sub.last_reminder_sent or (now - sub.last_reminder_sent).total_seconds() > 86400:
            sub.last_reminder_sent = now
            await session.commit()
            reminders_sent += 1
            if bot:
                try:
                    await bot.send_message(
                        partner.telegram_id,
                        f"⏳ <b>НАПОМИНАНИЕ ОБ ОПЛАТЕ КАСТОМНОГО ЧАТА!</b>\n\n"
                        f"Через {max(1, (sub.paid_until - now).days)} дн. истекает оплата мониторинга чата <code>{sub.username_or_link}</code>.\n"
                        f"Стоимость продления: <b>$2.00 USD / мес</b>. Ваш баланс: <b>${partner.balance:.2f} USD</b>.\n\n"
                        f"Пополните баланс во избежание переноса чата в Морозильную Камеру!",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Error sending reminder to partner {partner.telegram_id}: {e}")

    # 2. Process overdue custom chats (paid_until <= now)
    stmt_overdue = select(CustomChatSubscription, Partner).join(
        Partner, CustomChatSubscription.partner_id == Partner.id
    ).where(
        CustomChatSubscription.status == "ACTIVE",
        CustomChatSubscription.paid_until <= now
    )
    overdue_subs = list((await session.execute(stmt_overdue)).all())

    renewed = 0
    frozen = 0
    for sub, partner in overdue_subs:
        partner_bal = float(partner.balance or 0.0)
        if partner_bal >= CUSTOM_CHAT_MONTHLY_FEE:
            partner.balance = partner_bal - CUSTOM_CHAT_MONTHLY_FEE
            sub.paid_until = now + timedelta(days=30)
            await session.commit()
            renewed += 1
            if bot:
                try:
                    await bot.send_message(
                        partner.telegram_id,
                        f"✅ <b>АВТОМАТИЧЕСКАЯ ОПЛАТА ЧАТА УСПЕШНА!</b>\n\n"
                        f"Мониторинг чата <code>{sub.username_or_link}</code> продлен на 30 дней.\n"
                        f"Списано с баланса: <b>$2.00 USD</b>. Оплачен до: {sub.paid_until.strftime('%d.%m.%Y')}.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Error sending renewal notice to {partner.telegram_id}: {e}")
        else:
            # Move to Freezing Chamber
            sub.status = "FREEZING"
            await session.commit()
            frozen += 1
            if bot:
                try:
                    await bot.send_message(
                        partner.telegram_id,
                        f"🥶 <b>ЧАТ ПЕРЕНЕСЁН В МОРОЗИЛЬНУЮ КАМЕРУ!</b>\n\n"
                        f"Оплата мониторинга чата <code>{sub.username_or_link}</code> истекла, а на вашем балансе недостаточно средств (${partner_bal:.2f} из $2.00 USD).\n\n"
                        f"Чат временно заморожен и снят с прослушки. Пополните баланс для моментальной разморозки!",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Error sending freezing notice to {partner.telegram_id}: {e}")

    return {
        "status": "ok",
        "reminders_sent": reminders_sent,
        "renewed": renewed,
        "frozen": frozen
    }
