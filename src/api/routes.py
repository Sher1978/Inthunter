from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from pydantic import BaseModel, Field
from src.db.models import UserProfile, UserActivityLog, Lead, Partner, LeadPurchase, MonitoredChannel

class AddChannelSchema(BaseModel):
    username_or_link: str = Field(..., example="@auto_moscow_chat")
    niche_code: str = Field(default="auto_kasko", example="auto_kasko")
    title: str = Field(default=None, example="Чат Автомобилистов Москвы")

@router.get("/channels")
async def list_monitored_channels(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
    channels = list(res.scalars().all())
    return [
        {
            "id": c.id,
            "title": c.title,
            "username_or_link": c.username_or_link,
            "niche_code": c.niche_code,
            "status": c.status,
            "error_message": c.error_message,
            "created_at": c.created_at.isoformat() if c.created_at else None
        }
        for c in channels
    ]

@router.post("/channels")
async def add_monitored_channel(data: AddChannelSchema, db: AsyncSession = Depends(get_db)):
    # Check if exists
    clean_target = data.username_or_link.strip()
    stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == clean_target)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    
    if existing:
        return {"status": "exists", "channel_id": existing.id, "channel_status": existing.status}

    channel = MonitoredChannel(
        username_or_link=clean_target,
        title=data.title,
        niche_code=data.niche_code,
        status="PENDING"
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    # Attempt dynamic auto-join via global ingestor if running
    from src.api.app import ingestor
    if ingestor:
        success, title, error = await ingestor.join_channel(clean_target)
        if success:
            channel.status = "JOINED"
            if title:
                channel.title = title
            channel.error_message = None
        else:
            channel.status = "FAILED"
            channel.error_message = error
        await db.commit()
        await db.refresh(channel)

    return {
        "status": "added",
        "channel_id": channel.id,
        "channel_status": channel.status,
        "title": channel.title,
        "error": channel.error_message
    }

@router.delete("/channels/{channel_id}")
async def delete_monitored_channel(channel_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
    channel = (await db.execute(stmt)).scalar_one_or_none()
    if not channel:
        return {"status": "error", "message": "Channel not found"}
    
    await db.delete(channel)
    await db.commit()
    return {"status": "deleted", "channel_id": channel_id}

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
