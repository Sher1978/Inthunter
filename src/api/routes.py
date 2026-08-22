import logging
from datetime import timedelta, datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.session import get_db
from pydantic import BaseModel, Field
from src.db.models import UserProfile, UserActivityLog, Lead, Partner, LeadPurchase, MonitoredChannel, Rubric, AIEvaluationLog
from src.bot.keyboards import NICHE_NAMES, register_dynamic_rubric

logger = logging.getLogger("intent_hunter.api")
router = APIRouter()

class AddChannelSchema(BaseModel):
    username_or_link: str = Field(..., example="@auto_moscow_chat")
    niche_code: str = Field(default="auto_kasko", example="auto_kasko")
    location_code: Optional[str] = Field(default=None, example="nhatrang")
    title: str = Field(default=None, example="Чат Автомобилистов Москвы")
    chat_type: str = Field(default="channel", example="group")

class GrokSearchSchema(BaseModel):
    keywords: str = Field(..., example="нячанг аренда жилья")
    niche_code: str = Field(default="general", example="real_estate")

class GrokChatMessageSchema(BaseModel):
    role: str = Field(..., example="user")
    content: str = Field(..., example="Найди группы с арендой жилья")

class GrokChatRequestSchema(BaseModel):
    user_input: str = Field(..., example="Ищи чаты в Нячанге")
    history: list = Field(default=[], example=[])
    niche_code: str = Field(default="general", example="real_estate")

class AddRubricSchema(BaseModel):
    code: str = Field(..., example="legal_services")
    name: str = Field(..., example="⚖️ Юридические услуги")
    icon: str = Field(default="🏷️", example="⚖️")

class UpdateRubricSchema(BaseModel):
    name: str = Field(..., example="⚖️ Юридические консультации")
    icon: str = Field(default="🏷️", example="⚖️")

class UpdateChannelSchema(BaseModel):
    location_code: Optional[str] = None
    niche_code: Optional[str] = None

class VerifyPasscodeSchema(BaseModel):
    passcode: str = Field(..., example="260669")

@router.post("/auth/verify-passcode")
async def verify_admin_passcode(data: VerifyPasscodeSchema):
    if data.passcode.strip() == settings.ADMIN_PASSCODE:
        return {"status": "ok", "message": "Авторизация успешна"}
    return {"status": "error", "message": "Неверный пароль администратора"}

@router.post("/grok/search-channels")
async def grok_search_channels(data: GrokSearchSchema):
    from src.ai.grok_channel_finder import GrokChannelFinder
    finder = GrokChannelFinder()
    candidates = await finder.search_channels_and_groups(keywords=data.keywords, niche_code=data.niche_code, limit=8)
    return {"status": "ok", "keywords": data.keywords, "candidates": candidates}

@router.post("/grok/chat")
async def grok_proactive_chat(data: GrokChatRequestSchema):
    from src.ai.grok_channel_finder import GrokChannelFinder
    finder = GrokChannelFinder()
    res = await finder.proactive_chat_dialog(
        messages_history=data.history,
        user_input=data.user_input,
        niche_code=data.niche_code
    )
    return {"status": "ok", "response": res}

@router.get("/channels")
async def list_monitored_channels(
    location: str = None,
    niche: str = None,
    query: str = None,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc())
    if niche and niche != "all":
        stmt = stmt.where(MonitoredChannel.niche_code == niche)
    
    if location and location != "all":
        stmt = stmt.where(MonitoredChannel.location_code == location)

    res = await db.execute(stmt)
    channels = list(res.scalars().all())

    if query:
        q_clean = query.strip().lower()
        channels = [
            c for c in channels
            if q_clean in (c.title or "").lower() or q_clean in c.username_or_link.lower()
        ]

    return [
        {
            "id": c.id,
            "title": c.title,
            "username_or_link": c.username_or_link,
            "niche_code": c.niche_code,
            "location_code": getattr(c, "location_code", "nhatrang") or "nhatrang",
            "chat_type": getattr(c, "chat_type", "channel") or "channel",
            "status": c.status,
            "error_message": c.error_message,
            "created_at": (c.created_at + timedelta(hours=7)).isoformat() if c.created_at else None
        }
        for c in channels
    ]

@router.post("/channels")
async def add_monitored_channel(data: AddChannelSchema, db: AsyncSession = Depends(get_db)):
    raw_target = data.username_or_link.strip()

    # Check for internal client /c/ links
    if "/c/" in raw_target:
        return {
            "status": "error",
            "message": "Ссылка формата /c/3493020432 является внутренней ссылкой веб-клиента. Укажите публичный юзернейм (@username) или инвайт-ссылку (https://t.me/+...)."
        }

    if "/+" in raw_target or "joinchat/" in raw_target:
        canonical_target = raw_target
        clean_user = raw_target.split("/")[-1].replace("+", "").strip()
    else:
        clean_user = raw_target.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "").split("/")[0].strip()
        if not clean_user or len(clean_user) < 2:
            return {"status": "error", "message": "Укажите корректный юзернейм или ссылку на Telegram канал."}
        canonical_target = f"@{clean_user}"

    # Check if exists by username or raw link
    stmt = select(MonitoredChannel).where(
        (MonitoredChannel.username_or_link.ilike(f"%{clean_user}%"))
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    
    if existing:
        return {
            "status": "exists",
            "message": f"Чат или канал {canonical_target} уже есть в списке прослушки!",
            "channel_id": existing.id,
            "channel_status": existing.status
        }

    # Infer location code if not specified
    loc_code = data.location_code
    if not loc_code or loc_code == "all":
        u_low = clean_user.lower()
        if "danang" in u_low or "дананг" in u_low:
            loc_code = "danang"
        elif "dubai" in u_low or "дубай" in u_low:
            loc_code = "dubai"
        elif "phuket" in u_low or "пхукет" in u_low:
            loc_code = "phuket"
        elif "bali" in u_low or "бали" in u_low:
            loc_code = "bali"
        elif "tbilisi" in u_low or "тбилиси" in u_low:
            loc_code = "tbilisi"
        elif "nhatrang" in u_low or "нячанг" in u_low:
            loc_code = "nhatrang"
        else:
            loc_code = "global"

    channel = MonitoredChannel(
        username_or_link=canonical_target,
        title=data.title or canonical_target,
        niche_code=data.niche_code,
        location_code=loc_code,
        chat_type=data.chat_type,
        status="PENDING"
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    # Attempt dynamic auto-join via global ingestor if running
    from src.api.app import ingestor
    if ingestor:
        success, title, error = await ingestor.join_channel(canonical_target)
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

        try:
            import asyncio
            asyncio.create_task(ingestor.scrape_channel_now(channel.id))
        except Exception:
            pass

    return {
        "status": "added",
        "message": f"Канал {canonical_target} успешно добавлен!",
        "channel_id": channel.id,
        "channel_status": channel.status,
        "title": channel.title,
        "error": channel.error_message
    }

@router.delete("/channels/{channel_id}")
async def delete_monitored_channel(channel_id: str, target: str = None, db: AsyncSession = Depends(get_db)):
    channel = None
    if channel_id and channel_id != "by-target":
        stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
        channel = (await db.execute(stmt)).scalar_one_or_none()
    
    if not channel and (target or channel_id):
        raw_query = (target or channel_id).strip()
        clean_user = raw_query.replace("@", "").replace("https://t.me/s/", "").replace("https://t.me/", "")
        stmt = select(MonitoredChannel).where(
            (MonitoredChannel.username_or_link.ilike(f"%{clean_user}%")) |
            (MonitoredChannel.title.ilike(f"%{raw_query}%"))
        )
        channel = (await db.execute(stmt)).scalar_one_or_none()

    if not channel:
        return {"status": "error", "message": f"Канал не найден: {target or channel_id}"}
    
    ch_id = channel.id
    ch_title = channel.title
    clean_user = channel.username_or_link.replace("@", "").replace("https://t.me/", "")

    # Delete non-lead activity logs associated with this channel
    from sqlalchemy import delete
    if ch_title:
        await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{ch_title}%")))
    if clean_user:
        await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{clean_user}%")))

    await db.delete(channel)
    await db.commit()

    # Trigger restart of scraper loop to instantly update channel queue
    try:
        from src.api.app import ingestor
        if ingestor:
            import asyncio
            asyncio.create_task(ingestor.restart_scraper_loop())
    except Exception:
        pass

    return {"status": "deleted", "channel_id": ch_id, "title": ch_title or clean_user}

@router.patch("/channels/{channel_id}")
@router.put("/channels/{channel_id}")
async def update_monitored_channel(channel_id: str, data: UpdateChannelSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
    channel = (await db.execute(stmt)).scalar_one_or_none()
    if not channel:
        return {"status": "error", "message": "Channel not found"}
    
    if data.location_code is not None:
        channel.location_code = data.location_code
    if data.niche_code is not None:
        channel.niche_code = data.niche_code
        
    await db.commit()
    await db.refresh(channel)
    return {
        "status": "updated",
        "channel_id": channel.id,
        "location_code": channel.location_code,
        "niche_code": channel.niche_code
    }

@router.get("/ai-evaluation-logs")
async def get_ai_evaluation_logs(limit: int = 50, filter_type: str = "all", db: AsyncSession = Depends(get_db)):
    """Returns AI analyzer evaluation logs with CoT reasoning comments for each scanned message."""
    logs = []
    
    # Pre-build lookup map of MonitoredChannel by title and username
    ch_res = await db.execute(select(MonitoredChannel))
    monitored_channels = list(ch_res.scalars().all())
    ch_id_by_title = {c.title.strip().lower(): c.id for c in monitored_channels if c.title}
    ch_id_by_user = {c.username_or_link.replace("@", "").lower(): c.id for c in monitored_channels if c.username_or_link}

    try:
        stmt = select(AIEvaluationLog)
        if filter_type == "leads":
            stmt = stmt.where(AIEvaluationLog.is_lead == True)
        elif filter_type == "rejected":
            stmt = stmt.where(AIEvaluationLog.is_lead == False)

        stmt = stmt.order_by(AIEvaluationLog.created_at.desc()).limit(200)
        res = await db.execute(stmt)
        logs = list(res.scalars().all())

        # Check if the table has ANY records at all (to decide whether to use fallback)
        total_count = (await db.execute(select(func.count(AIEvaluationLog.id)))).scalar() or 0
    except Exception as e:
        logger.warning(f"AIEvaluationLog query warning, using UserActivityLog fallback: {e}")
        logs = []
        total_count = 0

    # Fallback to UserActivityLog ONLY if AIEvaluationLog table has never been populated yet
    if total_count == 0:
        u_stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(limit)
        u_logs = list((await db.execute(u_stmt)).scalars().all())

        items = []
        for log in u_logs:
            lead_stmt = select(Lead).where(Lead.user_id == log.user_id).order_by(Lead.created_at.desc()).limit(1)
            lead_obj = (await db.execute(lead_stmt)).scalar_one_or_none()

            prof_stmt = select(UserProfile).where(UserProfile.user_id == log.user_id)
            prof_obj = (await db.execute(prof_stmt)).scalar_one_or_none()

            is_lead = lead_obj is not None
            if filter_type == "leads" and not is_lead:
                continue
            if filter_type == "rejected" and is_lead:
                continue

            reasoning = lead_obj.intent_summary if is_lead and lead_obj else "Обсуждение в общем чате. ИИ-анализатор отсеял как флуд/информационное сообщение без конкретного клиентского спроса."
            ts_utc7 = (log.timestamp + timedelta(hours=7)) if log.timestamp else None
            ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"

            c_title = log.chat_title or "Группа/Чат"
            matched_id = ch_id_by_title.get(c_title.strip().lower())

            items.append({
                "id": str(log.id),
                "user_id": log.user_id,
                "username": f"@{prof_obj.username}" if prof_obj and prof_obj.username else f"ID {log.user_id}",
                "first_name": (prof_obj.first_name if prof_obj else "") or "Telegram User",
                "chat_title": c_title,
                "channel_id": matched_id,
                "message_text": log.message_text,
                "is_lead": is_lead,
                "reasoning": reasoning,
                "niche_code": lead_obj.niche_code if lead_obj else None,
                "temperature": lead_obj.temperature if lead_obj else None,
                "confidence_score": lead_obj.confidence_score if lead_obj else (0.95 if is_lead else 0.0),
                "created_at": ts_str
            })

        return items

    items = []
    seen_message_texts = set()
    for log in logs:
        seen_message_texts.add((log.user_id, (log.message_text or "").strip()))
        ts_utc7 = (log.created_at + timedelta(hours=7)) if log.created_at else None
        ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
        c_title = log.chat_title or "Группа/Чат"
        matched_id = ch_id_by_title.get(c_title.strip().lower())
        items.append({
            "id": log.id,
            "user_id": log.user_id,
            "username": log.username or f"ID {log.user_id}",
            "first_name": log.first_name or "Telegram User",
            "chat_title": c_title,
            "channel_id": matched_id,
            "message_text": log.message_text,
            "is_lead": log.is_lead,
            "reasoning": log.reasoning,
            "niche_code": log.niche_code,
            "temperature": log.temperature,
            "confidence_score": log.confidence_score or 0.0,
            "created_at": ts_str
        })

    # Fetch recent UserActivityLog entries to fill any gap if AI evaluation logs didn't capture them yet
    try:
        u_stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(limit)
        u_logs = list((await db.execute(u_stmt)).scalars().all())

        for log in u_logs:
            if (log.user_id, (log.message_text or "").strip()) in seen_message_texts:
                continue

            lead_stmt = select(Lead).where(Lead.user_id == log.user_id).order_by(Lead.created_at.desc()).limit(1)
            lead_obj = (await db.execute(lead_stmt)).scalar_one_or_none()

            prof_stmt = select(UserProfile).where(UserProfile.user_id == log.user_id)
            prof_obj = (await db.execute(prof_stmt)).scalar_one_or_none()

            is_lead = lead_obj is not None
            if filter_type == "leads" and not is_lead:
                continue
            if filter_type == "rejected" and is_lead:
                continue

            reasoning = lead_obj.intent_summary if is_lead and lead_obj else "Обсуждение в общем чате. ИИ-анализатор отсеял как флуд/информационное сообщение без конкретного клиентского спроса."
            ts_utc7 = (log.timestamp + timedelta(hours=7)) if log.timestamp else None
            ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"

            c_title = log.chat_title or "Группа/Чат"
            matched_id = ch_id_by_title.get(c_title.strip().lower())

            items.append({
                "id": str(log.id),
                "user_id": log.user_id,
                "username": f"@{prof_obj.username}" if prof_obj and prof_obj.username else f"ID {log.user_id}",
                "first_name": (prof_obj.first_name if prof_obj else "") or "Telegram User",
                "chat_title": c_title,
                "channel_id": matched_id,
                "message_text": log.message_text,
                "is_lead": is_lead,
                "reasoning": reasoning,
                "niche_code": lead_obj.niche_code if lead_obj else None,
                "temperature": lead_obj.temperature if lead_obj else None,
                "confidence_score": lead_obj.confidence_score if lead_obj else (0.95 if is_lead else 0.0),
                "created_at": ts_str
            })
    except Exception as u_err:
        logger.warning(f"UserActivityLog merge notice: {u_err}")

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]


@router.get("/live-stream")
async def get_live_activity_stream(limit: int = 35, db: AsyncSession = Depends(get_db)):
    """Returns recent activity logs for live-stream userbot parsing monitor."""
    stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(limit)
    res = await db.execute(stmt)
    logs = list(res.scalars().all())

    ch_stmt = select(MonitoredChannel)
    ch_res = await db.execute(ch_stmt)
    channels = list(ch_res.scalars().all())
    ch_map = {c.title: c.username_or_link for c in channels if c.title}
    ch_id_map = {c.title.strip().lower(): c.id for c in channels if c.title}
    ch_id_user_map = {c.username_or_link.replace("@", "").lower(): c.id for c in channels if c.username_or_link}

    items = []
    for log in logs:
        # Check if author had qualified lead or AI evaluation log
        lead_stmt = select(Lead).where(Lead.user_id == log.user_id).order_by(Lead.created_at.desc()).limit(1)
        lead_obj = (await db.execute(lead_stmt)).scalar_one_or_none()

        eval_stmt = select(AIEvaluationLog).where(
            AIEvaluationLog.user_id == log.user_id,
            AIEvaluationLog.message_text == log.message_text
        ).order_by(AIEvaluationLog.created_at.desc()).limit(1)
        eval_obj = (await db.execute(eval_stmt)).scalar_one_or_none()

        is_lead = (lead_obj is not None) or (eval_obj is not None and eval_obj.is_lead)

        tg_link = ch_map.get(log.chat_title, None)
        if not tg_link and log.chat_title and log.chat_title.startswith("@"):
            tg_link = log.chat_title

        c_title = log.chat_title or "Групповой чат"
        c_title_clean = c_title.strip().lower()
        link_clean = (tg_link or "").replace("@", "").lower()
        ch_id = ch_id_map.get(c_title_clean) or ch_id_user_map.get(link_clean)

        ts_utc7 = (log.timestamp + timedelta(hours=7)) if log.timestamp else None

        items.append({
            "id": log.id,
            "timestamp": ts_utc7.isoformat() if ts_utc7 else None,
            "time_str": ts_utc7.strftime("%H:%M:%S") if ts_utc7 else "",
            "chat_title": c_title,
            "channel_id": ch_id,
            "channel_link": tg_link,
            "user_id": log.user_id,
            "message_text": log.message_text,
            "is_lead": is_lead,
            "niche_code": lead_obj.niche_code if lead_obj else (eval_obj.niche_code if eval_obj else None),
            "temperature": lead_obj.temperature if lead_obj else (eval_obj.temperature if eval_obj else None)
        })

    return items

@router.get("/rubrics")
async def list_rubrics(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Rubric).order_by(Rubric.name.asc()))
    db_rubrics = list(res.scalars().all())
    db_dict = {r.code: {"code": r.code, "name": r.name, "icon": r.icon, "is_custom": r.is_custom} for r in db_rubrics}

    # Merge with memory NICHE_NAMES
    all_items = []
    seen_codes = set()

    for code, name in NICHE_NAMES.items():
        seen_codes.add(code)
        if code in db_dict:
            all_items.append(db_dict[code])
        else:
            all_items.append({"code": code, "name": name, "icon": "🏷️", "is_custom": False})

    for code, item in db_dict.items():
        if code not in seen_codes:
            all_items.append(item)

    return all_items

@router.post("/rubrics")
async def create_rubric(data: AddRubricSchema, db: AsyncSession = Depends(get_db)):
    code_clean = data.code.strip().lower().replace(" ", "_")
    stmt = select(Rubric).where(Rubric.code == code_clean)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return {"status": "exists", "rubric": existing.code}

    rub = Rubric(code=code_clean, name=data.name.strip(), icon=data.icon, is_custom=True)
    db.add(rub)
    await db.commit()
    
    register_dynamic_rubric(code_clean, data.name.strip())
    return {"status": "created", "code": code_clean, "name": data.name}

@router.put("/rubrics/{code}")
async def update_rubric(code: str, data: UpdateRubricSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(Rubric).where(Rubric.code == code)
    rub = (await db.execute(stmt)).scalar_one_or_none()
    
    if not rub:
        rub = Rubric(code=code, name=data.name.strip(), icon=data.icon, is_custom=True)
        db.add(rub)
    else:
        rub.name = data.name.strip()
        rub.icon = data.icon

    await db.commit()
    register_dynamic_rubric(code, data.name.strip())
    return {"status": "updated", "code": code, "name": data.name}

@router.delete("/rubrics/{code}")
async def delete_rubric(code: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Rubric).where(Rubric.code == code)
    rub = (await db.execute(stmt)).scalar_one_or_none()
    if rub:
        await db.delete(rub)
        await db.commit()

    if code in NICHE_NAMES:
        del NICHE_NAMES[code]

    return {"status": "deleted", "code": code}

@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "Intent Hunter CDP API"}

@router.get("/collector-logs")
async def get_collector_logs(limit: int = 100, db: AsyncSession = Depends(get_db)):
    """
    Returns real-time telemetry logs of Telegram message collector activity over the last 1 hour.
    Logs older than 1 hour are automatically pruned.
    """
    from src.db.models import CollectorLog
    cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
    
    stmt = (
        select(CollectorLog)
        .where(CollectorLog.created_at >= cutoff_1h)
        .order_by(CollectorLog.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    raw_logs = list(res.scalars().all())

    ch_res = await db.execute(select(MonitoredChannel))
    channels = list(ch_res.scalars().all())
    ch_id_map = {c.title.strip().lower(): c.id for c in channels if c.title}
    ch_id_user_map = {c.username_or_link.replace("@", "").lower(): c.id for c in channels if c.username_or_link}

    total_checks_1h = len(raw_logs)
    total_posts_seen_1h = sum(getattr(l, "total_fetched_count", 0) or 0 for l in raw_logs)
    total_new_msgs_1h = sum(l.new_messages_count for l in raw_logs)
    total_leads_1h = sum(l.new_leads_count for l in raw_logs)

    items = []
    for l in raw_logs:
        ts_utc7 = (l.created_at + timedelta(hours=7)) if l.created_at else None
        c_title_clean = (l.chat_title or "").strip().lower()
        user_clean = (l.username_or_link or "").replace("@", "").lower()
        ch_id = ch_id_map.get(c_title_clean) or ch_id_user_map.get(user_clean)

        items.append({
            "id": l.id,
            "chat_title": l.chat_title,
            "username_or_link": l.username_or_link,
            "channel_id": ch_id,
            "total_fetched_count": getattr(l, "total_fetched_count", 0) or 0,
            "new_messages_count": l.new_messages_count,
            "new_leads_count": l.new_leads_count,
            "status": l.status,
            "created_at_fmt": ts_utc7.strftime("%H:%M:%S") if ts_utc7 else "—",
            "time_full": ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
        })

    return {
        "status": "ok",
        "summary": {
            "checks_1h": total_checks_1h,
            "posts_seen_1h": total_posts_seen_1h,
            "new_messages_1h": total_new_msgs_1h,
            "new_leads_1h": total_leads_1h
        },
        "logs": items
    }

@router.get("/stats")
async def get_platform_stats(db: AsyncSession = Depends(get_db)):
    cutoff_1h_tz = datetime.now(timezone.utc) - timedelta(hours=1)
    cutoff_1h_naive = datetime.utcnow() - timedelta(hours=1)
    cutoff_15m_tz = datetime.now(timezone.utc) - timedelta(minutes=15)
    cutoff_15m_naive = datetime.utcnow() - timedelta(minutes=15)
    cutoff_24h_tz = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_24h_naive = datetime.utcnow() - timedelta(hours=24)

    users_count = (await db.execute(select(func.count(UserProfile.user_id)))).scalar() or 0
    logs_count = (await db.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
    logs_1h_count = (await db.execute(
        select(func.count(UserActivityLog.id)).where(
            (UserActivityLog.timestamp >= cutoff_1h_tz) | (UserActivityLog.timestamp >= cutoff_1h_naive)
        )
    )).scalar() or 0
    logs_pass_count = (await db.execute(
        select(func.count(UserActivityLog.id)).where(
            (UserActivityLog.timestamp >= cutoff_15m_tz) | (UserActivityLog.timestamp >= cutoff_15m_naive)
        )
    )).scalar() or 0
    logs_24h_count = (await db.execute(
        select(func.count(UserActivityLog.id)).where(
            (UserActivityLog.timestamp >= cutoff_24h_tz) | (UserActivityLog.timestamp >= cutoff_24h_naive)
        )
    )).scalar() or 0

    # Count unique AVAILABLE leads by distinct intent_summary (matches what list_leads displays)
    all_available = (await db.execute(
        select(Lead.intent_summary).where(Lead.status == "AVAILABLE").where(Lead.intent_summary.isnot(None))
    )).scalars().all()
    unique_summaries = set(s.strip().lower() for s in all_available if s and s.strip())
    leads_count = len(unique_summaries)

    # Count purchased leads across LeadPurchase table AND Lead status
    purchased_count = (await db.execute(select(func.count(LeadPurchase.id)))).scalar() or 0
    sold_status_count = (await db.execute(select(func.count(Lead.id)).where(Lead.status.in_(["SOLD", "PURCHASED", "EXCLUSIVE", "CLAIMED"])))).scalar() or 0
    sold_leads_count = max(purchased_count, sold_status_count)

    # Count partners strictly from Partner table
    partners_count = (await db.execute(select(func.count(Partner.id)))).scalar() or 0
    channels_count = (await db.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0

    db_size = "Н/Д"
    try:
        from sqlalchemy import text
        res = await db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
        val = res.scalar()
        if val:
            db_size = str(val)
    except Exception:
        try:
            from sqlalchemy import text
            res = await db.execute(text("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"))
            bytes_val = res.scalar()
            if bytes_val:
                db_size = f"{bytes_val / (1024 * 1024):.2f} MB"
        except Exception:
            pass

    # Query CollectorLog telemetry for actual posts checked in the last 1 hour
    from src.db.models import CollectorLog
    collector_res = await db.execute(
        select(
            func.sum(CollectorLog.total_fetched_count),
            func.sum(CollectorLog.new_messages_count)
        ).where(
            (CollectorLog.created_at >= cutoff_1h_tz) | (CollectorLog.created_at >= cutoff_1h_naive)
        )
    )
    c_row = collector_res.first()
    posts_seen_1h = (c_row[0] or 0) if c_row else 0
    collector_new_msgs = (c_row[1] or 0) if c_row else 0

    scanned_display_1h = logs_1h_count if logs_1h_count > 0 else (posts_seen_1h or collector_new_msgs or logs_24h_count)

    userbot_info = {
        "is_connected": True,
        "mode": "⚡ ИИ-Сканер & Сборщик (25s)",
        "last_check_at": "—",
        "last_scraped_at": "—"
    }
    try:
        from src.api.app import ingestor
        if ingestor:
            is_conn = bool(ingestor._is_running or (ingestor.app and getattr(ingestor.app, "is_connected", False)))
            mode_str = "⚡ Pyrogram MTProto Userbot" if (ingestor.app and getattr(ingestor.app, "is_connected", False)) else "⚡ ИИ-Сканер & Сборщик (25s)"
            ts_check = (ingestor.last_check_at + timedelta(hours=7)).strftime("%H:%M:%S") if ingestor.last_check_at else "—"
            ts_scrap = (ingestor.last_scraped_at + timedelta(hours=7)).strftime("%H:%M:%S") if ingestor.last_scraped_at else "—"
            userbot_info = {
                "is_connected": is_conn,
                "mode": mode_str,
                "last_check_at": ts_check,
                "last_scraped_at": ts_scrap
            }
    except Exception:
        pass

    return {
        "user_profiles": users_count,
        "activity_logs": logs_count,
        "scanned_1h": scanned_display_1h,
        "scanned_pass": logs_pass_count,
        "scanned_24h": logs_24h_count,
        "posts_seen_1h": posts_seen_1h,
        "total_leads": leads_count,
        "sold_leads": sold_leads_count,
        "b2b_partners": partners_count,
        "monitored_channels": channels_count,
        "db_size": db_size,
        "userbot_info": userbot_info
    }


@router.post("/collector/rescan-last-hour")
async def trigger_manual_rescan_hour():
    """Triggers an immediate forced 1-hour rescan across all monitored channels/groups."""
    try:
        from src.api.app import ingestor
        if ingestor:
            count = await ingestor.force_rescan_past_hour()
            return {"status": "ok", "message": f"Приоритетный перескан за 1 час успешно запущен для {count} каналов", "channels_count": count}
        return {"status": "error", "message": "Сборщик не запущен"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при запуске пересканирования: {e}")


@router.get("/collector/telemetry")
async def get_collector_telemetry(db: AsyncSession = Depends(get_db)):
    """Returns latest 50 real-time telemetry log entries (including 0-message poll attempts)."""
    from src.db.models import CollectorLog
    res = await db.execute(
        select(CollectorLog).order_by(CollectorLog.created_at.desc()).limit(50)
    )
    logs = list(res.scalars().all())
    return [
        {
            "id": l.id,
            "chat_title": l.chat_title,
            "username_or_link": l.username_or_link,
            "total_fetched_count": l.total_fetched_count,
            "new_messages_count": l.new_messages_count,
            "status": l.status,
            "details": l.details or ("Опрос выполнен (0 новых сообщений)" if l.status == "OK" else "Новые сообщения"),
            "created_at_fmt": (l.created_at + timedelta(hours=7)).strftime("%H:%M:%S") if l.created_at else "—"
        }
        for l in logs
    ]

LOCATION_NAMES = {
    "dubai": "🇦🇪 Дубай",
    "nhatrang": "🇻🇳 Нячанг",
    "phuket": "🇹🇭 Пхукет",
    "bali": "🇮🇩 Бали",
    "danang": "🇻🇳 Дананг",
    "tbilisi": "🇬🇪 Тбилиси",
    "global": "🌐 Глобал / РФ"
}

# ────────────────────────────────────────────────────────────────────────────
# CHANNEL EFFECTIVENESS REPORT
# Color coding: days since last scan activity → 7 heat levels
#   0d=fresh(teal) 1d=blue 2d=indigo 3d=yellow 4d=orange 5d=red-orange 6d=red 7d+=crimson
# ────────────────────────────────────────────────────────────────────────────
EFFECTIVENESS_COLORS = [
    {"label": "Активный",      "class": "eff-fresh",   "emoji": "🟢"},  # 0 days
    {"label": "1 день тишины", "class": "eff-day1",    "emoji": "🔵"},  # 1 day
    {"label": "2 дня тишины",  "class": "eff-day2",    "emoji": "🔷"},  # 2 days
    {"label": "3 дня тишины",  "class": "eff-day3",    "emoji": "🟡"},  # 3 days
    {"label": "4 дня тишины",  "class": "eff-day4",    "emoji": "🟠"},  # 4 days
    {"label": "5 дней тишины", "class": "eff-day5",    "emoji": "🔶"},  # 5 days
    {"label": "6 дней тишины", "class": "eff-day6",    "emoji": "🔴"},  # 6 days
    {"label": "Мёртвый канал", "class": "eff-dead",    "emoji": "💀"},  # 7+ days
]

@router.get("/channels/effectiveness")
async def get_channel_effectiveness(db: AsyncSession = Depends(get_db)):
    """
    Returns per-channel effectiveness stats:
    - msgs_7d: messages scanned in last 7 days
    - leads_7d: leads detected from users in this channel in last 7 days
    - leads_total: all-time leads from this channel's users
    - last_activity_at: last message timestamp for this channel
    - days_idle: days since last scanned message
    - color_class, color_label, color_emoji: heatmap color tier (0–7+)
    """
    now_utc = datetime.now(timezone.utc)
    cutoff_7d = now_utc - timedelta(days=7)

    channels_res = await db.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
    channels = list(channels_res.scalars().all())

    result = []
    for ch in channels:
        raw_title = (ch.title or "").strip()
        clean_title_key = raw_title.replace("Обнаружен в ", "").strip()
        username_key = (ch.username_or_link or "").strip().lower().replace("@", "").replace("https://t.me/", "")

        # Match activity logs by clean title or username
        log_conditions = []
        if clean_title_key:
            log_conditions.append(UserActivityLog.chat_title.ilike(f"%{clean_title_key}%"))
        if username_key:
            log_conditions.append(UserActivityLog.chat_title.ilike(f"%{username_key}%"))

        if log_conditions:
            from sqlalchemy import or_
            match_clause = or_(*log_conditions)
        else:
            match_clause = (UserActivityLog.chat_id == 0)

        # Count messages in last 7d
        msgs_stmt = select(func.count(UserActivityLog.id)).where(
            UserActivityLog.timestamp >= cutoff_7d,
            match_clause
        )
        msgs_7d = (await db.execute(msgs_stmt)).scalar() or 0

        # Last scan timestamp for this channel
        last_act_stmt = select(func.max(UserActivityLog.timestamp)).where(match_clause)
        last_activity_raw = (await db.execute(last_act_stmt)).scalar()

        # Get all user_ids who posted in this channel
        users_stmt = select(UserActivityLog.user_id).where(match_clause).distinct()
        user_ids = list((await db.execute(users_stmt)).scalars().all())

        leads_7d = 0
        leads_total = 0
        if user_ids:
            leads_7d = (await db.execute(
                select(func.count(Lead.id)).where(
                    Lead.user_id.in_(user_ids),
                    Lead.created_at >= cutoff_7d
                )
            )).scalar() or 0
            leads_total = (await db.execute(
                select(func.count(Lead.id)).where(Lead.user_id.in_(user_ids))
            )).scalar() or 0

        # Days in monitoring calculation
        days_in_monitoring = 0
        if ch.created_at:
            c_date = ch.created_at.replace(tzinfo=timezone.utc) if ch.created_at.tzinfo is None else ch.created_at
            days_in_monitoring = max(0, (now_utc - c_date).days)

        # Days idle calculation
        if last_activity_raw:
            if last_activity_raw.tzinfo is None:
                last_activity_raw = last_activity_raw.replace(tzinfo=timezone.utc)
            days_idle = max(0, (now_utc - last_activity_raw).days)
            last_activity_fmt = (last_activity_raw + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M")
        else:
            days_idle = days_in_monitoring
            last_activity_fmt = "—"

        # A channel can ONLY be marked as dead if it has been monitored for at least 7 full days!
        is_dead = (days_in_monitoring >= 7) and (days_idle >= 7 or leads_7d == 0)

        # Color tier: 0=fresh, 1-6=days idle, 7+=dead
        color_idx = 7 if is_dead else min(days_idle, 6)
        color_info = EFFECTIVENESS_COLORS[color_idx]

        result.append({
            "id": ch.id,
            "title": ch.title or ch.username_or_link,
            "username_or_link": ch.username_or_link,
            "niche_code": ch.niche_code,
            "niche_name": NICHE_NAMES.get(ch.niche_code, ch.niche_code),
            "location_code": ch.location_code or "global",
            "location_name": LOCATION_NAMES.get(ch.location_code or "global", "🌐 Глобал"),
            "status": ch.status,
            "msgs_7d": msgs_7d,
            "leads_7d": leads_7d,
            "leads_total": leads_total,
            "days_idle": days_idle if last_activity_raw else None,
            "days_in_monitoring": days_in_monitoring,
            "last_activity_at": last_activity_fmt,
            "color_class": color_info["class"],
            "color_label": color_info["label"],
            "color_emoji": color_info["emoji"],
            "is_dead": is_dead,
        })

    # Sort: dead channels first, then by days_idle desc
    result.sort(key=lambda x: (1 if x["is_dead"] else 0, x["days_idle"] or 999), reverse=True)
    return result


@router.delete("/channels/{channel_id}/dead")
async def delete_dead_channel(channel_id: str, db: AsyncSession = Depends(get_db)):
    """Deletes a monitored channel (used for dead channel cleanup)."""
    ch = (await db.execute(select(MonitoredChannel).where(MonitoredChannel.id == channel_id))).scalar_one_or_none()
    if not ch:
        return {"status": "error", "message": "Канал не найден"}
    await db.delete(ch)
    await db.commit()

    # Trigger restart of scraper loop to update polling queue
    try:
        from src.api.app import ingestor
        if ingestor:
            import asyncio
            asyncio.create_task(ingestor.restart_scraper_loop())
    except Exception:
        pass

    return {"status": "deleted", "channel_id": channel_id, "title": ch.title or ch.username_or_link}


class BatchImportRequest(BaseModel):
    text: str
    niche_code: Optional[str] = "community"
    location_code: Optional[str] = "nhatrang"
    auto_approve: Optional[bool] = True

@router.post("/channels/batch-import")
async def batch_import_channels(req: BatchImportRequest, db: AsyncSession = Depends(get_db)):
    """
    Parses any pasted text blob, extracts all Telegram @username and t.me/ links,
    verifies public accessibility, and imports valid ones into monitored_channels.
    """
    import re
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Текст для импорта пуст")

    raw_matches = re.findall(r'(?:https?://)?t\.me/([a-zA-Z0-9_]{5,32})|@([a-zA-Z0-9_]{5,32})', req.text)
    extracted_usernames = []
    seen = set()
    for m in raw_matches:
        u = (m[0] or m[1]).strip()
        if u and not u.endswith('_bot') and u.lower() not in ['telegram', 'joinchat', 'share', 'contact']:
            clean_u = f"@{u}"
            if clean_u.lower() not in seen:
                seen.add(clean_u.lower())
                extracted_usernames.append(clean_u)

    if not extracted_usernames:
        return {"status": "ok", "added": 0, "duplicates": 0, "invalid": 0, "message": "В тексте не найдено ссылок Telegram"}

    added_count = 0
    duplicate_count = 0
    invalid_count = 0
    details = []

    from src.ingestion.public_scraper import PublicTelegramScraper
    scraper = PublicTelegramScraper()

    for username in extracted_usernames:
        # Check duplicate in MonitoredChannel
        dup_ch = (await db.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == username))).scalar_one_or_none()
        if dup_ch:
            duplicate_count += 1
            details.append({"username": username, "status": "duplicate", "title": dup_ch.title or username})
            continue

        # Quick verify via public scraper
        posts = await scraper.fetch_latest_messages(username)
        if posts is None:
            invalid_count += 1
            details.append({"username": username, "status": "invalid", "title": "❌ Чат не существует"})
            continue

        title = (posts[0]["chat_title"] if posts else None) or username
        new_ch = MonitoredChannel(
            username_or_link=username,
            title=title,
            niche_code=req.niche_code or "community",
            location_code=req.location_code or "nhatrang",
            status="JOINED"
        )
        db.add(new_ch)
        added_count += 1
        details.append({"username": username, "status": "added", "title": title})

    await db.commit()

    # Trigger restart of scraper loop & userbot sync
    try:
        from src.api.app import ingestor
        if ingestor:
            asyncio.create_task(ingestor.restart_scraper_loop())
    except Exception:
        pass

    return {
        "status": "ok",
        "added": added_count,
        "duplicates": duplicate_count,
        "invalid": invalid_count,
        "details": details
    }


@router.get("/candidates")
async def list_channel_candidates(db: AsyncSession = Depends(get_db)):
    """Returns list of auto-discovered channel candidates awaiting approval."""
    from src.db.models import ChannelCandidate
    res = await db.execute(select(ChannelCandidate).where(ChannelCandidate.status == "DISCOVERED").order_by(ChannelCandidate.discovered_at.desc()))
    candidates = list(res.scalars().all())

    items = []
    for c in candidates:
        ts_utc7 = (c.discovered_at + timedelta(hours=7)) if c.discovered_at else None
        items.append({
            "id": c.id,
            "username_or_link": c.username_or_link,
            "title": c.title or c.username_or_link,
            "source": c.source,
            "niche_code": c.niche_code or "community",
            "location_code": c.location_code or "nhatrang",
            "member_count": c.member_count or 0,
            "discovered_at_fmt": ts_utc7.strftime("%d.%m %H:%M") if ts_utc7 else "—"
        })
    return items


@router.post("/candidates/{candidate_id}/approve")
async def approve_channel_candidate(candidate_id: str, db: AsyncSession = Depends(get_db)):
    """Approves a candidate channel and moves it into monitored channels."""
    from src.db.models import ChannelCandidate
    cand = (await db.execute(select(ChannelCandidate).where(ChannelCandidate.id == candidate_id))).scalar_one_or_none()
    if not cand:
        raise HTTPException(status_code=404, detail="Кандидат не найден")

    cand.status = "APPROVED"

    # Add to MonitoredChannel if not already present
    dup_ch = (await db.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == cand.username_or_link))).scalar_one_or_none()
    if not dup_ch:
        new_ch = MonitoredChannel(
            username_or_link=cand.username_or_link,
            title=cand.title or cand.username_or_link,
            niche_code=cand.niche_code or "community",
            location_code=cand.location_code or "nhatrang",
            status="JOINED"
        )
        db.add(new_ch)

    await db.commit()

    try:
        from src.api.app import ingestor
        if ingestor:
            asyncio.create_task(ingestor.restart_scraper_loop())
    except Exception:
        pass

    return {"status": "ok", "message": f"Канал {cand.username_or_link} успешно подсоединён в прослушку!"}


@router.post("/candidates/{candidate_id}/reject")
async def reject_channel_candidate(candidate_id: str, db: AsyncSession = Depends(get_db)):
    """Rejects a candidate channel."""
    from src.db.models import ChannelCandidate
    cand = (await db.execute(select(ChannelCandidate).where(ChannelCandidate.id == candidate_id))).scalar_one_or_none()
    if not cand:
        raise HTTPException(status_code=404, detail="Кандидат не найден")

    cand.status = "REJECTED"
    await db.commit()
    return {"status": "ok", "message": "Кандидат отклонён"}

@router.get("/leads")
async def list_leads(niche: str = None, location: str = None, limit: int = 50, is_vip: bool = False, db: AsyncSession = Depends(get_db)):
    cutoff_10m = datetime.now(timezone.utc) - timedelta(minutes=10)
    
    stmt = select(Lead).where(Lead.status == "AVAILABLE").order_by(Lead.created_at.desc()).limit(limit)

    if niche and niche != "all":
        stmt = stmt.where(Lead.niche_code == niche)
    if location and location != "all":
        stmt = stmt.where(Lead.location_code == location)
    
    res = await db.execute(stmt)
    raw_leads = list(res.scalars().all())

    # Deduplicate lead cards by intent_summary
    leads = []
    seen_summaries = set()
    for l in raw_leads:
        summary_clean = (l.intent_summary or "").strip().lower()
        if summary_clean and summary_clean not in seen_summaries:
            seen_summaries.add(summary_clean)
            leads.append(l)

    # Calculate user_message_count for each lead from UserActivityLog
    user_ids = [l.user_id for l in leads if l.user_id]
    msg_counts = {}
    if user_ids:
        cnt_stmt = select(UserActivityLog.user_id, func.count(UserActivityLog.id)).where(UserActivityLog.user_id.in_(user_ids)).group_by(UserActivityLog.user_id)
        cnt_res = await db.execute(cnt_stmt)
        msg_counts = {u_id: count for u_id, count in cnt_res.all()}

    return [
        {
            "id": l.id,
            "user_id": l.user_id,
            "niche_code": l.niche_code,
            "rubric_name": NICHE_NAMES.get(l.niche_code, "Прочее"),
            "location_code": getattr(l, "location_code", "global") or "global",
            "location_name": LOCATION_NAMES.get(getattr(l, "location_code", "global") or "global", "🌐 Глобал / РФ"),
            "temperature": l.temperature,
            "confidence_score": l.confidence_score,
            "intent_summary": l.intent_summary,
            "sales_hook": l.sales_hook,
            "user_message_count": max(1, msg_counts.get(l.user_id, 0)),
            "status": l.status,
            "price": float(l.price),
            "created_at": (l.created_at + timedelta(hours=7)).isoformat() if l.created_at else None
        }
        for l in leads
    ]

@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str, db: AsyncSession = Depends(get_db)):
    """Permanently deletes a lead record from the database (Web Admin only)."""
    stmt = select(Lead).where(Lead.id == lead_id)
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Лид не найден в системе"}

    await db.delete(lead)
    await db.commit()
    return {"status": "deleted", "lead_id": lead_id, "message": "Лид успешно удалён из системы"}

@router.post("/leads/{lead_id}/requalify")
async def requalify_lead(lead_id: str, db: AsyncSession = Depends(get_db)):
    """Triggers instant real-time AI re-evaluation of a lead via LLM (Groq/Gemini)."""
    stmt = select(Lead).where(Lead.id == lead_id)
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Лид не найден"}

    msg_stmt = select(UserActivityLog).where(UserActivityLog.user_id == lead.user_id).order_by(UserActivityLog.timestamp.desc()).limit(10)
    messages = list((await db.execute(msg_stmt)).scalars().all())

    if not messages:
        return {"status": "error", "message": "История сообщений пользователя не найдена в базе"}

    from src.ai.scorer import evaluate_user_timeline
    scoring_res = await evaluate_user_timeline(lead.user_id, db, messages)

    if not scoring_res:
        return {"status": "error", "message": "Ошибка обращения к ИИ-модели (Rate Limit / API Timeout). Попробуйте позже."}

    if not scoring_res.is_lead:
        await db.delete(lead)
        await db.commit()
        return {
            "status": "rejected",
            "is_lead": False,
            "message": "ИИ переквалифицировал запрос как НЕ ЛИД (удален из карточек)",
            "reasoning": scoring_res.reasoning or "Сообщение отсеяно ИИ как флуд или предложение риелтора."
        }

    lead.niche_code = scoring_res.niche_code
    lead.temperature = scoring_res.temperature
    lead.confidence_score = scoring_res.confidence_score
    if scoring_res.intent_summary:
        lead.intent_summary = scoring_res.intent_summary
    if scoring_res.sales_hook:
        lead.sales_hook = scoring_res.sales_hook

    await db.commit()
    await db.refresh(lead)

    return {
        "status": "requalified",
        "is_lead": True,
        "lead_id": lead.id,
        "niche_code": lead.niche_code,
        "rubric_name": NICHE_NAMES.get(lead.niche_code, lead.niche_code),
        "temperature": lead.temperature,
        "confidence_score": lead.confidence_score,
        "intent_summary": lead.intent_summary,
        "sales_hook": lead.sales_hook,
        "reasoning": scoring_res.reasoning or "ИИ подтвердил клиентский спрос."
    }

@router.get("/leads/{lead_id}/analysis")
async def get_lead_analysis(lead_id: str, db: AsyncSession = Depends(get_db)):
    """Returns full AI Chain-of-Thought analysis, score, and raw message timeline for a lead."""
    stmt = select(Lead).where(Lead.id == lead_id)
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Лид не найден"}

    eval_stmt = select(AIEvaluationLog).where(AIEvaluationLog.user_id == lead.user_id).order_by(AIEvaluationLog.created_at.desc()).limit(1)
    eval_log = (await db.execute(eval_stmt)).scalar_one_or_none()

    cot_reasoning = eval_log.reasoning if (eval_log and eval_log.reasoning) else "ИИ провел квалификацию контекста диалога и подтвердил прямой клиентский спрос."

    msg_stmt = select(UserActivityLog).where(UserActivityLog.user_id == lead.user_id).order_by(UserActivityLog.timestamp.desc()).limit(20)
    messages = list((await db.execute(msg_stmt)).scalars().all())

    raw_msgs = [
        {
            "id": str(m.id),
            "chat_title": m.chat_title or "Группа",
            "message_text": m.message_text,
            "timestamp": (m.timestamp + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M:%S") if m.timestamp else "—"
        }
        for m in messages
    ]

    return {
        "status": "ok",
        "lead_id": lead.id,
        "user_id": lead.user_id,
        "niche_code": lead.niche_code,
        "rubric_name": NICHE_NAMES.get(lead.niche_code, lead.niche_code),
        "location_code": getattr(lead, "location_code", "global") or "global",
        "location_name": LOCATION_NAMES.get(getattr(lead, "location_code", "global") or "global", "🌐 Глобал / РФ"),
        "temperature": lead.temperature,
        "confidence_score": lead.confidence_score,
        "intent_summary": lead.intent_summary,
        "sales_hook": lead.sales_hook,
        "reasoning": cot_reasoning,
        "lead_status": lead.status,
        "price": float(lead.price),
        "created_at": (lead.created_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M:%S") if lead.created_at else None,
        "raw_messages": raw_msgs
    }

@router.get("/user/{user_id}/messages")
async def get_user_messages(user_id: int, db: AsyncSession = Depends(get_db)):
    """Returns full history of raw messages for a given user_id (for Superadmin Decryption / РАСШИФРОВКА).
    Each record includes author name from UserProfile to distinguish multiple authors in same channel."""
    from sqlalchemy.orm import aliased

    # JOIN UserActivityLog with UserProfile to get the author's name
    log_alias = aliased(UserActivityLog)
    stmt = (
        select(UserActivityLog, UserProfile.first_name, UserProfile.username)
        .outerjoin(UserProfile, UserProfile.user_id == UserActivityLog.user_id)
        .where(UserActivityLog.user_id == user_id)
        .order_by(UserActivityLog.timestamp.desc())
        .limit(100)
    )
    res = await db.execute(stmt)
    rows = res.all()

    if not rows:
        # Check if there is a Lead intent summary as fallback
        lead_stmt = select(Lead).where(Lead.user_id == user_id)
        lead = (await db.execute(lead_stmt)).scalars().first()
        if lead:
            return [{
                "id": "seed",
                "chat_title": "Первичное сообщение (Seed Lead)",
                "author_name": None,
                "message_text": lead.intent_summary,
                "timestamp": (lead.created_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if lead.created_at else "Недавно"
            }]
        return []

    return [
        {
            "id": log.id,
            "chat_title": log.chat_title or "Групповой чат",
            "author_name": first_name or (f"@{username}" if username else None),
            "message_text": log.message_text,
            "timestamp": (log.timestamp + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if log.timestamp else "—"
        }
        for log, first_name, username in rows
    ]

class UpdatePartnerPrioritySchema(BaseModel):
    niche_code: str = Field(..., example="auto_kasko")
    priority: int = Field(..., example=1) # 1=VIP 0s, 2=High 30s, 3=Standard 60s

@router.get("/partners")
async def list_partners(db: AsyncSession = Depends(get_db)):
    mock_ids = [113767, 8866001783, 260669598, 777000111, 999111222, 888777666]
    res = await db.execute(select(Partner).where(Partner.telegram_id.not_in(mock_ids)).order_by(Partner.created_at.desc()))
    partners = list(res.scalars().all())

    # Filter out scraped user profiles that are not real registered B2B partners
    # A real partner must either be SUPERADMIN, or have telegram_id in SUPERADMIN_IDS, or have actually registered
    partner_list = []
    for p in partners:
        # Fetch detailed purchases for this partner with timestamp
        p_stmt = select(LeadPurchase, Lead).join(Lead, LeadPurchase.lead_id == Lead.id).where(LeadPurchase.partner_id == p.id).order_by(LeadPurchase.purchased_at.desc())
        p_res = await db.execute(p_stmt)
        purchases_data = []
        total_spent = 0.0

        for pur, lead_obj in p_res.all():
            price_val = float(pur.price_paid)
            total_spent += price_val
            pur_utc7 = (pur.purchased_at + timedelta(hours=7)) if pur.purchased_at else None
            purchases_data.append({
                "purchase_id": pur.id,
                "lead_id": pur.lead_id,
                "price_paid": price_val,
                "purchased_at": pur_utc7.isoformat() if pur_utc7 else None,
                "purchased_at_fmt": pur_utc7.strftime("%Y-%m-%d %H:%M:%S") if pur_utc7 else "",
                "niche_code": lead_obj.niche_code,
                "rubric_name": NICHE_NAMES.get(lead_obj.niche_code, "Прочее"),
                "intent_summary": lead_obj.intent_summary
            })

        p_created_utc7 = (p.created_at + timedelta(hours=7)) if p.created_at else None
        partner_list.append({
            "id": p.id,
            "telegram_id": p.telegram_id,
            "company_name": p.company_name,
            "role": p.role,
            "moderation_status": p.moderation_status,
            "balance": float(p.balance),
            "subscribed_niches": p.subscribed_niches,
            "niche_priorities": p.niche_priorities or {},
            "total_purchases_count": len(purchases_data),
            "total_spent": total_spent,
            "purchases": purchases_data,
            "created_at": p_created_utc7.isoformat() if p_created_utc7 else None,
            "created_at_fmt": p_created_utc7.strftime("%Y-%m-%d %H:%M") if p_created_utc7 else ""
        })

    return partner_list

@router.put("/partners/{partner_id}/priority")
async def update_partner_priority(partner_id: str, data: UpdatePartnerPrioritySchema, db: AsyncSession = Depends(get_db)):
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Partner not found"}

    priorities = dict(partner.niche_priorities or {})
    priorities[data.niche_code] = data.priority
    partner.niche_priorities = priorities

    await db.commit()
    await db.refresh(partner)

    return {
        "status": "updated",
        "partner_id": partner.id,
        "niche_priorities": partner.niche_priorities
    }

class UpdatePartnerRoleSchema(BaseModel):
    role: Optional[str] = Field(None, example="VIP")
    moderation_status: Optional[str] = Field(None, example="APPROVED")
    balance: Optional[float] = Field(None, example=100.0)

@router.put("/partners/{partner_id}/role")
@router.patch("/partners/{partner_id}/role")
async def update_partner_role(partner_id: str, data: UpdatePartnerRoleSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(Partner).where(
        (Partner.id == partner_id) | 
        (Partner.telegram_id == int(partner_id) if partner_id.isdigit() else False)
    )
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Partner not found"}

    if data.role is not None:
        partner.role = data.role
    if data.moderation_status is not None:
        partner.moderation_status = data.moderation_status
    if data.balance is not None:
        partner.balance = data.balance

    await db.commit()
    await db.refresh(partner)

    # Notify partner via Telegram bot asynchronously
    try:
        from src.bot.alert_bot import bot
        from src.bot.keyboards import get_main_reply_keyboard
        ROLE_LABELS = {
            "DEMO": "🆕 DEMO (Демо)",
            "REGULAR": "🔵 REGULAR (Регулярный)",
            "VIP": "⭐ VIP (ВИП)",
            "ADMIN": "🔑 ADMIN (Администратор)",
            "SUPERADMIN": "👑 SUPERADMIN (Суперадминистратор)"
        }
        if bot and partner.telegram_id:
            msg_role = ROLE_LABELS.get(partner.role, partner.role)
            await bot.send_message(
                chat_id=partner.telegram_id,
                text=f"👑 <b>ОБНОВЛЕНИЕ СТАТУСА В СИСТЕМЕ!</b>\n\n<b>Ваша новая роль:</b> {msg_role}\n<b>Текущий баланс:</b> ${partner.balance:.2f} USD",
                reply_markup=get_main_reply_keyboard(partner.is_monitoring_active, partner.role),
                parse_mode="HTML"
            )
    except Exception as e:
        logger.warning(f"Notice: Could not send Telegram notification to user: {e}")

    return {
        "status": "updated",
        "partner_id": partner.id,
        "role": partner.role,
        "moderation_status": partner.moderation_status,
        "balance": float(partner.balance)
    }

class BuyLeadSchema(BaseModel):
    telegram_id: int = Field(..., example=8866001783)
    is_exclusive: bool = Field(False, example=False)

@router.post("/leads/{lead_id}/buy")
async def buy_lead_api(lead_id: str, data: BuyLeadSchema, db: AsyncSession = Depends(get_db)):
    partner_stmt = select(Partner).where(Partner.telegram_id == data.telegram_id)
    partner = (await db.execute(partner_stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Партнер не найден"}

    from src.services.purchase_engine import process_lead_purchase
    res = await process_lead_purchase(db, partner.id, lead_id, is_exclusive=data.is_exclusive)
    return res

class ReferralWithdrawRequestSchema(BaseModel):
    telegram_id: int = Field(..., example=8866001783)
    payment_details: str = Field(..., example="USDT TRC20 TKhg9...")

@router.get("/referrals/stats")
async def get_referral_stats(telegram_id: int = 8866001783, db: AsyncSession = Depends(get_db)):
    stmt = select(Partner).where(Partner.telegram_id == telegram_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        stmt = select(Partner).where(Partner.role == "SUPERADMIN")
        partner = (await db.execute(stmt)).scalars().first()

    if not partner:
        return {"status": "error", "message": "Partner not found"}

    ref_count_stmt = select(func.count(Partner.id)).where(Partner.referred_by_id == partner.id)
    invited_count = (await db.execute(ref_count_stmt)).scalar() or 0

    ref_link = f"https://t.me/intenthunter_bot?start=ref_{partner.telegram_id}"
    from src.services.referral_engine import generate_referral_qr_base64
    qr_b64 = generate_referral_qr_base64(ref_link)

    from src.db.models import ReferralAccrual
    acc_stmt = select(ReferralAccrual).where(ReferralAccrual.referrer_id == partner.id).order_by(ReferralAccrual.created_at.desc())
    acc_res = await db.execute(acc_stmt)
    accruals = list(acc_res.scalars().all())

    accruals_data = [
        {
            "id": a.id,
            "payment_amount": float(a.payment_amount),
            "accrual_amount": float(a.accrual_amount),
            "created_at_fmt": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else ""
        }
        for a in accruals
    ]

    return {
        "partner_id": partner.id,
        "telegram_id": partner.telegram_id,
        "referral_link": ref_link,
        "qr_code_base64": qr_b64,
        "invited_count": invited_count,
        "referral_balance": float(partner.referral_balance or 0.0),
        "total_referral_earned": float(partner.total_referral_earned or 0.0),
        "can_withdraw": float(partner.referral_balance or 0.0) >= 50.0,
        "accruals": accruals_data
    }

@router.post("/referrals/withdraw")
async def create_referral_withdrawal(data: ReferralWithdrawRequestSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(Partner).where(Partner.telegram_id == data.telegram_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        return {"status": "error", "message": "Пользователь не найден"}

    bal = float(partner.referral_balance or 0.0)
    if bal < 50.0:
        return {"status": "error", "message": f"Минимальная сумма вывода составляет $50.00 USD. Ваш текущий реферальный баланс: ${bal:.2f} USD"}

    details = data.payment_details.strip()
    if not details:
        return {"status": "error", "message": "Укажите реквизиты для получения вывода (USDT / TON / Карта)"}

    from src.db.models import WithdrawalRequest
    req = WithdrawalRequest(
        partner_id=partner.id,
        amount=bal,
        payment_details=details,
        status="PENDING"
    )
    db.add(req)
    partner.referral_balance = 0.00
    await db.commit()

    # Notify Superadmins
    sa_stmt = select(Partner).where(Partner.role == "SUPERADMIN")
    superadmins = list((await db.execute(sa_stmt)).scalars().all())
    from src.bot.alert_bot import bot
    if bot:
        admin_msg = (
            f"🚨 <b>НОВАЯ ЗАЯВКА НА ВЫВОД РЕФЕРАЛЬНЫХ НАЧИСЛЕНИЙ (WEB)!</b>\n\n"
            f"<b>Партнер:</b> {partner.company_name}\n"
            f"<b>Telegram ID:</b> <code>{partner.telegram_id}</code>\n"
            f"<b>Сумма к выплате:</b> <b>${bal:.2f} USD</b>\n"
            f"<b>Реквизиты:</b> <code>{details}</code>"
        )
        for sa in superadmins:
            try:
                await bot.send_message(sa.telegram_id, admin_msg, parse_mode="HTML")
            except Exception as e:
                pass

    return {
        "status": "ok",
        "message": f"Заявка на вывод ${bal:.2f} USD успешно создана!",
        "amount": bal
    }

class QualifyManualSchema(BaseModel):
    message_text: str = Field(..., example="Нужен байк на месяц в Нячанге")
    chat_title: str = Field(default="Пользовательский чат")
    user_id: Optional[int] = None
    username: Optional[str] = None
    niche_code: Optional[str] = None

@router.post("/leads/qualify-manual")
async def qualify_lead_manually(data: QualifyManualSchema, db: AsyncSession = Depends(get_db)):
    import zlib
    user_id = data.user_id or (700000 + (zlib.crc32(data.message_text.encode("utf-8")) % 200000))

    u_stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    up = (await db.execute(u_stmt)).scalar_one_or_none()
    if not up:
        up = UserProfile(
            user_id=user_id,
            username=data.username or "telegram_user",
            first_name=data.username or "Пользователь Telegram",
            behavior_summary="Клиент с ручной квалификацией лида"
        )
        db.add(up)
        await db.flush()

    activity = UserActivityLog(
        user_id=user_id,
        chat_id=-1001990001,
        chat_title=data.chat_title or "Общий Чат",
        message_id=abs(hash(data.message_text)) % 100000,
        message_text=data.message_text
    )
    db.add(activity)
    await db.commit()

    from src.ai.scorer import evaluate_user_timeline, infer_location_code
    res = await evaluate_user_timeline(user_id, db, [activity])
    loc_code = infer_location_code(data.message_text + " " + (data.chat_title or ""))

    final_niche = data.niche_code or (res.niche_code if res and res.is_lead else "community")
    final_summary = data.message_text.strip()[:350]
    final_hook = res.sales_hook if res and res.is_lead else "Горячий покупательский запрос из чата"

    existing_stmt = select(Lead).where(Lead.user_id == user_id).order_by(Lead.created_at.desc())
    existing_lead = (await db.execute(existing_stmt)).scalar_one_or_none()

    if not existing_lead:
        lead = Lead(
            user_id=user_id,
            niche_code=final_niche,
            location_code=loc_code,
            temperature="HOT",
            confidence_score=0.98,
            intent_summary=final_summary,
            sales_hook=final_hook,
            status="AVAILABLE",
            price=1.00
        )
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
    else:
        lead = existing_lead
        lead.location_code = loc_code
        await db.commit()

    return {
        "status": "ok",
        "message": "Лид успешно квалифицирован и помещен в Маркетплейс!",
        "lead_id": lead.id,
        "niche_code": lead.niche_code,
        "location_code": lead.location_code,
        "intent_summary": lead.intent_summary
    }


@router.post("/scan-now")
async def trigger_manual_scan_now(db: AsyncSession = Depends(get_db)):
    """Triggers immediate pass of PublicTelegramScraper over all monitored channels."""
    from src.ingestion.public_scraper import PublicTelegramScraper
    from src.db.models import MonitoredChannel, UserActivityLog, UserProfile
    from src.ai.scorer import evaluate_user_timeline

    scraper = PublicTelegramScraper()
    stmt = select(MonitoredChannel).where(MonitoredChannel.status == "JOINED")
    channels = list((await db.execute(stmt)).scalars().all())

    total_scraped = 0
    new_leads = 0

    for ch in channels[:15]:
        try:
            posts = await scraper.fetch_latest_messages(ch.username_or_link)
            for p in posts:
                total_scraped += 1
                u_id = p["user_id"]

                up = (await db.execute(select(UserProfile).where(UserProfile.user_id == u_id))).scalar_one_or_none()
                if not up:
                    up = UserProfile(user_id=u_id, first_name=p["first_name"], username=p["username"])
                    db.add(up)
                    await db.flush()

                act = UserActivityLog(
                    user_id=u_id,
                    chat_id=abs(hash(ch.username_or_link)) % (10**9),
                    chat_title=ch.title or ch.username_or_link,
                    message_id=p["message_id"],
                    message_text=p["text"]
                )
                db.add(act)
                await db.commit()

                res = await evaluate_user_timeline(u_id, db, [act])
                if res and res.is_lead:
                    new_leads += 1
        except Exception as e:
            logger.warning(f"Error scraping {ch.username_or_link}: {e}")

    return {
        "status": "completed",
        "scraped_count": total_scraped,
        "new_leads_found": new_leads
    }


# ────────────────────────────────────────────────────────────────────────────
# B2B SELLER OUTREACH AUDIENCE ENDPOINTS (SUPERADMIN DASHBOARD)
# ────────────────────────────────────────────────────────────────────────────
class UpdateOutreachStatusSchema(BaseModel):
    status: str = Field(..., example="READY_FOR_OUTREACH")

@router.get("/outreach/leads")
async def get_outreach_leads(
    niche: Optional[str] = None,
    location: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import OutreachLead
    query = select(OutreachLead).order_by(OutreachLead.created_at.desc())
    
    if niche and niche != "all":
        query = query.where(OutreachLead.niche_code == niche)
    if location and location != "all":
        query = query.where(OutreachLead.location_code == location)
    if status and status != "all":
        query = query.where(OutreachLead.status == status)
        
    query = query.limit(limit).offset(offset)
    results = list((await db.execute(query)).scalars().all())
    
    out = []
    for lead in results:
        out.append({
            "id": lead.id,
            "author_username": lead.author_username,
            "author_first_name": lead.author_first_name,
            "telegram_id": lead.telegram_id,
            "niche_code": lead.niche_code,
            "location_code": lead.location_code,
            "confidence_score": lead.confidence_score,
            "status": lead.status,
            "raw_ad_text": lead.raw_ad_text,
            "sales_hook": lead.sales_hook,
            "chat_title": lead.chat_title,
            "messages_history": lead.messages_history or [],
            "created_at": lead.created_at.isoformat() if lead.created_at else None
        })
    return {"status": "ok", "count": len(out), "leads": out}


@router.post("/outreach/leads/{lead_id}/status")
async def update_outreach_lead_status(
    lead_id: str,
    payload: UpdateOutreachStatusSchema,
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import OutreachLead
    lead = (await db.execute(select(OutreachLead).where(OutreachLead.id == lead_id))).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Lead not found"}
    
    lead.status = payload.status
    await db.commit()
    return {"status": "ok", "lead_id": lead_id, "new_status": lead.status}


class ImportAccountSchema(BaseModel):
    session_string: str = Field(..., example="1BJW...")
    phone_number: Optional[str] = Field(default=None, example="+971501234567")
    proxy_url: Optional[str] = Field(default=None, example="http://user:pass@host:port")
    manager_name: Optional[str] = Field(default="Екатерина", example="Екатерина")
    manager_role: Optional[str] = Field(default="Руководитель B2B развития LeadRadar", example="Старший менеджер по развитию")
    max_daily_limit: int = Field(default=15, example=15)

@router.get("/outreach/accounts")
async def get_outreach_accounts(db: AsyncSession = Depends(get_db)):
    from src.db.models import OutreachAccount
    accounts = list((await db.execute(select(OutreachAccount).order_by(OutreachAccount.id.asc()))).scalars().all())
    out = []
    for a in accounts:
        out.append({
            "id": a.id,
            "phone_number": a.phone_number,
            "proxy_url": a.proxy_url,
            "manager_name": a.manager_name or "Екатерина",
            "manager_role": a.manager_role or "Руководитель B2B развития LeadRadar",
            "daily_sent_count": a.daily_sent_count,
            "max_daily_limit": a.max_daily_limit,
            "status": a.status,
            "has_premium": a.has_premium,
            "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
            "error_log": a.error_log
        })
    return {"status": "ok", "count": len(out), "accounts": out}

@router.post("/outreach/accounts/import")
async def import_outreach_account(payload: ImportAccountSchema, db: AsyncSession = Depends(get_db)):
    from src.db.models import OutreachAccount
    acc = OutreachAccount(
        session_string=payload.session_string.strip(),
        phone_number=payload.phone_number.strip() if payload.phone_number else None,
        proxy_url=payload.proxy_url.strip() if payload.proxy_url else None,
        manager_name=payload.manager_name or "Екатерина",
        manager_role=payload.manager_role or "Руководитель B2B развития LeadRadar",
        max_daily_limit=payload.max_daily_limit,
        status="ACTIVE"
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return {"status": "ok", "account_id": acc.id, "phone_number": acc.phone_number, "manager_name": acc.manager_name}

@router.get("/outreach/stats")
async def get_outreach_telemetry_stats(db: AsyncSession = Depends(get_db)):
    from src.db.models import OutreachAccount, B2BProspect
    total_accs = (await db.execute(select(func.count(OutreachAccount.id)))).scalar() or 0
    active_accs = (await db.execute(select(func.count(OutreachAccount.id)).where(OutreachAccount.status == "ACTIVE"))).scalar() or 0
    cooldown_accs = (await db.execute(select(func.count(OutreachAccount.id)).where(OutreachAccount.status == "COOL_DOWN"))).scalar() or 0
    banned_accs = (await db.execute(select(func.count(OutreachAccount.id)).where(OutreachAccount.status == "BANNED"))).scalar() or 0
    total_sent_today = (await db.execute(select(func.sum(OutreachAccount.daily_sent_count)))).scalar() or 0

    total_prospects = (await db.execute(select(func.count(B2BProspect.id)))).scalar() or 0
    ready_prospects = (await db.execute(select(func.count(B2BProspect.id)).where(B2BProspect.status == "READY_FOR_OUTREACH"))).scalar() or 0
    sent_prospects = (await db.execute(select(func.count(B2BProspect.id)).where(B2BProspect.status == "SENT"))).scalar() or 0
    failed_prospects = (await db.execute(select(func.count(B2BProspect.id)).where(B2BProspect.status == "FAILED"))).scalar() or 0

    return {
        "status": "ok",
        "accounts": {
            "total": total_accs,
            "active": active_accs,
            "cooldown": cooldown_accs,
            "banned": banned_accs,
            "sent_today": total_sent_today
        },
        "prospects": {
            "total": total_prospects,
            "ready": ready_prospects,
            "sent": sent_prospects,
            "failed": failed_prospects
        }
    }


# ────────────────────────────────────────────────────────────────────────────
# EMPLOYEE PERSONA & DIALOGUE HUMAN TAKEOVER ENDPOINTS
# ────────────────────────────────────────────────────────────────────────────
DEFAULT_EMPLOYEE_NAMES = ["Ульяна", "Петр", "Максим", "Влад"]

class UpdateEmployeeSchema(BaseModel):
    manager_name: Optional[str] = None
    manager_role: Optional[str] = None
    proxy_url: Optional[str] = None
    max_daily_limit: Optional[int] = None
    status: Optional[str] = None

class SendManualMessageSchema(BaseModel):
    text: str = Field(..., example="Здравствуйте! Отвечаю по поводу условий сотрудничества...")

class ToggleAISchema(BaseModel):
    ai_enabled: bool = Field(..., example=False)

@router.post("/outreach/employees/{account_id}/update")
async def update_employee_account(
    account_id: int,
    payload: UpdateEmployeeSchema,
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import OutreachAccount
    acc = (await db.execute(select(OutreachAccount).where(OutreachAccount.id == account_id))).scalar_one_or_none()
    if not acc:
        return {"status": "error", "message": "Account not found"}
    
    if payload.manager_name is not None:
        acc.manager_name = payload.manager_name
    if payload.manager_role is not None:
        acc.manager_role = payload.manager_role
    if payload.proxy_url is not None:
        acc.proxy_url = payload.proxy_url
    if payload.max_daily_limit is not None:
        acc.max_daily_limit = payload.max_daily_limit
    if payload.status is not None:
        acc.status = payload.status
        
    await db.commit()
    return {"status": "ok", "account_id": acc.id, "manager_name": acc.manager_name, "status": acc.status}

@router.get("/outreach/dialogues")
async def get_outreach_dialogues(
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import B2BProspect, OutreachAccount
    prospects = list((await db.execute(
        select(B2BProspect)
        .where(B2BProspect.dialogue_history != [])
        .order_by(B2BProspect.created_at.desc())
    )).scalars().all())

    out = []
    for p in prospects:
        acc = (await db.execute(select(OutreachAccount).where(OutreachAccount.id == p.assigned_account_id))).scalar_one_or_none() if p.assigned_account_id else None
        m_name = acc.manager_name if acc else "Ульяна"
        m_role = acc.manager_role if acc else "Менеджер развития"

        out.append({
            "id": p.id,
            "username": p.username,
            "telegram_id": p.telegram_id,
            "niche": p.niche,
            "sales_hook": p.sales_hook,
            "status": p.status,
            "ai_enabled": p.ai_enabled,
            "manager_name": m_name,
            "manager_role": m_role,
            "assigned_account_id": p.assigned_account_id,
            "dialogue_history": p.dialogue_history or [],
            "created_at": p.created_at.isoformat() if p.created_at else None
        })
    return {"status": "ok", "count": len(out), "dialogues": out}

@router.post("/outreach/dialogues/{prospect_id}/toggle-ai")
async def toggle_prospect_ai(
    prospect_id: int,
    payload: ToggleAISchema,
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import B2BProspect
    p = (await db.execute(select(B2BProspect).where(B2BProspect.id == prospect_id))).scalar_one_or_none()
    if not p:
        return {"status": "error", "message": "Prospect not found"}
    
    p.ai_enabled = payload.ai_enabled
    await db.commit()
    return {"status": "ok", "prospect_id": p.id, "ai_enabled": p.ai_enabled}

@router.post("/outreach/dialogues/{prospect_id}/send-manual")
async def send_manual_dialogue_message(
    prospect_id: int,
    payload: SendManualMessageSchema,
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import B2BProspect, OutreachAccount
    from src.outreach.account_manager import AccountManager

    p = (await db.execute(select(B2BProspect).where(B2BProspect.id == prospect_id))).scalar_one_or_none()
    if not p:
        return {"status": "error", "message": "Prospect not found"}

    acc = (await db.execute(select(OutreachAccount).where(OutreachAccount.id == p.assigned_account_id))).scalar_one_or_none() if p.assigned_account_id else await AccountManager.get_available_account(db)
    if not acc:
        return {"status": "error", "message": "No available manager account to send message"}

    # Update dialogue history with manual manager message
    history = list(p.dialogue_history or [])
    history.append({
        "role": "manager",
        "text": payload.text.strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_manual": True
    })
    p.dialogue_history = history
    await db.commit()

    # Send message via Pyrogram Client
    app = AccountManager.create_pyrogram_client(acc)
    try:
        await app.start()
        target_dest = f"@{p.username.replace('@','')}" if p.username else p.telegram_id
        await app.send_message(chat_id=target_dest, text=payload.text.strip())
        await app.stop()
        return {"status": "ok", "prospect_id": p.id, "sent_message": payload.text.strip()}
    except Exception as e:
        logger.error(f"Error sending manual Pyrogram message to prospect #{p.id}: {e}")
        return {"status": "error", "message": str(e)}
