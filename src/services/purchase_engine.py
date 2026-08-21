import logging
from typing import Dict, Any, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Lead, Partner, LeadPurchase, UserProfile, UserActivityLog
from src.services.referral_engine import process_lead_purchase_referral_accrual

logger = logging.getLogger(__name__)

async def process_lead_purchase(
    db: AsyncSession,
    partner_id: str,
    lead_id: str,
    is_exclusive: bool = False
) -> Dict[str, Any]:
    """
    Centralized business logic for purchasing a lead.
    Supports exclusive ($10.00) and standard ($1.00 or lead.price) purchases.
    Handles balance deduction, lead status updates, LeadPurchase recording,
    and triggering referral accruals.
    """
    # 1. Fetch Lead
    lead_stmt = select(Lead).where(Lead.id == lead_id)
    lead = (await db.execute(lead_stmt)).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Лид не найден"}
    
    if lead.status == "SOLD":
        return {"status": "error", "message": "Этот лид уже выкуплен эксклюзивно другим партнером"}

    # 2. Fetch Partner
    partner_stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(partner_stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Профиль партнера не найден"}

    # 3. Determine Price & Check Balance
    price = 10.00 if is_exclusive else float(lead.price or 1.00)
    current_balance = float(partner.balance or 0.0)

    if current_balance < price:
        return {
            "status": "insufficient_balance",
            "message": f"Недостаточно средств. Требуется: ${price:.2f} USD, ваш баланс: ${current_balance:.2f} USD",
            "required": price,
            "balance": current_balance
        }

    # 4. Deduct Balance & Update Lead Status
    partner.balance = round(current_balance - price, 2)
    if is_exclusive:
        lead.status = "SOLD"

    # 5. Record Lead Purchase
    purchase = LeadPurchase(
        lead_id=lead.id,
        partner_id=partner.id,
        price_paid=price
    )
    db.add(purchase)
    await db.commit()
    await db.refresh(purchase)

    # 6. Process Referral Accruals
    try:
        await process_lead_purchase_referral_accrual(purchase.id, db)
    except Exception as e:
        logger.error(f"Error processing referral accrual for purchase {purchase.id}: {e}")

    # 7. Fetch User Profile & Msg Count
    user_stmt = select(UserProfile).where(UserProfile.user_id == lead.user_id)
    user_profile = (await db.execute(user_stmt)).scalar_one_or_none()

    msg_count_stmt = select(func.count(UserActivityLog.id)).where(UserActivityLog.user_id == lead.user_id)
    user_msg_count = (await db.execute(msg_count_stmt)).scalar() or 1

    username = f"@{user_profile.username}" if user_profile and user_profile.username else f"ID {lead.user_id}"
    tg_link = f"https://t.me/{user_profile.username}" if user_profile and user_profile.username else f"tg://user?id={lead.user_id}"
    full_name = f"{user_profile.first_name or ''} {user_profile.last_name or ''}".strip() if user_profile else "Пользователь Telegram"

    return {
        "status": "ok",
        "message": "Лид успешно выкуплен!",
        "purchase_id": purchase.id,
        "new_balance": float(partner.balance),
        "price_paid": price,
        "is_exclusive": is_exclusive,
        "lead": {
            "id": lead.id,
            "user_id": lead.user_id,
            "intent_summary": lead.intent_summary,
            "sales_hook": lead.sales_hook,
            "niche_code": lead.niche_code,
            "user_msg_count": user_msg_count,
        },
        "contact": {
            "username": username,
            "tg_link": tg_link,
            "full_name": full_name,
            "raw_contact": username
        }
    }
