from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import datetime

from src.db.session import get_db
from src.db.models import OutreachProject, OutreachTask, OutreachTaskAccount, OutreachAccount, Partner, B2BProspect
from src.api.auth import get_current_user, require_admin
from src.outreach.jtbd_architect import (
    ingest_project_raw_data,
    process_interview_turn,
    finalize_and_lock_strategy,
    generate_leads_communication,
    refine_prospects_with_deep_prompt
)

outreach_router = APIRouter()

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    website_url: Optional[str] = None
    raw_document: Optional[str] = None
    system_prompt: Optional[str] = None
    knowledge_base: Optional[str] = None
    funnel_stages: Optional[list] = []

class JTBDIngestRequest(BaseModel):
    website_url: Optional[str] = None
    raw_document: str

class InterviewTurnRequest(BaseModel):
    user_message: str

class StrategyLockRequest(BaseModel):
    final_notes: Optional[str] = None

class GenerateCommunicationRequest(BaseModel):
    limit: Optional[int] = 50
    flow_filter: Optional[str] = "ALL" # 'ALL', 'B2B', 'B2C'
    location_code: Optional[str] = None # 'goa', 'dubai', 'phuket', 'all'
    niche_code: Optional[str] = None # 'real_estate', 'bike_rent', 'all'


class RefineDeepPromptRequest(BaseModel):
    prospect_ids: List[int]
    deep_prompt: str

class ApproveBatchRequest(BaseModel):
    prospect_ids: List[int]

class ProspectPatchRequest(BaseModel):
    msg_1_opener: Optional[str] = None
    msg_2_followup: Optional[str] = None
    target_cta_rule: Optional[str] = None
    approval_status: Optional[str] = None

class ToggleAIRequest(BaseModel):
    ai_enabled: bool

class SendManualMessageRequest(BaseModel):
    text: str

class TaskCreate(BaseModel):
    project_id: int
    name: str
    niche_code: Optional[str] = None
    location_code: Optional[str] = None
    persona_prompt: Optional[str] = None
    working_hours_start: Optional[str] = "09:00"
    working_hours_end: Optional[str] = "18:00"
    timezone: Optional[str] = "Asia/Dubai"
    filters: Optional[dict] = {}

class TaskAccountLink(BaseModel):
    account_id: int
    daily_limit: int = 15

@outreach_router.get("/projects")
async def get_projects(db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    result = await db.execute(select(OutreachProject).order_by(OutreachProject.id.desc()))
    return result.scalars().all()

@outreach_router.post("/projects")
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(require_admin)):
    project = OutreachProject(
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        knowledge_base=data.knowledge_base,
        funnel_stages=data.funnel_stages
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project

@outreach_router.get("/projects/{project_id}/tasks")
async def get_tasks_for_project(project_id: int, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    result = await db.execute(select(OutreachTask).where(OutreachTask.project_id == project_id))
    return result.scalars().all()

@outreach_router.post("/tasks")
async def create_task(data: TaskCreate, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(require_admin)):
    task = OutreachTask(
        project_id=data.project_id,
        name=data.name,
        niche_code=data.niche_code,
        location_code=data.location_code,
        persona_prompt=data.persona_prompt,
        working_hours_start=data.working_hours_start,
        working_hours_end=data.working_hours_end,
        timezone=data.timezone,
        filters=data.filters
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task

@outreach_router.post("/tasks/{task_id}/accounts")
async def link_account_to_task(task_id: int, data: TaskAccountLink, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(require_admin)):
    task = await db.get(OutreachTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    link = OutreachTaskAccount(
        task_id=task_id,
        account_id=data.account_id,
        daily_limit=data.daily_limit
    )
    db.add(link)
    await db.commit()
    return {"status": "success", "message": "Account linked to task"}

@outreach_router.get("/tasks/{task_id}/accounts")
async def get_task_accounts(task_id: int, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    result = await db.execute(select(OutreachTaskAccount).where(OutreachTaskAccount.task_id == task_id))
    return result.scalars().all()

# ── JTBD Strategy & Communication Endpoints ─────────────────────────────────

@outreach_router.post("/projects/{project_id}/ingest-jtbd")
async def project_ingest_jtbd(
    project_id: int,
    data: JTBDIngestRequest,
    current_user: Partner = Depends(require_admin)
):
    """Stage 1: Scrapes website and ingests raw document to form initial JTBD hypothesis and interview questions."""
    res = await ingest_project_raw_data(
        project_id=project_id,
        website_url=data.website_url,
        raw_document=data.raw_document
    )
    return res

@outreach_router.post("/projects/{project_id}/interview-turn")
async def project_interview_turn(
    project_id: int,
    data: InterviewTurnRequest,
    current_user: Partner = Depends(require_admin)
):
    """Stage 2: Chat-interview with AI Strategist to train and calibrate the JTBD strategy."""
    res = await process_interview_turn(
        project_id=project_id,
        user_message=data.user_message
    )
    return res

@outreach_router.post("/projects/{project_id}/lock-strategy")
async def project_lock_strategy(
    project_id: int,
    data: StrategyLockRequest,
    current_user: Partner = Depends(require_admin)
):
    """Finalizes and locks the JTBD strategy blueprint."""
    res = await finalize_and_lock_strategy(
        project_id=project_id,
        final_notes=data.final_notes
    )
    return res

@outreach_router.get("/projects/{project_id}/strategy")
async def get_project_strategy(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(get_current_user)
):
    """Returns project strategy state, raw summary, and interview history."""
    proj = await db.get(OutreachProject, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "id": proj.id,
        "name": proj.name,
        "strategy_status": proj.strategy_status,
        "website_url": proj.website_url,
        "raw_ingestion_summary": proj.raw_ingestion_summary,
        "interview_history": proj.interview_history,
        "locked_strategy": proj.locked_strategy,
        "system_prompt": proj.system_prompt
    }

@outreach_router.get("/projects/{project_id}/available-leads-stats")
async def get_available_leads_stats(
    project_id: int,
    location_code: Optional[str] = Query(None),
    niche_code: Optional[str] = Query(None),
    flow_filter: Optional[str] = Query("ALL"),
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(get_current_user)
):
    """Returns real-time count of available leads matching GEO and Niche criteria for generation preview."""
    from src.db.models import OutreachLead, Lead
    from src.core.taxonomy import normalize_niche
    norm_n = normalize_niche(niche_code) if niche_code and niche_code not in ("all", "any") else None

    # Count B2B leads
    b2b_stmt = select(func.count(OutreachLead.id)).where(
        OutreachLead.status.in_(["READY_FOR_OUTREACH", "AVAILABLE", "SYNCED"])
    )
    if location_code and location_code not in ("all", "global"):
        b2b_stmt = b2b_stmt.where(OutreachLead.location_code == location_code)
    if norm_n and norm_n != "general":
        b2b_stmt = b2b_stmt.where(OutreachLead.niche_code == norm_n)
    b2b_cnt = (await db.execute(b2b_stmt)).scalar() or 0

    # Count B2C leads
    b2c_stmt = select(func.count(Lead.id)).where(
        Lead.status.in_(["AVAILABLE", "NEW"])
    )
    if location_code and location_code not in ("all", "global"):
        b2c_stmt = b2c_stmt.where(Lead.location_code == location_code)
    if norm_n and norm_n != "general":
        b2c_stmt = b2c_stmt.where(Lead.niche == norm_n)
    b2c_cnt = (await db.execute(b2c_stmt)).scalar() or 0

    return {
        "b2b_count": b2b_cnt,
        "b2c_count": b2c_cnt,
        "total": b2b_cnt + b2c_cnt if flow_filter == "ALL" else (b2b_cnt if flow_filter == "B2B" else b2c_cnt)
    }

@outreach_router.post("/projects/{project_id}/generate-communication")
async def project_generate_communication(
    project_id: int,
    data: GenerateCommunicationRequest,
    current_user: Partner = Depends(require_admin)
):
    """Stage 3: Pulls scraped leads (B2C & B2B) and builds individualized communication table."""
    res = await generate_leads_communication(
        project_id=project_id,
        limit=data.limit or 50,
        flow_filter=data.flow_filter or "ALL",
        location_code=data.location_code,
        niche_code=data.niche_code
    )
    return {"generated_count": len(res), "prospects": res}

@outreach_router.get("/projects/{project_id}/communication-table")
async def get_communication_table(
    project_id: int,
    flow: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    approval: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    niche: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(get_current_user)
):
    """Returns the tabular communication matrix for the project."""
    stmt = select(B2BProspect).where(B2BProspect.project_id == project_id)
    if flow and flow != "ALL":
        stmt = stmt.where(B2BProspect.flow_type == flow)
    if status and status != "ALL":
        stmt = stmt.where(B2BProspect.status == status)
    if approval and approval != "ALL":
        stmt = stmt.where(B2BProspect.approval_status == approval)
    if location and location != "ALL":
        stmt = stmt.where(B2BProspect.location_code == location)
    if niche and niche != "ALL":
        from src.core.taxonomy import normalize_niche
        stmt = stmt.where(B2BProspect.niche == normalize_niche(niche))


    stmt = stmt.order_by(B2BProspect.id.desc())
    res = await db.execute(stmt)
    prospects = res.scalars().all()

    return [
        {
            "id": p.id,
            "flow_type": p.flow_type,
            "username": p.username,
            "telegram_id": p.telegram_id,
            "niche": p.niche,
            "location_code": p.location_code,
            "raw_ad_text": p.raw_ad_text,
            "jtbd_job": p.jtbd_job,
            "jtbd_trigger": p.jtbd_trigger,
            "msg_1_opener": p.msg_1_opener,
            "msg_2_followup": p.msg_2_followup,
            "target_cta_rule": p.target_cta_rule,
            "probable_objection_solution": p.probable_objection_solution,
            "approval_status": p.approval_status,
            "touch_stage": p.touch_stage,
            "status": p.status,
            "assigned_account_id": p.assigned_account_id,
            "ai_enabled": p.ai_enabled,
            "dialogue_history": p.dialogue_history or [],
            "sent_at": p.sent_at.isoformat() if p.sent_at else None,
            "last_touch_at": p.last_touch_at.isoformat() if p.last_touch_at else None
        }
        for p in prospects
    ]

@outreach_router.post("/projects/{project_id}/refine-with-prompt")
async def project_refine_with_prompt(
    project_id: int,
    data: RefineDeepPromptRequest,
    current_user: Partner = Depends(require_admin)
):
    """Applies user's deep prompt to batch of prospects in the table."""
    updated = await refine_prospects_with_deep_prompt(
        project_id=project_id,
        prospect_ids=data.prospect_ids,
        deep_prompt=data.deep_prompt
    )
    return {"updated_count": len(updated), "updated": updated}

@outreach_router.post("/prospects/approve-batch")
async def approve_prospects_batch(
    data: ApproveBatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(require_admin)
):
    """Approves selected prospects for live outreach dispatch."""
    pros = (await db.execute(select(B2BProspect).where(B2BProspect.id.in_(data.prospect_ids)))).scalars().all()
    for p in pros:
        p.approval_status = "APPROVED"
        p.status = "READY_FOR_OUTREACH"
        if not p.task_id and p.project_id:
            task = (await db.execute(select(OutreachTask).where(OutreachTask.project_id == p.project_id))).scalars().first()
            if not task:
                task = OutreachTask(
                    project_id=p.project_id,
                    name=f"Кампания Проекта {p.project_id}",
                    status="ACTIVE"
                )
                db.add(task)
                await db.flush()
            p.task_id = task.id

    await db.commit()
    return {"status": "success", "approved_count": len(data.prospect_ids)}


@outreach_router.patch("/prospects/{prospect_id}")
async def patch_prospect(
    prospect_id: int,
    data: ProspectPatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(require_admin)
):
    """Allows manual inline editing of any cell in the communication table."""
    p = await db.get(B2BProspect, prospect_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if data.msg_1_opener is not None:
        p.msg_1_opener = data.msg_1_opener
        p.generated_message = data.msg_1_opener
    if data.msg_2_followup is not None:
        p.msg_2_followup = data.msg_2_followup
    if data.target_cta_rule is not None:
        p.target_cta_rule = data.target_cta_rule
    if data.approval_status is not None:
        p.approval_status = data.approval_status
        if data.approval_status == "APPROVED":
            p.status = "READY_FOR_OUTREACH"

    await db.commit()
    await db.refresh(p)
    return {"status": "success", "id": p.id}


@outreach_router.get("/prospects/{prospect_id}/dialogue")
async def get_prospect_dialogue(
    prospect_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(get_current_user)
):
    """Returns conversation thread and assigned manager profile for a prospect."""
    p = (await db.execute(select(B2BProspect).where(B2BProspect.id == prospect_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")

    acc = None
    if p.assigned_account_id:
        acc = (await db.execute(select(OutreachAccount).where(OutreachAccount.id == p.assigned_account_id))).scalar_one_or_none()

    return {
        "status": "ok",
        "id": p.id,
        "username": p.username,
        "telegram_id": p.telegram_id,
        "niche": p.niche,
        "status": p.status,
        "ai_enabled": p.ai_enabled,
        "dialogue_history": p.dialogue_history or [],
        "manager_name": acc.manager_name if acc else "Екатерина",
        "manager_role": acc.manager_role if acc else "Менеджер развития",
        "account_phone": acc.phone_number if acc else None,
        "assigned_account_id": p.assigned_account_id
    }


@outreach_router.post("/prospects/{prospect_id}/toggle-ai")
async def toggle_prospect_ai(
    prospect_id: int,
    data: ToggleAIRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(require_admin)
):
    """Enables or disables automated AI employee responses for a specific conversation."""
    p = (await db.execute(select(B2BProspect).where(B2BProspect.id == prospect_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")
    p.ai_enabled = data.ai_enabled
    await db.commit()
    return {"status": "ok", "prospect_id": p.id, "ai_enabled": p.ai_enabled}


@outreach_router.post("/prospects/{prospect_id}/send-manual")
async def send_manual_message_prospect(
    prospect_id: int,
    data: SendManualMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(require_admin)
):
    """Dispatches a manual message from a manager to the customer via Pyrogram and updates dialogue history."""
    from src.outreach.account_manager import AccountManager
    p = (await db.execute(select(B2BProspect).where(B2BProspect.id == prospect_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found")

    target = p.username or p.telegram_id
    if not target:
        raise HTTPException(status_code=400, detail="Prospect has no contact details")

    acc = (await db.execute(select(OutreachAccount).where(OutreachAccount.id == p.assigned_account_id))).scalar_one_or_none() if p.assigned_account_id else await AccountManager.get_available_account(db)
    if not acc:
        raise HTTPException(status_code=400, detail="No active manager accounts available")

    text_to_send = data.text.strip()
    if not text_to_send:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        client = await AccountManager.get_or_start_client(acc)
        clean_username = p.username.replace("@", "") if p.username else None
        target_dest = f"@{clean_username}" if clean_username else p.telegram_id

        # Humanized typing simulation
        try:
            from pyrogram.enums import ChatAction
            await client.send_chat_action(chat_id=target_dest, action=ChatAction.TYPING)
            import asyncio
            await asyncio.sleep(min(3.0, max(1.0, len(text_to_send) / 45.0)))
        except Exception:
            pass

        await client.send_message(chat_id=target_dest, text=text_to_send)
        acc.last_used_at = datetime.datetime.now(datetime.timezone.utc)
    except Exception as send_err:
        raise HTTPException(status_code=500, detail=f"Telegram dispatch failed: {send_err}")

    history = list(p.dialogue_history or [])
    history.append({
        "role": "manager",
        "text": text_to_send,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "is_manual": True
    })
    p.dialogue_history = history
    p.assigned_account_id = acc.id
    await db.commit()

    return {"status": "ok", "prospect_id": p.id, "sent_message": text_to_send}


@outreach_router.get("/pool-status")
async def get_outreach_pool_status(
    db: AsyncSession = Depends(get_db),
    current_user: Partner = Depends(get_current_user)
):
    """Returns real-time health and throughput metrics of the worker account pool."""
    accs = list((await db.execute(select(OutreachAccount))).scalars().all())
    active_cnt = sum(1 for a in accs if a.status == "ACTIVE")
    cooldown_cnt = sum(1 for a in accs if a.status == "COOL_DOWN")
    banned_cnt = sum(1 for a in accs if a.status == "BANNED")
    total_sent_today = sum(a.daily_sent_count or 0 for a in accs)
    total_capacity = sum(a.max_daily_limit or 15 for a in accs if a.status == "ACTIVE")
    return {
        "status": "ok",
        "total_accounts": len(accs),
        "active_accounts": active_cnt,
        "cooldown_accounts": cooldown_cnt,
        "banned_accounts": banned_cnt,
        "total_sent_today": total_sent_today,
        "total_capacity": total_capacity
    }


