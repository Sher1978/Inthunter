from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import io
import openpyxl

from src.db.session import get_db
from src.db.models import B2BPartnerLead, B2BMessageLog

b2b_router = APIRouter()

@b2b_router.get("/b2b-leads")
async def get_b2b_leads(
    geo: Optional[str] = Query(None),
    niche: Optional[str] = Query(None),
    min_messages: Optional[int] = Query(1),
    days_active: Optional[int] = Query(None),
    sort_by: Optional[str] = Query("activity_desc"),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns a list of B2B Partner Leads based on filters.
    """
    stmt = select(B2BPartnerLead)
    
    if geo:
        stmt = stmt.where(B2BPartnerLead.location_code == geo)
    if niche:
        stmt = stmt.where(B2BPartnerLead.niche_code == niche)
    if min_messages and min_messages > 1:
        stmt = stmt.where(B2BPartnerLead.total_messages_count >= min_messages)
    if days_active:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_active)
        stmt = stmt.where(B2BPartnerLead.last_activity_at >= cutoff_date)

    if sort_by == "activity_desc":
        stmt = stmt.order_by(desc(B2BPartnerLead.last_activity_at))
    elif sort_by == "messages_desc":
        stmt = stmt.order_by(desc(B2BPartnerLead.total_messages_count))
    elif sort_by == "created_desc":
        stmt = stmt.order_by(desc(B2BPartnerLead.created_at))
    else:
        stmt = stmt.order_by(desc(B2BPartnerLead.last_activity_at))
        
    result = await db.execute(stmt)
    leads = result.scalars().all()
    
    return {
        "status": "success",
        "total": len(leads),
        "data": [
            {
                "id": lead.id,
                "telegram_id": lead.telegram_id,
                "author_username": lead.author_username,
                "business_name": lead.business_name,
                "niche_code": lead.niche_code,
                "location_code": lead.location_code,
                "service_types": lead.service_types,
                "total_messages_count": lead.total_messages_count,
                "status": lead.status,
                "last_activity_at": lead.last_activity_at.isoformat() if lead.last_activity_at else None,
                "created_at": lead.created_at.isoformat() if lead.created_at else None
            }
            for lead in leads
        ]
    }

@b2b_router.get("/b2b-leads/export")
async def export_b2b_leads(
    geo: Optional[str] = Query(None),
    niche: Optional[str] = Query(None),
    min_messages: Optional[int] = Query(1),
    days_active: Optional[int] = Query(None),
    sort_by: Optional[str] = Query("activity_desc"),
    db: AsyncSession = Depends(get_db)
):
    """
    Exports the filtered B2B Partner Leads to an Excel (.xlsx) file.
    """
    stmt = select(B2BPartnerLead)
    
    if geo:
        stmt = stmt.where(B2BPartnerLead.location_code == geo)
    if niche:
        stmt = stmt.where(B2BPartnerLead.niche_code == niche)
    if min_messages and min_messages > 1:
        stmt = stmt.where(B2BPartnerLead.total_messages_count >= min_messages)
    if days_active:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_active)
        stmt = stmt.where(B2BPartnerLead.last_activity_at >= cutoff_date)

    if sort_by == "activity_desc":
        stmt = stmt.order_by(desc(B2BPartnerLead.last_activity_at))
    elif sort_by == "messages_desc":
        stmt = stmt.order_by(desc(B2BPartnerLead.total_messages_count))
    elif sort_by == "created_desc":
        stmt = stmt.order_by(desc(B2BPartnerLead.created_at))
    else:
        stmt = stmt.order_by(desc(B2BPartnerLead.last_activity_at))
        
    result = await db.execute(stmt)
    leads = result.scalars().all()

    # Create Excel file in memory
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "B2B Leads"

    # Define headers
    headers = [
        "ID", "Telegram ID", "Username", "Business Name", 
        "Niche", "Geo", "Services", "Total Messages", 
        "Status", "Last Activity", "Created At"
    ]
    ws.append(headers)

    for lead in leads:
        services_str = ", ".join(lead.service_types) if lead.service_types else ""
        row = [
            lead.id,
            lead.telegram_id or "",
            f"@{lead.author_username}" if lead.author_username else "",
            lead.business_name or "",
            lead.niche_code or "",
            lead.location_code or "",
            services_str,
            lead.total_messages_count,
            lead.status,
            lead.last_activity_at.strftime("%Y-%m-%d %H:%M:%S") if lead.last_activity_at else "",
            lead.created_at.strftime("%Y-%m-%d %H:%M:%S") if lead.created_at else ""
        ]
        ws.append(row)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"b2b_leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return Response(
        content=output.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@b2b_router.get("/b2b-leads/{lead_id}/messages")
async def get_b2b_lead_messages(
    lead_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns the message logs for a specific B2B Partner Lead.
    """
    stmt = select(B2BMessageLog).where(B2BMessageLog.b2b_lead_id == lead_id).order_by(desc(B2BMessageLog.published_at))
    result = await db.execute(stmt)
    logs = result.scalars().all()
    
    if not logs:
        # Check if lead exists
        lead_stmt = select(B2BPartnerLead).where(B2BPartnerLead.id == lead_id)
        lead = (await db.execute(lead_stmt)).scalars().first()
        if not lead:
            raise HTTPException(status_code=404, detail="B2B Lead not found")
            
    return {
        "status": "success",
        "total": len(logs),
        "data": [
            {
                "id": log.id,
                "chat_id": log.chat_id,
                "chat_title": log.chat_title,
                "channel_username": log.channel_username,
                "message_id": log.message_id,
                "message_text": log.message_text,
                "extracted_location": log.extracted_location,
                "published_at": log.published_at.isoformat() if log.published_at else None
            }
            for log in logs
        ]
    }
