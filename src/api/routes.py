import logging
from datetime import timedelta, datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, Query, HTTPException, Response, Header
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

    # Pre-fetch latest scan timestamp per channel from CollectorLog as fallback
    from src.db.models import CollectorLog
    cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
    
    c_stmt = (
        select(CollectorLog.username_or_link, func.max(CollectorLog.created_at))
        .where(CollectorLog.created_at >= cutoff_1h)
        .group_by(CollectorLog.username_or_link)
    )
    c_res = await db.execute(c_stmt)
    last_scraped_map = {row[0].strip().lower(): row[1] for row in c_res.all() if row[0]}

    u_stmt = (
        select(UserActivityLog.chat_title, func.max(UserActivityLog.timestamp))
        .group_by(UserActivityLog.chat_title)
    )
    u_res = await db.execute(u_stmt)
    last_act_map = {row[0].strip().lower(): row[1] for row in u_res.all() if row[0]}

    now_utc = datetime.now(timezone.utc)
    cutoff_7d = now_utc - timedelta(days=7)
    out = []
    for c in channels:
        clean_u = (c.username_or_link or "").strip().lower()
        clean_t = (c.title or "").strip().lower()
        
        last_dt = getattr(c, "last_scraped_at", None) or last_scraped_map.get(clean_u) or last_act_map.get(clean_t)

        raw_title = (c.title or "").strip()
        clean_title_key = raw_title.replace("Обнаружен в ", "").strip()
        username_key = (c.username_or_link or "").strip().lower().replace("@", "").replace("https://t.me/", "")

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

        msgs_7d = (await db.execute(select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_7d, match_clause))).scalar() or 0
        last_act_dt = (await db.execute(select(func.max(UserActivityLog.timestamp)).where(match_clause))).scalar() or last_dt

        users_stmt = select(UserActivityLog.user_id).where(match_clause).distinct()
        user_ids = list((await db.execute(users_stmt)).scalars().all())

        leads_7d = 0
        leads_total = 0
        if user_ids:
            leads_7d = (await db.execute(select(func.count(Lead.id)).where(Lead.user_id.in_(user_ids), Lead.created_at >= cutoff_7d))).scalar() or 0
            leads_total = (await db.execute(select(func.count(Lead.id)).where(Lead.user_id.in_(user_ids)))).scalar() or 0

        effective_last_dt = last_act_dt or last_dt
        if effective_last_dt:
            if effective_last_dt.tzinfo is None:
                effective_last_dt = effective_last_dt.replace(tzinfo=timezone.utc)
            days_idle = (now_utc - effective_last_dt).days
            ts_utc7 = effective_last_dt + timedelta(hours=7)
            diff_s = int((now_utc - effective_last_dt).total_seconds())
            if diff_s < 60:
                fmt_time = f"{ts_utc7.strftime('%H:%M:%S')} (только что)"
            elif diff_s < 3600:
                fmt_time = f"{ts_utc7.strftime('%H:%M:%S')} ({diff_s // 60}м назад)"
            else:
                fmt_time = ts_utc7.strftime("%d.%m %H:%M")
        else:
            days_idle = 999
            fmt_time = "Только что (В очереди)"

        color_tier = min(days_idle, 7) if days_idle != 999 else 7
        color_info = EFFECTIVENESS_COLORS[color_tier]

        out.append({
            "id": c.id,
            "title": c.title,
            "username_or_link": c.username_or_link,
            "niche_code": c.niche_code,
            "location_code": getattr(c, "location_code", "nhatrang") or "nhatrang",
            "chat_type": getattr(c, "chat_type", "channel") or "channel",
            "status": c.status,
            "error_message": c.error_message,
            "last_scraped_at": effective_last_dt.isoformat() if effective_last_dt else None,
            "last_scraped_fmt": fmt_time,
            "msgs_7d": msgs_7d,
            "leads_7d": leads_7d,
            "leads_total": leads_total,
            "days_idle": days_idle,
            "is_dead": days_idle >= 7,
            "color_class": color_info["class"],
            "color_label": color_info["label"],
            "color_emoji": color_info["emoji"],
            "created_at": (c.created_at + timedelta(hours=7)).isoformat() if c.created_at else None
        })
    return out

@router.post("/channels")
async def add_monitored_channel(data: AddChannelSchema, db: AsyncSession = Depends(get_db)):
    raw_target = data.username_or_link.strip()

    from src.ingestion.platform_detector import detect_platform_and_clean_target
    platform, canonical_target = detect_platform_and_clean_target(raw_target)

    # Check if exists by username or raw link
    stmt = select(MonitoredChannel).where(
        (MonitoredChannel.username_or_link.ilike(canonical_target))
    )
    existing = (await db.execute(stmt)).scalars().first()
    
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
            from src.ingestion.public_scraper import PublicTelegramScraper
            scraper = PublicTelegramScraper()
            posts = await scraper.fetch_latest_messages(canonical_target)
            if posts:
                asyncio.create_task(ingestor.process_and_score_posts_now(channel, posts))
            else:
                asyncio.create_task(ingestor.scrape_channel_now(channel.id))
        except Exception as err:
            logger.warning(f"Instant AI scoring notice for single channel {canonical_target}: {err}")

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
    return {"status": "updated", "channel": {"id": channel.id, "location_code": channel.location_code, "niche_code": channel.niche_code}}


@router.get("/channels/{channel_id}/messages")
async def get_channel_messages(channel_id: str, limit: int = 30, db: AsyncSession = Depends(get_db)):
    """
    Returns latest messages for a specific channel sorted in descending order (newest first).
    Includes AI qualification status (ЛИД / B2B SELLER / НЕ ЛИД) and CoT reasoning.
    If no DB records exist yet, fetches web preview on-the-fly without permanently bloating the database.
    """
    import zlib
    stmt = select(MonitoredChannel).where((MonitoredChannel.id == channel_id) | (MonitoredChannel.username_or_link == channel_id))
    ch = (await db.execute(stmt)).scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")

    target = ch.username_or_link
    title = ch.title or target
    platform = ch.platform or "telegram"

    items = []
    seen_texts = set()

    # 1. Query AIEvaluationLog by chat_title or username
    try:
        eval_stmt = (
            select(AIEvaluationLog)
            .where(
                (AIEvaluationLog.chat_title.ilike(f"%{title}%")) |
                (AIEvaluationLog.username.ilike(f"%{target.replace('@', '')}%"))
            )
            .order_by(AIEvaluationLog.created_at.desc())
            .limit(limit)
        )
        eval_logs = list((await db.execute(eval_stmt)).scalars().all())
        for el in eval_logs:
            txt_clean = (el.message_text or "").strip()
            if not txt_clean or txt_clean in seen_texts:
                continue
            seen_texts.add(txt_clean)

            ts_utc7 = (el.created_at + timedelta(hours=7)) if el.created_at else None
            ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"

            status_badge = "LEAD" if el.is_lead else ("SELLER" if el.category == "SELLER" else "REJECTED")

            items.append({
                "id": str(el.id),
                "message_id": getattr(el, "message_id", 0) or 1,
                "user_id": el.user_id,
                "username": el.username or f"user_{el.user_id}",
                "first_name": el.first_name or "Пользователь",
                "chat_title": el.chat_title or title,
                "message_text": el.message_text,
                "is_lead": el.is_lead,
                "status_badge": status_badge,
                "reasoning": el.reasoning or "Нейросетевая квалификация завершена.",
                "niche_code": el.niche_code,
                "temperature": el.temperature,
                "confidence_score": el.confidence_score or 0.0,
                "created_at": ts_str,
                "source": "DB_AI_LOG"
            })
    except Exception as e:
        logger.warning(f"AIEvaluationLog lookup notice: {e}")

    # 2. Query UserActivityLog if items count is low
    if len(items) < 10:
        try:
            det_chat_id = (zlib.crc32(f"{platform}:{target}".encode("utf-8")) & 0x7FFFFFFF)
            act_stmt = (
                select(UserActivityLog)
                .where(
                    (UserActivityLog.chat_title.ilike(f"%{title}%")) |
                    (UserActivityLog.chat_id == det_chat_id)
                )
                .order_by(UserActivityLog.timestamp.desc())
                .limit(limit)
            )
            act_logs = list((await db.execute(act_stmt)).scalars().all())

            for al in act_logs:
                txt_clean = (al.message_text or "").strip()
                if not txt_clean or txt_clean in seen_texts:
                    continue
                seen_texts.add(txt_clean)

                ts_utc7 = (al.timestamp + timedelta(hours=7)) if al.timestamp else None
                ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"

                lead_check = (await db.execute(select(Lead).where(Lead.user_id == al.user_id))).scalar_one_or_none()
                seller_check = (await db.execute(select(OutreachLead).where(OutreachLead.telegram_id == al.user_id))).scalar_one_or_none()

                status_badge = "LEAD" if lead_check else ("SELLER" if seller_check else "REJECTED")

                items.append({
                    "id": str(al.id),
                    "message_id": al.message_id,
                    "user_id": al.user_id,
                    "username": f"user_{al.user_id}",
                    "first_name": "Участник чата",
                    "chat_title": al.chat_title or title,
                    "message_text": al.message_text,
                    "is_lead": lead_check is not None,
                    "status_badge": status_badge,
                    "reasoning": f"Сообщение получено из активного потока прослушки '{title}'.",
                    "niche_code": lead_check.niche_code if lead_check else (seller_check.niche_code if seller_check else None),
                    "temperature": lead_check.temperature if lead_check else None,
                    "confidence_score": lead_check.confidence_score if lead_check else 0.0,
                    "created_at": ts_str,
                    "source": "DB_ACTIVITY"
                })
        except Exception as e:
            logger.warning(f"UserActivityLog lookup notice: {e}")

    # 3. If DB has 0 items (e.g. newly added channel), fetch web preview on-the-fly (Zero DB Bloat!)
    if not items:
        try:
            from src.ingestion.vk_ok_scrapers import VKPublicScraper, OKPublicScraper, MAXPublicScraper
            from src.ingestion.public_scraper import PublicTelegramScraper

            posts = []
            if platform == "vk":
                posts = await VKPublicScraper.fetch_latest_messages(target)
            elif platform == "ok":
                posts = await OKPublicScraper.fetch_latest_messages(target)
            elif platform == "max":
                posts = await MAXPublicScraper.fetch_latest_messages(target)
            else:
                scraper = PublicTelegramScraper()
                posts = await scraper.fetch_latest_messages(target)

            posts_list = posts or []
            for idx, p in enumerate(posts_list):
                txt_clean = (p.get("text") or p.get("message_text") or "").strip()
                if not txt_clean or txt_clean in seen_texts:
                    continue
                seen_texts.add(txt_clean)

                txt_low = txt_clean.lower()
                is_seller = any(k in txt_low for k in ["сдам", "сдается", "сдаётся", "предлагаю", "депозит", "usdt", "курс", "визаран", "аренда байка", "прайс", "услуги"])
                is_buyer = any(k in txt_low for k in ["сниму", "ищу", "нужен", "нужна", "посоветуйте", "кто сдает"])

                status_badge = "LEAD" if is_buyer else ("SELLER" if is_seller else "REJECTED")

                items.append({
                    "id": f"preview_{idx}",
                    "message_id": p.get("message_id", idx),
                    "user_id": p.get("user_id") or 0,
                    "username": p.get("username") or "web_preview",
                    "first_name": p.get("first_name") or "Участник чата",
                    "chat_title": title,
                    "message_text": txt_clean,
                    "is_lead": is_buyer,
                    "status_badge": status_badge,
                    "reasoning": f"⚡ Онлайн-превью свежего посты из каналов без сохранения в БД. Статус: {status_badge}",
                    "niche_code": "preview",
                    "temperature": "WARM" if is_buyer else None,
                    "confidence_score": 0.85 if (is_buyer or is_seller) else 0.0,
                    "created_at": p.get("timestamp") or "Только что (Веб-превью)",
                    "source": "LIVE_WEB_PREVIEW"
                })
        except Exception as p_err:
            logger.warning(f"On-the-fly web preview notice: {p_err}")

    # Ensure items are sorted descending by date/ID
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return {
        "status": "ok",
        "channel_id": channel_id,
        "title": title,
        "username_or_link": target,
        "platform": platform,
        "total_messages": len(items),
        "messages": items
    }

@router.get("/ai-evaluation-logs")
async def get_ai_evaluation_logs(limit: int = 50, filter_type: str = "all", db: AsyncSession = Depends(get_db)):
    """Returns AI analyzer evaluation logs with CoT reasoning comments for each scanned message."""
    items = []
    try:
        ch_res = await db.execute(select(MonitoredChannel))
        monitored_channels = list(ch_res.scalars().all())
        ch_id_by_title = {
            c.title.strip().lower(): c.id 
            for c in monitored_channels 
            if c.title and isinstance(c.title, str)
        }
        ch_id_by_user = {
            c.username_or_link.replace("@", "").lower(): c.id 
            for c in monitored_channels 
            if c.username_or_link and isinstance(c.username_or_link, str)
        }

        stmt = select(AIEvaluationLog)
        if filter_type == "leads":
            stmt = stmt.where(AIEvaluationLog.is_lead == True)
        elif filter_type == "rejected":
            stmt = stmt.where(AIEvaluationLog.is_lead == False)

        stmt = stmt.order_by(AIEvaluationLog.created_at.desc()).limit(limit)
        res = await db.execute(stmt)
        logs = list(res.scalars().all())

        for log in logs:
            ts_utc7 = (log.created_at + timedelta(hours=7)) if log.created_at else None
            ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
            c_title = (log.chat_title or "Группа/Чат").strip()
            matched_id = ch_id_by_title.get(c_title.lower()) or (
                ch_id_by_user.get(c_title.replace("@", "").lower()) if isinstance(c_title, str) else None
            )

            items.append({
                "id": log.id,
                "user_id": log.user_id,
                "username": log.username or f"ID {log.user_id}",
                "first_name": log.first_name or "Telegram User",
                "chat_title": c_title,
                "channel_id": matched_id,
                "message_text": log.message_text,
                "is_lead": log.is_lead,
                "reasoning": log.reasoning or "Оценка ИИ завершена.",
                "niche_code": log.niche_code,
                "temperature": log.temperature,
                "confidence_score": log.confidence_score or 0.0,
                "created_at": ts_str
            })
    except Exception as e:
        logger.error(f"Error in get_ai_evaluation_logs endpoint: {e}")

    return items


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
        # Match Lead specifically for this exact message text
        lead_obj = None
        try:
            lead_stmt = select(Lead).where(
                Lead.user_id == log.user_id,
                Lead.intent_summary.ilike(f"%{log.message_text[:20]}%")
            ).order_by(Lead.created_at.desc()).limit(1)
            lead_obj = (await db.execute(lead_stmt)).scalar_one_or_none()
        except Exception:
            pass

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
    # Auto-reconcile any missing rubrics from Lead table into Rubric table
    try:
        from src.db.models import Lead
        lead_niches = list((await db.execute(select(Lead.niche_code).distinct())).scalars().all())
        
        res = await db.execute(select(Rubric).order_by(Rubric.name.asc()))
        db_rubrics = list(res.scalars().all())
        db_dict = {r.code: {"code": r.code, "name": r.name, "icon": r.icon, "is_custom": r.is_custom} for r in db_rubrics}

        new_rubrics_added = False
        for code in lead_niches:
            if code and code not in db_dict and code != "all":
                clean_code = code.strip().lower()
                name_label = NICHE_NAMES.get(clean_code) or clean_code.replace("_", " ").title()
                icon_char = "🏷️"
                if "market" in clean_code or "smm" in clean_code:
                    icon_char = "📣"
                    name_label = "Маркетинг & SMM"
                elif "relo" in clean_code or "visa" in clean_code:
                    icon_char = "🛂"
                    name_label = "Релокация & Юристы"
                
                new_r = Rubric(code=clean_code, name=name_label, icon=icon_char, is_custom=True)
                db.add(new_r)
                db_dict[clean_code] = {"code": clean_code, "name": name_label, "icon": icon_char, "is_custom": True}
                new_rubrics_added = True
                
        if new_rubrics_added:
            await db.commit()
    except Exception as e:
        logger.warning(f"Notice during rubrics auto-reconciliation: {e}")

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

@router.get("/platforms/status")
async def get_platform_scaler_status(db: AsyncSession = Depends(get_db)):
    """
    Returns platform threshold telemetry (Stage 1 Telegram/MAX, Stage 2 VK @ 1000, Stage 3 OK @ 1500).
    """
    from src.discovery.platform_auto_scaler import PlatformAutoScaler
    return await PlatformAutoScaler.evaluate_platform_status(db)

@router.get("/public/leads/archive")
async def get_public_leads_archive(limit: int = 30, db: AsyncSession = Depends(get_db)):
    """
    Public Proof-of-Performance Lead Archive Endpoint.
    Returns recent qualified and sold leads with direct permalinks to original chat messages.
    """
    from src.utils.telegram_links import generate_message_permalink
    
    stmt = (
        select(Lead, UserProfile)
        .join(UserProfile, Lead.user_id == UserProfile.user_id)
        .order_by(Lead.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    rows = list(res.all())

    archive_items = []
    for lead, prof in rows:
        # Fetch corresponding UserActivityLog for message_id and chat_id
        act_stmt = (
            select(UserActivityLog)
            .where(UserActivityLog.user_id == lead.user_id)
            .order_by(UserActivityLog.timestamp.desc())
            .limit(1)
        )
        act = (await db.execute(act_stmt)).scalar_one_or_none()

        message_link = None
        chat_title = None
        if act:
            chat_title = act.chat_title
            chat_user = act.chat_title.replace("@", "") if act.chat_title and act.chat_title.startswith("@") else None
            message_link = generate_message_permalink(act.chat_id, act.message_id, chat_username=chat_user)

        niche_label = NICHE_NAMES.get(lead.niche_code, lead.niche_code.replace("_", " ").title())
        conf_pct = int((lead.confidence_score or 0.85) * 100)
        ts_utc7 = (lead.created_at + timedelta(hours=7)) if lead.created_at else datetime.now(timezone.utc)

        archive_items.append({
            "id": lead.id,
            "niche_code": lead.niche_code,
            "niche_label": niche_label,
            "temperature": lead.temperature,
            "confidence_pct": conf_pct,
            "intent_summary": lead.intent_summary,
            "sales_hook": lead.sales_hook,
            "status": lead.status,
            "chat_title": chat_title or "Групповой B2B Чат",
            "original_message_url": message_link,
            "created_at_fmt": ts_utc7.strftime("%d.%m.%Y %H:%M")
        })

    return {
        "status": "ok",
        "total": len(archive_items),
        "leads": archive_items
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
        select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_1h_tz)
    )).scalar() or 0
    logs_pass_count = (await db.execute(
        select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_15m_tz)
    )).scalar() or 0
    logs_24h_count = (await db.execute(
        select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_24h_tz)
    )).scalar() or 0

    from sqlalchemy import update
    ttl_hours = getattr(settings, "LEAD_TTL_HOURS", 3)
    cutoff_3h = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)

    # Auto-expire AVAILABLE leads created > 3h ago
    await db.execute(
        update(Lead)
        .where(Lead.status == "AVAILABLE", Lead.created_at < cutoff_3h)
        .values(status="EXPIRED")
    )
    await db.commit()

    # Count total all-time leads in database
    total_leads_all = (await db.execute(select(func.count(Lead.id)))).scalar() or 0

    # Count unique AVAILABLE leads created within 3h by distinct intent_summary
    all_available = (await db.execute(
        select(Lead.intent_summary).where(
            Lead.status == "AVAILABLE",
            Lead.created_at >= cutoff_3h,
            Lead.intent_summary.isnot(None)
        )
    )).scalars().all()
    unique_summaries = set(s.strip().lower() for s in all_available if s and s.strip())
    active_leads_count = len(unique_summaries)

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

    from src.db.models import CollectorLog
    collector_res = await db.execute(
        select(
            func.sum(CollectorLog.total_fetched_count),
            func.sum(CollectorLog.new_messages_count)
        ).where(CollectorLog.created_at >= cutoff_1h_tz)
    )
    c_row = collector_res.first()
    posts_seen_1h = (c_row[0] or 0) if c_row else 0
    collector_new_msgs = (c_row[1] or 0) if c_row else 0

    scanned_display_1h = logs_1h_count if logs_1h_count > 0 else (posts_seen_1h or collector_new_msgs or logs_24h_count or logs_count)

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
        "total_leads": total_leads_all,
        "active_leads": active_leads_count,
        "sold_leads": sold_leads_count,
        "b2b_partners": partners_count,
        "monitored_channels": channels_count,
        "db_size": db_size,
        "userbot_info": userbot_info
    }


@router.post("/system/clean-db")
async def trigger_db_clean():
    """Triggers immediate automated Database Guard size enforcement and retention pruning pass."""
    from src.services.db_guard import db_guard
    res = await db_guard.run_enforcement_pass()
    return {
        "status": "ok",
        "message": f"Очистка базы успешно выполнена. Исходный размер: {res['initial_size_mb']} MB, Итоговый размер: {res['final_size_mb']} MB.",
        "stats": res
    }


@router.post("/collector/rescan-last-hour")
async def trigger_manual_rescan_hour():
    """Triggers an immediate forced 1-hour rescan across all monitored channels/groups."""
    try:
        from src.api.app import ingestor
        if ingestor:
            asyncio.create_task(ingestor.force_rescan_past_hour())
        else:
            from src.ingestion.telegram import TelegramIngestor
            temp_ingestor = TelegramIngestor()
            asyncio.create_task(temp_ingestor.force_rescan_past_hour())
        return {"status": "ok", "message": "Приоритетный перескан за 1 час успешно запущен"}
    except Exception as e:
        logger.error(f"Error triggering rescan: {e}")
        return {"status": "ok", "message": "Приоритетный перескан отправлен в обработку"}


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


@router.get("/collector/live-process-logs")
async def get_live_process_logs(
    response: Response,
    since_id: int = Query(default=0),
    limit: int = Query(default=100),
    category: Optional[str] = Query(default="all"),
    db: AsyncSession = Depends(get_db)
):
    """Returns real-time fine-grained micro-events stream for the live console terminal."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    from src.services.process_logger import process_logger
    
    # Auto-hydrate if in-memory buffer has few items
    if len(process_logger._logs) < 5:
        try:
            from src.db.models import CollectorLog, AIEvaluationLog
            res_c = await db.execute(select(CollectorLog).order_by(CollectorLog.created_at.desc()).limit(30))
            db_c_logs = list(res_c.scalars().all())
            
            res_a = await db.execute(select(AIEvaluationLog).order_by(AIEvaluationLog.created_at.desc()).limit(20))
            db_a_logs = list(res_a.scalars().all())
            
            process_logger.hydrate_from_db_logs(db_c_logs, db_a_logs)
        except Exception as hydr_err:
            logger.warning(f"Process logger hydration notice: {hydr_err}")

    logs = process_logger.get_logs(since_id=since_id, limit=limit, category_filter=category)
    last_idle_s = process_logger.get_last_activity_seconds()
    return {
        "status": "ok",
        "logs": logs,
        "last_activity_seconds": int(last_idle_s),
        "is_stalled": last_idle_s > 45.0
    }

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

        # Instantly run AI scoring on recent 20 messages of newly added channel
        try:
            from src.api.app import ingestor
            if ingestor and posts:
                asyncio.create_task(ingestor.process_and_score_posts_now(new_ch, posts))
        except Exception as e:
            logger.warning(f"Instant AI scoring trigger notice for {username}: {e}")

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
async def list_leads(niche: str = None, location: str = None, status: str = "AVAILABLE", limit: int = 50, is_vip: bool = False, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import update
    ttl_hours = getattr(settings, "LEAD_TTL_HOURS", 3)
    cutoff_3h = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    
    # Auto-expire AVAILABLE leads created > 3h ago
    await db.execute(
        update(Lead)
        .where(Lead.status == "AVAILABLE", Lead.created_at < cutoff_3h)
        .values(status="EXPIRED")
    )
    await db.commit()

    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit)

    status_upper = (status or "AVAILABLE").upper()
    if status_upper in ["AVAILABLE", "CURRENT", "ACTIVE"]:
        stmt = stmt.where(Lead.status == "AVAILABLE", Lead.created_at >= cutoff_3h)
    elif status_upper in ["SOLD", "PURCHASED", "BUYOUT", "EXCLUSIVES"]:
        stmt = stmt.where(Lead.status.in_(["SOLD", "PURCHASED", "EXCLUSIVE", "CLAIMED"]))
    elif status_upper in ["EXPIRED", "ARCHIVE", "ARCHIVED"]:
        stmt = stmt.where((Lead.status == "EXPIRED") | (Lead.status == "ARCHIVED") | ((Lead.status == "AVAILABLE") & (Lead.created_at < cutoff_3h)))
    elif status_upper != "ALL":
        stmt = stmt.where(Lead.status == status_upper)

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

    # Calculate user_message_count and reasoning for each lead
    user_ids = [l.user_id for l in leads if l.user_id]
    msg_counts = {}
    reasonings = {}
    if user_ids:
        cnt_stmt = select(UserActivityLog.user_id, func.count(UserActivityLog.id)).where(UserActivityLog.user_id.in_(user_ids)).group_by(UserActivityLog.user_id)
        cnt_res = await db.execute(cnt_stmt)
        msg_counts = {u_id: count for u_id, count in cnt_res.all()}

        from src.db.models import AIEvaluationLog
        eval_stmt = (
            select(AIEvaluationLog.user_id, AIEvaluationLog.reasoning)
            .where(AIEvaluationLog.user_id.in_(user_ids), AIEvaluationLog.is_lead == True)
            .order_by(AIEvaluationLog.created_at.desc())
        )
        eval_res = await db.execute(eval_stmt)
        for u_id, r_text in eval_res.all():
            if u_id not in reasonings and r_text:
                reasonings[u_id] = r_text

    now_utc = datetime.now(timezone.utc)
    items_out = []
    for l in leads:
        conf_val = float(l.confidence_score or 0.85)
        if conf_val > 1.0:
            conf_val = conf_val / 100.0

        items_out.append({
            "id": l.id,
            "user_id": l.user_id,
            "niche_code": l.niche_code,
            "rubric_name": NICHE_NAMES.get(l.niche_code, "Прочее"),
            "location_code": getattr(l, "location_code", "global") or "global",
            "location_name": LOCATION_NAMES.get(getattr(l, "location_code", "global") or "global", "🌐 Глобал / РФ"),
            "temperature": l.temperature,
            "confidence_score": conf_val,
            "intent_summary": l.intent_summary,
            "sales_hook": l.sales_hook,
            "reasoning": getattr(l, "reasoning", None) or reasonings.get(l.user_id) or l.sales_hook or "ИИ подтвердил клиентский спрос.",
            "user_message_count": max(1, msg_counts.get(l.user_id, 0)),
            "status": l.status,
            "price": float(l.price),
            "created_at": (l.created_at + timedelta(hours=7)).isoformat() if l.created_at else None,
            "is_archived": l.status in ["EXPIRED", "ARCHIVED"] or (l.created_at and l.created_at < cutoff_3h),
            "ttl_remaining_minutes": max(0, int((l.created_at + timedelta(hours=ttl_hours) - now_utc).total_seconds() / 60)) if (l.created_at and l.status == "AVAILABLE") else 0
        })
    return items_out

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

@router.post("/leads/{lead_id}/mark-not-lead")
async def mark_lead_as_not_lead(lead_id: str, db: AsyncSession = Depends(get_db)):
    """
    Marks a lead as Hard Negative (NOT A LEAD) and adds its text to AIStudyExemplar Knowledge Base.
    Permanently trains future AI model prompts to recognize this pattern and reject similar posts.
    """
    stmt = select(Lead).where(Lead.id == lead_id)
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Лид не найден"}

    target_text = lead.intent_summary or "Обученный не-лид пример"
    
    from src.db.models import AIStudyExemplar
    exemplar = AIStudyExemplar(
        raw_message_text=target_text,
        niche_code=lead.niche_code or "other",
        temperature=None,
        is_lead=False,
        intent_summary=f"Обученный Hard Negative (НЕ ЛИД): {target_text[:100]}",
        sales_hook=""
    )
    db.add(exemplar)
    await db.delete(lead)
    await db.commit()

    return {
        "status": "learned",
        "lead_id": lead_id,
        "exemplar_id": exemplar.id,
        "message": "✅ Запрос помечен как НЕ ЛИД и внесен в Базу Знаний ИИ (Few-Shot Prompt Trained)!"
    }

@router.post("/leads/{lead_id}/requalify")
async def requalify_lead(lead_id: str, db: AsyncSession = Depends(get_db)):
    """Triggers instant real-time AI re-evaluation of a lead via LLM (Groq/Gemini)."""
    stmt = select(Lead).where(Lead.id == lead_id)
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Лид не найден"}

    msg_stmt = select(UserActivityLog).where(UserActivityLog.user_id == lead.user_id).order_by(UserActivityLog.timestamp.asc()).limit(15)
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
    """Returns full history of raw messages for a given user_id.
    Hides group name (chat_title) for unpurchased leads to protect lead value."""
    from sqlalchemy.orm import aliased

    # Check if lead is purchased (status == 'SOLD')
    lead_stmt = select(Lead).where(Lead.user_id == user_id).order_by(Lead.created_at.desc()).limit(1)
    lead = (await db.execute(lead_stmt)).scalar_one_or_none()
    is_purchased = bool(lead and lead.status == "SOLD")

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
        if lead:
            title_label = getattr(lead, "chat_title", None) or "Групповой чат"
            masked_title = title_label if is_purchased else "🔒 Групповой чат (скрыто до выкупа)"
            return [{
                "id": "seed",
                "chat_title": masked_title,
                "author_name": None,
                "message_text": lead.intent_summary,
                "timestamp": (lead.created_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if lead.created_at else "Недавно"
            }]
        return []

    result = []
    for log, first_name, username in rows:
        title_label = log.chat_title or "Групповой чат"
        masked_title = title_label if is_purchased else "🔒 Групповой чат (скрыто до выкупа)"
        result.append({
            "id": log.id,
            "chat_title": masked_title,
            "author_name": first_name or (f"@{username}" if username else None),
            "message_text": log.message_text,
            "timestamp": (log.timestamp + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if log.timestamp else "—"
        })

    return result

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
        "company_name": partner.company_name,
        "role": partner.role,
        "balance": float(partner.balance or 0.0),
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


class UpdateOutreachNicheSchema(BaseModel):
    niche_code: str = Field(..., example="real_estate")

@router.post("/outreach/leads/{lead_id}/niche")
async def update_outreach_lead_niche(
    lead_id: str,
    payload: UpdateOutreachNicheSchema,
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import OutreachLead, Rubric
    lead = (await db.execute(select(OutreachLead).where(OutreachLead.id == lead_id))).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Lead not found"}
    
    n_clean = payload.niche_code.strip().lower()
    lead.niche_code = n_clean

    # Auto create rubric if missing
    existing = (await db.execute(select(Rubric).where(Rubric.code == n_clean))).scalar_one_or_none()
    if not existing:
        new_rub = Rubric(code=n_clean, name=n_clean.replace("_", " ").title(), icon="🏷️", is_custom=True)
        db.add(new_rub)

    await db.commit()
    return {"status": "ok", "lead_id": lead_id, "new_niche": lead.niche_code}


@router.delete("/outreach/leads/{lead_id}")
async def delete_outreach_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db)
):
    from src.db.models import OutreachLead
    lead = (await db.execute(select(OutreachLead).where(OutreachLead.id == lead_id))).scalar_one_or_none()
    if not lead:
        return {"status": "error", "message": "Lead not found"}
    
    await db.delete(lead)
    await db.commit()
    return {"status": "ok", "lead_id": lead_id, "message": "Лид успешно удалён из B2B аудитории"}


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
    all_prospects = list((await db.execute(
        select(B2BProspect)
        .where(B2BProspect.dialogue_history.isnot(None))
        .order_by(B2BProspect.created_at.desc())
    )).scalars().all())

    prospects = [p for p in all_prospects if p.dialogue_history and isinstance(p.dialogue_history, list) and len(p.dialogue_history) > 0]

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

    return {"status": "ok", "prospect_id": p.id, "sent_message": payload.text.strip()}


# ────────────────────────────────────────────────────────────────────────────
# DISCOVERY ENGINE API ENDPOINTS
# ────────────────────────────────────────────────────────────────────────────

@router.get("/discovery/chats")
async def get_discovered_chats(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
    db: AsyncSession = Depends(get_db)
):
    """Returns paginated list of discovered chats with scores and LLM audit verdicts."""
    from src.db.models import DiscoveredChat
    stmt = select(DiscoveredChat).order_by(DiscoveredChat.discovered_at.desc()).limit(limit)
    if status and status.upper() != "ALL":
        stmt = stmt.where(DiscoveredChat.audit_status == status.upper())

    chats = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "id": c.id,
            "chat_username": c.chat_username,
            "title": c.title,
            "source": c.source,
            "audit_status": c.audit_status,
            "score": c.score,
            "chat_type": c.chat_type,
            "detected_niches": c.detected_niches or [],
            "verdict_reason": c.verdict_reason,
            "discovered_at_fmt": (c.discovered_at + timedelta(hours=7)).strftime("%d.%m %H:%M") if c.discovered_at else "—",
            "audited_at_fmt": (c.audited_at + timedelta(hours=7)).strftime("%d.%m %H:%M") if c.audited_at else "—"
        }
        for c in chats
    ]


@router.get("/discovery/stats")
async def get_discovery_stats(db: AsyncSession = Depends(get_db)):
    """Returns summary statistics for the Autonomous Chat Discovery & Audit Engine."""
    from src.db.models import DiscoveredChat, BlacklistedChat
    pending = (await db.execute(select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "PENDING"))).scalar() or 0
    approved = (await db.execute(select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "APPROVED"))).scalar() or 0
    rejected = (await db.execute(select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "REJECTED"))).scalar() or 0
    total_blacklisted = (await db.execute(select(func.count(BlacklistedChat.id)))).scalar() or 0

    return {
        "status": "ok",
        "pending_audit_queue": pending,
        "total_approved": approved,
        "total_rejected": rejected,
        "total_blacklisted": total_blacklisted
    }


@router.post("/discovery/trigger")
async def trigger_manual_discovery_cycle():
    """Triggers an instant full discovery & AI audit cycle."""
    from src.discovery.chat_manager import ChatDiscoveryManager
    asyncio.create_task(ChatDiscoveryManager.run_full_discovery_cycle())
    return {"status": "ok", "message": "Автономный цикл поиска и ИИ-аудита чатов запущен!"}


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


class UniversalIngestSchema(BaseModel):
    platform: str = Field(default="custom", example="max")
    chat_title: str = Field(..., example="Дубай Бизнес Чат")
    message_text: str = Field(..., example="Сниму квартиру в Дубае на месяц")
    user_id: Any = Field(..., example="max_user_9912")
    username: Optional[str] = Field(default=None, example="alex_dubai")
    first_name: Optional[str] = Field(default=None, example="Алексей")
    location_code: Optional[str] = Field(default="global", example="dubai")


@router.post("/ingest/message")
async def universal_message_ingest(
    data: UniversalIngestSchema,
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db)
):
    """
    Universal REST API endpoint for ingesting messages from MAX, VK, OK, or external webhooks.
    """
    expected_key = getattr(settings, "INGEST_API_KEY", "lr_sec_ingest_key_2026")
    if api_key and api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    from src.ingestion.multichannel_adapter import MultiChannelAdapter
    res = await MultiChannelAdapter.process_inbound_message(
        session=db,
        platform=data.platform,
        chat_title=data.chat_title,
        message_text=data.message_text,
        user_id_raw=data.user_id,
        username=data.username,
        first_name=data.first_name,
        location_code=data.location_code
    )
    return res


@router.post("/webhooks/max")
async def max_messenger_webhook(
    payload: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook endpoint for MAX Bot API (dev.max.ru / business.max.ru).
    """
    from src.ingestion.multichannel_adapter import MultiChannelAdapter
    parsed = MultiChannelAdapter.parse_max_payload(payload)
    if not parsed.get("message_text"):
        return {"status": "ok", "message": "Ignored empty text"}

    res = await MultiChannelAdapter.process_inbound_message(
        session=db,
        platform="max",
        chat_title=parsed.get("chat_title"),
        message_text=parsed.get("message_text"),
        user_id_raw=parsed.get("user_id_raw"),
        username=parsed.get("username"),
        first_name=parsed.get("first_name"),
        location_code=parsed.get("location_code")
    )
    return res


@router.post("/webhooks/vk")
async def vk_callback_api_webhook(
    payload: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook endpoint for VK Callback API (vk.com/dev).
    Handles 'confirmation' server verification and 'message_new' events.
    """
    msg_type = payload.get("type")
    if msg_type == "confirmation":
        conf_code = getattr(settings, "VK_CONFIRMATION_CODE", "leadradar_vk_ok")
        return Response(content=conf_code, media_type="text/plain")

    if msg_type == "message_new":
        from src.ingestion.multichannel_adapter import MultiChannelAdapter
        parsed = MultiChannelAdapter.parse_vk_payload(payload)
        if parsed.get("message_text"):
            await MultiChannelAdapter.process_inbound_message(
                session=db,
                platform="vk",
                chat_title=parsed.get("chat_title"),
                message_text=parsed.get("message_text"),
                user_id_raw=parsed.get("user_id_raw"),
                username=parsed.get("username"),
                first_name=parsed.get("first_name"),
                location_code=parsed.get("location_code")
            )

    return Response(content="ok", media_type="text/plain")


@router.post("/webhooks/ok")
async def ok_bot_webhook(
    payload: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook endpoint for Odnoklassniki Bot API (apiok.ru).
    """
    from src.ingestion.multichannel_adapter import MultiChannelAdapter
    parsed = MultiChannelAdapter.parse_ok_payload(payload)
    if not parsed.get("message_text"):
        return {"status": "ok"}

    res = await MultiChannelAdapter.process_inbound_message(
        session=db,
        platform="ok",
        chat_title=parsed.get("chat_title"),
        message_text=parsed.get("message_text"),
        user_id_raw=parsed.get("user_id_raw"),
        username=parsed.get("username"),
        first_name=parsed.get("first_name"),
        location_code=parsed.get("location_code")
    )
    return res
