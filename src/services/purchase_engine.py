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

    # 2.5 Prevent Double Purchase
    existing_purchase_stmt = select(LeadPurchase).where(
        (LeadPurchase.lead_id == lead.id) & 
        (LeadPurchase.partner_id == partner.id)
    )
    existing_purchase = (await db.execute(existing_purchase_stmt)).scalars().first()
    if existing_purchase:
        return {"status": "error", "message": "Вы уже выкупили этот лид ранее"}

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
    # Standard purchase ($1.00): gives contacts, lead remains AVAILABLE on marketplace for others
    # Exclusive buyout ($10.00): sets lead.status = SOLD, removing lead from marketplace for all others
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

    contact_data = {
        "username": username,
        "tg_link": tg_link,
        "full_name": full_name,
        "raw_contact": username
    }

    # 8. Dispatch Secure Webhook to Partner's CRM (if webhook_url configured)
    if partner.webhook_url and partner.webhook_url.strip():
        import httpx, asyncio
        from datetime import datetime, timezone

        async def send_partner_webhook(url: str, payload: dict, partner_id: str):
            try:
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "RADAR-LeadScanner-Webhook/1.0",
                    "X-Radar-Event": "lead.purchased",
                    "X-Radar-Partner-ID": partner_id,
                    "X-Radar-Signature": f"sig_{partner_id[:8]}"
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(url, json=payload, headers=headers)
                    logger.info(f"✅ Webhook successfully dispatched to {url}: HTTP {res.status_code}")
            except Exception as wh_err:
                logger.error(f"❌ Error dispatching webhook to {url}: {wh_err}")

        webhook_payload = {
            "event": "lead.purchased",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "lead_id": lead.id,
            "niche_code": lead.niche_code,
            "intent_summary": lead.intent_summary,
            "sales_hook": lead.sales_hook,
            "price_paid": price,
            "is_exclusive": is_exclusive,
            "contact": contact_data
        }
        asyncio.create_task(send_partner_webhook(partner.webhook_url.strip(), webhook_payload, partner.id))

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
        "contact": contact_data
    }
