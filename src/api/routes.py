from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.db.models import UserProfile, UserActivityLog, Lead, Partner, LeadPurchase

router = APIRouter()

@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "Intent Hunter CDP API"}

@router.get("/stats")
async def get_platform_stats(db: AsyncSession = Depends(get_db)):
    users_count = (await db.execute(select(func.count(UserProfile.user_id)))).scalar() or 0
    logs_count = (await db.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
    leads_count = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
    sold_leads_count = (await db.execute(select(func.count(Lead.id)).where(Lead.status == "SOLD"))).scalar() or 0
    partners_count = (await db.execute(select(func.count(Partner.id)))).scalar() or 0

    return {
        "user_profiles": users_count,
        "activity_logs": logs_count,
        "total_leads": leads_count,
        "sold_leads": sold_leads_count,
        "b2b_partners": partners_count
    }

@router.get("/leads")
async def list_leads(niche: str = None, limit: int = 50, db: AsyncSession = Depends(get_db)):
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit)
    if niche:
        stmt = stmt.where(Lead.niche_code == niche)
    
    res = await db.execute(stmt)
    leads = list(res.scalars().all())
    return [
        {
            "id": l.id,
            "user_id": l.user_id,
            "niche_code": l.niche_code,
            "temperature": l.temperature,
            "confidence_score": l.confidence_score,
            "intent_summary": l.intent_summary,
            "sales_hook": l.sales_hook,
            "status": l.status,
            "price": float(l.price),
            "created_at": l.created_at.isoformat() if l.created_at else None
        }
        for l in leads
    ]

@router.get("/partners")
async def list_partners(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Partner))
    partners = list(res.scalars().all())
    return [
        {
            "id": p.id,
            "telegram_id": p.telegram_id,
            "company_name": p.company_name,
            "balance": float(p.balance),
            "subscribed_niches": p.subscribed_niches,
            "created_at": p.created_at.isoformat() if p.created_at else None
        }
        for p in partners
    ]
