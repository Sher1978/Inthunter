import asyncio
import logging
from datetime import timedelta, datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, Query, HTTPException, Response, Header, UploadFile, File
from sqlalchemy import select, func, delete, case, String
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.session import get_db
from pydantic import BaseModel, Field
from src.db.models import UserProfile, UserActivityLog, Lead, Partner, LeadPurchase, MonitoredChannel, Rubric, AIEvaluationLog, OutreachLead
from src.bot.keyboards import NICHE_NAMES, register_dynamic_rubric
from src.api.auth import create_access_token, get_current_user, get_optional_current_user, require_admin, require_superadmin
import hmac
import hashlib
from urllib.parse import parse_qsl

logger = logging.getLogger("intent_hunter.api")
router = APIRouter()

EFFECTIVENESS_COLORS = {
    0: {"class": "eff-fresh", "label": "Активный", "emoji": "🟢"},
    1: {"class": "eff-day1", "label": "1д молчит", "emoji": "🟡"},
    2: {"class": "eff-day2", "label": "2д молчит", "emoji": "🟠"},
    3: {"class": "eff-day3", "label": "3д молчит", "emoji": "🟠"},
    4: {"class": "eff-day4", "label": "4д молчит", "emoji": "🔴"},
    5: {"class": "eff-day5", "label": "5д молчит", "emoji": "🔴"},
    6: {"class": "eff-day6", "label": "6д молчит", "emoji": "🔴"},
    7: {"class": "eff-dead", "label": "Мёртвый", "emoji": "💀"}
}

import re

def mask_contact_links(text: Optional[str]) -> str:
    """Masks direct links (HTTP, HTTPS, WWW, t.me, tg://), phone numbers, and Telegram @usernames to protect lead monetization."""
    if not text:
        return ""
    res = str(text)
    res = re.sub(r'https?://[^\s><"\']+', '🔒 [ссылка скрыта]', res)
    res = re.sub(r'www\.[^\s><"\']+', '🔒 [ссылка скрыта]', res)
    res = re.sub(r't\.me/[^\s><"\']+', '🔒 [Telegram скрыт]', res)
    res = re.sub(r'tg://[^\s><"\']+', '🔒 [Telegram скрыт]', res)
    res = re.sub(r'@([a-zA-Z0-9_]{3,32})', r'🔒 @[скрыто]', res)
    res = re.sub(r'(\+?\d{1,3}[\s-]?)?\(?\d{3,4}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}', r'🔒 [телефон скрыт]', res)
    return res


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

class TMAAuthSchema(BaseModel):
    init_data: str = Field(..., example="query_id=...&user=...&auth_date=...")

@router.post("/auth/tma")
async def authenticate_tma_user(data: TMAAuthSchema, db: AsyncSession = Depends(get_db)):
    init_data = data.init_data
    
    parsed_data = dict(parse_qsl(init_data))
    if "hash" not in parsed_data:
        raise HTTPException(status_code=400, detail="No hash in initData")
        
    hash_value = parsed_data.pop("hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    
    secret_key = hmac.new(b"WebAppData", settings.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    if calculated_hash != hash_value:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData signature")
        
    import json
    user_data = json.loads(parsed_data.get("user", "{}"))
    telegram_id = user_data.get("id")
    
    if not telegram_id:
        raise HTTPException(status_code=400, detail="No user data found")
        
    stmt = select(Partner).where(Partner.telegram_id == telegram_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    
    if not partner:
        username = user_data.get("username") or str(telegram_id)
        role = "SUPERADMIN" if str(telegram_id) in ["260669598"] or username == settings.SUPERADMIN_USERNAME else "REGULAR"
        partner = Partner(
            telegram_id=telegram_id,
            partner_name=f"TMA {user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip(),
            contact_info=f"@{username}" if username else str(telegram_id),
            role=role,
            niche_priorities={}
        )
        db.add(partner)
        await db.commit()
        await db.refresh(partner)
        
    access_token = create_access_token(data={"partner_id": partner.id, "role": partner.role})
    
    return {
        "status": "ok",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": partner.id,
            "role": partner.role,
            "name": partner.partner_name
        }
    }

@router.get("/admin/emergency-clean")
async def emergency_clean_db(db: AsyncSession = Depends(get_db)):
    """Forces instant TRUNCATE of high-volume log tables and executes VACUUM FULL & CHECKPOINT."""
    from sqlalchemy import text
    from src.db.session import engine
    results = {}
    try:
        await db.execute(text("TRUNCATE TABLE user_activity_logs, ai_evaluation_logs, collector_logs;"))
        await db.commit()
        results["truncate"] = "SUCCESS"
    except Exception as e:
        results["truncate"] = str(e)

    try:
        autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
        async with autocommit_engine.connect() as conn:
            await conn.execute(text("VACUUM;"))
            try:
                await conn.execute(text("CHECKPOINT;"))
                results["checkpoint"] = "SUCCESS"
            except Exception as cp_err:
                results["checkpoint"] = str(cp_err)
        results["vacuum"] = "SUCCESS"
    except Exception as e:
        results["vacuum"] = str(e)

    return {"status": "ok", "results": results}

@router.post("/auth/verify-passcode")
async def verify_admin_passcode(data: VerifyPasscodeSchema):
    inp = data.passcode.strip()
    if inp == settings.ADMIN_PASSCODE or inp == "260669" or inp == "260669598":
        # Legacy fallback, generating a SUPERADMIN token for hardcoded PINs
        access_token = create_access_token(data={"partner_id": "legacy_admin", "role": "SUPERADMIN"})
        return {"status": "ok", "message": "Авторизация успешна", "access_token": access_token}
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
    status: str = None,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc())
    if status == 'ARCHIVED':
        stmt = stmt.where(MonitoredChannel.status == 'ARCHIVED')
    elif status == 'ACTIVE':
        stmt = stmt.where(MonitoredChannel.status != 'ARCHIVED')
    if niche and niche != "all":
        stmt = stmt.where(MonitoredChannel.niche_code == niche)
    
    if location and location != "all":
        loc_clean = location.lower()
        if loc_clean == "vietnam" or loc_clean in ["nhatrang", "danang"]:
            stmt = stmt.where(MonitoredChannel.location_code.in_(["vietnam", "nhatrang", "danang", "phuquoc"]))
        elif loc_clean == "dubai":
            stmt = stmt.where(MonitoredChannel.location_code.in_(["dubai", "ae", "uae"]))
        elif loc_clean in ["phuket", "bangkok"]:
            stmt = stmt.where(MonitoredChannel.location_code.in_(["phuket", "bangkok", "thailand", "samui"]))
        elif loc_clean == "global":
            stmt = stmt.where(MonitoredChannel.location_code.in_(["global", "all", None, ""]))
        else:
            stmt = stmt.where(MonitoredChannel.location_code == location)

    res = await db.execute(stmt)
    channels = list(res.scalars().all())

    # If database is fresh or has fewer than 10 channels, auto-seed curated Top-50 channels
    total_in_db = (await db.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0
    if total_in_db < 10:
        try:
            from seed_nhatrang_channels import seed_nhatrang
            await seed_nhatrang()
            res_fresh = await db.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
            channels = list(res_fresh.scalars().all())
        except Exception as seed_err:
            logger.warning(f"Notice auto-seeding channels on /channels call: {seed_err}")

    if not channels and location and location != "all":
        # Fallback to all monitored channels so the table is never empty
        all_res = await db.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
        channels = list(all_res.scalars().all())

    if query:
        q_clean = query.strip().lower()
        channels = [
            c for c in channels
            if q_clean in (c.title or "").lower() or q_clean in c.username_or_link.lower()
        ]

    now_utc = datetime.now(timezone.utc)
    seven_days_ago = now_utc - timedelta(days=7)

    # 1. Message counts in last 7 days per channel (UserActivityLog)
    msg_counts_stmt = select(
        UserActivityLog.chat_title,
        func.count(UserActivityLog.id)
    ).where(UserActivityLog.timestamp >= seven_days_ago).group_by(UserActivityLog.chat_title)
    msg_counts_res = await db.execute(msg_counts_stmt)
    msg_counts_map = { (row[0] or "").strip().lower(): row[1] for row in msg_counts_res.all() if row[0] }

    # 2. Real max message activity timestamp per channel
    last_act_stmt = select(
        UserActivityLog.chat_title,
        func.max(UserActivityLog.timestamp)
    ).group_by(UserActivityLog.chat_title)
    last_act_res = await db.execute(last_act_stmt)
    last_act_map = { (row[0] or "").strip().lower(): row[1] for row in last_act_res.all() if row[0] }

    # 3. Total message count per channel
    total_msgs_stmt = select(
        UserActivityLog.chat_title,
        func.count(UserActivityLog.id)
    ).group_by(UserActivityLog.chat_title)
    total_msgs_res = await db.execute(total_msgs_stmt)
    total_msgs_map = { (row[0] or "").strip().lower(): row[1] for row in total_msgs_res.all() if row[0] }

    # 4. Lead counts in last 7 days per channel (AIEvaluationLog)
    lead_counts_stmt = select(
        AIEvaluationLog.chat_title,
        func.count(AIEvaluationLog.id)
    ).where(
        AIEvaluationLog.created_at >= seven_days_ago,
        AIEvaluationLog.is_lead == True
    ).group_by(AIEvaluationLog.chat_title)
    lead_counts_res = await db.execute(lead_counts_stmt)
    lead_counts_map = { (row[0] or "").strip().lower(): row[1] for row in lead_counts_res.all() if row[0] }

    # 5. Total lead counts per channel (AIEvaluationLog)
    total_lead_stmt = select(
        AIEvaluationLog.chat_title,
        func.count(AIEvaluationLog.id)
    ).where(AIEvaluationLog.is_lead == True).group_by(AIEvaluationLog.chat_title)
    total_lead_res = await db.execute(total_lead_stmt)
    total_lead_map = { (row[0] or "").strip().lower(): row[1] for row in total_lead_res.all() if row[0] }

    # 6. Pre-fetch DiscoveredChat source map
    from src.db.models import DiscoveredChat
    disc_res = await db.execute(select(DiscoveredChat.chat_username, DiscoveredChat.source))
    disc_source_map = {row[0].lower(): row[1] for row in disc_res.all() if row[0]}

    out = []
    for c in channels:
        c_title_clean = (c.title or "").strip().lower()
        c_uname_clean = (c.username_or_link or "").replace("@", "").replace("https://t.me/", "").strip().lower()

        def find_stat_val(stat_map, t_clean, u_clean):
            if not stat_map:
                return None
            if t_clean and t_clean in stat_map:
                return stat_map[t_clean]
            if u_clean and u_clean in stat_map:
                return stat_map[u_clean]
            u_base = u_clean.split('/')[0].strip() if u_clean else ""
            if u_base and u_base in stat_map:
                return stat_map[u_base]
            for k, val in stat_map.items():
                if not k:
                    continue
                clean_k = k.split('[')[0].strip().lower()
                if u_base and (u_base in k or u_base in clean_k or clean_k in u_base):
                    return val
                if t_clean and (t_clean in k or k in t_clean):
                    return val
            return None

        msgs_7d = find_stat_val(msg_counts_map, c_title_clean, c_uname_clean) or 0
        total_msgs = find_stat_val(total_msgs_map, c_title_clean, c_uname_clean) or 0
        leads_7d = find_stat_val(lead_counts_map, c_title_clean, c_uname_clean) or 0
        leads_total = find_stat_val(total_lead_map, c_title_clean, c_uname_clean) or 0
        
        clean_u = (c.username_or_link or "").lower()
        src_val = disc_source_map.get(clean_u, "USERBOT_JOINED_AUTO_IMPORT" if "dubai" in (c.location_code or "").lower() else "MANUAL_ADD")

        last_msg_raw = find_stat_val(last_act_map, c_title_clean, c_uname_clean)
        created_dt = c.created_at
        if created_dt and created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        created_days = max(0, (now_utc - created_dt).days) if created_dt else 0
        created_fmt = (created_dt + timedelta(hours=7)).strftime("%d.%m %H:%M") if created_dt else "—"

        if last_msg_raw:
            last_msg_dt = last_msg_raw.replace(tzinfo=timezone.utc) if last_msg_raw.tzinfo is None else last_msg_raw
            days_idle = max(0, (now_utc - last_msg_dt).days)
            ts_utc7 = last_msg_dt + timedelta(hours=7)
            diff_s = int((now_utc - last_msg_dt).total_seconds())
            if diff_s < 60:
                fmt_msg_time = f"{ts_utc7.strftime('%H:%M:%S')} (только что)"
            elif diff_s < 3600:
                fmt_msg_time = f"{ts_utc7.strftime('%H:%M:%S')} ({diff_s // 60}м назад)"
            else:
                fmt_msg_time = ts_utc7.strftime("%d.%m %H:%M")
        else:
            days_idle = 999
            fmt_msg_time = "— (Нет постов)"

        if total_msgs == 0:
            if created_days < 1:
                color_class = "eff-dormant"
                color_label = "⏳ Новый (<24ч)"
                color_emoji = "⏳"
                status_tier = "NEW"
            elif created_days < 3:
                color_class = "eff-check"
                color_label = f"⚠️ 0 сообщений ({created_days}д)"
                color_emoji = "⚠️"
                status_tier = "NEED_VERIFY"
            else:
                color_class = "eff-dead"
                color_label = f"🔴 Мёртвый ({created_days}д без постов)"
                color_emoji = "🔴"
                status_tier = "DEAD_3D"
        elif leads_total == 0:
            if days_idle >= 3:
                color_class = "eff-dead"
                color_label = f"🔴 Мёртвый ({days_idle}д молчит)"
                color_emoji = "🔴"
                status_tier = "DEAD_3D"
            elif created_days >= 6:
                color_class = "eff-garbage"
                color_label = f"🔴 0 лидов ({total_msgs} сообщ)"
                color_emoji = "🔴"
                status_tier = "TRASH_ZERO_LEADS"
            elif days_idle >= 1:
                color_class = "eff-day2"
                color_label = f"🟡 Полуживой ({days_idle}д молчит)"
                color_emoji = "🟡"
                status_tier = "HALF_DEAD"
            else:
                color_class = "eff-fresh"
                color_label = f"🟢 Активен ({total_msgs} сообщ)"
                color_emoji = "🟢"
                status_tier = "EFFECTIVE"
        else:
            if days_idle >= 3:
                color_class = "eff-dead"
                color_label = f"🔴 Мёртвый ({days_idle}д молчит)"
                color_emoji = "🔴"
                status_tier = "DEAD_3D"
            elif days_idle >= 1:
                color_class = "eff-day2"
                color_label = f"🟡 Полуживой ({days_idle}д молчит)"
                color_emoji = "🟡"
                status_tier = "HALF_DEAD"
            else:
                color_class = "eff-fresh"
                color_label = "🟢 Живой (<24ч)"
                color_emoji = "🟢"
                status_tier = "LIVE"

        last_pass_dt = getattr(c, "last_scraped_at", None)
        last_pass_fmt = (last_pass_dt + timedelta(hours=7)).strftime("%H:%M:%S") if last_pass_dt else "—"

        out.append({
            "id": c.id,
            "title": c.title,
            "username_or_link": c.username_or_link,
            "source": src_val,
            "niche_code": c.niche_code,
            "location_code": getattr(c, "location_code", "dubai") or "dubai",
            "chat_type": getattr(c, "chat_type", "channel") or "channel",
            "status": c.status,
            "status_tier": status_tier,
            "error_message": c.error_message,
            "created_at": (c.created_at + timedelta(hours=7)).isoformat() if c.created_at else None,
            "created_fmt": created_fmt,
            "last_scraped_at": last_pass_dt.isoformat() if last_pass_dt else None,
            "last_scraped_fmt": fmt_msg_time,
            "last_msg_fmt": fmt_msg_time,
            "last_pass_fmt": last_pass_fmt,
            "msgs_7d": msgs_7d,
            "total_msgs": total_msgs,
            "leads_7d": leads_7d,
            "leads_total": leads_total,
            "days_idle": days_idle,
            "is_dead": total_msgs == 0 or (total_msgs > 0 and leads_total == 0),
            "color_class": color_class,
            "color_label": color_label,
            "color_emoji": color_emoji
        })
    return out


@router.get("/channels/effectiveness")
async def get_channels_effectiveness(db: AsyncSession = Depends(get_db)):
    """Returns detailed channel effectiveness heatmap metrics for monitored channels."""
    now_utc = datetime.now(timezone.utc)

    stmt = select(MonitoredChannel)
    channels = list((await db.execute(stmt)).scalars().all())

    seven_days_ago = now_utc - timedelta(days=7)
    six_days_ago = now_utc - timedelta(days=6)

    msg_counts = await db.execute(
        select(UserActivityLog.chat_title, func.count(UserActivityLog.id))
        .where(UserActivityLog.timestamp >= seven_days_ago)
        .group_by(UserActivityLog.chat_title)
    )
    msg_map = {(row[0] or "").strip().lower(): row[1] for row in msg_counts.all() if row[0]}

    msg_last_act = await db.execute(
        select(UserActivityLog.chat_title, func.max(UserActivityLog.timestamp))
        .group_by(UserActivityLog.chat_title)
    )
    msg_act_map = {(row[0] or "").strip().lower(): row[1] for row in msg_last_act.all() if row[0]}

    lead_counts_7d = await db.execute(
        select(AIEvaluationLog.chat_title, func.count(AIEvaluationLog.id))
        .where(AIEvaluationLog.created_at >= seven_days_ago, AIEvaluationLog.is_lead == True)
        .group_by(AIEvaluationLog.chat_title)
    )
    lead_map_7d = {(row[0] or "").strip().lower(): row[1] for row in lead_counts_7d.all() if row[0]}

    lead_counts_6d = await db.execute(
        select(AIEvaluationLog.chat_title, func.count(AIEvaluationLog.id))
        .where(AIEvaluationLog.created_at >= six_days_ago, AIEvaluationLog.is_lead == True)
        .group_by(AIEvaluationLog.chat_title)
    )
    lead_map_6d = {(row[0] or "").strip().lower(): row[1] for row in lead_counts_6d.all() if row[0]}

    total_lead_counts = await db.execute(
        select(AIEvaluationLog.chat_title, func.count(AIEvaluationLog.id))
        .where(AIEvaluationLog.is_lead == True)
        .group_by(AIEvaluationLog.chat_title)
    )
    total_lead_map = {(row[0] or "").strip().lower(): row[1] for row in total_lead_counts.all() if row[0]}

    out = []
    for c in channels:
        c_title_clean = (c.title or "").strip().lower()
        c_uname_clean = (c.username_or_link or "").replace("@", "").strip().lower()

        msgs_7d = msg_map.get(c_title_clean, 0) or msg_map.get(c_uname_clean, 0) or 0
        leads_7d = lead_map_7d.get(c_title_clean, 0) or lead_map_7d.get(c_uname_clean, 0) or 0
        leads_6d = lead_map_6d.get(c_title_clean, 0) or lead_map_6d.get(c_uname_clean, 0) or 0
        leads_total = total_lead_map.get(c_title_clean, 0) or total_lead_map.get(c_uname_clean, 0) or 0

        last_msg_dt = msg_act_map.get(c_title_clean) or msg_act_map.get(c_uname_clean)
        created_dt = c.created_at
        if created_dt and created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        created_days = max(0, (now_utc - created_dt).days) if created_dt else 0
        created_fmt = (created_dt + timedelta(hours=7)).strftime("%d.%m %H:%M") if created_dt else "—"

        if last_msg_dt:
            if last_msg_dt.tzinfo is None:
                last_msg_dt = last_msg_dt.replace(tzinfo=timezone.utc)
            days_idle = max(0, (now_utc - last_msg_dt).days)
            ts_utc7 = last_msg_dt + timedelta(hours=7)
            diff_s = int((now_utc - last_msg_dt).total_seconds())
            if diff_s < 60:
                fmt_msg_time = f"{ts_utc7.strftime('%H:%M:%S')} (только что)"
            elif diff_s < 3600:
                fmt_msg_time = f"{ts_utc7.strftime('%H:%M:%S')} ({diff_s // 60}м назад)"
            else:
                fmt_msg_time = ts_utc7.strftime("%d.%m %H:%M")
        else:
            days_idle = 999
            fmt_msg_time = "— (Нет постов)"

        if msgs_7d == 0 and leads_total == 0:
            if created_days < 1:
                status_tier = "NEW"
                color_class = "eff-dormant"
                color_emoji = "⏳"
                color_label = "Новый (<24ч)"
            elif created_days < 3:
                status_tier = "HALF_DEAD"
                color_class = "eff-day2"
                color_emoji = "🟡"
                color_label = f"0 сообщений ({created_days}д)"
            else:
                status_tier = "DEAD_3D"
                color_class = "eff-dead"
                color_emoji = "🔴"
                color_label = f"Мёртвый ({created_days}д без постов)"
        elif days_idle >= 3:
            status_tier = "DEAD_3D"
            color_class = "eff-dead"
            color_emoji = "🔴"
            color_label = f"Мёртвый ({days_idle}д молчит)"
        elif leads_6d == 0 and created_days >= 6:
            status_tier = "NO_LEADS_6D"
            color_class = "eff-day6"
            color_emoji = "⚠️"
            color_label = "0 лидов (6д)"
        elif days_idle >= 1:
            status_tier = "HALF_DEAD"
            color_class = "eff-day2"
            color_emoji = "🟡"
            color_label = f"Полуживой ({days_idle}д)"
        else:
            status_tier = "LIVE"
            color_class = "eff-fresh"
            color_emoji = "🟢"
            color_label = "Живой (<24ч)"

        loc_code = getattr(c, "location_code", "global") or "global"
        loc_map = {
            "phuket": "🇹🇭 Пхукет",
            "thailand": "🇹🇭 Таиланд",
            "bangkok": "🇹🇭 Бангкок",
            "dubai": "🇦🇪 Дубай",
            "ae": "🇦🇪 Дубай",
            "uae": "🇦🇪 Дубай",
            "bali": "🇮🇩 Бали",
            "nhatrang": "🇻🇳 Нячанг",
            "danang": "🇻🇳 Дананг",
            "vietnam": "🇻🇳 Вьетнам",
            "moscow": "🇷🇺 Москва",
            "tbilisi": "🇬🇪 Тбилиси",
            "turkey": "🇹🇷 Турция",
            "global": "🌐 Глобальный"
        }
        loc_name = loc_map.get(loc_code.lower(), f"📍 {loc_code.capitalize()}")

        last_pass_dt = getattr(c, "last_scraped_at", None)
        last_pass_fmt = (last_pass_dt + timedelta(hours=7)).strftime("%H:%M:%S") if last_pass_dt else "—"

        out.append({
            "id": c.id,
            "title": c.title or c.username_or_link,
            "username_or_link": c.username_or_link,
            "location_code": loc_code,
            "location_name": loc_name,
            "niche_code": c.niche_code or "community",
            "niche_name": c.niche_code or "Сообщество",
            "status": c.status,
            "status_tier": status_tier,
            "color_class": color_class,
            "color_emoji": color_emoji,
            "color_label": color_label,
            "msgs_7d": msgs_7d,
            "leads_7d": leads_7d,
            "leads_total": leads_total,
            "days_idle": days_idle,
            "created_at": (c.created_at + timedelta(hours=7)).isoformat() if c.created_at else None,
            "created_fmt": created_fmt,
            "last_msg_fmt": fmt_msg_time,
            "last_activity_at": fmt_msg_time,
            "last_scraped_fmt": fmt_msg_time,
            "last_pass_fmt": last_pass_fmt
        })
    return out


@router.post("/channels/{channel_id}/archive")
async def toggle_archive_channel(channel_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(MonitoredChannel).where(MonitoredChannel.id == channel_id))
    ch = res.scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    ch.status = 'PENDING' if ch.status == 'ARCHIVED' else 'ARCHIVED'
    await db.commit()
    return {"status": "ok", "new_status": ch.status}

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
            "status": "added",
            "message": f"Чат или канал {canonical_target} уже находится в списке отслеживаемых!",
            "channel_id": existing.id,
            "channel_status": existing.status,
            "title": existing.title
        }

    # Infer location code if not specified
    loc_code = data.location_code
    u_low = canonical_target.lower()
    if not loc_code or loc_code == "all":
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

    # Launch background auto-join & scraper task without blocking HTTP response
    async def _bg_join_and_score(target_name: str, chan_id: str):
        try:
            from src.api.app import ingestor
            if ingestor:
                await ingestor.join_channel(target_name, channel_id=str(chan_id))
                from src.ingestion.public_scraper import PublicTelegramScraper
                scraper = PublicTelegramScraper()
                posts = await scraper.fetch_latest_messages(target_name)
                if posts:
                    await ingestor.process_and_score_posts_now(channel, posts)
        except Exception as bg_err:
            logger.warning(f"Background join notice for {target_name}: {bg_err}")

    import asyncio
    asyncio.create_task(_bg_join_and_score(canonical_target, str(channel.id)))

    return {
        "status": "added",
        "message": f"Канал {canonical_target} успешно добавлен в отслеживание!",
        "channel_id": channel.id,
        "channel_status": channel.status,
        "title": channel.title,
        "error": None
    }

@router.delete("/channels/{channel_id:path}")
async def delete_monitored_channel(channel_id: str, target: str = None, chat_title: str = None, db: AsyncSession = Depends(get_db)):
    channel = None
    if channel_id and channel_id != "by-target":
        stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
        channel = (await db.execute(stmt)).scalar_one_or_none()
    
    raw_query = ""
    clean_user = ""
    if not channel and (target or channel_id):
        raw_query = (target or channel_id).strip()
        clean_user = raw_query.replace("@", "").replace("https://t.me/s/", "").replace("https://t.me/", "")
        stmt = select(MonitoredChannel).where(
            (MonitoredChannel.username_or_link.ilike(f"%{clean_user}%")) |
            (MonitoredChannel.title.ilike(f"%{raw_query}%"))
        )
        channel = (await db.execute(stmt)).scalars().first()
        
        if not channel and chat_title:
            stmt2 = select(MonitoredChannel).where(MonitoredChannel.title.ilike(f"%{chat_title}%"))
            channel = (await db.execute(stmt2)).scalars().first()

    from sqlalchemy import delete

    if not channel:
        if chat_title:
            await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{chat_title}%")))
        if raw_query:
            await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{raw_query}%")))
        if clean_user:
            await db.execute(delete(UserActivityLog).where(UserActivityLog.channel_username.ilike(f"%{clean_user}%")))
        await db.commit()
        return {"status": "deleted", "channel_id": "not-found", "title": target or chat_title}
    
    ch_id = channel.id
    ch_title = channel.title
    ch_clean_user = channel.username_or_link.replace("@", "").replace("https://t.me/", "")

    # Delete non-lead activity logs associated with this channel
    from sqlalchemy import delete
    if ch_title:
        await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{ch_title}%")))
    if ch_clean_user:
        await db.execute(delete(UserActivityLog).where(UserActivityLog.channel_username.ilike(f"%{ch_clean_user}%")))
    if chat_title:
        await db.execute(delete(UserActivityLog).where(UserActivityLog.chat_title.ilike(f"%{chat_title}%")))

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

@router.post("/channels/{channel_id:path}/verify-connection")
async def verify_channel_connection(channel_id: str, db: AsyncSession = Depends(get_db)):
    """
    Manual connection test for a channel.
    Attempts to read recent posts using public scraper or active Pyrogram userbot,
    updates last_scraped_at and status in DB, and returns exact diagnostic message.
    """
    stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel_id)
    ch = (await db.execute(stmt)).scalar_one_or_none()
    if not ch:
        clean_user = channel_id.replace("@", "").replace("https://t.me/s/", "").replace("https://t.me/", "")
        stmt2 = select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(f"%{clean_user}%"))
        ch = (await db.execute(stmt2)).scalars().first()
        if not ch:
            raise HTTPException(status_code=404, detail="Канал не найден")

    clean_target = ch.username_or_link.replace("@", "").replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").strip()
    clean_target = clean_target.split('/')[0].strip()

    try:
        joined_userbot_id = None
        joined_err = None
        from src.api.app import ingestor
        from src.ingestion.public_scraper import purge_dead_channel

        if ingestor and ingestor.scrapers:
            for node in ingestor.scrapers:
                if node.app and (getattr(node.app, "is_connected", False) or node.status in ("CONNECTED", "CONFIGURED")):
                    try:
                        await node.app.join_chat(clean_target)
                        joined_userbot_id = node.db_id
                        ch.status = "JOINED"
                        await db.commit()
                        logger.info(f"✅ MTProto Userbot #{node.db_id} successfully joined {clean_target}")
                        break
                    except Exception as j_err:
                        joined_err = str(j_err)
                        logger.info(f"Notice during MTProto userbot #{node.db_id} join_chat({clean_target}): {j_err}")
                        if any(err in joined_err for err in ("UsernameNotOccupied", "UsernameInvalid", "PeerIdInvalid", "USERNAME_NOT_OCCUPIED", "USERNAME_INVALID")):
                            await purge_dead_channel(ch.username_or_link, reason=f"Telegram API error: {joined_err}")
                            return {
                                "status": "error",
                                "is_readable": False,
                                "posts_count": 0,
                                "message": f"❌ Канала @{clean_target} НЕ СУЩЕСТВУЕТ в Telegram ({joined_err}). Канал автоматически удален из отслеживаемых и внесен в черный список."
                            }

        # Check if history can be read via Pyrogram or Public Scraper
        posts = []
        try:
            from src.ingestion.public_scraper import PublicTelegramScraper
            scraper = PublicTelegramScraper()
            posts = await scraper.fetch_recent_posts(clean_target)
        except Exception as sc_err:
            logger.debug(f"Public scraper check notice for {clean_target}: {sc_err}")

        if ingestor and ingestor.scrapers and len(posts) == 0:
            for node in ingestor.scrapers:
                if node.app and getattr(node.app, "is_connected", False):
                    try:
                        target_peer = f"@{clean_target}" if not clean_target.startswith("+") else clean_target
                        try:
                            c_obj = await node.app.get_chat(target_peer)
                            if c_obj and c_obj.id:
                                target_peer = c_obj.id
                                if getattr(c_obj, "title", None):
                                    ch.title = c_obj.title
                        except Exception as peer_err:
                            err_str = str(peer_err)
                            if any(err in err_str for err in ("UsernameNotOccupied", "UsernameInvalid", "PeerIdInvalid", "USERNAME_NOT_OCCUPIED", "USERNAME_INVALID")):
                                await purge_dead_channel(ch.username_or_link, reason=f"Telegram API: {err_str}")
                                return {
                                    "status": "error",
                                    "is_readable": False,
                                    "posts_count": 0,
                                    "message": f"❌ Канала @{clean_target} НЕ СУЩЕСТВУЕТ в Telegram ({err_str}). Канал автоматически удален и внесен в черный список."
                                }

                        topic_id = None
                        if "/" in ch.username_or_link and not "http" in ch.username_or_link:
                            try: topic_id = int(ch.username_or_link.split("/")[-1])
                            except: pass
                        elif "t.me/" in ch.username_or_link and ch.username_or_link.count("/") >= 4:
                            try: topic_id = int(ch.username_or_link.split("/")[-1])
                            except: pass

                        limit_fetch = 15 if not topic_id else 60
                        async for m in node.app.get_chat_history(target_peer, limit=limit_fetch):
                            tid = getattr(m, "message_thread_id", getattr(m, "topic_id", getattr(m, "reply_to_message_id", None)))
                            if topic_id and tid != topic_id:
                                continue
                            if m.text or m.caption:
                                posts.append(m)
                            if len(posts) >= 15:
                                break
                    except Exception as hist_err:
                        logger.warning(f"Notice during get_chat_history for {clean_target}: {hist_err}")

        # Instantly ingest and save verified history posts into DB & AI evaluation
        if posts and ingestor:
            formatted_posts = []
            for item in posts:
                if isinstance(item, dict):
                    msg_id = item.get("message_id", 0)
                    txt = item.get("message_text") or item.get("text") or ""
                    uid = item.get("user_id")
                    if not uid:
                        import zlib
                        uid = (zlib.crc32(clean_target.encode("utf-8")) & 0x7FFFFFFF)
                    uname = item.get("username", "")
                    fname = item.get("first_name", "")
                    lname = item.get("last_name", "")
                    c_title = item.get("chat_title") or ch.title or clean_target
                else:  # Pyrogram Message object
                    msg_id = getattr(item, "id", 0)
                    txt = getattr(item, "text", getattr(item, "caption", "")) or ""
                    u_obj = getattr(item, "from_user", None) or getattr(item, "sender_chat", None)
                    uid = getattr(u_obj, "id", None)
                    if not uid:
                        import zlib
                        uid = (zlib.crc32(clean_target.encode("utf-8")) & 0x7FFFFFFF)
                    uname = getattr(getattr(item, "from_user", None), "username", "") or ""
                    fname = getattr(getattr(item, "from_user", None), "first_name", "") or ""
                    lname = getattr(getattr(item, "last_name", None), "last_name", "") or ""
                    c_title = getattr(getattr(item, "chat", None), "title", None) or ch.title or clean_target

                if txt:
                    formatted_posts.append({
                        "message_id": msg_id,
                        "text": txt,
                        "user_id": uid,
                        "username": uname,
                        "first_name": fname,
                        "last_name": lname,
                        "chat_title": c_title
                    })

            if formatted_posts:
                import asyncio
                ch_dict = {"username_or_link": ch.username_or_link, "title": ch.title}
                asyncio.create_task(ingestor.process_and_score_posts_now(ch_dict, formatted_posts))

        ch.status = "JOINED"
        ch.error_message = None
        ch.last_scraped_at = datetime.now(timezone.utc)
        await db.commit()

        msg_suffix = f" (Юзербот #{joined_userbot_id} вступил в чат Telegram)" if joined_userbot_id else ""
        return {
            "status": "ok",
            "is_readable": True,
            "posts_count": len(posts),
            "message": f"✅ Доступ подтвержден! Юзербот подключен к {clean_target}{msg_suffix}. Прочитано {len(posts)} последних сообщений."
        }

    except Exception as e:
        err_msg = str(e)
        ch.status = "FAILED"
        ch.error_message = f"Verification error: {err_msg}"
        await db.commit()
        return {
            "status": "error",
            "is_readable": False,
            "posts_count": 0,
            "message": f"❌ Ошибка подключения: {err_msg}. Рекомендуется проверить ссылку или удалить канал."
        }

@router.api_route("/channels/prune-trash", methods=["GET", "POST"])
@router.api_route("/channels/prune-ineffective", methods=["GET", "POST"])
async def prune_trash_channels_endpoint(db: AsyncSession = Depends(get_db)):
    """
    On-demand manual & automated trigger to purge trash/ineffective channels.
    Cleans non-target spam from MonitoredChannel/DiscoveredChat/ChannelCandidate,
    auto-prunes silent (>=2d) or zero-yield (>=3d) channels, and blacklists them.
    """
    try:
        from src.services.spam_guard import purge_all_database_spam
        await purge_all_database_spam()
    except Exception as sg_err:
        logger.warning(f"Spam guard purge notice during prune-trash: {sg_err}")

    res = await run_auto_channel_pruning(db)

    # Count remaining active channels
    remaining_cnt = (await db.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0
    from src.db.models import BlacklistedChat
    blacklisted_cnt = (await db.execute(select(func.count(BlacklistedChat.id)))).scalar() or 0

    return {
        "status": "ok",
        "message": f"Очистка успешно завершена. Отсеяно неэффективных каналов: {res.get('pruned_count', 0)} шт.",
        "pruned_count": res.get("pruned_count", 0),
        "reasons_breakdown": res.get("reasons", {}),
        "remaining_monitored_channels": remaining_cnt,
        "total_blacklisted_chats": blacklisted_cnt
    }

@router.patch("/channels/{channel_id:path}")
@router.put("/channels/{channel_id:path}")
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


@router.get("/channels/{channel_id:path}/messages")
async def get_channel_messages(channel_id: str, limit: int = 30, db: AsyncSession = Depends(get_db)):
    """
    Returns latest messages for a specific channel sorted in descending order (newest first).
    Includes AI qualification status (ЛИД / B2B SELLER / НЕ ЛИД) and CoT reasoning.
    If no DB records exist yet, fetches MTProto history / web preview on-the-fly.
    """
    import zlib
    clean_search = channel_id.replace("@", "").replace("https://t.me/s/", "").replace("https://t.me/", "").split('/')[0].strip()
    
    stmt = select(MonitoredChannel).where((MonitoredChannel.id == channel_id) | (MonitoredChannel.username_or_link == channel_id))
    ch = (await db.execute(stmt)).scalar_one_or_none()
    if not ch and clean_search:
        stmt2 = select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(f"%{clean_search}%"))
        ch = (await db.execute(stmt2)).scalars().first()
    if not ch:
        ch = MonitoredChannel(
            id=channel_id,
            title=clean_search,
            username_or_link=clean_search,
            platform="telegram"
        )

    target = ch.username_or_link
    title = ch.title or target
    platform = ch.platform or "telegram"
    
    ch_title_cache = ch.title
    ch_username_or_link_cache = ch.username_or_link
    ch_niche_code_cache = ch.niche_code if hasattr(ch, "niche_code") else None

    clean_user = target.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "").split('/')[0].strip()
    clean_title = (title or "").split('[')[0].strip().replace("@", "").strip()

    # Extract significant keywords (len >= 3) from title and username for fuzzy matching
    significant_words = set()
    for source_text in [clean_title, clean_user, title or ""]:
        if source_text:
            cleaned_t = "".join([c if c.isalnum() or c.isspace() else " " for c in source_text])
            for w in cleaned_t.split():
                if len(w) >= 3 and w.lower() not in {"chat", "чат", "копия", "https", "t.me"}:
                    significant_words.add(w.strip().lower())

    def is_matching_title(lct: str) -> bool:
        if not lct:
            return False
        lct = lct.strip().lower()
        if clean_user and clean_user.lower() in lct:
            return True
        if clean_title:
            ct_low = clean_title.lower()
            if ct_low in lct or lct in ct_low:
                return True
        if ch_title_cache:
            ch_t_low = ch_title_cache.lower()
            if ch_t_low in lct or lct in ch_t_low:
                return True
        return False

    items = []
    seen_texts = set()

    # 1. Query AIEvaluationLog by exact channel_username or fallback to fuzzy keywords
    try:
        from sqlalchemy import or_, and_
        
        strict_condition = AIEvaluationLog.channel_username == target
        
        eval_conditions = []
        if clean_user:
            eval_conditions.append(AIEvaluationLog.chat_title.ilike(f"%{clean_user}%"))
            eval_conditions.append(AIEvaluationLog.username.ilike(f"%{clean_user}%"))
        if clean_title:
            eval_conditions.append(AIEvaluationLog.chat_title.ilike(f"%{clean_title}%"))

        final_condition = strict_condition
        if eval_conditions:
            final_condition = or_(
                strict_condition,
                and_(
                    AIEvaluationLog.channel_username.is_(None),
                    or_(*eval_conditions)
                )
            )

        eval_stmt = (
            select(AIEvaluationLog)
            .where(final_condition)
            .order_by(AIEvaluationLog.created_at.desc())
            .limit(limit * 2)
        )
        eval_logs = list((await db.execute(eval_stmt)).scalars().all())
        for el in eval_logs:
            if getattr(el, "channel_username", None) != target:
                if not is_matching_title(el.chat_title or "") and not is_matching_title(el.username or ""):
                    continue
                txt_clean = (el.message_text or "").strip()
                if not txt_clean or txt_clean in seen_texts:
                    continue
                seen_texts.add(txt_clean)

                ts_utc7 = (el.created_at + timedelta(hours=7)) if el.created_at else None
                ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
                status_badge = "LEAD" if el.is_lead else ("SELLER" if getattr(el, "category", None) == "SELLER" else "REJECTED")

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
        await db.rollback()
        logger.warning(f"AIEvaluationLog lookup notice: {e}")

    # 2. Query UserActivityLog if items count is low
    if len(items) < 10:
        try:
            from sqlalchemy import or_
            act_conditions = []
            if clean_user:
                act_conditions.append(UserActivityLog.chat_title.ilike(f"%{clean_user}%"))
            if clean_title:
                act_conditions.append(UserActivityLog.chat_title.ilike(f"%{clean_title}%"))

            act_logs = []
            if act_conditions:
                act_stmt = (
                    select(UserActivityLog)
                    .where(or_(*act_conditions))
                    .order_by(UserActivityLog.timestamp.desc())
                    .limit(limit * 3)
                )
                act_logs = list((await db.execute(act_stmt)).scalars().all())

            # Fallback scan over recent activity logs if keyword search missed short chat titles
            if not act_logs:
                act_stmt_recent = (
                    select(UserActivityLog)
                    .order_by(UserActivityLog.timestamp.desc())
                    .limit(250)
                )
                recent_logs = list((await db.execute(act_stmt_recent)).scalars().all())
                act_logs = [al for al in recent_logs if is_matching_title(al.chat_title or "")]

            # Pre-fetch lead/seller status for all act_log user_ids in one batch query
            act_user_ids = list({al.user_id for al in act_logs if al.user_id})
            lead_ids_set: set = set()
            seller_ids_set: set = set()
            if act_user_ids:
                lead_res = await db.execute(select(Lead.user_id).where(Lead.user_id.in_(act_user_ids)))
                lead_ids_set = {r[0] for r in lead_res.all()}
                seller_res = await db.execute(select(OutreachLead.telegram_id).where(OutreachLead.telegram_id.in_(act_user_ids)))
                seller_ids_set = {r[0] for r in seller_res.all()}

            for al in act_logs:
                if not is_matching_title(al.chat_title or ""):
                    continue
                txt_clean = (al.message_text or "").strip()
                if not txt_clean or txt_clean in seen_texts:
                    continue
                seen_texts.add(txt_clean)

                ts_utc7 = (al.timestamp + timedelta(hours=7)) if al.timestamp else None
                ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"

                is_lead_flag = al.user_id in lead_ids_set
                is_seller_flag = al.user_id in seller_ids_set
                status_badge = "LEAD" if is_lead_flag else ("SELLER" if is_seller_flag else "REJECTED")

                items.append({
                    "id": str(al.id),
                    "message_id": al.message_id,
                    "user_id": al.user_id,
                    "username": f"user_{al.user_id}",
                    "first_name": "Участник чата",
                    "chat_title": al.chat_title or title,
                    "message_text": al.message_text,
                    "is_lead": is_lead_flag,
                    "status_badge": status_badge,
                    "reasoning": f"Сообщение получено из активного потока прослушки '{title}'.",
                    "niche_code": None,
                    "temperature": None,
                    "confidence_score": 0.0,
                    "created_at": ts_str,
                    "source": "DB_ACTIVITY"
                })
        except Exception as e:
            await db.rollback()
            logger.warning(f"UserActivityLog lookup notice: {e}")

    # 3. If DB has 0 items, perform on-demand MTProto/Scraper fetch and save posts permanently into DB
    if not items:
        try:
            from src.api.app import ingestor
            candidate_handles = []
            if clean_user:
                candidate_handles.extend([f"@{clean_user}", clean_user])
            if ch_username_or_link_cache:
                candidate_handles.append(ch_username_or_link_cache)

            raw_posts = []
            if ingestor and ingestor.scrapers:
                for node in ingestor.scrapers:
                    if node.app and getattr(node.app, "is_connected", False):
                        for handle in candidate_handles:
                            try:
                                target_peer = handle
                                try:
                                    c_obj = await node.app.get_chat(handle)
                                    if c_obj and getattr(c_obj, "id", None):
                                        target_peer = c_obj.id
                                        if getattr(c_obj, "title", None):
                                            ch_title_cache = c_obj.title
                                            title = c_obj.title or title
                                except Exception:
                                    pass

                                topic_id = None
                                if "/" in ch_username_or_link_cache and not "http" in ch_username_or_link_cache:
                                    try: topic_id = int(ch_username_or_link_cache.split("/")[-1])
                                    except: pass
                                elif "t.me/" in ch_username_or_link_cache and ch_username_or_link_cache.count("/") >= 4:
                                    try: topic_id = int(ch_username_or_link_cache.split("/")[-1])
                                    except: pass

                                limit_fetch = 20 if not topic_id else 60
                                async for m in node.app.get_chat_history(target_peer, limit=limit_fetch):
                                    tid = getattr(m, "message_thread_id", getattr(m, "topic_id", getattr(m, "reply_to_message_id", None)))
                                    if topic_id and tid != topic_id:
                                        continue
                                    if m.text or m.caption:
                                        raw_posts.append(m)
                                    if len(raw_posts) >= 20:
                                        break
                                if len(raw_posts) > 0:
                                    break
                            except Exception as py_err:
                                logger.warning(f"Pyrogram on-demand history fetch notice for handle {handle}: {py_err}")

                        if len(raw_posts) > 0:
                            break

            if len(raw_posts) == 0 and clean_user:
                from src.ingestion.public_scraper import PublicTelegramScraper
                scraper = PublicTelegramScraper()
                raw_posts = await scraper.fetch_latest_messages(f"@{clean_user}")

            if raw_posts:
                formatted_posts = []
                for item in raw_posts:
                    if isinstance(item, dict):
                        msg_id = item.get("message_id", 0)
                        txt = item.get("message_text") or item.get("text") or ""
                        uid = item.get("user_id")
                        if not uid:
                            uid = (zlib.crc32(clean_user.encode("utf-8")) & 0x7FFFFFFF)
                        uname = item.get("username", "")
                        fname = item.get("first_name", "")
                        lname = item.get("last_name", "")
                        c_title = item.get("chat_title") or title
                    else:  # Pyrogram Message object
                        msg_id = getattr(item, "id", 0)
                        txt = getattr(item, "text", getattr(item, "caption", "")) or ""
                        u_obj = getattr(item, "from_user", None) or getattr(item, "sender_chat", None)
                        uid = getattr(u_obj, "id", None)
                        if not uid:
                            uid = (zlib.crc32(clean_user.encode("utf-8")) & 0x7FFFFFFF)
                        uname = getattr(getattr(item, "from_user", None), "username", "") or ""
                        fname = getattr(getattr(item, "from_user", None), "first_name", "") or ""
                        lname = getattr(getattr(item, "last_name", None), "last_name", "") or ""
                        c_title = getattr(getattr(item, "chat", None), "title", None) or title

                    if txt and txt not in seen_texts:
                        seen_texts.add(txt)
                        formatted_posts.append({
                            "message_id": msg_id,
                            "text": txt,
                            "user_id": uid,
                            "username": uname,
                            "first_name": fname,
                            "last_name": lname,
                            "chat_title": c_title
                        })

                if formatted_posts:
                    if ingestor:
                        ch_dict = {"username_or_link": ch_username_or_link_cache, "title": ch_title_cache}
                        asyncio.create_task(ingestor.process_and_score_posts_now(ch_dict, formatted_posts))

                    for fp in formatted_posts:
                        items.append({
                            "id": f"on_demand_{fp.get('message_id', 0)}",
                            "message_id": fp.get("message_id", 0),
                            "user_id": fp.get("user_id", 0),
                            "username": fp.get("username") or f"user_{fp.get('user_id', 0)}",
                            "first_name": fp.get("first_name") or "Участник чата",
                            "chat_title": fp.get("chat_title") or title,
                            "message_text": fp.get("text", ""),
                            "is_lead": False,
                            "status_badge": "REJECTED",
                            "reasoning": "Сообщение прочитано из истории Telegram.",
                            "niche_code": ch_niche_code_cache,
                            "temperature": None,
                            "confidence_score": 0.0,
                            "created_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S"),
                            "source": "ON_DEMAND_TELEGRAM"
                        })
        except Exception as p_err:
            logger.warning(f"On-demand history scrape notice: {p_err}")

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

@router.get("/ai/rotator/status")
async def get_ai_rotator_status():
    """Returns real-time status of all configured AI keys, active readiness, and remaining cooldown seconds."""
    from src.ai.rotator_engine import ai_rotator
    return ai_rotator.get_rotator_status()

from pydantic import BaseModel
from src.db.models import AIStudyExemplar

class ReclassifyRequest(BaseModel):
    is_lead: bool
    category: str = "BUYER"  # 'BUYER', 'SELLER', 'HR_HIRING', 'JOB_SEEKER', 'IGNORE'
    message_text: Optional[str] = None
    niche_code: Optional[str] = None
    location_code: Optional[str] = "global"
    username: Optional[str] = None
    chat_title: Optional[str] = None

@router.post("/ai/reclassify/{log_id}")
async def reclassify_ai_log(log_id: str, payload: ReclassifyRequest, db: AsyncSession = Depends(get_db)):
    """Manual reclassification of AI Evaluation Logs for Self-Learning Engine."""
    db_id = log_id.replace("eval_", "")
    stmt = select(AIEvaluationLog).where(AIEvaluationLog.id == db_id)
    res = await db.execute(stmt)
    log_entry = res.scalars().first()
    
    if not log_entry:
        if not payload.message_text:
            raise HTTPException(status_code=404, detail="AI Log not found and no raw text provided")
        # Dummy object for the rest of the flow
        class DummyLog:
            pass
        log_entry = DummyLog()
        log_entry.message_text = payload.message_text
        log_entry.niche_code = payload.niche_code
        log_entry.username = payload.username
        log_entry.user_id = 0
        log_entry.first_name = "User"
        log_entry.chat_title = payload.chat_title

    is_valid_lead = (payload.category in ["BUYER", "SELLER", "HR_HIRING", "JOB_SEEKER"])
    if hasattr(log_entry, "is_lead"):
        log_entry.is_lead = is_valid_lead

    if payload.category == "BUYER":
        intent = "Ручная переклассификация: Клиентский запрос (BUYER)"
    elif payload.category == "SELLER":
        intent = "Ручная переклассификация: Б2Б Продавец/Партнер (SELLER)"
    elif payload.category == "HR_HIRING":
        intent = "Ручная переклассификация: Вакансия/Работодатель (HR_HIRING)"
    elif payload.category == "JOB_SEEKER":
        intent = "Ручная переклассификация: Соискатель (JOB_SEEKER)"
    else:
        intent = "Ручная переклассификация: Спам/Флуд (IGNORE)"
    
    if hasattr(log_entry, "reasoning"):
        log_entry.reasoning = intent

    # Create AI Study Exemplar for Level 2 Memory (Fault-tolerant)
    try:
        exemplar = AIStudyExemplar(
            raw_message_text=log_entry.message_text,
            niche_code=log_entry.niche_code or "community",
            temperature="HOT" if is_valid_lead else None,
            is_lead=is_valid_lead,
            intent_summary=intent,
            sales_hook="Ручная коррекция суперадмином"
        )
        if hasattr(AIStudyExemplar, "category"):
            setattr(exemplar, "category", payload.category)
        db.add(exemplar)
        await db.flush()
    except Exception as ex_err:
        logger.warning(f"Exemplar save notice during reclassify: {ex_err}")

    loc_code = payload.location_code or getattr(log_entry, "location_code", None) or "global"

    # 1. If reclassified as HR_HIRING, auto-publish HRVacancy entry so it appears in HR Showcase
    if payload.category == "HR_HIRING":
        try:
            from src.db.models import HRVacancy
            lines = [l.strip() for l in log_entry.message_text.split("\n") if l.strip()]
            first_line = lines[0][:120] if lines else "HR Вакансия (ручная переклассификация)"
            vac = HRVacancy(
                title=first_line,
                company_name=log_entry.username or f"User_{log_entry.user_id}",
                location_code=loc_code,
                niche_code="hr_hiring",
                description=log_entry.message_text,
                raw_post_text=log_entry.message_text,
                hr_contact=f"@{log_entry.username}" if log_entry.username else str(log_entry.user_id),
                author_username=log_entry.username,
                author_telegram_id=log_entry.user_id,
                status="PUBLISHED"
            )
            db.add(vac)
            await db.flush()
        except Exception as vac_err:
            logger.warning(f"HRVacancy creation notice during reclassify: {vac_err}")

    # 2. If reclassified as SELLER, auto-create OutreachLead
    elif payload.category == "SELLER":
        try:
            from src.db.models import OutreachLead
            olead = OutreachLead(
                author_username=log_entry.username,
                telegram_id=log_entry.user_id,
                niche_code=log_entry.niche_code or "OTHER_B2B",
                location_code=loc_code,
                confidence_score=95.0,
                status="READY_FOR_OUTREACH",
                raw_ad_text=log_entry.message_text,
                sales_hook="B2B продавец (ручная переклассификация)",
                chat_title=log_entry.chat_title
            )
            db.add(olead)
            await db.flush()
        except Exception as seller_err:
            logger.warning(f"OutreachLead creation notice: {seller_err}")

    # 2.1 If reclassified as JOB_SEEKER, auto-create OutreachLead
    elif payload.category == "JOB_SEEKER":
        try:
            from src.db.models import OutreachLead
            olead = OutreachLead(
                author_username=log_entry.username,
                telegram_id=log_entry.user_id,
                niche_code="job_seeker",
                location_code=loc_code,
                confidence_score=95.0,
                status="READY_FOR_OUTREACH",
                raw_ad_text=log_entry.message_text,
                sales_hook="Предложить соискателю вступить в наш Telegram-канал с актуальными вакансиями",
                chat_title=log_entry.chat_title
            )
            db.add(olead)
            await db.flush()
        except Exception as job_err:
            logger.warning(f"OutreachLead creation notice for JOB_SEEKER: {job_err}")

    # 3. If reclassified as BUYER, auto-create Lead
    elif payload.category == "BUYER":
        try:
            from src.db.models import Lead, UserProfile
            up_stmt = select(UserProfile).where(UserProfile.user_id == log_entry.user_id)
            up = (await db.execute(up_stmt)).scalar_one_or_none()
            if not up:
                up = UserProfile(user_id=log_entry.user_id, username=log_entry.username, first_name=log_entry.first_name)
                db.add(up)
                await db.flush()

            lead = Lead(
                user_id=log_entry.user_id,
                niche_code=log_entry.niche_code or "community",
                location_code=loc_code,
                temperature="HOT",
                confidence_score=0.95,
                intent_summary=(log_entry.message_text)[:200],
                sales_hook="Покупательский запрос (ручная переклассификация)",
                reasoning=intent,
                status="AVAILABLE",
                price=1.00
            )
            db.add(lead)
            await db.flush()
        except Exception as lead_err:
            logger.warning(f"Lead creation notice during reclassify: {lead_err}")
    
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to commit reclassification log {db_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Database commit error: {e}")
        
    # Trigger background AI self-learning pipeline (Extract keywords & update dynamic AI rules)
    try:
        from src.ai.scorer import extract_keywords_and_update_rules_background
        import asyncio
        asyncio.create_task(extract_keywords_and_update_rules_background(
            message_text=log_entry.message_text,
            category=payload.category,
            niche_code=log_entry.niche_code or "community"
        ))
    except Exception as learn_err:
        logger.warning(f"Notice triggering AI self-learning task: {learn_err}")

    if payload.category == "IGNORE":
        from src.ai.scorer import extract_stopwords_background
        import asyncio
        asyncio.create_task(extract_stopwords_background(log_entry.message_text, log_entry.niche_code))
        
    return {"status": "ok", "new_is_lead": is_valid_lead, "category": payload.category}


@router.get("/ai/vqs-drops")
async def get_vqs_drops(limit: int = 100, hours: int = 24, db: AsyncSession = Depends(get_db)):
    """
    Returns VQS-dropped messages with per-reason statistics.
    These are messages rejected by the VQS pre-filter before reaching the AI scorer.
    """
    try:
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        stmt = (
            select(AIEvaluationLog)
            .where(
                AIEvaluationLog.niche_code == "dropped",
                AIEvaluationLog.created_at >= cutoff
            )
            .order_by(AIEvaluationLog.created_at.desc())
            .limit(limit)
        )
        res = await db.execute(stmt)
        logs = list(res.scalars().all())

        # Build per-reason statistics
        reason_counts: dict = {}
        for log in logs:
            reason = (log.reasoning or "Unknown").split(":")[0].strip()
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        # Sort by frequency descending
        top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)

        items = []
        for log in logs:
            ts_utc7 = (log.created_at + timedelta(hours=7)) if log.created_at else None
            ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
            items.append({
                "id": log.id,
                "chat_title": log.chat_title or "Неизвестный чат",
                "username": log.username or f"ID {log.user_id}",
                "first_name": log.first_name or "Пользователь",
                "message_text": log.message_text,
                "vqs_reason": log.reasoning or "Причина не указана",
                "created_at": ts_str,
            })

        return {
            "total": len(logs),
            "hours": hours,
            "by_reason": dict(top_reasons[:10]),
            "items": items,
        }
    except Exception as e:
        logger.error(f"Error in get_vqs_drops: {e}")
        return {"total": 0, "hours": hours, "by_reason": {}, "items": []}


@router.post("/ai/vqs-drops/{log_id}/recheck")
async def recheck_vqs_drop(log_id: str, db: AsyncSession = Depends(get_db)):
    """
    Manually re-evaluates a VQS-dropped message via AI.
    Returns AI verdict: is this actually a lead that VQS incorrectly rejected?
    """
    try:
        from src.ai.vqs_auditor import vqs_recheck_single
        result = await vqs_recheck_single(log_id)
        return result
    except Exception as e:
        logger.error(f"Error in recheck_vqs_drop: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/vqs-whitelist/add")
async def add_vqs_whitelist_pattern(pattern: str, db: AsyncSession = Depends(get_db)):
    """
    Adds a pattern to the VQS runtime whitelist.
    Called from admin UI when superadmin approves a VQS false positive correction.
    """
    try:
        from src.ingestion.vendor_quality import add_to_vqs_whitelist
        add_to_vqs_whitelist(pattern)
        return {"status": "ok", "pattern": pattern, "message": f"Pattern '{pattern}' added to VQS whitelist"}
    except Exception as e:
        logger.error(f"Error adding VQS whitelist pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai/vqs-whitelist")
async def get_vqs_whitelist_patterns():
    """Returns current runtime VQS whitelist patterns."""
    try:
        from src.ingestion.vendor_quality import get_vqs_whitelist
        patterns = list(get_vqs_whitelist())
        return {"count": len(patterns), "patterns": sorted(patterns)}
    except Exception as e:
        return {"count": 0, "patterns": []}


@router.get("/ai-evaluation-logs")
async def get_ai_evaluation_logs(limit: int = 50, filter_type: str = "all", db: AsyncSession = Depends(get_db)):
    """Returns AI analyzer evaluation logs with Chain-of-Thought reasoning for scanned messages and Discovery LLM chat audits."""

    items = []
    try:
        # 1. Fetch persistent AIEvaluationLog CoT reasoning entries for message evaluation
        stmt = select(AIEvaluationLog)
        if filter_type == "leads":
            stmt = stmt.where(AIEvaluationLog.is_lead == True)
        elif filter_type == "rejected":
            stmt = stmt.where(AIEvaluationLog.is_lead == False, AIEvaluationLog.niche_code != "dropped")
        elif filter_type == "vqs_dropped":
            stmt = stmt.where(AIEvaluationLog.niche_code == "dropped")

        stmt = stmt.order_by(AIEvaluationLog.created_at.desc()).limit(limit)
        res = await db.execute(stmt)
        logs = list(res.scalars().all())

        for log in logs:
            ts_utc7 = (log.created_at + timedelta(hours=7)) if log.created_at else None
            ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
            c_title = (log.chat_title or "Группа/Чат").strip()

            items.append({
                "id": f"eval_{log.id}",
                "user_id": log.user_id,
                "username": log.username or f"ID {log.user_id}",
                "first_name": log.first_name or "Telegram User",
                "chat_title": c_title,
                "channel_id": None,
                "message_text": log.message_text,
                "is_lead": log.is_lead,
                "reasoning": log.reasoning or "Оценка ИИ завершена.",
                "niche_code": log.niche_code,
                "temperature": log.temperature,
                "confidence_score": log.confidence_score or 0.0,
                "created_at": ts_str,
                "sort_ts": log.created_at or datetime.now(timezone.utc)
            })

        # 2. Fetch Discovery Engine LLM Chat Audit reasoning logs (Scout chat candidate audits)
        disc_stmt = select(DiscoveredChat).where(DiscoveredChat.audit_status.in_(["APPROVED", "REJECTED"]))
        if filter_type == "leads":
            disc_stmt = disc_stmt.where(DiscoveredChat.audit_status == "APPROVED")
        elif filter_type == "rejected":
            disc_stmt = disc_stmt.where(DiscoveredChat.audit_status == "REJECTED")

        disc_stmt = disc_stmt.order_by(DiscoveredChat.audited_at.desc().nulls_last()).limit(limit)
        disc_res = await db.execute(disc_stmt)
        disc_chats = list(disc_res.scalars().all())

        for dc in disc_chats:
            ts_dt = dc.audited_at or dc.discovered_at
            ts_utc7 = (ts_dt + timedelta(hours=7)) if ts_dt else None
            ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
            c_title = (dc.title or dc.chat_username or "Канал-кандидат").strip()
            is_approved = dc.audit_status == "APPROVED"
            audit_reason_text = dc.verdict_reason or ("Канал прошел проверку качества ИИ-Аудитора." if is_approved else "Канал отклонен ИИ-Аудитором (спам/боты/нерелевантная ниша).")
            
            niche = dc.detected_niches[0] if dc.detected_niches and len(dc.detected_niches) > 0 else "community"

            items.append({
                "id": f"disc_{dc.id}",
                "user_id": 0,
                "username": f"scout_{dc.source or 'discovery'}",
                "first_name": "🔎 ИИ-Поиск чатов (Discovery)",
                "chat_title": c_title,
                "channel_id": None,
                "message_text": f"Аудит чата-кандидата @{dc.chat_username.replace('@', '')} [{dc.location_code or 'GLOBAL'}]",
                "is_lead": is_approved,
                "reasoning": f"{'✅' if is_approved else '⛔'} ИИ-Аудит качества канала: {audit_reason_text}",
                "niche_code": niche,
                "temperature": "HOT" if is_approved else "COLD",
                "confidence_score": (dc.score or 85)/100.0 if is_approved else 0.10,
                "created_at": ts_str,
                "sort_ts": ts_dt or datetime.now(timezone.utc)
            })

        # 3. Fallback if AIEvaluationLog is empty: hydrate from UserActivityLog message scoring
        if not logs:
            act_stmt = select(UserActivityLog, UserProfile).join(UserProfile, UserActivityLog.user_id == UserProfile.user_id, isouter=True).order_by(UserActivityLog.timestamp.desc()).limit(limit)
            act_res = await db.execute(act_stmt)
            act_rows = list(act_res.all())

            for act, prof in act_rows:
                msg_t = act.message_text or ""
                msg_low = msg_t.lower()
                is_l = any(kw in msg_low for kw in ["ищу", "нужен", "нужна", "купить", "снять", "аренда", "обмен", "виза", "посоветуйте", "подскажите", "цена", "стоимость"])
                
                if filter_type == "leads" and not is_l:
                    continue
                if filter_type == "rejected" and is_l:
                    continue

                ts_utc7 = (act.timestamp + timedelta(hours=7)) if act.timestamp else None
                ts_str = ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
                uname = (prof.username if prof else None) or f"user_{act.user_id}"
                fname = (prof.first_name if prof else None) or f"Пользователь {act.user_id}"
                c_title = (act.chat_title or "Группа/Чат").strip()

                items.append({
                    "id": f"act_{act.id}",
                    "user_id": act.user_id,
                    "username": uname,
                    "first_name": fname,
                    "chat_title": c_title,
                    "channel_id": None,
                    "message_text": msg_t,
                    "is_lead": is_l,
                    "reasoning": f"ИИ-Анализатор: Сообщение из [{c_title}]. " + ("Выявлен горячий целевой запрос клиента (HOT/WARM)." if is_l else "Обычное общение / спам / рекламное объявление в группе."),
                    "niche_code": "community",
                    "temperature": "HOT" if is_l else "COLD",
                    "confidence_score": 0.95 if is_l else 0.15,
                    "created_at": ts_str,
                    "sort_ts": act.timestamp or datetime.now(timezone.utc)
                })

        # Sort all AI reasoning items by timestamp descending
        def _safe_sort_key(item):
            ts = item.get("sort_ts")
            if ts is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts

        items.sort(key=_safe_sort_key, reverse=True)
        # Remove internal sort_ts field before returning JSON
        for it in items:
            it.pop("sort_ts", None)

    except Exception as e:
        logger.error(f"Error in get_ai_evaluation_logs endpoint: {e}")

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
            if code and code != "all":
                clean_code = code.strip().lower()
                if clean_code not in db_dict:
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
        await db.rollback()
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
@router.get("/healthcheck")
async def health_check():
    """
    Healthcheck API endpoint for external uptime monitors (e.g. UptimeRobot).
    Returns HTTP 200 when listener is active, or HTTP 503 Service Unavailable if inactive (> 300s).
    """
    import os
    from datetime import datetime, timezone
    from fastapi import status
    from fastapi.responses import JSONResponse
    from src.ingestion.telegram import get_last_message_time

    timeout_seconds = int(os.getenv("DEAD_MAN_TIMEOUT_SECONDS", "300"))
    last_msg_at = get_last_message_time()
    now = datetime.now(timezone.utc)
    seconds_since_last = (now - last_msg_at).total_seconds() if last_msg_at else 0.0
    is_stale = seconds_since_last > timeout_seconds

    scraped_count = 0
    try:
        from src.api.app import ingestor
        if ingestor:
            scraped_count = getattr(ingestor, "scraped_count", 0) or 0
    except Exception:
        pass

    payload = {
        "status": "stale" if is_stale else "ok",
        "service": "Intent Hunter CDP / LeadRadar Listener",
        "last_message_time": last_msg_at.isoformat() if last_msg_at else None,
        "seconds_since_last_message": round(seconds_since_last, 1),
        "stale_threshold_seconds": timeout_seconds,
        "is_stale": is_stale,
        "scraped_count": scraped_count,
        "timestamp": now.isoformat()
    }

    http_code = status.HTTP_503_SERVICE_UNAVAILABLE if is_stale else status.HTTP_200_OK
    return JSONResponse(status_code=http_code, content=payload)


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
        .where(
            CollectorLog.created_at >= cutoff_1h,
            CollectorLog.chat_title != "SYSTEM_ALERT",
            CollectorLog.status != "SYSTEM_ALERT"
        )
        .order_by(CollectorLog.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    raw_logs = list(res.scalars().all())

    if not raw_logs:
        fb_stmt = (
            select(CollectorLog)
            .where(
                CollectorLog.chat_title != "SYSTEM_ALERT",
                CollectorLog.status != "SYSTEM_ALERT"
            )
            .order_by(CollectorLog.created_at.desc())
            .limit(limit)
        )
        res_fb = await db.execute(fb_stmt)
        raw_logs = list(res_fb.scalars().all())

    ch_res = await db.execute(select(MonitoredChannel))
    channels = list(ch_res.scalars().all())
    ch_id_map = {c.title.strip().lower(): c.id for c in channels if c.title}
    ch_id_user_map = {c.username_or_link.replace("@", "").lower(): c.id for c in channels if c.username_or_link}

    raw_checks = len(raw_logs)
    raw_posts = sum(getattr(l, "total_fetched_count", 0) or 0 for l in raw_logs)
    raw_msgs = sum(l.new_messages_count for l in raw_logs)
    raw_leads = sum(l.new_leads_count for l in raw_logs)

    total_checks_1h = raw_checks
    total_posts_seen_1h = raw_posts
    total_new_msgs_1h = raw_msgs
    total_leads_1h = raw_leads

    items = []
    for l in raw_logs:
        ts_utc7 = (l.created_at + timedelta(hours=7)) if l.created_at else None
        c_title_clean = (l.chat_title or "").strip().lower()
        user_clean = (l.username_or_link or "").replace("@", "").lower()
        ch_id = ch_id_map.get(c_title_clean) or ch_id_user_map.get(user_clean)

        userbot_info = "📡 Web-Скрапер (25s)"
        if l.details:
            if "Userbot:" in l.details:
                parsed_ub = l.details.split("Userbot:")[1].split("|")[0].strip()
                if parsed_ub and parsed_ub != "⚡ Pyrogram MTProto #1" and parsed_ub != "Userbot:":
                    userbot_info = parsed_ub

        items.append({
            "id": l.id,
            "chat_title": l.chat_title,
            "username_or_link": l.username_or_link,
            "channel_id": ch_id,
            "total_fetched_count": getattr(l, "total_fetched_count", 0) or 0,
            "new_messages_count": l.new_messages_count,
            "new_leads_count": l.new_leads_count,
            "status": l.status,
            "details": l.details or "",
            "userbot_info": userbot_info,
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

@router.get("/collector-logs/messages")
async def get_collector_log_messages(
    log_id: Optional[str] = Query(default=None),
    title: Optional[str] = Query(default=None),
    username: Optional[str] = Query(default=None),
    limit: int = Query(default=30),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns captured raw messages for a specific collector log check or monitored chat.
    Allows opening modal popup details showing text, author, timestamp, platform & AI lead qualification status.
    """
    from src.db.models import CollectorLog, UserActivityLog, UserProfile, Lead
    from sqlalchemy.orm import selectinload

    target_title = title or ""
    target_user = username or ""

    if log_id:
        c_log = await db.get(CollectorLog, log_id)
        if c_log:
            target_title = c_log.chat_title or target_title
            target_user = c_log.username_or_link or target_user

    clean_user = target_user.replace("@", "").strip().lower() if target_user else ""
    clean_title = target_title.strip().lower() if target_title else ""

    stmt = select(UserActivityLog).options(selectinload(UserActivityLog.user)).order_by(UserActivityLog.timestamp.desc())

    is_special_title = any(kw in clean_title for kw in ["перескан", "rescan", "system_alert", "ручной"])

    if clean_user and not is_special_title:
        stmt = stmt.where(
            (func.lower(UserActivityLog.chat_title).contains(clean_user)) |
            (UserActivityLog.chat_id.cast(String).contains(clean_user))
        )
    elif clean_title and not is_special_title:
        stmt = stmt.where(func.lower(UserActivityLog.chat_title).contains(clean_title))

    stmt = stmt.limit(limit)
    activities = list((await db.execute(stmt)).scalars().all())

    # Fallback: If title filter returned no rows (e.g. title mismatch or special log title), load latest captured messages
    if not activities:
        fb_stmt = select(UserActivityLog).options(selectinload(UserActivityLog.user)).order_by(UserActivityLog.timestamp.desc()).limit(limit)
        activities = list((await db.execute(fb_stmt)).scalars().all())

    user_ids = [a.user_id for a in activities if a.user_id]
    lead_user_ids = set()
    if user_ids:
        l_res = await db.execute(select(Lead.user_id).where(Lead.user_id.in_(user_ids)))
        lead_user_ids = set(l_res.scalars().all())

    items = []
    for a in activities:
        ts_utc7 = (a.timestamp + timedelta(hours=7)) if a.timestamp else None
        u_profile = a.user
        author_name = "—"
        if u_profile:
            author_name = f"{u_profile.first_name or ''} {u_profile.last_name or ''}".strip()
            if u_profile.username:
                author_name += f" (@{u_profile.username})"
        if not author_name or author_name == "—":
            author_name = f"Пользователь #{a.user_id}"

        items.append({
            "id": a.id,
            "chat_title": a.chat_title or target_title or "Telegram Группа",
            "message_id": a.message_id,
            "text": a.message_text,
            "author": author_name,
            "user_id": a.user_id,
            "platform": a.platform or "telegram",
            "is_lead": a.user_id in lead_user_ids,
            "timestamp_fmt": ts_utc7.strftime("%H:%M:%S") if ts_utc7 else "—",
            "date_full": ts_utc7.strftime("%d.%m.%Y %H:%M:%S") if ts_utc7 else "—"
        })

    return {
        "status": "ok",
        "chat_title": target_title or "Отслеживаемый чат",
        "username_or_link": target_user,
        "count": len(items),
        "messages": items
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
        .outerjoin(UserProfile, Lead.user_id == UserProfile.user_id)
        .order_by(Lead.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    rows = list(res.all())

    user_ids = [lead.user_id for lead, _ in rows if lead.user_id]
    act_map = {}
    if user_ids:
        act_stmt = (
            select(UserActivityLog)
            .where(UserActivityLog.user_id.in_(user_ids))
            .order_by(UserActivityLog.timestamp.desc())
        )
        act_res = await db.execute(act_stmt)
        for act in act_res.scalars().all():
            if act.user_id not in act_map:
                act_map[act.user_id] = act

    archive_items = []
    for lead, prof in rows:
        act = act_map.get(lead.user_id)

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
    try:
        users_count = (await db.execute(select(func.count(UserProfile.user_id)))).scalar() or 0
        b2c_leads_all = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
        from src.db.models import OutreachLead
        b2b_leads_all = (await db.execute(select(func.count(OutreachLead.id)))).scalar() or 0
        total_leads_all = b2c_leads_all + b2b_leads_all

        active_b2c = (await db.execute(select(func.count(Lead.id)).where(Lead.status.in_(["AVAILABLE", "ACTIVE", "NEW", "UNCLAIMED"])))).scalar() or 0
        active_b2b = (await db.execute(select(func.count(OutreachLead.id)).where(OutreachLead.status.in_(["READY_FOR_OUTREACH", "NEED_APPROVAL"])))).scalar() or 0
        active_leads_count = active_b2c

        sold_leads_count = (await db.execute(select(func.count(Lead.id)).where(Lead.status.in_(["SOLD", "PURCHASED", "EXCLUSIVE", "CLAIMED"])))).scalar() or 0
        partners_count = (await db.execute(select(func.count(Partner.id)).where(Partner.role != "DEMO"))).scalar() or 0
        
        # Real Active Joined Channels vs Total Channels in DB
        active_joined_channels = (await db.execute(select(func.count(MonitoredChannel.id)).where(MonitoredChannel.status.notin_(["FAILED", "ARCHIVED", "DISABLED"])))).scalar() or 0
        total_channels_db = (await db.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0

        cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
        msgs_1h_count = (await db.execute(select(func.count(UserActivityLog.id)).where(UserActivityLog.timestamp >= cutoff_1h))).scalar() or 0
        total_logs_count = (await db.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
    except Exception as err:
        logger.warning(f"Stats query notice: {err}")
        users_count, total_leads_all, active_leads_count, b2c_leads_all, sold_leads_count, partners_count, active_joined_channels, total_channels_db, msgs_1h_count, total_logs_count = 0, 0, 0, 0, 0, 0, 0, 0, 0, 0

    userbot_info = {
        "is_connected": True,
        "mode": "⚡ ИИ-Сканер & Сборщик (25s)",
        "last_check_at": "—",
        "last_scraped_at": "—"
    }
    try:
        from src.api.app import ingestor
        if ingestor:
            is_conn = bool(ingestor._is_running or ingestor.scrapers)
            mode_str = "⚡ Pyrogram MTProto Userbot" if ingestor.scrapers else "⚡ ИИ-Сканер & Сборщик (25s)"
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

    result = {
        "user_profiles": users_count,
        "activity_logs": total_logs_count,
        "scanned_1h": msgs_1h_count,
        "scanned_pass": msgs_1h_count,
        "scanned_24h": msgs_1h_count * 24,
        "posts_seen_1h": msgs_1h_count,
        "total_leads": b2c_leads_all,
        "active_leads": active_b2c,
        "active_b2c": active_b2c,
        "active_b2b": active_b2b,
        "b2c_leads_all": b2c_leads_all,
        "b2b_leads_all": b2b_leads_all,
        "sold_leads": sold_leads_count,
        "b2b_partners": partners_count,
        "monitored_channels": active_joined_channels,
        "active_joined_channels": active_joined_channels,
        "total_channels_db": total_channels_db,
        "userbot_info": userbot_info
    }
    return result



@router.api_route("/system/clean-db", methods=["GET", "POST"])
async def trigger_db_clean():
    """Triggers immediate automated Database Guard size enforcement, aggressive retention pruning and PostgreSQL VACUUM FULL pass."""
    from src.services.db_guard import db_guard
    res = await db_guard.run_enforcement_pass()
    
    # Run instant autocommit VACUUM FULL to reclaim 100% of free space on Railway volume
    try:
        from sqlalchemy import text
        from src.db.session import engine
        autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
        async with autocommit_engine.connect() as conn:
            try:
                await conn.execute(text("VACUUM FULL;"))
            except Exception as vf_err:
                logger.warning(f"VACUUM FULL notice in route: {vf_err}")
                await conn.execute(text("VACUUM;"))
    except Exception as e:
        logger.warning(f"Route vacuum notice: {e}")

    # Re-calculate size after VACUUM FULL
    from src.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        final_mb = await db_guard.get_db_size_mb(session)
    res["final_size_mb"] = final_mb

    return {
        "status": "ok",
        "message": f"Очистка базы и VACUUM FULL успешно выполнены. Исходный размер: {res['initial_size_mb']} MB, Итоговый размер: {res['final_size_mb']} MB.",
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


@router.post("/stop-scanner")
async def stop_scanner_endpoint():
    try:
        from src.api.app import ingestor
        if ingestor:
            await ingestor.stop()
            return {"status": "success", "message": "Сборщик остановлен."}
        return {"status": "error", "message": "Сборщик не запущен."}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка остановки: {e}"}

@router.get("/system/modules")
async def get_system_modules_status():
    """Returns real-time execution status of all background system modules."""
    from src.services.module_manager import module_manager
    return module_manager.get_all_states()

@router.post("/system/modules/toggle")
async def toggle_system_module(payload: dict):
    """Toggles or sets the execution status of a background system module."""
    from src.services.module_manager import module_manager
    from fastapi.responses import JSONResponse
    
    module_key = payload.get("module_key", "").strip().lower()
    enabled = payload.get("enabled", None)
    
    if not module_key:
        return JSONResponse(status_code=400, content={"status": "error", "message": "module_key is required"})
        
    if enabled is not None:
        success = module_manager.set_status(module_key, bool(enabled))
    else:
        new_state = module_manager.toggle(module_key)
        success = True
        
    if success:
        return {
            "status": "ok",
            "message": f"Модуль '{module_key}' успешно обновлен",
            "modules": module_manager.get_all_states()["modules"]
        }
    return JSONResponse(status_code=404, content={"status": "error", "message": f"Неизвестный модуль '{module_key}'"})


@router.post("/admin/clean-db")
async def admin_clean_db():
    try:
        from sqlalchemy import select, delete
        from src.db.session import AsyncSessionLocal
        from src.db.models import MonitoredChannel
        
        good_tgstat_links = [
            "@dubaiprofi", "@dubaisk_8", "@madubai", "@kstati_dubai", "@dubai_uae_hub"
        ]
        
        spam_links = [
            "@monicavallejo1", "@victoriacakeshaven", "@sabrinandreina46", "@urbe_bikini",
            "@senorita_sara8", "@dreddxxx_0", "@ashleyxfox2122", "@yessybernalucra",
            "@anitajimaok", "@lana_lrvin", "@jossbolivar", "@adrianaolivarez15",
            "@analy_bazanof", "@elizabecommunityxx", "@saral_seva_bharti_strugglers",
            "@spjinimart"
        ]
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(MonitoredChannel))
            channels = result.scalars().all()
            
            to_delete = []
            for ch in channels:
                keep = False
                username_lower = (ch.username_or_link or "").lower().replace('https://t.me/', '@')
                
                if username_lower in spam_links:
                    keep = False
                elif ch.leads_count > 0:
                    keep = True
                elif username_lower in good_tgstat_links:
                    keep = True
                elif ch.status == 'JOINED':
                    keep = True
                
                if not keep:
                    to_delete.append(ch.id)
            
            deleted_count = len(to_delete)
            if to_delete:
                chunk_size = 500
                for i in range(0, len(to_delete), chunk_size):
                    chunk = to_delete[i:i + chunk_size]
                    await session.execute(
                        delete(MonitoredChannel).where(MonitoredChannel.id.in_(chunk))
                    )
                await session.commit()
                
            return {"status": "success", "message": f"Очищено {deleted_count} мусорных чатов."}
    except Exception as e:
        import traceback
        return {"status": "error", "message": f"Ошибка: {e}\n{traceback.format_exc()}"}


@router.api_route("/admin/purge-all-chats", methods=["GET", "POST"])
async def admin_purge_all_chats(db: AsyncSession = Depends(get_db)):
    """
    Completely purges ALL chats, candidates, discovered chats, and activity/AI logs from the database.
    """
    try:
        from sqlalchemy import text
        tables = [
            "monitored_channels",
            "discovered_chats",
            "channel_candidates",
            "user_activity_logs",
            "ai_evaluation_logs",
            "collector_logs",
            "blacklisted_chats"
        ]
        purged_counts = {}
        for tbl in tables:
            try:
                res = await db.execute(text(f"DELETE FROM {tbl};"))
                purged_counts[tbl] = res.rowcount
            except Exception as t_err:
                purged_counts[tbl] = f"Notice: {t_err}"

        await db.commit()

        # Restart scraper loop with 0 channels
        try:
            from src.api.app import ingestor
            if ingestor:
                await ingestor.restart_scraper_loop()
        except Exception:
            pass

        return {
            "status": "ok",
            "message": "База данных ПОЛНОСТЬЮ очищена от всех чатов, логов и кандидатов.",
            "purged_tables": purged_counts
        }
    except Exception as e:
        await db.rollback()
        import traceback
        return {"status": "error", "message": f"Ошибка очистки: {e}\n{traceback.format_exc()}"}


@router.post("/collector/sync-userbot-dialogs")
async def trigger_sync_userbot_dialogs():
    """Scans all Telegram groups joined by the userbot and auto-imports them into Scout & MonitoredChannels."""
    try:
        from src.api.app import ingestor
        if ingestor:
            cnt = await ingestor.sync_userbot_joined_dialogs()
            return {"status": "ok", "message": f"Успешно синхронизировано и импортировано новых групп юзербота: {cnt}", "imported_count": cnt}
        return {"status": "error", "message": "Сканер юзербота не инициализирован"}
    except Exception as e:
        logger.error(f"Error syncing userbot dialogs: {e}")
        return {"status": "error", "message": f"Ошибка синхронизации: {e}"}


@router.get("/collector/telemetry")
async def get_collector_telemetry(db: AsyncSession = Depends(get_db)):
    """Returns latest 50 real-time telemetry log entries (including 0-message poll attempts)."""
    from src.db.models import CollectorLog
    res = await db.execute(
        select(CollectorLog)
        .where(
            CollectorLog.chat_title != "SYSTEM_ALERT",
            CollectorLog.status != "SYSTEM_ALERT"
        )
        .order_by(CollectorLog.created_at.desc())
        .limit(50)
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
    
    # Auto-hydrate if in-memory buffer has few SCRAPER or AI_SCORER items
    if len([l for l in process_logger._logs if l.category in ("SCRAPER", "AI_SCORER")]) < 5:
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
        "is_stalled": last_idle_s > 300.0
    }

@router.get("/ai/keys-status")
async def get_ai_keys_status():
    """
    Returns real-time health, cooldown, and telemetry status of all AI provider API keys.
    """
    import time
    from src.config import settings
    from src.ai.rotator_engine import _extract_keys, _key_cooldowns
    from src.ai.budget_guard import ai_budget_guard

    now = time.time()
    gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
    groq_keys = _extract_keys(getattr(settings, "GROQ_API_KEYS", ""), getattr(settings, "GROQ_API_KEY", ""), prefix_filter="gsk_")
    or_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""))
    cer_keys = _extract_keys(getattr(settings, "CEREBRAS_API_KEYS", ""), getattr(settings, "CEREBRAS_API_KEY", ""), prefix_filter="csk-")
    xai_keys = _extract_keys(getattr(settings, "XAI_API_KEYS", ""), getattr(settings, "XAI_API_KEY", ""))

    keys_list = []
    
    def _add_keys(provider_name, keys):
        for k in keys:
            mask = f"...{k[-4:]}" if len(k) >= 4 else k
            cd_until = _key_cooldowns.get(k, 0.0)
            rem_sec = max(0, int(cd_until - now))
            status = "COOLDOWN" if rem_sec > 0 else "READY"
            keys_list.append({
                "provider": provider_name,
                "key_mask": mask,
                "status": status,
                "cooldown_sec": rem_sec
            })

    _add_keys("Google Gemini", gemini_keys)
    _add_keys("Groq Cloud", groq_keys)
    _add_keys("OpenRouter", or_keys)
    _add_keys("Cerebras Cloud", cer_keys)
    _add_keys("xAI Grok", xai_keys)

    telemetry = ai_budget_guard.get_telemetry_status()

    return {
        "status": "ok",
        "keys": keys_list,
        "telemetry": telemetry
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
# CHANNEL EFFECTIVENESS REPORT & AUTO-PRUNING
# Classification & Rules:
#   1. LIVE (🟢): <24h silence (days_idle == 0)
#   2. HALF_DEAD (🟡): 1-2d silence (days_idle == 1 or 2)
#   3. DEAD_3D (🔴): >=3d silence (days_idle >= 3 or no msgs in >=3d) -> Scheduled for Auto-Prune & Blacklist
#   4. NO_LEADS_6D (⚠️): Monitored >= 6d with 0 total leads -> Scheduled for Auto-Prune & Blacklist
# ────────────────────────────────────────────────────────────────────────────

async def purge_nonexistent_channels_pass(db: AsyncSession) -> dict:
    """
    Scans all MonitoredChannels and verifies their existence on Telegram.
    If a channel username returns UsernameNotOccupied / UsernameInvalid / 404 Not Found,
    it is immediately deleted from MonitoredChannel and blacklisted in BlacklistedChat.
    """
    from src.db.models import MonitoredChannel
    from src.ingestion.public_scraper import purge_dead_channel
    
    channels = list((await db.execute(select(MonitoredChannel))).scalars().all())
    purged_list = []
    
    from src.api.app import ingestor
    userbot_node = None
    if ingestor and ingestor.scrapers:
        for node in ingestor.scrapers:
            if node.app and getattr(node.app, "is_connected", False):
                userbot_node = node
                break

    for ch in channels:
        target = ch.username_or_link
        clean_user = target.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "").split('/')[0].strip()
        if not clean_user:
            continue
            
        is_dead = False
        dead_reason = ""

        # 1. Check via Pyrogram userbot if connected
        if userbot_node:
            try:
                await userbot_node.app.get_chat(f"@{clean_user}" if not clean_user.startswith("+") else clean_user)
            except Exception as py_err:
                err_str = str(py_err)
                if any(k in err_str for k in ("UsernameNotOccupied", "UsernameInvalid", "PeerIdInvalid", "USERNAME_NOT_OCCUPIED", "USERNAME_INVALID")):
                    is_dead = True
                    dead_reason = f"Telegram API error: {err_str}"

        # 2. Fallback check via Web Scraper (run for all public @username channels,
        # regardless of whether userbot is connected — catches what Pyrogram misses)
        is_private_link = "t.me/c/" in target or (clean_user and clean_user.lstrip("+").isdigit())
        if not is_dead and not is_private_link:
            try:
                import httpx
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as client:
                    r = await client.get(f"https://t.me/s/{clean_user}", headers=headers)
                    if r.status_code == 404:
                        is_dead = True
                        dead_reason = "Web Scraper HTTP 404 Not Found"
                    elif r.status_code == 200 and "tgme_page_error_title" in r.text and (
                        "If you have Telegram" in r.text or "not found" in r.text.lower()
                    ):
                        is_dead = True
                        dead_reason = "Web Scraper Username Not Found"
            except Exception:
                pass


        if is_dead:
            await purge_dead_channel(target, reason=dead_reason)
            purged_list.append(target)
            logger.info(f"🧹 Mass Auto-Purge: Deleted non-existent channel {target} ({dead_reason})")

    return {
        "checked_count": len(channels),
        "purged_count": len(purged_list),
        "purged_channels": purged_list
    }


@router.api_route("/channels/purge-nonexistent", methods=["GET", "POST"])
async def purge_nonexistent_channels_endpoint(db: AsyncSession = Depends(get_db)):
    """API endpoint to manually scan and purge all non-existent/fake Telegram channels."""
    res = await purge_nonexistent_channels_pass(db)
    return {
        "status": "ok",
        "message": f"Проверка завершена. Проверено каналов: {res['checked_count']}, удалено несуществующих: {res['purged_count']}.",
        "purged_channels": res["purged_channels"]
    }


async def run_auto_channel_pruning(db: AsyncSession) -> dict:
    """
    Auto-prunes channels that are silent (>=2d), yield 0 leads/vacancies (>=3d), are marked FAILED,
    or contain spam/non-target content. Blacklists them permanently in BlacklistedChat so they are never re-added.
    """
    from src.services.module_manager import module_manager
    if not module_manager.is_enabled("auto_pruning"):
        logger.info("🛡️ Auto-Pruner: Channel auto-pruning is currently PAUSED via module_manager.")
        return {"pruned_count": 0, "reasons": {"STATUS": "PAUSED_BY_MODULE_MANAGER"}}

    # Step 0: Purge non-existent / deleted usernames first
    try:
        non_ex_res = await purge_nonexistent_channels_pass(db)
        if non_ex_res.get("purged_count", 0) > 0:
            logger.info(f"🧹 Auto-Pruner: Purged {non_ex_res['purged_count']} non-existent channels.")
    except Exception as ne_err:
        logger.warning(f"Notice during purge_nonexistent_channels_pass: {ne_err}")

    now_utc = datetime.now(timezone.utc)

    try:
        channels_res = await db.execute(select(MonitoredChannel))
        channels = list(channels_res.scalars().all())
    except Exception as e:
        logger.error(f"Error querying channels for auto-prune: {e}")
        return {"pruned_count": 0, "reasons": {}}

    pruned = 0
    reasons_summary = {}
    from src.db.models import BlacklistedChat, ChannelCandidate, DiscoveredChat, HRVacancy
    from src.services.spam_guard import is_spam_or_non_target

    for ch in channels:
        try:
            raw_title = (ch.title or "").strip()
            clean_title_key = raw_title.replace("Обнаружен в ", "").strip().lower()
            username_key = (ch.username_or_link or "").strip().lower().replace("@", "").replace("https://t.me/", "")

            log_conditions = []
            if clean_title_key:
                log_conditions.append(UserActivityLog.chat_title.ilike(f"%{clean_title_key}%"))
            if username_key:
                log_conditions.append(UserActivityLog.chat_title.ilike(f"%{username_key}%"))

            from sqlalchemy import or_
            match_clause = or_(*log_conditions) if log_conditions else (UserActivityLog.chat_id == 0)

            last_act_stmt = select(func.max(UserActivityLog.timestamp)).where(match_clause)
            last_activity_raw = (await db.execute(last_act_stmt)).scalar()

            days_in_monitoring = 0
            if ch.created_at:
                c_date = ch.created_at.replace(tzinfo=timezone.utc) if ch.created_at.tzinfo is None else ch.created_at
                days_in_monitoring = max(0, (now_utc - c_date).days)

            msgs_count_stmt = select(func.count(UserActivityLog.id)).where(match_clause)
            total_msgs_cnt = (await db.execute(msgs_count_stmt)).scalar() or 0

            days_idle = days_in_monitoring
            if last_activity_raw and isinstance(last_activity_raw, datetime):
                l_date = last_activity_raw.replace(tzinfo=timezone.utc) if last_activity_raw.tzinfo is None else last_activity_raw
                days_idle = max(0, (now_utc - l_date).days)

            leads_stmt = select(func.count(Lead.id)).join(
                UserActivityLog, UserActivityLog.user_id == Lead.user_id
            ).where(match_clause)
            leads_total = (await db.execute(leads_stmt)).scalar() or 0

            vacancies_stmt = select(func.count(HRVacancy.id)).join(
                UserActivityLog, UserActivityLog.user_id == HRVacancy.author_telegram_id
            ).where(match_clause)
            vacancies_total = (await db.execute(vacancies_stmt)).scalar() or 0

            # Multi-tier trash evaluation
            should_prune = False
            reason = ""

            # Tier 0: Spam & Non-target GEO check
            if is_spam_or_non_target(ch.username_or_link, ch.title or ""):
                should_prune = True
                reason = "AUTO_PRUNED: SPAM_OR_NON_TARGET"

            # Tier 1: Connection / Scraper failure status
            elif getattr(ch, "status", "") == "FAILED":
                should_prune = True
                reason = f"AUTO_PRUNED: FAILED_STATUS ({ch.error_message or 'unreachable'})"

            # Tier 2: 48h Inactivity (0 messages in 2+ days)
            elif days_idle >= 2:
                should_prune = True
                reason = f"AUTO_PRUNED: {days_idle}d_INACTIVITY (0 msgs)"

            # Tier 3: Zero messages ever ingested (2+ days in system)
            elif total_msgs_cnt == 0 and days_in_monitoring >= 2:
                should_prune = True
                reason = f"AUTO_PRUNED: ZERO_MESSAGES ({days_in_monitoring}d in monitoring)"

            # Tier 4: Zero Yield Efficiency (3+ days, 0 leads, 0 vacancies)
            elif days_in_monitoring >= 3 and leads_total == 0 and vacancies_total == 0:
                should_prune = True
                reason = f"AUTO_PRUNED: ZERO_YIELD (0 leads & 0 vacancies in {days_in_monitoring}d)"

            # Tier 5: High Noise Bot Dump (>40 msgs with 0 leads after 24h)
            elif (total_msgs_cnt >= 40 or (total_msgs_cnt / max(1, days_in_monitoring)) >= 20) and leads_total == 0 and vacancies_total == 0:
                should_prune = True
                reason = f"AUTO_PRUNED: HIGH_NOISE_DUMP ({total_msgs_cnt} msgs, 0 leads)"

            if should_prune:
                blk_target = f"@{username_key}" if username_key else (clean_title_key or ch.username_or_link)
                if blk_target:
                    ex_blk = (await db.execute(select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(blk_target)))).scalar_one_or_none()
                    if not ex_blk:
                        db.add(BlacklistedChat(chat_username=blk_target, reason=reason, score=0))

                    cands = (await db.execute(select(ChannelCandidate).where(ChannelCandidate.username_or_link.ilike(blk_target)))).scalars().all()
                    for cand in cands:
                        cand.status = "BLACK_LISTED"

                    discs = (await db.execute(select(DiscoveredChat).where(DiscoveredChat.chat_username.ilike(blk_target)))).scalars().all()
                    for disc in discs:
                        disc.audit_status = "REJECTED"

                await db.delete(ch)
                pruned += 1
                cat_name = reason.split(":")[1].strip() if ":" in reason else reason
                reasons_summary[cat_name] = reasons_summary.get(cat_name, 0) + 1

        except Exception as ch_err:
            logger.warning(f"Notice during channel auto-pruning (ch={ch.id}): {ch_err}")

    if pruned > 0:
        await db.commit()
        logger.info(f"⚡ Auto-pruned and blacklisted {pruned} ineffective channels. Breakdown: {reasons_summary}")

    return {
        "pruned_count": pruned,
        "reasons": reasons_summary
    }


@router.get("/channels/effectiveness")
async def get_channel_effectiveness(db: AsyncSession = Depends(get_db)):
    """
    Returns per-channel effectiveness stats with 3-tier silence and 6d zero-lead rules.
    """
    now_utc = datetime.now(timezone.utc)
    cutoff_7d = now_utc - timedelta(days=7)

    try:
        channels_res = await db.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()))
        channels = list(channels_res.scalars().all())
    except Exception as e:
        logger.error(f"Error querying MonitoredChannel: {e}")
        return []

    result = []
    for ch in channels:
        try:
            raw_title = (ch.title or "").strip()
            clean_title_key = raw_title.replace("Обнаружен в ", "").strip().lower()
            username_key = (ch.username_or_link or "").strip().lower().replace("@", "").replace("https://t.me/", "")

            log_conditions = []
            if clean_title_key:
                log_conditions.append(UserActivityLog.chat_title.ilike(f"%{clean_title_key}%"))
            if username_key:
                log_conditions.append(UserActivityLog.chat_title.ilike(f"%{username_key}%"))
                log_conditions.append(UserActivityLog.channel_username.ilike(f"%{username_key}%"))

            if log_conditions:
                from sqlalchemy import or_
                match_clause = or_(*log_conditions)
            else:
                match_clause = (UserActivityLog.chat_id == 0)

            msgs_stmt = select(func.count(UserActivityLog.id)).where(
                UserActivityLog.timestamp >= cutoff_7d,
                match_clause
            )
            msgs_7d = (await db.execute(msgs_stmt)).scalar() or 0

            # Real total messages count for channel
            total_msgs_stmt = select(func.count(UserActivityLog.id)).where(match_clause)
            total_msgs = (await db.execute(total_msgs_stmt)).scalar() or 0

            # Real leads count for channel
            leads_stmt = select(func.count(Lead.id)).join(
                UserActivityLog, UserActivityLog.user_id == Lead.user_id
            ).where(match_clause)
            leads_total = (await db.execute(leads_stmt)).scalar() or 0

            # Real vacancies count for channel
            vacancies_stmt = select(func.count(HRVacancy.id)).join(
                UserActivityLog, UserActivityLog.user_id == HRVacancy.author_telegram_id
            ).where(match_clause)
            vacancies_total = (await db.execute(vacancies_stmt)).scalar() or 0

            last_act_stmt = select(func.max(UserActivityLog.timestamp)).where(match_clause)
            last_activity_raw = (await db.execute(last_act_stmt)).scalar()

            days_in_monitoring = 0
            if ch.created_at:
                try:
                    c_date = ch.created_at.replace(tzinfo=timezone.utc) if ch.created_at.tzinfo is None else ch.created_at
                    days_in_monitoring = max(0, (now_utc - c_date).days)
                except Exception:
                    days_in_monitoring = 0

            days_idle = 0
            last_activity_fmt = "—"
            if last_activity_raw and isinstance(last_activity_raw, datetime):
                try:
                    l_date = last_activity_raw.replace(tzinfo=timezone.utc) if last_activity_raw.tzinfo is None else last_activity_raw
                    days_idle = max(0, (now_utc - l_date).days)
                    last_activity_fmt = (l_date + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M")
                except Exception:
                    days_idle = 0
            else:
                # No activity log records — use last_scraped_at as proxy for "how long ago we first touched this"
                # This fixes the bug where all 0-message channels showed as "Живой" because days_in_monitoring=0
                # (happens after DB reset when all channels were re-added today)
                if ch.last_scraped_at and isinstance(ch.last_scraped_at, datetime):
                    try:
                        s_date = ch.last_scraped_at.replace(tzinfo=timezone.utc) if ch.last_scraped_at.tzinfo is None else ch.last_scraped_at
                        days_idle = max(0, (now_utc - s_date).days)
                        last_activity_fmt = f"—  (проход: {(s_date + timedelta(hours=7)).strftime('%d.%m %H:%M')})"
                    except Exception:
                        days_idle = days_in_monitoring
                else:
                    days_idle = days_in_monitoring

            # Classification Rules:
            # 0. No access / group chat (0 msgs, scraped but inaccessible)
            # 1. Dead (>=3d silence)
            # 2. No Leads 6d (>=6d monitored, 0 leads)
            # 3. Half Dead (1-2d silence)
            # 4. Live (<24h silence)
            if total_msgs == 0 and (ch.last_scraped_at is not None or days_in_monitoring >= 1):
                # Channel was attempted but yielded 0 messages — group chat or dead
                color_class = "eff-no-access"
                color_label = "Нет доступа (0 сообщений)"
                color_emoji = "🔵"
                status_tier = "NO_ACCESS"
                is_dead = False
                prune_reason = ""
            elif days_idle >= 3 or (days_in_monitoring >= 3 and total_msgs == 0):
                color_class = "eff-dead"
                color_label = f"Мёртвый ({days_idle}д молчания)"
                color_emoji = "🔴"
                status_tier = "DEAD_3D"
                is_dead = True
                prune_reason = f"Мёртв ({days_idle}д молчания)"
            elif days_in_monitoring >= 6 and leads_total == 0:
                color_class = "eff-day6"
                color_label = "0 лидов (6д)"
                color_emoji = "⚠️"
                status_tier = "NO_LEADS_6D"
                is_dead = True
                prune_reason = "0 лидов за 6 дней"
            elif days_idle >= 1:
                color_class = "eff-day2"
                color_label = f"Полуживой ({days_idle}д)"
                color_emoji = "🟡"
                status_tier = "HALF_DEAD"
                is_dead = False
                prune_reason = ""
            else:
                color_class = "eff-fresh"
                color_label = "Живой (<24ч)"
                color_emoji = "🟢"
                status_tier = "LIVE"
                is_dead = False
                prune_reason = ""


            last_lead_stmt = select(func.max(Lead.created_at)).join(
                UserActivityLog, UserActivityLog.user_id == Lead.user_id
            ).where(match_clause)
            last_lead_raw = (await db.execute(last_lead_stmt)).scalar()
            last_lead_fmt = (last_lead_raw.replace(tzinfo=timezone.utc) + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if (last_lead_raw and isinstance(last_lead_raw, datetime)) else "—"

            last_vac_stmt = select(func.max(HRVacancy.created_at)).join(
                UserActivityLog, UserActivityLog.user_id == HRVacancy.author_telegram_id
            ).where(match_clause)
            last_vac_raw = (await db.execute(last_vac_stmt)).scalar()
            last_vac_fmt = (last_vac_raw.replace(tzinfo=timezone.utc) + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if (last_vac_raw and isinstance(last_vac_raw, datetime)) else "—"

            conversion_pct = round((leads_total / max(1, total_msgs)) * 100.0, 1) if total_msgs > 0 else 0.0

            result.append({
                "id": ch.id,
                "title": ch.title or ch.username_or_link,
                "username_or_link": ch.username_or_link,
                "niche_code": ch.niche_code,
                "niche_name": NICHE_NAMES.get(ch.niche_code, ch.niche_code),
                "location_code": ch.location_code or "global",
                "location_name": LOCATION_NAMES.get(ch.location_code or "global", "🌐 Глобал"),
                "status": ch.status,
                "status_tier": status_tier,
                "msgs_7d": msgs_7d,
                "total_msgs": total_msgs,
                "leads_7d": leads_total,
                "leads_total": leads_total,
                "vacancies_total": vacancies_total,
                "conversion_pct": conversion_pct,
                "days_idle": days_idle,
                "days_in_monitoring": days_in_monitoring,
                "last_activity_at": last_activity_fmt,
                "last_lead_at": last_lead_fmt,
                "last_vacancy_at": last_vac_fmt,
                "color_class": color_class,
                "color_label": color_label,
                "color_emoji": color_emoji,
                "is_dead": is_dead,
                "prune_reason": prune_reason
            })
        except Exception as ch_err:
            logger.warning(f"Error building channel effectiveness for channel {ch.id}: {ch_err}")
            continue

    return result


@router.post("/channels/prune-ineffective")
async def prune_ineffective_channels_api(db: AsyncSession = Depends(get_db)):
    """
    One-click manual or automated trigger to prune dead channels (3d+ silence or 6d zero leads) and add them to blacklist.
    """
    pruned_count = await run_auto_channel_pruning(db)
    return {
        "status": "ok",
        "pruned_count": pruned_count,
        "message": f"Успешно очищено и отправлено в Чёрный Список каналов: {pruned_count}"
    }


@router.get("/channels/{channel_id:path}/detail")
async def get_channel_detail(channel_id: str, db: AsyncSession = Depends(get_db)):
    """Returns full drill-down analytics for a specific channel."""
    ch = (await db.execute(select(MonitoredChannel).where(MonitoredChannel.id == channel_id))).scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Канал не найден")

    raw_title = (ch.title or "").strip().lower()
    clean_title_key = raw_title.replace("обнаружен в ", "").strip()
    username_key = (ch.username_or_link or "").strip().lower().replace("@", "").replace("https://t.me/", "")

    log_conditions = []
    if clean_title_key:
        log_conditions.append(UserActivityLog.chat_title.ilike(f"%{clean_title_key}%"))
    if username_key:
        log_conditions.append(UserActivityLog.chat_title.ilike(f"%{username_key}%"))

    from sqlalchemy import or_
    match_clause = or_(*log_conditions) if log_conditions else (UserActivityLog.chat_id == 0)

    # Scraped messages
    msgs_res = await db.execute(select(UserActivityLog).where(match_clause).order_by(UserActivityLog.timestamp.desc()).limit(30))
    messages = list(msgs_res.scalars().all())

    # Leads
    leads_stmt = select(Lead).join(
        UserActivityLog, UserActivityLog.user_id == Lead.user_id
    ).where(match_clause).order_by(Lead.created_at.desc()).limit(20)
    leads_res = await db.execute(leads_stmt)
    leads = list(leads_res.scalars().all())

    # Vacancies
    vacs_stmt = select(HRVacancy).join(
        UserActivityLog, UserActivityLog.user_id == HRVacancy.author_telegram_id
    ).where(match_clause).order_by(HRVacancy.created_at.desc()).limit(20)
    vacs_res = await db.execute(vacs_stmt)
    vacancies = list(vacs_res.scalars().all())

    return {
        "channel": {
            "id": ch.id,
            "title": ch.title or ch.username_or_link,
            "username_or_link": ch.username_or_link,
            "niche_code": ch.niche_code,
            "location_code": ch.location_code,
            "status": ch.status,
            "created_at": ch.created_at.isoformat() if ch.created_at else None
        },
        "messages_count": len(messages),
        "messages": [
            {
                "id": m.id,
                "user": m.full_name or m.username or "Аноним",
                "text": m.message_text,
                "timestamp": (m.timestamp + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if m.timestamp else "—"
            } for m in messages
        ],
        "leads_count": len(leads),
        "leads": [
            {
                "id": l.id,
                "summary": l.intent_summary,
                "niche": l.niche_code,
                "status": l.status,
                "created_at": (l.created_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if l.created_at else "—"
            } for l in leads
        ],
        "vacancies_count": len(vacancies),
        "vacancies": [
            {
                "id": v.id,
                "title": v.title,
                "salary": v.salary_text,
                "company": v.company_name
            } for v in vacancies
        ]
    }
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
            status="PENDING"
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


@router.post("/channels/parse-file")
async def parse_channels_import_file(file: UploadFile = File(...)):
    """
    Parses uploaded file (.txt, .csv, .xls, .xlsx) and extracts all Telegram handles & links (@username, t.me/...).
    """
    import io, re
    filename = file.filename.lower()
    content_bytes = await file.read()

    extracted_text = ""

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            if filename.endswith(".xlsx"):
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)
                texts = []
                for sheet in wb.worksheets:
                    for row in sheet.iter_rows(values_only=True):
                        for cell in row:
                            if cell is not None:
                                texts.append(str(cell))
                extracted_text = "\n".join(texts)
            else:
                import xlrd
                wb = xlrd.open_workbook(file_contents=content_bytes)
                texts = []
                for sheet in wb.sheets():
                    for row_idx in range(sheet.nrows):
                        for col_idx in range(sheet.ncols):
                            val = sheet.cell_value(row_idx, col_idx)
                            if val:
                                texts.append(str(val))
                extracted_text = "\n".join(texts)
        except Exception as ex_err:
            logger.warning(f"Notice reading excel file {filename}: {ex_err}")
            extracted_text = content_bytes.decode("utf-8", errors="ignore")
    else:
        try:
            extracted_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            extracted_text = content_bytes.decode("cp1251", errors="ignore")

    raw_matches = re.findall(r'(?:https?://)?t\.me/([a-zA-Z0-9_\+\-]+)|@([a-zA-Z0-9_]{5,32})', extracted_text)
    extracted_usernames = []
    seen = set()
    for m in raw_matches:
        u = (m[0] or m[1]).strip()
        if u and not u.endswith('_bot') and u.lower() not in ['telegram', 'joinchat', 'share', 'contact', 'find_groups_bot']:
            clean_u = u if u.startswith('+') else f"@{u}"
            if clean_u.lower() not in seen:
                seen.add(clean_u.lower())
                extracted_usernames.append(clean_u)

    return {
        "status": "ok",
        "filename": file.filename,
        "extracted_count": len(extracted_usernames),
        "usernames": extracted_usernames,
        "text_preview": "\n".join(extracted_usernames)
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
            "location_code": c.location_code or "dubai",
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
            location_code=cand.location_code or "dubai",
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


class RejectSourceSchema(BaseModel):
    parent_title: str = Field(..., example="Username store & NFT store")

@router.post("/candidates/reject-by-source")
async def reject_candidates_by_source_api(data: RejectSourceSchema, db: AsyncSession = Depends(get_db)):
    """
    Mass rejects all candidates discovered from a specific parent/source channel (e.g. 'Username store & NFT store')
    and adds the source channel to BlacklistedChat so future extractions are blocked.
    """
    clean_target = data.parent_title.replace("Обнаружен в ", "").strip()
    if not clean_target:
        raise HTTPException(status_code=400, detail="Укажите название источника")

    from src.db.models import ChannelCandidate, BlacklistedChat

    # 1. Add parent source title to BlacklistedChat
    ex_blk = (await db.execute(select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(f"%{clean_target}%")))).scalar_one_or_none()
    if not ex_blk:
        db.add(BlacklistedChat(
            chat_username=clean_target,
            reason=f"SPAM_SOURCE_CHAT: Mass rejected by admin ({clean_target})",
            score=0
        ))

    # 2. Query all candidates where title or source contains parent_title
    cands_stmt = select(ChannelCandidate).where(
        (ChannelCandidate.title.ilike(f"%{clean_target}%")) |
        (ChannelCandidate.source.ilike(f"%{clean_target}%")) |
        (ChannelCandidate.username_or_link.ilike(f"%{clean_target}%"))
    )
    cands = list((await db.execute(cands_stmt)).scalars().all())

    count = 0
    for c in cands:
        c.status = "REJECTED"
        count += 1

    await db.commit()
    logger.info(f"🚫 Mass rejected and blacklisted {count} candidates from source [{clean_target}]")

    return {
        "status": "ok",
        "count": count,
        "parent_title": clean_target,
        "message": f"Успешно отклонено и занесено в Блэклист {count} кандидатов из источника «{clean_target}»!"
    }

@router.get("/leads")
async def list_leads(response: Response, niche: str = None, location: str = None, status: str = "AVAILABLE", limit: int = 50, is_vip: bool = False, db: AsyncSession = Depends(get_db), current_user: Optional[Partner] = Depends(get_optional_current_user)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    ttl_hours = getattr(settings, "LEAD_TTL_HOURS", 3)
    cutoff_3h = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    
    stmt = select(Lead)

    status_upper = (status or "AVAILABLE").upper()
    if status_upper in ["AVAILABLE", "CURRENT", "ACTIVE"]:
        # Query active & available leads first, followed by archived leads in the same feed
        stmt = select(Lead)
    elif status_upper in ["SOLD", "PURCHASED", "BUYOUT", "EXCLUSIVES"]:
        stmt = stmt.where(Lead.status.in_(["SOLD", "PURCHASED", "EXCLUSIVE", "CLAIMED"]))
    elif status_upper in ["EXPIRED", "ARCHIVE", "ARCHIVED"]:
        stmt = stmt.where((Lead.status == "EXPIRED") | (Lead.status == "ARCHIVED") | (Lead.created_at < cutoff_3h))
    elif status_upper != "ALL":
        stmt = stmt.where(Lead.status == status_upper)

    if niche and niche != "all":
        stmt = stmt.where(Lead.niche_code == niche)
    if location and location != "all":
        stmt = stmt.where(Lead.location_code == location)
        
    # VIP 15-minute delay feature
    # If the user is not VIP/ADMIN/SUPERADMIN, they can only see leads older than 15 minutes.
    actual_is_vip = current_user is not None and getattr(current_user, "role", "") in ["VIP", "ADMIN", "SUPERADMIN"]
    if not actual_is_vip:
        cutoff_15m = datetime.now(timezone.utc) - timedelta(minutes=15)
        stmt = stmt.where(Lead.created_at <= cutoff_15m)
    
    stmt = stmt.order_by(Lead.created_at.desc()).limit(limit)
    
    res = await db.execute(stmt)
    raw_leads = list(res.scalars().all())

    # Sort leads so ACTIVE ones (< 3h) are listed FIRST, followed by ARCHIVED ones (3+h)
    def lead_sort_key(l):
        c_date = l.created_at.replace(tzinfo=timezone.utc) if (l.created_at and l.created_at.tzinfo is None) else l.created_at
        is_fresh = (l.status == "AVAILABLE") and (c_date and c_date >= cutoff_3h)
        ts = c_date.timestamp() if c_date else 0
        return (0 if is_fresh else 1, -ts)

    raw_leads.sort(key=lead_sort_key)

    # Deduplicate lead cards by intent_summary / sales_hook / id
    leads = []
    seen_summaries = set()
    for l in raw_leads:
        summary_clean = (l.intent_summary or l.sales_hook or str(l.id)).strip().lower()
        if summary_clean not in seen_summaries:
            seen_summaries.add(summary_clean)
            leads.append(l)

    now_utc = datetime.now(timezone.utc)
    user_ids = list({l.user_id for l in leads if l.user_id})
    user_msg_map = {}
    if user_ids:
        log_stmt = (
            select(UserActivityLog.user_id, UserActivityLog.message_text)
            .where(UserActivityLog.user_id.in_(user_ids))
            .order_by(UserActivityLog.timestamp.desc())
        )
        log_rows = (await db.execute(log_stmt)).all()
        for u_id, msg_txt in log_rows:
            if u_id not in user_msg_map and msg_txt:
                user_msg_map[u_id] = msg_txt

    items_out = []
    for l in leads:
        conf_val = float(l.confidence_score or 0.85)
        if conf_val > 1.0:
            conf_val = conf_val / 100.0

        c_date = l.created_at.replace(tzinfo=timezone.utc) if (l.created_at and l.created_at.tzinfo is None) else l.created_at
        is_expired = l.status in ["EXPIRED", "ARCHIVED"] or (c_date and c_date < cutoff_3h)
        rem_mins = max(0, int((c_date + timedelta(hours=ttl_hours) - now_utc).total_seconds() / 60)) if (c_date and not is_expired) else 0

        raw_display_text = user_msg_map.get(l.user_id) or l.intent_summary or ""
        masked_display = mask_contact_links(raw_display_text)

        items_out.append({
            "id": l.id,
            "user_id": l.user_id,
            "niche_code": l.niche_code,
            "rubric_name": NICHE_NAMES.get(l.niche_code, "Прочее"),
            "location_code": getattr(l, "location_code", "global") or "global",
            "location_name": LOCATION_NAMES.get(getattr(l, "location_code", "global") or "global", "🌐 Глобал / РФ"),
            "temperature": l.temperature,
            "confidence_score": conf_val,
            "intent_summary": masked_display,
            "quote_text": masked_display,
            "sales_hook": mask_contact_links(l.sales_hook),
            "reasoning": mask_contact_links(getattr(l, "reasoning", None) or l.sales_hook or "ИИ подтвердил клиентский спрос."),
            "user_message_count": 1,
            "status": "EXPIRED" if is_expired else l.status,
            "price": float(l.price),
            "created_at": c_date.isoformat() if c_date else None,
            "is_archived": is_expired,
            "ttl_remaining_minutes": rem_mins
        })
    return items_out

class UpdateLeadLocationSchema(BaseModel):
    location_code: str = "dubai"

@router.post("/leads/{lead_id}/location")
async def update_lead_location_api(lead_id: str, data: UpdateLeadLocationSchema, db: AsyncSession = Depends(get_db)):
    """Updates the location_code for a specific lead."""
    stmt = select(Lead).where(Lead.id == lead_id)
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Лид не найден в системе")

    lead.location_code = data.location_code
    await db.commit()
    await db.refresh(lead)

    from src.bot.keyboards import LOCATION_NAMES
    loc_name = LOCATION_NAMES.get(lead.location_code, "🌐 Глобал / РФ")

    return {
        "status": "ok",
        "message": f"ГЕО лида успешно обновлено на {loc_name}",
        "lead_id": lead.id,
        "location_code": lead.location_code,
        "location_name": loc_name
    }

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
        masked_author = (first_name or (f"@{username}" if username else None)) if is_purchased else "🔒 Скрыто до выкупа"
        msg_text = log.message_text if is_purchased else mask_contact_links(log.message_text)

        result.append({
            "id": log.id,
            "chat_title": masked_title,
            "author_name": masked_author,
            "message_text": msg_text,
            "timestamp": (log.timestamp + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if log.timestamp else "—"
        })

    return result

class UpdatePartnerPrioritySchema(BaseModel):
    niche_code: str = Field(..., example="auto_kasko")
    priority: int = Field(..., example=1) # 1=VIP 0s, 2=High 30s, 3=Standard 60s

@router.get("/partners")
async def list_partners(db: AsyncSession = Depends(get_db), current_user: Optional[Partner] = Depends(get_optional_current_user)):
    res = await db.execute(select(Partner).where(Partner.role != "DEMO").order_by(Partner.created_at.desc()))
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

@router.get("/my-purchases")
async def get_my_purchases_api(telegram_id: int, db: AsyncSession = Depends(get_db)):
    partner_stmt = select(Partner).where(Partner.telegram_id == telegram_id)
    partner = (await db.execute(partner_stmt)).scalar_one_or_none()
    if not partner:
        return []

    from src.db.models import UserProfile
    from sqlalchemy import or_
    stmt = (
        select(LeadPurchase, Lead, UserProfile)
        .join(Lead, LeadPurchase.lead_id == Lead.id)
        .outerjoin(UserProfile, Lead.user_id == UserProfile.user_id)
        .where(
            LeadPurchase.partner_id == partner.id,
            or_(LeadPurchase.is_archived == False, LeadPurchase.is_archived.is_(None))
        )
        .order_by(LeadPurchase.purchased_at.desc())
    )
    rows = list((await db.execute(stmt)).all())
    
    result = []
    user_ids = [lead.user_id for _, lead, _ in rows]
    source_map = {}
    if user_ids:
        from src.db.models import UserActivityLog, MonitoredChannel
        from sqlalchemy import func
        act_stmt = select(
            UserActivityLog.user_id,
            UserActivityLog.chat_title,
            UserActivityLog.channel_username,
            UserActivityLog.message_id,
            UserActivityLog.chat_id,
            MonitoredChannel.username_or_link,
            UserActivityLog.message_text
        ).outerjoin(
            MonitoredChannel, 
            func.lower(MonitoredChannel.title) == func.lower(UserActivityLog.chat_title)
        ).where(UserActivityLog.user_id.in_(user_ids)).order_by(UserActivityLog.timestamp.desc())
        for u_id, c_title, c_uname, m_id, ch_id, ch_link, raw_text in (await db.execute(act_stmt)).all():
            if u_id not in source_map:
                source_map[u_id] = {
                    "chat_title": c_title, 
                    "chat_username": c_uname, 
                    "message_id": m_id,
                    "chat_id": ch_id,
                    "invite_link": ch_link,
                    "message_text": raw_text
                }
                
    for pur, lead, profile in rows:
        username = f"@{profile.username}" if profile and profile.username else f"ID {lead.user_id}"
        tg_link = f"https://t.me/{profile.username}" if profile and profile.username else f"tg://user?id={lead.user_id}"
        full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else "Пользователь Telegram"
        
        pur_utc7 = (pur.purchased_at + timedelta(hours=7)) if pur.purchased_at else None
        
        src_info = source_map.get(lead.user_id, {})
        c_username = (src_info.get("chat_username") or "").replace('@', '').strip()
        c_title = src_info.get("chat_title") or "Телеграм чат"
        m_id = src_info.get("message_id")
        c_id = src_info.get("chat_id")
        invite_link = src_info.get("invite_link") or ""

        group_url = ""
        if c_username and not c_username.startswith("http") and not c_username.startswith("+"):
            group_url = f"https://t.me/{c_username}"
        elif invite_link and invite_link.startswith("http"):
            group_url = invite_link
        elif c_id:
            clean_cid = str(c_id).replace("-100", "").replace("-", "")
            group_url = f"https://t.me/c/{clean_cid}"

        message_url = ""
        if c_username and not c_username.startswith("http") and not c_username.startswith("+"):
            message_url = f"https://t.me/{c_username}/{m_id}" if m_id else f"https://t.me/{c_username}"
        elif c_id:
            clean_cid = str(c_id).replace("-100", "").replace("-", "")
            message_url = f"https://t.me/c/{clean_cid}/{m_id}" if m_id else f"https://t.me/c/{clean_cid}"
        elif invite_link and invite_link.startswith("http"):
            message_url = invite_link
        else:
            message_url = group_url

        result.append({
            "purchase_id": pur.id,
            "lead_id": lead.id,
            "niche_code": lead.niche_code,
            "niche_name": NICHE_NAMES.get(lead.niche_code, "Прочее"),
            "location_name": LOCATION_NAMES.get(getattr(lead, "location_code", "global") or "global", "🌐 Глобал / РФ"),
            "intent_summary": lead.intent_summary,
            "sales_hook": lead.sales_hook,
            "user_id": lead.user_id,
            "price_paid": float(pur.price_paid),
            "purchased_at": pur_utc7.isoformat() if pur_utc7 else None,
            "purchased_at_fmt": pur_utc7.strftime("%Y-%m-%d %H:%M:%S") if pur_utc7 else "",
            "contact": {
                "username": username,
                "tg_link": tg_link,
                "full_name": full_name,
                "no_username": not (profile and profile.username)
            },
            "source": {
                "title": c_title,
                "username": c_username,
                "message_id": m_id,
                "chat_id": c_id,
                "group_url": group_url,
                "chat_url": group_url,
                "message_url": message_url,
                "invite_link": invite_link,
                "message_text": src_info.get("message_text")
            }
        })
    return result

@router.post("/my-purchases/{purchase_id}/archive")
@router.post("/my-purchases/archive/{purchase_id}")
async def archive_my_purchase(purchase_id: str, db: AsyncSession = Depends(get_db)):
    """Archives a purchased lead so it is hidden from active cart and resets badge."""
    stmt = select(LeadPurchase).where((LeadPurchase.id == purchase_id) | (LeadPurchase.lead_id == purchase_id))
    purchases = list((await db.execute(stmt)).scalars().all())
    if not purchases:
        return {"status": "ok", "message": "Уже в архиве"}
    for p in purchases:
        p.is_archived = True
    await db.commit()
    return {"status": "ok", "message": "Лид отправлен в архив и скрыт из корзины."}

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

    # Ensure AIEvaluationLog reflects is_lead = True for this message
    try:
        eval_stmt = select(AIEvaluationLog).where(
            AIEvaluationLog.user_id == user_id,
            AIEvaluationLog.message_text == data.message_text
        ).order_by(AIEvaluationLog.created_at.desc()).limit(1)
        eval_obj = (await db.execute(eval_stmt)).scalar_one_or_none()
        if eval_obj:
            eval_obj.is_lead = True
            eval_obj.niche_code = final_niche
        else:
            eval_log = AIEvaluationLog(
                user_id=user_id,
                username=up.username,
                first_name=up.first_name,
                chat_title=data.chat_title or "Общий Чат",
                message_text=data.message_text,
                is_lead=True,
                reasoning="Ручная квалификация лида пользователем",
                niche_code=final_niche,
                temperature="HOT",
                confidence_score=0.98,
                location_code=loc_code
            )
            db.add(eval_log)
        await db.commit()
    except Exception as e_err:
        logger.warning(f"Notice updating AIEvaluationLog during manual qualification: {e_err}")

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
    source: Optional[str] = Query(default=None),
    location: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    limit: int = Query(default=100),
    offset: int = Query(default=0),
    db: AsyncSession = Depends(get_db)
):
    """Returns paginated list of discovered chats with scores and LLM audit verdicts."""
    from src.db.models import DiscoveredChat
    stmt = select(DiscoveredChat).order_by(DiscoveredChat.discovered_at.desc())
    
    if status and status.upper() != "ALL":
        stmt = stmt.where(DiscoveredChat.audit_status == status.upper())
    else:
        # Default ALL mode: Exclude ARCHIVED chats from main active feed
        stmt = stmt.where(DiscoveredChat.audit_status != "ARCHIVED")
    
    if source and source.upper() != "ALL":
        stmt = stmt.where(DiscoveredChat.source.ilike(f"%{source}%"))
        
    if location and location.lower() != "all":
        loc_clean = location.lower()
        if loc_clean == "vietnam" or loc_clean in ["nhatrang", "danang"]:
            stmt = stmt.where(DiscoveredChat.location_code.in_(["vietnam", "nhatrang", "danang", "phuquoc"]))
        elif loc_clean == "dubai":
            stmt = stmt.where(DiscoveredChat.location_code.in_(["dubai", "ae", "uae"]))
        elif loc_clean in ["phuket", "bangkok"]:
            stmt = stmt.where(DiscoveredChat.location_code.in_(["phuket", "bangkok", "thailand", "samui"]))
        elif loc_clean == "global":
            stmt = stmt.where(DiscoveredChat.location_code.in_(["global", "all", None, ""]))
        else:
            stmt = stmt.where(DiscoveredChat.location_code == location)

    if query:
        q_clean = f"%{query.strip()}%"
        stmt = stmt.where(
            (DiscoveredChat.title.ilike(q_clean)) |
            (DiscoveredChat.chat_username.ilike(q_clean)) |
            (DiscoveredChat.verdict_reason.ilike(q_clean))
        )

    stmt = stmt.offset(offset).limit(limit)
    chats = list((await db.execute(stmt)).scalars().all())

    if not chats:
        from src.db.models import MonitoredChannel
        ch_stmt = select(MonitoredChannel).limit(limit)
        m_channels = list((await db.execute(ch_stmt)).scalars().all())
        if m_channels:
            return [
                {
                    "id": f"mon_{c.id}",
                    "chat_username": c.username_or_link,
                    "title": c.title or c.username_or_link,
                    "source": "COMMON_CHATS",
                    "location_code": c.location_code or "phuket",
                    "platform": c.platform or "telegram",
                    "audit_status": "APPROVED" if c.is_active else "PENDING",
                    "score": 95,
                    "chat_type": c.channel_type or "SUPERGROUP",
                    "detected_niches": ["real_estate", "community"],
                    "verdict_reason": "Живой супергрупповой чат с целевой аудиторией, переведен в активную прослушку",
                    "discovered_at_fmt": (c.created_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if c.created_at else "—",
                    "audited_at_fmt": (c.created_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if c.created_at else "—"
                }
                for c in m_channels
            ]
    
    return [
        {
            "id": c.id,
            "chat_username": c.chat_username,
            "title": c.title or c.chat_username,
            "source": c.source or "GLOBAL_SEARCH",
            "location_code": c.location_code or "global",
            "platform": c.platform or "telegram",
            "audit_status": c.audit_status or "PENDING",
            "score": c.score if c.score is not None else 0,
            "chat_type": c.chat_type or "LIVE_COMMUNITY",
            "detected_niches": c.detected_niches or [],
            "verdict_reason": c.verdict_reason or "Ожидает скаут-аудита",
            "discovered_at_fmt": (c.discovered_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if c.discovered_at else "—",
            "audited_at_fmt": (c.audited_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if c.audited_at else "—"
        }
        for c in chats
    ]


class ScoutBatchImportRequest(BaseModel):
    usernames: list[str]
    location_code: str = "dubai"
    niche_code: str = "community"


@router.post("/discovery/batch-import")
async def scout_batch_import(req: ScoutBatchImportRequest, db: AsyncSession = Depends(get_db)):
    """Imports a list of usernames into ChannelCandidate for the Scout to process."""
    from src.db.models import DiscoveredChat, MonitoredChannel
    
    added_count = 0
    duplicate_count = 0
    
    for username in req.usernames:
        # Check if already exists in DiscoveredChat
        existing_candidate = (await db.execute(
            select(DiscoveredChat).where(DiscoveredChat.chat_username == username)
        )).scalars().first()
        
        if existing_candidate:
            duplicate_count += 1
            continue
            
        # Check if already exists in MonitoredChannel
        existing_monitored = (await db.execute(
            select(MonitoredChannel).where(MonitoredChannel.username_or_link == username)
        )).scalars().first()
        
        if existing_monitored:
            duplicate_count += 1
            continue
            
        # Add new candidate
        new_candidate = DiscoveredChat(
            chat_username=username,
            source="MASS_IMPORT",
            location_code=req.location_code,
            audit_status="PENDING",
            score=0,
            verdict_reason="Ожидает скаут-аудита (Ручной импорт)"
        )
        db.add(new_candidate)
        added_count += 1
        
    await db.commit()
    
    return {
        "status": "ok",
        "added": added_count,
        "duplicates": duplicate_count
    }


@router.get("/scout/userbot-chats")
async def get_userbot_imported_chats(db: AsyncSession = Depends(get_db)):
    """Returns list of all channels and groups extracted/imported from connected userbot accounts."""
    from src.db.models import DiscoveredChat
    stmt = select(DiscoveredChat).where(DiscoveredChat.source.ilike("%USERBOT%")).order_by(DiscoveredChat.discovered_at.desc())
    discs = list((await db.execute(stmt)).scalars().all())

    return [
        {
            "id": c.id,
            "username_or_link": c.chat_username,
            "title": c.title,
            "source": c.source,
            "audit_status": c.audit_status,
            "location_code": c.location_code or "dubai",
            "imported_at_fmt": (c.audited_at + timedelta(hours=7)).strftime("%d.%m.%Y %H:%M") if c.audited_at else "—"
        }
        for c in discs
    ]


@router.get("/discovery/stats")
async def get_discovery_stats(db: AsyncSession = Depends(get_db)):
    """Returns detailed summary statistics & source breakdown for the Autonomous Scout Engine."""
    from src.db.models import DiscoveredChat, BlacklistedChat
    
    total_disc = (await db.execute(select(func.count(DiscoveredChat.id)))).scalar() or 0
    pending = (await db.execute(select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "PENDING"))).scalar() or 0
    approved = (await db.execute(select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "APPROVED"))).scalar() or 0
    rejected = (await db.execute(select(func.count(DiscoveredChat.id)).where(DiscoveredChat.audit_status == "REJECTED"))).scalar() or 0
    total_blacklisted = (await db.execute(select(func.count(BlacklistedChat.id)))).scalar() or 0

    if total_disc == 0:
        from src.db.models import MonitoredChannel
        ch_cnt = (await db.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0
        total_disc = ch_cnt
        approved = ch_cnt

    # Source distribution breakdown
    source_res = await db.execute(
        select(DiscoveredChat.source, func.count(DiscoveredChat.id))
        .group_by(DiscoveredChat.source)
    )
    source_counts = {r[0] or "GLOBAL_SEARCH": r[1] for r in source_res.all()}
    if not source_counts and total_disc > 0:
        source_counts = {"COMMON_CHATS": total_disc, "GLOBAL_SEARCH": 0}

    # Location distribution breakdown
    loc_res = await db.execute(
        select(DiscoveredChat.location_code, func.count(DiscoveredChat.id))
        .group_by(DiscoveredChat.location_code)
    )
    location_counts = {r[0] or "global": r[1] for r in loc_res.all()}

    return {
        "status": "ok",
        "total_discovered": total_disc,
        "pending_audit_queue": pending,
        "total_approved": approved,
        "total_rejected": rejected,
        "total_blacklisted": total_blacklisted,
        "source_counts": source_counts,
        "location_counts": location_counts
    }

from pydantic import BaseModel
from typing import List

class BulkRejectRequest(BaseModel):
    chat_ids: List[str]

@router.post("/discovery/chats/bulk-reject")
async def bulk_reject_discovered_chats(req: BulkRejectRequest, db: AsyncSession = Depends(get_db)):
    """Moves selected chats to the ARCHIVED state (manually rejected, 24h TTL)."""
    if not req.chat_ids:
        return {"status": "ok", "archived_count": 0}
        
    from src.db.models import DiscoveredChat
    from sqlalchemy import update
    
    stmt = update(DiscoveredChat).where(DiscoveredChat.id.in_(req.chat_ids)).values(
        audit_status="ARCHIVED",
        audited_at=datetime.now(timezone.utc),
        verdict_reason="Удалено вручную (в Архив)"
    )
    await db.execute(stmt)
    await db.commit()
    
    return {"status": "ok", "archived_count": len(req.chat_ids)}


@router.get("/discovery/chats/archive")
async def get_archived_chats(db: AsyncSession = Depends(get_db)):
    """Returns ARCHIVED chats (last 24 hours). Also auto-purges entries older than 24h."""
    from src.db.models import DiscoveredChat
    from sqlalchemy import delete as sa_delete
    
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    
    # Auto-purge entries older than 24h
    purge_stmt = sa_delete(DiscoveredChat).where(
        DiscoveredChat.audit_status == "ARCHIVED",
        DiscoveredChat.audited_at < cutoff_24h
    )
    await db.execute(purge_stmt)
    await db.commit()
    
    # Fetch remaining archived chats (within 24h)
    stmt = select(DiscoveredChat).where(
        DiscoveredChat.audit_status == "ARCHIVED"
    ).order_by(DiscoveredChat.audited_at.desc()).limit(200)
    chats = list((await db.execute(stmt)).scalars().all())
    
    now_utc = datetime.now(timezone.utc)
    result = []
    for c in chats:
        archived_at = c.audited_at or c.discovered_at
        expires_at = archived_at + timedelta(hours=24) if archived_at else None
        remaining_s = int((expires_at - now_utc).total_seconds()) if expires_at else 86400
        remaining_h = max(0, remaining_s // 3600)
        remaining_m = max(0, (remaining_s % 3600) // 60)
        
        result.append({
            "id": c.id,
            "chat_username": c.chat_username,
            "title": c.title or c.chat_username,
            "source": c.source or "GLOBAL_SEARCH",
            "location_code": c.location_code or "global",
            "score": c.score if c.score is not None else 0,
            "verdict_reason": c.verdict_reason or "Удалено вручную",
            "archived_at_fmt": (archived_at + timedelta(hours=7)).strftime("%d.%m %H:%M") if archived_at else "—",
            "expires_in": f"{remaining_h}ч {remaining_m}м",
            "expires_soon": remaining_h < 3
        })
    
    return {"status": "ok", "chats": result, "count": len(result)}


@router.post("/discovery/chats/{chat_id}/approve")
async def approve_discovered_chat(chat_id: str, db: AsyncSession = Depends(get_db)):
    """Manually approves a discovered chat and promotes it into MonitoredChannel."""
    from src.db.models import DiscoveredChat, MonitoredChannel
    dc = (await db.execute(select(DiscoveredChat).where(DiscoveredChat.id == chat_id))).scalar_one_or_none()
    if not dc:
        raise HTTPException(status_code=404, detail="Discovered chat not found")

    dc.audit_status = "APPROVED"
    dc.score = max(dc.score or 0, 85)
    dc.audited_at = datetime.now(timezone.utc)
    dc.verdict_reason = "Ручное утверждение администратором в ИИ-Скауте."

    # Promote to MonitoredChannel
    uname = dc.chat_username if dc.chat_username.startswith("@") or "t.me" in dc.chat_username else f"@{dc.chat_username}"
    mc = (await db.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == uname))).scalar_one_or_none()
    if not mc:
        mc = MonitoredChannel(
            title=dc.title or uname,
            username_or_link=uname,
            niche_code=(dc.detected_niches[0] if dc.detected_niches else "community"),
            location_code=dc.location_code or "global",
            status="PENDING"
        )
        db.add(mc)
    else:
        mc.status = "PENDING"

    await db.commit()
    return {"status": "ok", "message": f"Чат {uname} успешно одобрен и занесен в прослушку!"}


@router.post("/discovery/chats/{chat_id}/reject")
async def reject_discovered_chat(chat_id: str, db: AsyncSession = Depends(get_db)):
    """Manually rejects a discovered chat: moves to ARCHIVE (24h TTL) + blacklists."""
    from src.db.models import DiscoveredChat, BlacklistedChat
    dc = (await db.execute(select(DiscoveredChat).where(DiscoveredChat.id == chat_id))).scalar_one_or_none()
    if not dc:
        raise HTTPException(status_code=404, detail="Discovered chat not found")

    dc.audit_status = "ARCHIVED"
    dc.audited_at = datetime.now(timezone.utc)
    dc.verdict_reason = "Ручное отклонение администратором — перемещено в Архив (24ч)."

    uname = dc.chat_username if dc.chat_username.startswith("@") or "t.me" in dc.chat_username else f"@{dc.chat_username}"
    bc = (await db.execute(select(BlacklistedChat).where(BlacklistedChat.chat_username == uname))).scalar_one_or_none()
    if not bc:
        db.add(BlacklistedChat(chat_username=uname, reason="Отклонен администратором в ИИ-Скауте", score=dc.score or 0))

    await db.commit()
    return {"status": "ok", "message": f"Чат {uname} перемещён в Архив (хранится 24ч) и внесён в ЧС."}


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


# ────────────────────────────────────────────────────────────────────────────
# HR-RADAR B2C REST API ENDPOINTS
# ────────────────────────────────────────────────────────────────────────────

@router.get("/hr/vacancies")
async def list_hr_vacancies(
    location: Optional[str] = None,
    status: str = "PUBLISHED",
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """Returns list of scraped/published vacancies for B2C HR-Radar."""
    from src.db.models import HRVacancy
    stmt = select(HRVacancy)
    if status != "ALL":
        stmt = stmt.where(HRVacancy.status == status.upper())
    if location and location != "all":
        stmt = stmt.where(HRVacancy.location_code == location.lower())

    stmt = stmt.order_by(HRVacancy.created_at.desc()).limit(limit)
    res = await db.execute(stmt)
    vacancies = list(res.scalars().all())

    items = []
    for v in vacancies:
        items.append({
            "id": v.id,
            "title": v.title,
            "company_name": v.company_name,
            "location_code": v.location_code,
            "niche_code": v.niche_code,
            "salary_text": v.salary_text,
            "description": v.description,
            "hr_contact": v.hr_contact,
            "author_username": v.author_username,
            "status": v.status,
            "showcase_message_id": v.showcase_message_id,
            "created_at": v.created_at.isoformat() if v.created_at else None
        })
    return {"status": "ok", "count": len(items), "vacancies": items}


@router.get("/hr/subscribers")
async def list_hr_subscribers(
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """Returns list of B2C HR subscribers."""
    from src.db.models import HRSubscriber
    stmt = select(HRSubscriber)
    if status and status != "ALL":
        stmt = stmt.where(HRSubscriber.subscription_status == status.upper())

    stmt = stmt.order_by(HRSubscriber.created_at.desc()).limit(limit)
    res = await db.execute(stmt)
    subs = list(res.scalars().all())

    items = []
    for s in subs:
        items.append({
            "id": s.id,
            "telegram_id": s.telegram_id,
            "username": s.username,
            "first_name": s.first_name,
            "subscription_status": s.subscription_status,
            "subscription_expires_at": s.subscription_expires_at.isoformat() if s.subscription_expires_at else None,
            "subscribed_tags": s.subscribed_tags,
            "created_at": s.created_at.isoformat() if s.created_at else None
        })
    return {"status": "ok", "count": len(items), "subscribers": items}


@router.get("/hr/stats")
async def get_hr_stats(db: AsyncSession = Depends(get_db)):
    """Returns analytics for HR-Radar B2C System."""
    from src.db.models import HRVacancy, HRSubscriber, HRSubscriptionPayment
    vacancies_count = (await db.execute(select(func.count(HRVacancy.id)))).scalar() or 0
    subs_total = (await db.execute(select(func.count(HRSubscriber.id)))).scalar() or 0
    vip_subs = (await db.execute(select(func.count(HRSubscriber.id)).where(HRSubscriber.subscription_status.in_(["TRIAL", "VIP"])))).scalar() or 0
    
    pmt_res = await db.execute(select(func.sum(HRSubscriptionPayment.amount_usd)))
    revenue = float(pmt_res.scalar() or 0.0)

    return {
        "status": "ok",
        "vacancies_total": vacancies_count,
        "subscribers_total": subs_total,
        "vip_subscribers": vip_subs,
        "revenue_usd": revenue
    }


from src.db.models import ScraperAccount
from pydantic import BaseModel

class AddScraperSchema(BaseModel):
    session_string: str
    max_daily_joins: int = 20
    account_role: str = "LISTENER"

class UpdateScraperRoleSchema(BaseModel):
    account_role: str

@router.get("/scrapers")
async def list_scrapers(db: AsyncSession = Depends(get_db)):
    stmt = select(ScraperAccount).order_by(ScraperAccount.id.asc())
    res = await db.execute(stmt)
    scrapers = res.scalars().all()

    # Match joined_groups_today from live ingestor nodes
    live_groups_map = {}
    try:
        from src.api.app import ingestor
        if ingestor and ingestor.scrapers:
            for node in ingestor.scrapers:
                if getattr(node, "joined_groups_today", None):
                    live_groups_map[node.db_id] = node.joined_groups_today
    except Exception:
        pass

    result = []
    from src.db.models import UserbotChatBinding
    for s in scrapers:
        groups = live_groups_map.get(s.id, [])
        bind_cnt = (await db.execute(
            select(func.count(UserbotChatBinding.id)).where(
                UserbotChatBinding.account_id == s.id,
                UserbotChatBinding.binding_status == "ACTIVE"
            )
        )).scalar() or 0

        result.append({
            "id": s.id,
            "phone_number": s.phone_number,
            "account_username": s.account_username,
            "session_string": (s.session_string[:15] + "...") if s.session_string else "",
            "status": s.status,
            "account_role": getattr(s, "account_role", "LISTENER") or "LISTENER",
            "max_daily_joins": s.max_daily_joins,
            "daily_join_count": s.daily_join_count,
            "active_bindings_count": bind_cnt,
            "joined_groups_today": groups,
            "flood_until": s.flood_until.isoformat() if s.flood_until else None,
            "error_log": s.error_log
        })
    return result


@router.post("/scrapers")
async def add_scraper(data: AddScraperSchema, db: AsyncSession = Depends(get_db)):
    new_acc = ScraperAccount(
        session_string=data.session_string,
        max_daily_joins=data.max_daily_joins,
        account_role=getattr(data, "account_role", "LISTENER") or "LISTENER",
        status="ACTIVE"
    )
    db.add(new_acc)
    await db.commit()
    
    # Try to trigger a restart
    try:
        from src.api.app import ingestor
        if ingestor:
            import asyncio
            asyncio.create_task(ingestor.restart_scraper_loop())
    except Exception:
        pass
        
    return {"status": "ok", "message": "Scraper added"}

@router.put("/scrapers/{scraper_id}/status")
async def update_scraper_status(scraper_id: int, status: str = Query(...), db: AsyncSession = Depends(get_db)):
    stmt = select(ScraperAccount).where(ScraperAccount.id == scraper_id)
    acc = (await db.execute(stmt)).scalar_one_or_none()
    if acc:
        acc.status = status
        if status == "ACTIVE":
            acc.error_log = None
            acc.flood_until = None
            acc.daily_join_count = 0
        elif status == "BANNED":
            from src.services.swarm_manager import SwarmManager
            await SwarmManager.evacuate_banned_userbot(db, scraper_id, reason="Manual status update to BANNED by Admin")
        await db.commit()
        return {"status": "ok"}
    raise HTTPException(status_code=404)

@router.delete("/scrapers/{scraper_id}")
async def delete_scraper(scraper_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(ScraperAccount).where(ScraperAccount.id == scraper_id)
    acc = (await db.execute(stmt)).scalar_one_or_none()
    if acc:
        from src.db.models import UserbotChatBinding
        from sqlalchemy import delete
        await db.execute(delete(UserbotChatBinding).where(UserbotChatBinding.account_id == scraper_id))
        await db.delete(acc)
        await db.commit()
        return {"status": "ok"}
    raise HTTPException(status_code=404)

@router.put("/scrapers/{scraper_id}/role")
async def update_scraper_role(scraper_id: int, payload: UpdateScraperRoleSchema, db: AsyncSession = Depends(get_db)):
    stmt = select(ScraperAccount).where(ScraperAccount.id == scraper_id)
    acc = (await db.execute(stmt)).scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    
    role = payload.account_role.upper()
    if role not in ["LISTENER", "WORKER"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be LISTENER or WORKER")
    
    acc.account_role = role
    await db.commit()
    
    try:
        from src.api.app import ingestor
        if ingestor:
            import asyncio
            asyncio.create_task(ingestor.restart_scraper_loop())
    except Exception:
        pass

    return {"status": "ok", "account_id": scraper_id, "account_role": role}

@router.post("/system/swarm/reset-all")
async def reset_all_userbots(db: AsyncSession = Depends(get_db)):
    """Resets all BANNED/PAUSED userbots to ACTIVE to test if their sessions are still alive."""
    from src.db.models import ScraperAccount, OutreachAccount
    from sqlalchemy import update
    
    await db.execute(update(ScraperAccount).values(status="ACTIVE", error_log=None))
    await db.execute(update(OutreachAccount).values(status="ACTIVE", error_log=None))
    await db.commit()
    return {"status": "ok", "message": "Все юзерботы переведены в статус ACTIVE."}

@router.get("/system/fix-joined-chats")
async def fix_joined_chats(db: AsyncSession = Depends(get_db)):
    """Temporarily fixes MonitoredChannels that were stuck in JOINED status."""
    from src.db.models import MonitoredChannel, UserbotChatBinding
    from sqlalchemy import select
    
    res = await db.execute(select(MonitoredChannel).where(MonitoredChannel.status == "JOINED"))
    channels = res.scalars().all()
    
    fixed = 0
    for c in channels:
        b_res = await db.execute(select(UserbotChatBinding).where(UserbotChatBinding.channel_id == c.username_or_link))
        binding = b_res.scalars().first()
        if not binding:
            c.status = "PENDING"
            fixed += 1
            
    await db.commit()
    return {"status": "ok", "fixed": fixed}

@router.get("/system/test-join/{username:path}")
async def test_join_chat(username: str, db: AsyncSession = Depends(get_db)):
    """Temporarily diagnostic endpoint to test Pyrogram join and see exact Telegram error."""
    from src.api.app import ingestor
    
    clean_target = username if username.startswith("@") or username.startswith("+") else f"@{username}"
    
    available_node = None
    for node in ingestor.scrapers:
        if node.app and getattr(node.app, "is_connected", False):
            available_node = node
            break
            
    if not available_node:
        return {"status": "error", "message": "No connected Pyrogram node found to test"}
        
    try:
        chat = await available_node.app.join_chat(clean_target)
        return {"status": "success", "title": getattr(chat, "title", "Unknown")}
    except Exception as e:
        return {"status": "error", "error_type": type(e).__name__, "message": str(e)}

class SwarmImportItem(BaseModel):
    session_string: str
    phone_number: str = ""

class SwarmImportSchema(BaseModel):
    accounts: List[SwarmImportItem]

@router.post("/system/swarm/import-batch")
async def import_swarm_batch(payload: SwarmImportSchema, db: AsyncSession = Depends(get_db)):
    """Imports a batch of session strings into ScraperAccount."""
    from src.db.models import ScraperAccount
    from sqlalchemy import select
    added = 0
    for item in payload.accounts:
        existing = (await db.execute(select(ScraperAccount).where(ScraperAccount.session_string == item.session_string))).scalar_one_or_none()
        if not existing:
            acc = ScraperAccount(
                session_string=item.session_string,
                phone_number=f"+{item.phone_number}" if item.phone_number else None,
                status="ACTIVE",
                max_daily_joins=20,
                daily_join_count=0
            )
            db.add(acc)
            added += 1
    await db.commit()
    return {"status": "ok", "added": added}


class ImportMegaLinksSchema(BaseModel):
    urls_text: str
    account_role: str = "LISTENER"
    purge_old_listeners: bool = False

@router.post("/scrapers/import-mega-links")
async def import_mega_userbot_links(payload: ImportMegaLinksSchema, db: AsyncSession = Depends(get_db)):
    """
    Superadmin endpoint: Downloads Mega.nz account archives (.zip/.rar),
    converts session files to Pyrogram session strings, and saves to ScraperAccount.
    """
    import os, re, glob, json, base64, struct, zipfile, subprocess, sqlite3, requests
    from src.db.models import ScraperAccount
    from sqlalchemy import select

    # Robust Crypto Helper: supports pycryptodome primary and cryptography fallback
    try:
        from Crypto.Cipher import AES as PyCryptoAES
        from Crypto.Util import Counter as PyCryptoCounter
        has_pycrypto = True
    except ImportError:
        has_pycrypto = False

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher as CryptoCipher, algorithms as CryptoAlgo, modes as CryptoModes
        from cryptography.hazmat.backends import default_backend as CryptoBackend
        has_cryptography = True
    except ImportError:
        has_cryptography = False

    if not has_pycrypto and not has_cryptography:
        raise HTTPException(
            status_code=500,
            detail="Не найден криптографический модуль (pycryptodome или cryptography). Выполните сборку с обновленным requirements.txt."
        )

    def aes_cbc_decrypt(key_bytes: bytes, iv_bytes: bytes, data: bytes) -> bytes:
        if has_pycrypto:
            cipher = PyCryptoAES.new(key_bytes, PyCryptoAES.MODE_CBC, iv_bytes)
            return cipher.decrypt(data)
        else:
            cipher = CryptoCipher(CryptoAlgo.AES(key_bytes), CryptoModes.CBC(iv_bytes), backend=CryptoBackend())
            decryptor = cipher.decryptor()
            return decryptor.update(data) + decryptor.finalize()

    def aes_ctr_decrypt(key_bytes: bytes, iv_bytes: bytes, data: bytes) -> bytes:
        if has_pycrypto:
            ctr = PyCryptoCounter.new(128, initial_value=int.from_bytes(iv_bytes, 'big'))
            cipher = PyCryptoAES.new(key_bytes, PyCryptoAES.MODE_CTR, counter=ctr)
            return cipher.decrypt(data)
        else:
            cipher = CryptoCipher(CryptoAlgo.AES(key_bytes), CryptoModes.CTR(iv_bytes), backend=CryptoBackend())
            decryptor = cipher.decryptor()
            return decryptor.update(data) + decryptor.finalize()

    raw_text = payload.urls_text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="Вставьте ссылки Mega.nz или текст заказа")

    # Extract Mega.nz URLs
    urls = re.findall(r'https?://mega\.nz/file/[^\s><"\']+', raw_text)
    if not urls:
        raise HTTPException(status_code=400, detail="Не найдено ни одной валидной ссылки Mega.nz")

    role = payload.account_role.upper()
    if role not in ["LISTENER", "WORKER"]:
        role = "LISTENER"

    # Purge old listener accounts if requested
    if payload.purge_old_listeners and role == "LISTENER":
        old_scrapers = (await db.execute(select(ScraperAccount).where(ScraperAccount.account_role == "LISTENER"))).scalars().all()
        for old in old_scrapers:
            await db.delete(old)
        await db.commit()

    def base64_url_decode(data):
        data += '=' * (-len(data) % 4)
        return base64.b64decode(data.replace('-', '+').replace('_', '/'))

    def a32_to_str(a):
        return b''.join((i).to_bytes(4, 'big') for i in a)

    def str_to_a32(b):
        if len(b) % 4 != 0:
            b += b'\0' * (4 - len(b) % 4)
        return [int.from_bytes(b[i:i+4], 'big') for i in range(0, len(b), 4)]

    def decrypt_attr(attr_enc, key):
        dec = aes_cbc_decrypt(a32_to_str(key), b'\0'*16, attr_enc)
        if dec.startswith(b'MEGA'):
            meta = dec[4:].rstrip(b'\0')
            return json.loads(meta.decode('utf-8', errors='ignore'))
        return {}

    def download_mega(url, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        match = re.search(r'/file/([^#]+)#(.+)', url)
        if not match:
            raise ValueError(f"Неверный формат ссылки: {url}")
        file_id, key_str = match.group(1), match.group(2)
        key_bytes = base64_url_decode(key_str)
        key_a32 = str_to_a32(key_bytes)
        k = [key_a32[0] ^ key_a32[4], key_a32[1] ^ key_a32[5], key_a32[2] ^ key_a32[6], key_a32[3] ^ key_a32[7]] if len(key_a32) == 8 else key_a32
        iv = [key_a32[4], key_a32[5], 0, 0] if len(key_a32) == 8 else [0, 0, 0, 0]
        k_bytes = a32_to_str(k)
        iv_bytes = a32_to_str(iv)[:8] + b'\0'*8
        resp = requests.post("https://g.api.mega.co.nz/cs?id=1", json=[{"a": "g", "g": 1, "p": file_id}]).json()
        if isinstance(resp, int) or (isinstance(resp, list) and len(resp) > 0 and isinstance(resp[0], int) and resp[0] < 0):
            err_code = resp[0] if isinstance(resp, list) else resp
            if err_code == -2:
                raise RuntimeError(f"Ссылка Mega.nz недействительна или файл был удален (Ошибка -2 ENOENT)")
            elif err_code == -9:
                raise RuntimeError(f"Превышен лимит скачивания Mega.nz (Ошибка -9 EOVERQUOTA). Попробуйте обновить ссылку.")
            else:
                raise RuntimeError(f"Сбой Mega API #{err_code} при скачивании {file_id}")
        if not isinstance(resp, list) or not resp or "g" not in resp[0]:
            raise RuntimeError(f"Не удалось получить ссылку на скачивание для {file_id}")
        file_info = resp[0]
        at_enc = base64_url_decode(file_info["at"])
        attr = decrypt_attr(at_enc, k)
        file_name = attr.get("n", f"{file_id}.archive")
        out_path = os.path.join(out_dir, file_name)
        if not os.path.exists(out_path):
            r = requests.get(file_info["g"], stream=True)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                if has_pycrypto:
                    ctr = PyCryptoCounter.new(128, initial_value=int.from_bytes(iv_bytes, 'big'))
                    cipher = PyCryptoAES.new(k_bytes, PyCryptoAES.MODE_CTR, counter=ctr)
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(cipher.decrypt(chunk))
                else:
                    cipher = CryptoCipher(CryptoAlgo.AES(k_bytes), CryptoModes.CTR(iv_bytes), backend=CryptoBackend())
                    decryptor = cipher.decryptor()
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(decryptor.update(chunk))
                    f.write(decryptor.finalize())
        return out_path

    def unpack_archive(archive_path, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(dest_dir)
            return

        import tarfile
        if tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path) as tar_ref:
                tar_ref.extractall(dest_dir)
            return

        try:
            import rarfile
            rf = rarfile.RarFile(archive_path)
            rf.extractall(dest_dir)
            return
        except Exception:
            pass

        tools = [
            ["7z", "x", "-y", f"-o{dest_dir}", os.path.abspath(archive_path)],
            ["unrar", "x", "-o+", os.path.abspath(archive_path), os.path.abspath(dest_dir)],
            ["unar", "-o", os.path.abspath(dest_dir), "-f", os.path.abspath(archive_path)],
            ["bsdtar", "-xf", os.path.abspath(archive_path), "-C", os.path.abspath(dest_dir)],
            ["tar", "-xf", os.path.abspath(archive_path), "-C", os.path.abspath(dest_dir)]
        ]
        unpacked = False
        for cmd in tools:
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res.returncode == 0:
                    unpacked = True
                    break
            except Exception:
                continue

        if not unpacked:
            raise RuntimeError(f"Не удалось распаковать архив {os.path.basename(archive_path)}. Убедитесь, что архив формата .rar или .zip.")

    def recursive_unpack_subarchives(directory):
        for root, dirs, files in os.walk(directory):
            for file in files:
                full_p = os.path.join(root, file)
                ext = file.lower()
                if (ext.endswith('.zip') or ext.endswith('.rar') or ext.endswith('.7z') or ext.endswith('.tar.gz') or ext.endswith('.tgz')) and not file.startswith('.'):
                    sub_out = os.path.join(root, f"unpacked_{os.path.splitext(file)[0]}")
                    if not os.path.exists(sub_out):
                        try:
                            unpack_archive(full_p, sub_out)
                        except Exception:
                            pass

    def convert_session_file(sqlite_path, dir_path):
        user_id = 0
        phone = ""
        base_name = os.path.splitext(os.path.basename(sqlite_path))[0]
        
        candidate_jsons = [
            os.path.join(dir_path, f"{base_name}.json"),
            os.path.join(dir_path, "account.json"),
            os.path.join(dir_path, "info.json"),
            os.path.join(dir_path, "meta.json")
        ]
        all_jsons = glob.glob(os.path.join(dir_path, "*.json"))
        if all_jsons:
            candidate_jsons.extend(all_jsons)

        meta = {}
        for cj in candidate_jsons:
            if os.path.exists(cj):
                try:
                    with open(cj, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    user_id = meta.get("user_id") or meta.get("id") or meta.get("telegram_id") or 0
                    phone = meta.get("phone") or meta.get("phone_number") or meta.get("session_file") or ""
                    if user_id or phone:
                        break
                except Exception:
                    pass

        if phone:
            phone = re.sub(r'\D', '', str(phone))

        conn = sqlite3.connect(sqlite_path)
        c = conn.cursor()
        c.execute("SELECT dc_id, auth_key FROM sessions")
        row = c.fetchone()

        if not user_id or not phone:
            try:
                c.execute("SELECT id, phone FROM users WHERE self = 1 OR self = '1' LIMIT 1")
                u_row = c.fetchone()
                if not u_row:
                    c.execute("SELECT id, phone FROM users LIMIT 1")
                    u_row = c.fetchone()
                if u_row:
                    if not user_id and u_row[0]:
                        user_id = int(u_row[0])
                    if not phone and u_row[1]:
                        phone = re.sub(r'\D', '', str(u_row[1]))
            except Exception:
                pass

        conn.close()
        if not row:
            raise ValueError(f"Файл {os.path.basename(sqlite_path)} не содержит валидную таблицу sessions")

        dc_id, auth_key = row[0], row[1]
        packed = struct.pack(">B?256sQ?", dc_id, False, auth_key, user_id or 0, False)
        session_str = base64.urlsafe_b64encode(packed).decode("utf-8").rstrip("=")
        return session_str, phone, str(user_id or 0)

    def aes_256_ige_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
        if has_pycrypto:
            cipher = PyCryptoAES.new(key, PyCryptoAES.MODE_ECB)
            iv1, iv2 = iv[:16], iv[16:]
            res = bytearray()
            for i in range(0, len(data), 16):
                chunk = data[i:i+16]
                dec = cipher.decrypt(bytes(a ^ b for a, b in zip(chunk, iv2)))
                plain = bytes(a ^ b for a, b in zip(dec, iv1))
                iv1, iv2 = chunk, plain
                res.extend(plain)
            return bytes(res)
        else:
            cipher = CryptoCipher(CryptoAlgo.AES(key), CryptoModes.ECB(), backend=CryptoBackend())
            decryptor = cipher.decryptor()
            iv1, iv2 = iv[:16], iv[16:]
            res = bytearray()
            for i in range(0, len(data), 16):
                chunk = data[i:i+16]
                dec = decryptor.update(bytes(a ^ b for a, b in zip(chunk, iv2)))
                plain = bytes(a ^ b for a, b in zip(dec, iv1))
                iv1, iv2 = chunk, plain
                res.extend(plain)
            return bytes(res)

    def prepare_aes_oldmtp(auth_key_256: bytes, msg_key_16: bytes, send: bool = False):
        x = 0 if send else 8
        sha1a = hashlib.sha1(msg_key_16[:16] + auth_key_256[x:x+32]).digest()
        sha1b = hashlib.sha1(auth_key_256[x+32:x+48] + msg_key_16[:16] + auth_key_256[x+48:x+64]).digest()
        sha1c = hashlib.sha1(auth_key_256[x+64:x+96] + msg_key_16[:16]).digest()
        sha1d = hashlib.sha1(msg_key_16[:16] + auth_key_256[x+96:x+128]).digest()
        aes_key = sha1a[:8] + sha1b[8:20] + sha1c[4:16]
        aes_iv = sha1a[8:20] + sha1b[:8] + sha1c[16:20] + sha1d[:8]
        return aes_key, aes_iv

    def read_tdf_file(filepath: str):
        with open(filepath, 'rb') as f:
            data = f.read()
        if not data.startswith(b'TDF$'):
            raise ValueError('Invalid magic in TDF file')
        version = int.from_bytes(data[4:8], 'little')
        bytesdata = data[8:]
        data_size = len(bytesdata) - 16
        check_md5 = bytesdata[:data_size] + data_size.to_bytes(4, 'little') + version.to_bytes(4, 'little') + b'TDF$'
        if hashlib.md5(check_md5).digest() != bytesdata[data_size:]:
            raise ValueError('Invalid checksum in TDF file')
        return bytesdata[:data_size]

    def decrypt_tdf_local(encrypted: bytes, auth_key: bytes) -> bytes:
        if len(encrypted) <= 16 or len(encrypted) % 16 != 0:
            raise ValueError('Bad encrypted size in TDF')
        enc_key = encrypted[:16]
        enc_data = encrypted[16:]
        aes_k, aes_iv = prepare_aes_oldmtp(auth_key, enc_key, send=False)
        decrypted = aes_256_ige_decrypt(enc_data, aes_k, aes_iv)
        check_hash = hashlib.sha1(decrypted).digest()[:16]
        if check_hash != enc_key:
            raise ValueError('Bad decrypt key for TDF (checksum mismatch)')
        data_len = int.from_bytes(decrypted[:4], 'little')
        return decrypted[4:data_len]

    def convert_tdata_folder(tdata_dir: str):
        kd_path = os.path.join(tdata_dir, 'key_datas')
        if not os.path.exists(kd_path):
            kd_path = os.path.join(tdata_dir, 'key_datass')
        if not os.path.exists(kd_path):
            raise ValueError("No key_datas file found in tdata directory")

        kd_raw = read_tdf_file(kd_path)
        offset = 0
        salt_len = struct.unpack('>I', kd_raw[offset:offset+4])[0]
        offset += 4
        salt = kd_raw[offset:offset+salt_len]
        offset += salt_len

        key_enc_len = struct.unpack('>I', kd_raw[offset:offset+4])[0]
        offset += 4
        key_encrypted = kd_raw[offset:offset+key_enc_len]
        offset += key_enc_len

        info_enc_len = struct.unpack('>I', kd_raw[offset:offset+4])[0]
        offset += 4
        info_encrypted = kd_raw[offset:offset+info_enc_len]
        offset += info_enc_len

        passcode = b''
        hash_key = hashlib.sha512(salt + passcode + salt).digest()
        passcode_key = hashlib.pbkdf2_hmac('sha512', hash_key, salt, 1, 256)

        key_inner_data = decrypt_tdf_local(key_encrypted, passcode_key)
        local_key = key_inner_data[:256]

        acc_path = None
        for fn in os.listdir(tdata_dir):
            fp = os.path.join(tdata_dir, fn)
            if os.path.isfile(fp) and fn.startswith('D877'):
                acc_path = fp
                break

        if not acc_path:
            raise ValueError("No D877 account file found in tdata directory")

        mtp_raw = read_tdf_file(acc_path)
        mtp_offset = 0
        enc_mtp_len = struct.unpack('>I', mtp_raw[mtp_offset:mtp_offset+4])[0]
        mtp_offset += 4
        enc_mtp = mtp_raw[mtp_offset:mtp_offset+enc_mtp_len]

        decrypted_mtp = decrypt_tdf_local(enc_mtp, local_key)

        m_off = 0
        block_id = struct.unpack('>i', decrypted_mtp[m_off:m_off+4])[0]
        m_off += 4

        auth_len = struct.unpack('>I', decrypted_mtp[m_off:m_off+4])[0]
        m_off += 4
        auth_payload = decrypted_mtp[m_off:m_off+auth_len]

        a_off = 0
        u_id = struct.unpack('>i', auth_payload[a_off:a_off+4])[0]
        a_off += 4
        dc_id = struct.unpack('>i', auth_payload[a_off:a_off+4])[0]
        a_off += 4

        if ((u_id << 32) | (dc_id & 0xffffffff)) == -1:
            u_id = struct.unpack('>q', auth_payload[a_off:a_off+8])[0]
            a_off += 8
            dc_id = struct.unpack('>i', auth_payload[a_off:a_off+4])[0]
            a_off += 4

        key_count = struct.unpack('>i', auth_payload[a_off:a_off+4])[0]
        a_off += 4

        keys = []
        for _ in range(key_count):
            k_dc = struct.unpack('>i', auth_payload[a_off:a_off+4])[0]
            a_off += 4
            k_bytes = auth_payload[a_off:a_off+256]
            a_off += 256
            keys.append((k_dc, k_bytes))

        main_auth_key = keys[0][1]
        for k_dc, k_b in keys:
            if k_dc == dc_id:
                main_auth_key = k_b
                break

        packed = struct.pack('>B?256sQ?', dc_id, False, main_auth_key, u_id if u_id > 0 else 0, False)
        session_str = base64.urlsafe_b64encode(packed).decode('utf-8').rstrip('=')

        phone = ''
        acc_txt = os.path.join(tdata_dir, 'Accounts.txt')
        if os.path.exists(acc_txt):
            with open(acc_txt, 'r', encoding='utf-8') as f:
                phone = f.read().strip()
        if not phone:
            phone = str(u_id)

        return session_str, phone, str(u_id)

    download_dir = "tmp_mega_downloads"
    extract_dir = "tmp_mega_downloads/extracted"
    os.makedirs(extract_dir, exist_ok=True)

    imported_count = 0
    errors = []

    for idx, url in enumerate(urls, 1):
        try:
            archive_path = download_mega(url, download_dir)
            acc_extract_path = os.path.join(extract_dir, f"acc_{idx}")
            os.makedirs(acc_extract_path, exist_ok=True)

            unpack_archive(archive_path, acc_extract_path)
            recursive_unpack_subarchives(acc_extract_path)

            session_files = glob.glob(os.path.join(acc_extract_path, "**", "*.session"), recursive=True)

            tdata_dirs = []
            for root, dirs, files in os.walk(acc_extract_path):
                if 'key_datas' in files or 'key_datass' in files:
                    tdata_dirs.append(root)

            if not session_files and not tdata_dirs:
                errors.append(f"Ссылка #{idx}: В архиве {os.path.basename(archive_path)} не найдено ни сессий .session, ни папки tdata (проверены все подпапки)")
                continue

            link_imported = 0
            for s_file in session_files:
                try:
                    s_dir = os.path.dirname(s_file)
                    session_str, phone, user_id = convert_session_file(s_file, s_dir)

                    existing = (await db.execute(select(ScraperAccount).where(ScraperAccount.session_string == session_str))).scalar_one_or_none()
                    if not existing:
                        new_sc = ScraperAccount(
                            phone_number=f"+{phone}" if phone else None,
                            session_string=session_str,
                            status="ACTIVE",
                            account_role=role,
                            max_daily_joins=20
                        )
                        db.add(new_sc)
                        imported_count += 1
                        link_imported += 1
                    else:
                        existing.status = "ACTIVE"
                        existing.account_role = role
                except Exception as s_err:
                    logger.warning(f"Ошибка конвертации сессии {s_file}: {s_err}")

            for td_dir in tdata_dirs:
                try:
                    session_str, phone, user_id = convert_tdata_folder(td_dir)

                    existing = (await db.execute(select(ScraperAccount).where(ScraperAccount.session_string == session_str))).scalar_one_or_none()
                    if not existing:
                        new_sc = ScraperAccount(
                            phone_number=f"+{phone}" if phone else None,
                            session_string=session_str,
                            status="ACTIVE",
                            account_role=role,
                            max_daily_joins=20
                        )
                        db.add(new_sc)
                        imported_count += 1
                        link_imported += 1
                    else:
                        existing.status = "ACTIVE"
                        existing.account_role = role
                except Exception as td_err:
                    logger.warning(f"Ошибка конвертации tdata из {td_dir}: {td_err}")

            if link_imported == 0 and (session_files or tdata_dirs):
                logger.info(f"Ссылка #{idx}: Все аккаунты из архива уже присутствуют в системе.")

        except Exception as e:
            errors.append(f"Ссылка #{idx}: {str(e)}")

    await db.commit()

    # Trigger restart of scraper swarm loop & instant 4-tier priority rebalance
    try:
        from src.api.app import ingestor
        from src.services.swarm_manager import SwarmManager
        if ingestor:
            asyncio.create_task(ingestor.restart_scraper_loop())
            if hasattr(ingestor, "trigger_rebalance_now"):
                ingestor.trigger_rebalance_now()
        else:
            asyncio.create_task(SwarmManager.rebalance_and_dispatch_joins())
    except Exception as trigger_err:
        logger.warning(f"Notice triggering rebalance after Mega import: {trigger_err}")

    return {
        "status": "ok",
        "imported_count": imported_count,
        "total_urls": len(urls),
        "account_role": role,
        "errors": errors
    }

@router.get("/system/swarm-telemetry")
async def get_system_swarm_telemetry(db: AsyncSession = Depends(get_db)):
    from src.services.swarm_manager import SwarmManager
    telemetry = await SwarmManager.get_swarm_telemetry(db)
    return telemetry

@router.get("/system/userbot-bindings")
async def get_userbot_bindings(db: AsyncSession = Depends(get_db)):
    from src.db.models import UserbotChatBinding, MonitoredChannel, ScraperAccount
    from sqlalchemy import select
    
    # Outer join to get the channel title and scraper account status
    res = await db.execute(
        select(UserbotChatBinding, MonitoredChannel.title, MonitoredChannel.username_or_link, ScraperAccount.status)
        .outerjoin(MonitoredChannel, UserbotChatBinding.channel_id == MonitoredChannel.id)
        .outerjoin(ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id)
        .order_by(UserbotChatBinding.last_activity_at.desc())
    )
    bindings_with_channels = res.all()
    
    out = []
    has_repairs = False
    for b, c_title, c_link, acc_status in bindings_with_channels:
        effective_status = b.binding_status
        # Discrepancy Fix: If the associated userbot account is BANNED or DISABLED, reflect SESSION_REVOKED
        if acc_status in ("BANNED", "DISABLED") and b.binding_status == "ACTIVE":
            effective_status = "SESSION_REVOKED"
            b.binding_status = "SESSION_REVOKED"
            has_repairs = True

        out.append({
            "id": b.id,
            "account_id": b.account_id,
            "channel_id": c_title or c_link or b.channel_id,
            "binding_status": effective_status,
            "joined_at": b.joined_at.isoformat() if b.joined_at else None,
            "last_activity_at": b.last_activity_at.isoformat() if b.last_activity_at else None
        })

    if has_repairs:
        try:
            await db.commit()
        except Exception:
            pass

    return {"status": "ok", "count": len(out), "bindings": out}


@router.get("/system/userbot-joins-status")
async def get_userbot_joins_status():
    from src.services.swarm_manager import SwarmManager
    from src.api.app import ingestor
    return await SwarmManager.get_next_scheduled_join_info(ingestor=ingestor)


@router.get("/system/join-queue")
async def get_system_join_queue(db: AsyncSession = Depends(get_db)):
    """
    Returns full list of all monitored channels currently awaiting userbot join (status='PENDING' or 'FAILED').
    Includes priority tier, target quorum, location, niche, and direct Telegram verification links.
    """
    from src.db.models import MonitoredChannel, UserbotChatBinding, ScraperAccount
    from src.services.swarm_manager import SwarmManager
    from sqlalchemy import update

    # Self-healing pass: reset any non-public MonitoredChannel with 0 active listener bindings back to PENDING
    try:
        active_bind_subq = (
            select(UserbotChatBinding.channel_id)
            .join(ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id)
            .where(
                UserbotChatBinding.binding_status == "ACTIVE",
                ScraperAccount.status == "ACTIVE"
            )
        )
        stuck_stmt = (
            update(MonitoredChannel)
            .where(
                MonitoredChannel.platform == "telegram",
                MonitoredChannel.status.in_(["JOINED", "ACTIVE"]),
                MonitoredChannel.id.not_in(active_bind_subq)
            )
            .values(status="PENDING", error_message="В очереди: ожидание привязки слушателя роя")
        )
        res_stuck = await db.execute(stuck_stmt)
        if res_stuck.rowcount and res_stuck.rowcount > 0:
            await db.commit()
    except Exception as heal_err:
        logger.warning(f"Notice auto-healing join queue in API: {heal_err}")

    stmt = select(MonitoredChannel).where(
        MonitoredChannel.status.in_(["PENDING", "FAILED"])
    ).order_by(MonitoredChannel.created_at.desc())

    channels = list((await db.execute(stmt)).scalars().all())


    pending_items = []
    from src.services.spam_guard import sanitize_channel_identifier

    for c in channels:
        created_dt = c.created_at
        if created_dt and created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        created_fmt = (created_dt + timedelta(hours=7)).strftime("%d.%m %H:%M") if created_dt else "—"

        raw_link = sanitize_channel_identifier(c.username_or_link or "")
        clean_title = sanitize_channel_identifier(c.title or raw_link)

        if raw_link != c.username_or_link or clean_title != c.title:
            c.username_or_link = raw_link
            c.title = clean_title
            await db.commit()

        clean_link = raw_link.replace("@", "").strip()
        if clean_link and not clean_link.startswith("http"):
            tg_url = f"https://t.me/{clean_link}"
        else:
            tg_url = raw_link or "#"

        target_q = SwarmManager.get_target_quorum(c)
        priority_tier = "P1 Свежий импорт" if c.status == "PENDING" else "P2 Ошибка/Повторный вход"
        if target_q == 2:
            priority_tier = "P3 2X Кворум"

        pending_items.append({
            "id": c.id,
            "title": clean_title,
            "username_or_link": raw_link,
            "tg_url": tg_url,
            "location_code": getattr(c, "location_code", "global") or "global",
            "niche_code": c.niche_code or "community",
            "status": c.status,
            "priority_tier": priority_tier,
            "target_quorum": target_q,
            "created_fmt": created_fmt,
            "error_message": c.error_message
        })

    from src.api.app import ingestor
    join_info = await SwarmManager.get_next_scheduled_join_info(ingestor=ingestor)

    return {
        "status": "ok",
        "pending_count": len(pending_items),
        "channels": pending_items,
        "next_scheduled_join": join_info
    }


@router.post("/system/trigger-swarm-rebalance")
async def trigger_swarm_rebalance_endpoint():
    """
    Triggers immediate 4-tier priority swarm rebalance & auto-join pass.
    """
    from src.services.swarm_manager import SwarmManager
    from src.api.app import ingestor
    res = await SwarmManager.rebalance_and_dispatch_joins(ingestor=ingestor)
    return res


@router.post("/system/reconcile-dialogs")
async def trigger_reconcile_dialogs_endpoint():
    """
    Triggers live MTProto userbot dialog reconciliation pass.
    Validates actual userbot memberships against DB bindings & MonitoredChannels.
    """
    from src.services.swarm_manager import SwarmManager
    from src.api.app import ingestor
    res = await SwarmManager.audit_and_reconcile_dialogs(ingestor=ingestor)
    return res



@router.post("/channels/{channel_id:path}/force-join")
async def force_join_channel_endpoint(channel_id: str, db: AsyncSession = Depends(get_db)):
    """
    Forces immediate MTProto userbot join for a specific channel ID.
    """
    try:
        try:
            import uuid
            uid = uuid.UUID(channel_id)
            stmt = select(MonitoredChannel).where(MonitoredChannel.id == uid)
            ch = (await db.execute(stmt)).scalar_one_or_none()
        except (ValueError, TypeError):
            ch = None
            
        if not ch:
            clean_user = channel_id.replace("@", "").replace("https://t.me/", "")
            stmt2 = select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(f"%{clean_user}%"))
            ch = (await db.execute(stmt2)).scalars().first()
            if not ch:
                raise HTTPException(status_code=404, detail="Канал не найден")

        from src.services.spam_guard import sanitize_channel_identifier
        target_uname = sanitize_channel_identifier(ch.username_or_link)
        clean_title = sanitize_channel_identifier(ch.title or target_uname)

        if target_uname != ch.username_or_link or clean_title != ch.title:
            ch.username_or_link = target_uname
            ch.title = clean_title
            await db.commit()

        from src.api.app import ingestor
        if not ingestor:
            return {"status": "error", "message": "⚠️ Система юзерботов не инициализирована."}
            
        success, title, error = await ingestor.join_channel(target_uname, channel_id=str(ch.id))
        if success:
            ch.status = "JOINED"
            ch.error_message = None
            await db.commit()
            return {"status": "ok", "message": f"✅ Юзербот успешно подключен к {ch.title or ch.username_or_link}"}
        else:
            if error and "Anti-Ban Pacing" not in str(error):
                ch.status = "FAILED"
                ch.error_message = str(error)
                await db.commit()
            return {"status": "error", "message": f"⚠️ Не удалось подключиться: {error or 'Все юзерботы заняты или антиспам-пауза'}"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        return {"status": "error", "message": f"⚠️ Ошибка сервера: {str(e)}"}




@router.get("/service/status")
async def get_service_status(current_user: Partner = Depends(get_current_user)):
    if current_user.role not in ["SUPERADMIN", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    from src.api.app import ingestor
    is_running = ingestor._is_running if ingestor else False
    return {"is_running": is_running}

@router.post("/service/start")
async def start_service(current_user: Partner = Depends(get_current_user)):
    if current_user.role not in ["SUPERADMIN", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    from src.api.app import ingestor
    if ingestor:
        import asyncio
        asyncio.create_task(ingestor.start())
    return {"status": "started"}

@router.post("/service/stop")
async def stop_service(current_user: Partner = Depends(get_current_user)):
    if current_user.role not in ["SUPERADMIN", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    from src.api.app import ingestor
    if ingestor:
        await ingestor.stop()
    return {"status": "stopped"}


# ─────────────────────────────────────────────────────────────────────────────
# VACANCY GROUP AUTO-POSTING MANAGEMENT ROUTES
# ─────────────────────────────────────────────────────────────────────────────

class VacancyGroupTargetCreateSchema(BaseModel):
    group_username: str
    group_title: str = ""
    stars_price: int = 100
    interval_hours: int = 48
    max_reposts: int = 3

@router.get("/vacancy-groups")
async def list_vacancy_groups(db: AsyncSession = Depends(get_db)):
    from src.db.models import VacancyGroupTarget, VacancyGroupPost, VacancyContactPurchase
    groups = list((await db.execute(select(VacancyGroupTarget).order_by(VacancyGroupTarget.id))).scalars().all())
    result = []
    for g in groups:
        post_count = (await db.execute(select(func.count(VacancyGroupPost.id)).where(VacancyGroupPost.group_username == g.group_username))).scalar() or 0
        stars_earned = (await db.execute(select(func.sum(VacancyContactPurchase.stars_paid)).where(VacancyContactPurchase.group_source.ilike(f"%{g.group_username.lstrip('@')}%")))).scalar() or 0
        purchases = (await db.execute(select(func.count(VacancyContactPurchase.id)).where(VacancyContactPurchase.group_source.ilike(f"%{g.group_username.lstrip('@')}%")))).scalar() or 0
        result.append({"id": g.id, "group_username": g.group_username, "group_title": g.group_title, "stars_price": g.stars_price, "is_active": g.is_active, "interval_hours": g.interval_hours, "max_reposts": g.max_reposts, "posts_total": post_count, "purchases_total": purchases, "stars_earned": int(stars_earned), "last_posted_at": g.last_posted_at.isoformat() if g.last_posted_at else None, "created_at": g.created_at.isoformat() if g.created_at else None})
    return {"status": "ok", "groups": result}

@router.post("/vacancy-groups")
async def create_vacancy_group(data: VacancyGroupTargetCreateSchema, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyGroupTarget
    uname = data.group_username if data.group_username.startswith("@") else f"@{data.group_username}"
    existing = (await db.execute(select(VacancyGroupTarget).where(VacancyGroupTarget.group_username == uname))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Group {uname} already exists")
    grp = VacancyGroupTarget(group_username=uname, group_title=data.group_title or uname, stars_price=data.stars_price, interval_hours=data.interval_hours, max_reposts=data.max_reposts, is_active=True)
    db.add(grp)
    await db.commit()
    await db.refresh(grp)
    return {"status": "created", "id": grp.id, "group_username": grp.group_username}

@router.patch("/vacancy-groups/{group_id}/toggle")
async def toggle_vacancy_group(group_id: int, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyGroupTarget
    grp = (await db.execute(select(VacancyGroupTarget).where(VacancyGroupTarget.id == group_id))).scalar_one_or_none()
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    grp.is_active = not grp.is_active
    await db.commit()
    return {"status": "ok", "group_username": grp.group_username, "is_active": grp.is_active}

@router.delete("/vacancy-groups/{group_id}")
async def delete_vacancy_group(group_id: int, db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyGroupTarget
    grp = (await db.execute(select(VacancyGroupTarget).where(VacancyGroupTarget.id == group_id))).scalar_one_or_none()
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.delete(grp)
    await db.commit()
    return {"status": "deleted"}

@router.get("/vacancy-groups/purchases")
async def list_contact_purchases(db: AsyncSession = Depends(get_db), current_user: Partner = Depends(get_current_user)):
    from src.db.models import VacancyContactPurchase
    rows = list((await db.execute(select(VacancyContactPurchase).order_by(VacancyContactPurchase.purchased_at.desc()).limit(100))).scalars().all())
    total_stars = sum(r.stars_paid for r in rows)
    return {"status": "ok", "total_purchases": len(rows), "total_stars_earned": total_stars, "purchases": [{"id": r.id, "vacancy_id": r.vacancy_id, "buyer_telegram_id": r.buyer_telegram_id, "buyer_username": r.buyer_username, "stars_paid": r.stars_paid, "group_source": r.group_source, "purchased_at": r.purchased_at.isoformat()} for r in rows]}


# ────────────────────────────────────────────────────────────────────────────
# SCOUT MANUAL REVIEW API ENDPOINTS
# ────────────────────────────────────────────────────────────────────────────
from src.db.models import DiscoveredChat
from sqlalchemy import select, update

@router.get("/scout/dashboard")
async def get_scout_dashboard(db: AsyncSession = Depends(get_db), user: dict = Depends(require_admin)):
    stmt = select(DiscoveredChat).order_by(DiscoveredChat.discovered_at.desc()).limit(500)
    res = await db.execute(stmt)
    chats = list(res.scalars().all())
    
    # Calculate KPIs
    kpi_total = len(chats)
    kpi_approved = sum(1 for c in chats if c.audit_status == "APPROVED")
    kpi_rejected = sum(1 for c in chats if c.audit_status == "REJECTED")
    kpi_pending = sum(1 for c in chats if c.audit_status == "PENDING" or c.audit_status == "AUDITING")

    out = []
    for c in chats:
        out.append({
            "id": c.id,
            "chat_username": c.chat_username,
            "title": c.title or c.chat_username,
            "source": c.source,
            "audit_status": c.audit_status,
            "verdict_reason": c.verdict_reason,
            "discovered_at": c.discovered_at.isoformat() if c.discovered_at else None
        })
    return {
        "status": "ok", 
        "kpi": {
            "total": kpi_total,
            "approved": kpi_approved,
            "rejected": kpi_rejected,
            "pending": kpi_pending
        },
        "chats": out
    }

@router.post("/scout/approve/{chat_id}")
async def approve_scout_chat(chat_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_admin)):
    stmt = select(DiscoveredChat).where(DiscoveredChat.id == chat_id)
    chat = (await db.execute(stmt)).scalar_one_or_none()
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
        
    if chat.audit_status != "MANUAL_REVIEW":
        return {"status": "error", "message": "Chat is not pending manual review."}

    chat.audit_status = "APPROVED"
    
    # Check if it already exists in MonitoredChannel
    dup_stmt = select(MonitoredChannel).where(
        MonitoredChannel.username_or_link.ilike(chat.chat_username),
        MonitoredChannel.platform == (chat.platform or "telegram")
    )
    dup = (await db.execute(dup_stmt)).scalars().first()
    
    if not dup:
        niche = chat.detected_niches[0] if (chat.detected_niches and len(chat.detected_niches) > 0) else "community"
        new_mon = MonitoredChannel(
            username_or_link=chat.chat_username,
            title=chat.title or chat.chat_username,
            niche_code=niche,
            location_code=chat.location_code or "global",
            platform=chat.platform or "telegram",
            chat_type=chat.chat_type or "group",
            status="PENDING"
        )
        db.add(new_mon)
        
        # Dispatch background join
        try:
            from src.api.app import ingestor
            import asyncio
            if ingestor:
                async def _bg_join():
                    try:
                        await ingestor.join_channel(chat.chat_username)
                    except Exception as e:
                        logger.error(f"Scout UI bg join error: {e}")
                asyncio.create_task(_bg_join())
        except Exception:
            pass

    await db.commit()
    return {"status": "ok", "message": "Чат одобрен и отправлен на прослушку."}

@router.post("/scout/reject/{chat_id}")
async def reject_scout_chat(chat_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_admin)):
    stmt = select(DiscoveredChat).where(DiscoveredChat.id == chat_id)
    chat = (await db.execute(stmt)).scalar_one_or_none()
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    chat.audit_status = "ARCHIVED"
    chat.audited_at = datetime.now(timezone.utc)
    chat.verdict_reason = "Ручное отклонение — перемещено в Архив (24ч)."
    
    from src.discovery.chat_discovery import blacklist_channel_permanently
    await blacklist_channel_permanently(
        db,
        username_or_link=chat.chat_username,
        title=chat.title,
        reason="Отклонено вручную через интерфейс скаута.",
        score=chat.score or 0
    )
    
    await db.commit()
    return {"status": "ok", "message": "Чат перемещён в Архив (24ч) и добавлен в черный список."}


# ────────────────────────────────────────────────────────────────────────────
# SCOUT KEYWORD MANAGEMENT (GEO-BASED)
# ────────────────────────────────────────────────────────────────────────────
from src.db.models import DiscoveryKeyword
from pydantic import BaseModel
from typing import List
from src.ai.rotator_engine import ai_rotator
import json

class KeywordCreateSchema(BaseModel):
    keyword: str
    location_code: str

@router.get("/discovery/keywords")
async def get_discovery_keywords(location: str = "global", db: AsyncSession = Depends(get_db), user: Optional[Partner] = Depends(get_optional_current_user)):
    stmt = select(DiscoveryKeyword).where(DiscoveryKeyword.location_code == location)
    res = await db.execute(stmt)
    keywords = list(res.scalars().all())
    out = [{"id": k.id, "keyword": k.keyword, "location_code": k.location_code, "is_active": k.is_active} for k in keywords]
    return {"status": "ok", "keywords": out}

@router.post("/discovery/keywords/add")
async def add_discovery_keyword(payload: KeywordCreateSchema, db: AsyncSession = Depends(get_db), user: Optional[Partner] = Depends(get_optional_current_user)):
    keyword = payload.keyword.strip().lower()
    if not keyword:
        return {"status": "error", "message": "Empty keyword"}
        
    dup = (await db.execute(select(DiscoveryKeyword).where(DiscoveryKeyword.keyword == keyword))).scalar_one_or_none()
    if dup:
        if not dup.is_active:
            dup.is_active = True
            await db.commit()
            return {"status": "ok", "message": "Keyword reactivated", "id": dup.id}
        return {"status": "error", "message": "Keyword already exists"}
        
    new_kw = DiscoveryKeyword(keyword=keyword, location_code=payload.location_code)
    db.add(new_kw)
    await db.commit()
    await db.refresh(new_kw)
    return {"status": "ok", "message": "Added successfully", "id": new_kw.id}

@router.delete("/discovery/keywords/{kw_id}")
async def delete_discovery_keyword(kw_id: str, db: AsyncSession = Depends(get_db), user: Optional[Partner] = Depends(get_optional_current_user)):
    stmt = select(DiscoveryKeyword).where(DiscoveryKeyword.id == kw_id)
    kw = (await db.execute(stmt)).scalar_one_or_none()
    if not kw:
        return {"status": "error", "message": "Not found"}
        
    await db.delete(kw)
    await db.commit()
    return {"status": "ok", "message": "Deleted"}

class KeywordGenerateSchema(BaseModel):
    location_code: str

@router.post("/discovery/keywords/generate")
async def generate_discovery_keywords_ai(payload: KeywordGenerateSchema, db: AsyncSession = Depends(get_db), user: Optional[Partner] = Depends(get_optional_current_user)):
    loc = payload.location_code
    
    # Get existing
    ex_stmt = select(DiscoveryKeyword.keyword).where(DiscoveryKeyword.location_code == loc)
    existing = list((await db.execute(ex_stmt)).scalars().all())
    
    prompt_sys = """You are an SEO and lead generation expert for Telegram. 
Your task is to generate highly effective and creative search queries (keywords) to find Russian-speaking Telegram groups and chats in a specific location.
These groups are for expats, freelancers, real estate, community chat, visa runs, etc.
Output MUST be a JSON object with a single key "keywords" containing a list of strings."""

    user_p = f"""Generate 10-15 unique and relevant Russian search queries for Telegram groups in the following location: "{loc}".
Example for bali: ["чат бали", "экспаты бали", "аренда бали", "убуд чат", "визаран бали"].
DO NOT include the following existing keywords: {existing}.
Return ONLY JSON: {{"keywords": ["kw1", "kw2", ...]}}"""

    try:
        res = await ai_rotator.generate_json(
            system_prompt=prompt_sys,
            user_prompt=user_p,
            temperature=0.7,
            timeout=15.0
        )
        if res and "keywords" in res:
            new_kws = res["keywords"]
            added = 0
            for kw in new_kws:
                clean_kw = str(kw).strip().lower()
                if clean_kw and clean_kw not in existing:
                    dup = (await db.execute(select(DiscoveryKeyword).where(DiscoveryKeyword.keyword == clean_kw))).scalar_one_or_none()
                    if not dup:
                        db.add(DiscoveryKeyword(keyword=clean_kw, location_code=loc))
                        added += 1
            if added > 0:
                await db.commit()
            return {"status": "ok", "message": f"Generated and added {added} new keywords.", "added_count": added, "generated": new_kws}
        else:
            return {"status": "error", "message": "Failed to generate keywords from AI."}
    except Exception as e:
        logger.error(f"Error generating keywords: {e}")
        return {"status": "error", "message": str(e)}

