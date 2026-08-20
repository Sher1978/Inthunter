import base64
import io
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import Partner, LeadPurchase, ReferralAccrual, Lead

logger = logging.getLogger("intent_hunter.referral_engine")

async def process_lead_purchase_referral_accrual(
    lead_purchase_id: str,
    session: AsyncSession
) -> Optional[ReferralAccrual]:
    """
    Processes 20% RevShare referral accrual strictly upon lead purchase.
    Protects against duplicate accruals (idempotent per lead_purchase_id).
    Does NOT trigger on deposit top-ups.
    Sends real-time Telegram notification to the referrer.
    """
    try:
        # 1. Deduplication check
        existing = (await session.execute(
            select(ReferralAccrual).where(ReferralAccrual.lead_purchase_id == lead_purchase_id)
        )).scalar_one_or_none()
        if existing:
            logger.info(f"Referral accrual for purchase {lead_purchase_id} already processed. Skipping.")
            return existing

        # 2. Fetch LeadPurchase & Purchaser
        purchase = (await session.execute(
            select(LeadPurchase).where(LeadPurchase.id == lead_purchase_id)
        )).scalar_one_or_none()
        if not purchase:
            logger.warning(f"LeadPurchase {lead_purchase_id} not found.")
            return None

        purchaser = (await session.execute(
            select(Partner).where(Partner.id == purchase.partner_id)
        )).scalar_one_or_none()
        if not purchaser or not purchaser.referred_by_id:
            logger.info(f"Partner {purchase.partner_id} was not invited by any referrer. No bonus.")
            return None

        # 3. Fetch Referrer
        referrer = (await session.execute(
            select(Partner).where(Partner.id == purchaser.referred_by_id)
        )).scalar_one_or_none()
        if not referrer:
            logger.warning(f"Referrer partner ID {purchaser.referred_by_id} not found.")
            return None

        # 4. Calculate 20% Accrual
        price_paid = float(purchase.price_paid)
        accrual_amount = round(price_paid * 0.20, 2)
        if accrual_amount <= 0:
            return None

        # Update Referrer Balance
        referrer.referral_balance = round(float(referrer.referral_balance) + accrual_amount, 2)
        referrer.total_referral_earned = round(float(referrer.total_referral_earned) + accrual_amount, 2)

        # 5. Save Accrual Record
        accrual = ReferralAccrual(
            lead_purchase_id=lead_purchase_id,
            referrer_id=referrer.id,
            referred_user_id=purchaser.id,
            payment_amount=price_paid,
            accrual_amount=accrual_amount
        )
        session.add(accrual)
        await session.commit()
        await session.refresh(accrual)

        logger.info(f"🎉 20% RevShare Accrual (+${accrual_amount}) credited to referrer {referrer.telegram_id} for purchase {lead_purchase_id}")

        # 6. Real-time Telegram Notification to Referrer
        await notify_referrer_accrual(referrer.telegram_id, price_paid, accrual_amount, float(referrer.referral_balance))

        return accrual

    except Exception as e:
        logger.error(f"Error processing referral accrual for purchase {lead_purchase_id}: {e}")
        return None


async def notify_referrer_accrual(
    referrer_telegram_id: int,
    price_paid: float,
    accrual_amount: float,
    current_balance: float
):
    """Sends real-time Telegram notification to referrer when a bonus is earned."""
    if not settings.TELEGRAM_BOT_TOKEN or not referrer_telegram_id:
        return

    msg = (
        f"💰 <b>ВАМ НАЧИСЛЕН 20% БОНУС!</b>\n"
        f"───────────────────────────\n\n"
        f"🎉 Ваш приглашенный реферал выкупил целевой лид на <b>${price_paid:.2f} USD</b>.\n"
        f"✨ Вам мгновенно зачислено: <b>+${accrual_amount:.2f} USD</b> (20% RevShare)\n\n"
        f"💼 Доступный реферальный баланс: <b>${current_balance:.2f} USD</b>\n"
    )
    if current_balance >= 50.0:
        msg += f"💸 <i>Вы можете запросить вывод средств от $50 USD в главном меню бота или веб-профиле!</i>"
    else:
        msg += f"ℹ️ <i>Накопите $50.00 USD для создания заявки на вывод.</i>"

    try:
        import httpx
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={
                "chat_id": referrer_telegram_id,
                "text": msg,
                "parse_mode": "HTML"
            })
    except Exception as e:
        logger.error(f"Error sending Telegram notification to referrer {referrer_telegram_id}: {e}")


def generate_referral_qr_base64(referral_link: str) -> str:
    """Generates base64 PNG data URL for referral QR Code."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(referral_link)
        qr.make(fit=True)

        img = qr.make_image(fill_color="#4F46E5", back_color="#FFFFFF")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{b64_str}"
    except Exception as e:
        logger.warning(f"qrcode library failed: {e}. Returning fallback API QR URL.")
        # Fallback to quick QR API URL
        import urllib.parse
        encoded = urllib.parse.quote(referral_link)
        return f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={encoded}&color=4F46E5"
