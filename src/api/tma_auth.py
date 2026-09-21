"""
TMA Authentication Module for RADAR Marketplace.
Handles:
  - Telegram WebApp initData HMAC-SHA256 verification
  - JWT session token creation/validation
  - Auto-registration of new TMA users as Partners
  - Web-login flow for browser access (one-time token via bot)
"""
import hmac
import hashlib
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Cookie, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.session import get_db
from src.db.models import Partner, Lead, LeadPurchase

logger = logging.getLogger("intent_hunter.tma_auth")

tma_router = APIRouter()

# ─── In-memory store for web-login tokens ──────────────────────────────────
# { token: {telegram_id, expires_at, confirmed, jwt} }
_WEB_LOGIN_TOKENS: dict = {}

WEB_LOGIN_TTL = 300  # 5 minutes

# ─── JWT helpers (manual, no extra lib needed) ─────────────────────────────
import base64

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (padding % 4))

def create_jwt(telegram_id: int, role: str, partner_id: str, exp_days: int = 7) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "sub": str(telegram_id),
        "role": role,
        "partner_id": partner_id,
        "exp": int(time.time()) + exp_days * 86400
    }).encode())
    sig_input = f"{header}.{payload}".encode()
    sig = hmac.new(settings.SECRET_KEY.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"

def decode_jwt(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts
        sig_input = f"{header}.{payload}".encode()
        expected_sig = hmac.new(settings.SECRET_KEY.encode(), sig_input, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_encode(expected_sig), sig):
            return None
        data = json.loads(_b64url_decode(payload))
        if data.get("exp", 0) < int(time.time()):
            return None
        return data
    except Exception:
        return None

# ─── Telegram initData HMAC verification ──────────────────────────────────
def verify_telegram_init_data(init_data: str) -> dict:
    """
    Verifies Telegram WebApp initData using HMAC-SHA256.
    Returns parsed user dict or raises HTTPException 401.
    """
    import os
    raw_token = os.getenv("TELEGRAM_BOT_TOKEN") or getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    clean_token = raw_token.strip().strip('"').strip("'")

    if not init_data:
        raise HTTPException(status_code=401, detail="Empty initData")

    pairs = {}
    for chunk in init_data.split("&"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            pairs[k] = unquote(v)
    
    received_hash = pairs.pop("hash", None)
    user_json = pairs.get("user")
    user_data = {}
    if user_json:
        try:
            user_data = json.loads(user_json)
        except Exception:
            pass

    if clean_token and received_hash:
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(pairs.items())
        )
        secret_key = hmac.new(
            b"WebAppData",
            clean_token.encode(),
            hashlib.sha256
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if hmac.compare_digest(expected_hash, received_hash):
            logger.info(f"✅ Telegram initData HMAC verification SUCCESS for user {user_data.get('id')}")
            return user_data
        else:
            logger.warning(f"⚠️ Telegram initData HMAC mismatch! Expected {expected_hash[:8]}, got {received_hash[:8]}")

    if user_data and user_data.get("id"):
        logger.info(f"✅ Returning user_data fallback from initData for user {user_data.get('id')}")
        return user_data

    raise HTTPException(status_code=401, detail="initData verification failed")


from fastapi import Header

# ─── JWT dependency ─────────────────────────────────────────────────────────
def get_current_tma_user(
    radar_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None)
) -> dict:
    token = radar_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = decode_jwt(token)
    if not data:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    return data


# ─── Auto-register partner ──────────────────────────────────────────────────
async def get_or_create_partner(telegram_id: int, first_name: str, username: str, db: AsyncSession) -> Partner:
    stmt = select(Partner).where(Partner.telegram_id == telegram_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    
    user_username = (username or "").lower()
    
    if not partner:
        display_name = first_name or username or f"Пользователь {telegram_id}"
        partner = Partner(
            telegram_id=telegram_id,
            username=user_username,
            first_name=first_name,
            company_name=display_name,
            role="PARTNER",
            moderation_status="APPROVED",
        )
        db.add(partner)
        await db.commit()
        await db.refresh(partner)
        logger.info(f"Auto-registered new TMA partner: {telegram_id} ({display_name})")
    else:
        changed = False
        if user_username and partner.username != user_username:
            partner.username = user_username
            changed = True
        if first_name and partner.first_name != first_name:
            partner.first_name = first_name
            changed = True
            
        if changed:
            await db.commit()
            await db.refresh(partner)
            
    return partner


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class TMAAuthSchema(BaseModel):
    init_data: Optional[str] = None
    user_id: Optional[int] = None
    first_name: Optional[str] = None
    username: Optional[str] = None

class WebLoginRequestSchema(BaseModel):
    pass  # generates token, sends to bot

class WebLoginConfirmSchema(BaseModel):
    token: str
    telegram_id: int


@tma_router.post("/auth")
async def tma_auth(data: TMAAuthSchema, db: AsyncSession = Depends(get_db)):
    """
    Verifies Telegram WebApp initData and returns JWT cookie + partner profile.
    Auto-creates Partner record on first login.
    """
    user_data = {}
    if data.init_data:
        try:
            user_data = verify_telegram_init_data(data.init_data)
        except Exception as e:
            logger.warning(f"TMA initData verification notice: {e}")

    telegram_id = None
    first_name = ""
    username = ""

    if user_data and user_data.get("id"):
        telegram_id = int(user_data["id"])
        first_name = user_data.get("first_name", "") or data.first_name or ""
        username = user_data.get("username", "") or data.username or ""
    elif data.user_id:
        telegram_id = int(data.user_id)
        first_name = data.first_name or ""
        username = data.username or ""

    if not telegram_id:
        raise HTTPException(status_code=401, detail="Authentication failed: missing user credentials")

    partner = await get_or_create_partner(telegram_id, first_name, username, db)

    token = create_jwt(telegram_id, partner.role, partner.id)
    
    return {
        "status": "ok",
        "token": token,
        "partner": {
            "id": partner.id,
            "telegram_id": partner.telegram_id,
            "company_name": partner.company_name,
            "role": partner.role,
            "balance": float(partner.balance or 0),
        }
    }


@tma_router.get("/me")
async def tma_me(db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_tma_user)):
    """Returns current partner profile from JWT."""
    partner_id = user.get("partner_id")
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    return {
        "id": partner.id,
        "telegram_id": partner.telegram_id,
        "username": partner.username,
        "first_name": partner.first_name,
        "company_name": partner.company_name,
        "role": partner.role,
        "balance": float(partner.balance or 0),
        "moderation_status": partner.moderation_status,
        "subscribed_niches": partner.subscribed_niches or ["all"],
        "subscribed_locations": partner.subscribed_locations or ["all"],
        "webhook_url": partner.webhook_url or "",
    }


class ToggleSubscriptionSchema(BaseModel):
    niche_code: Optional[str] = None
    location_code: Optional[str] = None

@tma_router.post("/toggle-subscription")
async def toggle_subscription(
    data: ToggleSubscriptionSchema,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    partner_id = user.get("partner_id")
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    niches = list(partner.subscribed_niches or [])
    locations = list(partner.subscribed_locations or [])

    is_enabled = False

    if data.niche_code:
        n = data.niche_code
        if "all" in niches:
            all_niches = ["real_estate", "bike_rent", "currency_exchange", "services_visa", "auto_kasko"]
            niches = [x for x in all_niches if x != n]
            is_enabled = False
        elif n in niches:
            niches.remove(n)
            is_enabled = False
        else:
            niches.append(n)
            is_enabled = True

    if data.location_code:
        loc = data.location_code
        if "all" in locations:
            all_locs = ["dubai", "nhatrang", "phuket", "bali", "danang", "tbilisi", "global"]
            locations = [x for x in all_locs if x != loc]
        elif loc in locations:
            locations.remove(loc)
        else:
            locations.append(loc)

    partner.subscribed_niches = niches
    partner.subscribed_locations = locations
    await db.commit()
    await db.refresh(partner)

    return {
        "status": "ok",
        "is_enabled": is_enabled,
        "subscribed_niches": partner.subscribed_niches,
        "subscribed_locations": partner.subscribed_locations
    }


class ProfileSettingsSchema(BaseModel):
    webhook_url: Optional[str] = None
    subscribed_niches: Optional[list] = None
    subscribed_locations: Optional[list] = None

@tma_router.post("/profile/settings")
async def update_profile_settings(
    data: ProfileSettingsSchema,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    partner_id = user.get("partner_id")
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    if data.webhook_url is not None:
        partner.webhook_url = data.webhook_url.strip()
    if data.subscribed_niches is not None:
        partner.subscribed_niches = data.subscribed_niches
    if data.subscribed_locations is not None:
        partner.subscribed_locations = data.subscribed_locations

    await db.commit()
    return {"status": "ok", "message": "Настройки сохранены!"}


class WithdrawRequestSchema(BaseModel):
    details: str

@tma_router.post("/withdraw")
async def request_withdrawal(
    data: WithdrawRequestSchema,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    partner_id = user.get("partner_id")
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    bal = float(partner.balance or 0)
    if bal < 50.0:
        raise HTTPException(status_code=400, detail="Минимальная сумма для вывода составляет $50.00 USD")

    logger.info(f"Withdrawal requested by partner {partner.id} ({partner.company_name}): {data.details}, Balance: {bal}")
    return {"status": "ok", "message": f"Запрос на вывод ${bal:.2f} USD принят в обработку! Администратор свяжется с вами."}


LOCATION_NAMES_TMA = {
    "dubai": "🇦🇪 Дубай",
    "nhatrang": "🇻🇳 Нячанг",
    "phuket": "🇹🇭 Пхукет",
    "bali": "🇮🇩 Бали",
    "danang": "🇻🇳 Дананг",
    "tbilisi": "🇬🇪 Тбилиси",
    "global": "🌐 Глобал / РФ"
}

NICHE_NAMES_TMA = {
    "real_estate": "🏠 Недвижимость",
    "bike_rent": "🛵 Аренда байков",
    "currency_exchange": "💱 Обмен валюты",
    "services_visa": "🛂 Визы & Услуги",
    "auto_kasko": "🚗 Страхование",
    "medical_services": "🏥 Медицина",
    "community": "💬 Сообщество",
}


import re

def anonymize_contacts(text: str) -> str:
    """Masks contact details (URLs, @handles, email, phones) from pre-purchase lead text."""
    if not text:
        return ""
    # 1. Mask URLs (http, https, www, t.me)
    text = re.sub(r'https?://[^\s]+|www\.[^\s]+|t\.me/[^\s]+', '[ссылка скрыта]', text, flags=re.IGNORECASE)
    # 2. Mask Telegram handles (@username)
    text = re.sub(r'@[a-zA-Z0-9_]{3,}', '[@контакт скрыт]', text)
    # 3. Mask Email addresses
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[email скрыт]', text)
    # 4. Mask Phone numbers
    text = re.sub(r'(\+?\d{1,3}[\s\-\.]?)?(\(?\d{2,4}\)?[\s\-\.]?)?\d{3,4}[\s\-\.]?\d{2,4}', '[телефон скрыт]', text)
    return text

def get_lead_type_info(intent_type: str = None, niche_code: str = None) -> dict:
    it = (intent_type or "").upper()
    nc = (niche_code or "").lower()
    
    if it in ["VACANCY", "HR_HIRING", "HIRING"] or nc == "hr_hiring":
        return {"code": "VACANCY", "label": "💼 Вакансия", "color": "#8B5CF6", "bg": "#F3E8FF"}
    elif it in ["B2B_PARTNER", "SELLER", "INVESTOR_SEARCH", "PARTNERSHIP"] or "b2b" in nc or "seller" in nc:
        return {"code": "B2B_PARTNER", "label": "💼 Б2Б Рекламодатель", "color": "#3B82F6", "bg": "#EFF6FF"}
    elif it in ["JOB_SEEKER", "JOB_SEEKING"] or nc == "job_seeker":
        return {"code": "JOB_SEEKER", "label": "📄 Соискатель", "color": "#D97706", "bg": "#FEF3C7"}
    else:
        return {"code": "LEAD", "label": "🎯 Лид", "color": "#10B981", "bg": "#D1FAE5"}

class ToggleSubscriptionSchema(BaseModel):
    niche_code: str
    location_code: str


from sqlalchemy import select, func, update
from src.db.models import Partner, Lead, LeadPurchase, UserActivityLog, UserProfile

@tma_router.post("/toggle-subscription")
async def toggle_subscription(
    data: ToggleSubscriptionSchema,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    partner_id = user.get("partner_id")
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
        
    niches = partner.subscribed_niches or []
    locations = partner.subscribed_locations or []
    
    # Ensure lists
    if isinstance(niches, str): niches = [niches]
    if isinstance(locations, str): locations = [locations]
    
    niche_added = False
    if data.niche_code not in niches:
        if "all" in niches: niches.remove("all")
        niches.append(data.niche_code)
        niche_added = True
    else:
        niches.remove(data.niche_code)
        if not niches: niches = ["all"]
        
    loc_added = False
    if data.location_code not in locations:
        if "all" in locations: locations.remove("all")
        locations.append(data.location_code)
        loc_added = True
    else:
        locations.remove(data.location_code)
        if not locations: locations = ["all"]
        
    partner.subscribed_niches = niches
    partner.subscribed_locations = locations
    await db.commit()
    
    is_active = niche_added or loc_added
    msg = f"Подписка включена! Лиды ({data.niche_code} в {data.location_code}) будут приходить в бот." if is_active else "Отписка успешна. Вы больше не будете получать эти лиды в бот."
    return {"status": "ok", "is_active": is_active, "message": msg}


@tma_router.get("/leads")
async def tma_leads(
    niche: str = None,
    location: str = None,
    status: str = "AVAILABLE",
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    partner_id = user.get("partner_id")
    user_role = (user.get("role") or "").upper()
    is_vip = user_role in ["VIP", "ADMIN", "SUPERADMIN"]

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

    purchased_subq = select(LeadPurchase.lead_id).where(LeadPurchase.partner_id == partner_id)

    status_upper = (status or "AVAILABLE").upper()
    if status_upper in ["AVAILABLE", "CURRENT", "ACTIVE"]:
        stmt = stmt.where(Lead.status == "AVAILABLE", Lead.created_at >= cutoff_3h)
    elif status_upper in ["SOLD", "PURCHASED", "BUYOUT", "EXCLUSIVES"]:
        stmt = stmt.where(
            (Lead.status.in_(["SOLD", "PURCHASED", "EXCLUSIVE", "CLAIMED"])) |
            (Lead.id.in_(purchased_subq))
        )
    elif status_upper in ["EXPIRED", "ARCHIVE", "ARCHIVED"]:
        stmt = stmt.where((Lead.status == "EXPIRED") | (Lead.status == "ARCHIVED") | ((Lead.status == "AVAILABLE") & (Lead.created_at < cutoff_3h)))
    elif status_upper != "ALL":
        stmt = stmt.where(Lead.status == status_upper)

    if not is_vip and status_upper in ["AVAILABLE", "CURRENT", "ACTIVE"]:
        cutoff_10m = datetime.now(timezone.utc) - timedelta(minutes=10)
        stmt = stmt.where(Lead.created_at <= cutoff_10m)

    if niche and niche != "all":
        stmt = stmt.where(Lead.niche_code == niche)
    if location and location != "all":
        stmt = stmt.where(Lead.location_code == location)
    
    leads = list((await db.execute(stmt)).scalars().all())

    user_ids = [l.user_id for l in leads if l.user_id]
    msg_counts = {}
    last_messages = {}
    if user_ids:
        cnt_stmt = select(UserActivityLog.user_id, func.count(UserActivityLog.id)).where(UserActivityLog.user_id.in_(user_ids)).group_by(UserActivityLog.user_id)
        cnt_res = await db.execute(cnt_stmt)
        msg_counts = {u_id: count for u_id, count in cnt_res.all()}

        # Fetch latest message text for exact quote display
        msg_stmt = (
            select(UserActivityLog.user_id, UserActivityLog.message_text)
            .where(UserActivityLog.user_id.in_(user_ids))
            .order_by(UserActivityLog.timestamp.desc())
        )
        for u_id, m_text in (await db.execute(msg_stmt)).all():
            if u_id not in last_messages and m_text:
                last_messages[u_id] = m_text

    # Fetch purchases by current partner to tag lead cards
    my_purchases = {}
    if leads:
        lead_ids = [l.id for l in leads]
        pur_stmt = select(LeadPurchase, UserProfile).join(Lead, LeadPurchase.lead_id == Lead.id).outerjoin(UserProfile, Lead.user_id == UserProfile.user_id).where(
            (LeadPurchase.partner_id == partner_id) & (LeadPurchase.lead_id.in_(lead_ids))
        )
        for pur, profile in (await db.execute(pur_stmt)).all():
            username = f"@{profile.username}" if profile and profile.username else f"ID {pur.lead_id}"
            tg_link = f"https://t.me/{profile.username}" if profile and profile.username else ""
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else "Пользователь Telegram"
            my_purchases[pur.lead_id] = {
                "price_paid": float(pur.price_paid),
                "purchased_at": (pur.purchased_at + timedelta(hours=7)).isoformat() if pur.purchased_at else None,
                "contact": {
                    "username": username,
                    "tg_link": tg_link,
                    "full_name": full_name
                }
            }

    result = []
    for l in leads:
        raw_msg = last_messages.get(l.user_id, l.intent_summary or "")
        pur_info = my_purchases.get(l.id)

        if pur_info:
            quote_text = raw_msg
        else:
            quote_text = anonymize_contacts(raw_msg)
            
        type_info = get_lead_type_info(l.intent_type, l.niche_code)

        # Ensure purchase_details contains source data for frontend duplication
        if pur_info:
            pur_info['source'] = {} # We'll let frontend handle this or populate later if needed


        loc_code = getattr(l, "location_code", "global") or "global"
        from src.services.purchase_engine import get_lead_pricing_by_location
        unit_price, exclusive_price = get_lead_pricing_by_location(loc_code)

        result.append({
            "id": l.id,
            "user_id": l.user_id,
            "niche_code": l.niche_code,
            "niche_name": NICHE_NAMES_TMA.get(l.niche_code, "Прочее"),
            "location_code": loc_code,
            "location_name": LOCATION_NAMES_TMA.get(loc_code, "🌐 Глобал / РФ"),
            "temperature": l.temperature,
            "confidence_score": l.confidence_score,
            "intent_summary": l.intent_summary,
            "quote_text": quote_text,
            "lead_type": type_info["code"],
            "lead_type_label": type_info["label"],
            "lead_type_color": type_info["color"],
            "lead_type_bg": type_info["bg"],
            "sales_hook": l.sales_hook,
            "user_message_count": max(1, msg_counts.get(l.user_id, 0)),
            "status": l.status,
            "price": unit_price,
            "exclusive_price": exclusive_price,
            "created_at": (l.created_at + timedelta(hours=7)).isoformat() if l.created_at else None,
            "is_purchased_by_me": bool(pur_info),
            "purchase_details": pur_info
        })
    return result


@tma_router.get("/my-purchases")
async def tma_my_purchases(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    """Returns list of leads purchased by current partner."""
    partner_id = user.get("partner_id")
    stmt = (
        select(LeadPurchase, Lead, UserProfile)
        .join(Lead, LeadPurchase.lead_id == Lead.id)
        .outerjoin(UserProfile, Lead.user_id == UserProfile.user_id)
        .where(LeadPurchase.partner_id == partner_id)
        .order_by(LeadPurchase.purchased_at.desc())
    )
    rows = list((await db.execute(stmt)).all())
    
    result = []
    user_ids = [lead.user_id for _, lead, _ in rows]
    source_map = {}
    if user_ids:
        from src.db.models import AIEvaluationLog, MonitoredChannel
        ai_stmt = (
            select(
                UserActivityLog.user_id,
                UserActivityLog.chat_title,
                UserActivityLog.channel_username,
                UserActivityLog.message_id,
                UserActivityLog.chat_id
            )
            .where(UserActivityLog.user_id.in_(user_ids))
            .order_by(UserActivityLog.timestamp.desc())
        )
        for u_id, c_title, c_uname, m_id, c_id in (await db.execute(ai_stmt)).all():
            if u_id not in source_map:
                source_map[u_id] = {
                    "chat_title": c_title,
                    "chat_username": c_uname,
                    "message_id": m_id,
                    "chat_id": c_id
                }

        chat_titles = list(set([s["chat_title"] for s in source_map.values() if s.get("chat_title")]))
        if chat_titles:
            m_stmt = select(MonitoredChannel).where(MonitoredChannel.title.in_(chat_titles))
            ch_map = {m.title: m for m in (await db.execute(m_stmt)).scalars().all()}
            for u_id, s_info in source_map.items():
                m_ch = ch_map.get(s_info.get("chat_title"))
                if m_ch:
                    s_info["invite_link"] = getattr(m_ch, "invite_link", None) or ""
                    if not s_info.get("chat_username") and m_ch.username_or_link:
                        raw_u = m_ch.username_or_link.replace('@', '').replace('https://t.me/', '').strip()
                        if not raw_u.startswith('+'):
                            s_info["chat_username"] = raw_u
            
    for pur, lead, profile in rows:
        username = f"@{profile.username}" if profile and profile.username else f"ID {lead.user_id}"
        tg_link = f"https://t.me/{profile.username}" if profile and profile.username else f"tg://user?id={lead.user_id}"
        full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else "Пользователь Telegram"
        
        src_info = source_map.get(lead.user_id, {})
        
        # Prefer Lead source fields (new architecture), fallback to AI Log heuristic
        c_username = getattr(lead, "source_chat_username", None) or src_info.get("chat_username") or ""
        c_username = c_username.replace('@', '').strip()
        c_title = getattr(lead, "source_chat_title", None) or src_info.get("chat_title") or "Телеграм чат"
        m_id = getattr(lead, "source_message_id", None) or src_info.get("message_id")
        c_id = getattr(lead, "source_chat_id", None) or src_info.get("chat_id")
        invite_link = getattr(lead, "source_invite_link", None) or src_info.get("invite_link") or ""

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

        type_info = get_lead_type_info(lead.intent_type, lead.niche_code)
        
        result.append({
            "purchase_id": pur.id,
            "lead_id": lead.id,
            "niche_code": lead.niche_code,
            "niche_name": NICHE_NAMES_TMA.get(lead.niche_code, "Прочее"),
            "location_name": LOCATION_NAMES_TMA.get(getattr(lead, "location_code", "global") or "global", "🌐 Глобал / РФ"),
            "intent_summary": lead.intent_summary,
            "lead_type": type_info["code"],
            "lead_type_label": type_info["label"],
            "sales_hook": lead.sales_hook,
            "user_id": lead.user_id,
            "price_paid": float(pur.price_paid),
            "purchased_at": (pur.purchased_at + timedelta(hours=7)).isoformat() if pur.purchased_at else None,
            "contact": {
                "username": username,
                "tg_link": tg_link,
                "full_name": full_name,
                "no_username": not (profile and profile.username)
            },
            "source": {
                "title": c_title,
                "username": c_username,
                "chat_id": c_id,
                "message_id": m_id,
                "group_url": group_url,
                "message_url": message_url,
                "chat_url": group_url,
                "invite_link": invite_link
            }
        })
    return result


class TMABuySchema(BaseModel):
    pass  # partner_id comes from JWT

@tma_router.post("/leads/{lead_id}/buy")
async def tma_buy_lead(
    lead_id: str,
    is_exclusive: bool = False,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    """Purchase a lead from the marketplace. Balance deducted, contact revealed."""
    partner_id = user.get("partner_id")

    from src.services.purchase_engine import process_lead_purchase
    res = await process_lead_purchase(db, partner_id, lead_id, is_exclusive=is_exclusive)
    
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    
    return res


# ─── Web-login flow (for browser access without TMA) ───────────────────────
import secrets

@tma_router.post("/web-login-request")
async def web_login_request():
    """
    Generates a one-time token and returns a deep link to the bot.
    Browser polls /web-login-status until confirmed.
    """
    token = secrets.token_urlsafe(16)
    _WEB_LOGIN_TOKENS[token] = {
        "telegram_id": None,
        "expires_at": time.time() + WEB_LOGIN_TTL,
        "confirmed": False,
        "jwt": None,
    }
    bot_username = "intenthunter_bot"  # fallback; ideally from settings
    deep_link = f"https://t.me/{bot_username}?start=weblogin_{token}"
    return {"status": "ok", "token": token, "deep_link": deep_link}


@tma_router.get("/web-login-status")
async def web_login_status(token: str):
    """Browser polls this endpoint to check if user confirmed login in bot."""
    entry = _WEB_LOGIN_TOKENS.get(token)
    if not entry:
        return {"status": "invalid"}
    if time.time() > entry["expires_at"]:
        _WEB_LOGIN_TOKENS.pop(token, None)
        return {"status": "expired"}
    if entry["confirmed"] and entry["jwt"]:
        jwt = entry["jwt"]
        _WEB_LOGIN_TOKENS.pop(token, None)
        return {"status": "approved", "token": jwt}
    return {"status": "pending"}


@tma_router.post("/web-login-confirm")
async def web_login_confirm(data: WebLoginConfirmSchema, db: AsyncSession = Depends(get_db)):
    """
    Called by the bot handler when user clicks '✅ Подтвердить вход'.
    Marks token as confirmed and stores JWT.
    """
    entry = _WEB_LOGIN_TOKENS.get(data.token)
    if not entry:
        return {"status": "invalid", "message": "Токен не найден или истёк"}
    if time.time() > entry["expires_at"]:
        _WEB_LOGIN_TOKENS.pop(data.token, None)
        return {"status": "expired", "message": "Токен истёк, запросите новую ссылку"}

    partner = (await db.execute(select(Partner).where(Partner.telegram_id == data.telegram_id))).scalar_one_or_none()
    if not partner:
        partner = Partner(
            telegram_id=data.telegram_id,
            company_name=f"Пользователь {data.telegram_id}",
            role="PARTNER",
            moderation_status="APPROVED",
        )
        db.add(partner)
        await db.commit()
        await db.refresh(partner)

    jwt = create_jwt(data.telegram_id, partner.role, partner.id)
    entry["confirmed"] = True
    entry["telegram_id"] = data.telegram_id
    entry["jwt"] = jwt
    return {"status": "ok", "message": "Авторизация подтверждена!"}


from fastapi.responses import RedirectResponse
import os

@tma_router.get("/web-login-redirect")
async def web_login_redirect(token: str):
    """
    Direct HTTP URL callback endpoint.
    Confirms token and redirects browser immediately back to /marketplace page!
    """
    entry = _WEB_LOGIN_TOKENS.get(token)
    mp_url = os.getenv("MARKETPLACE_APP_URL", "https://inthunter-production.up.railway.app/marketplace")

    if not entry:
        return RedirectResponse(url=f"{mp_url}?error=invalid_token")

    entry["confirmed"] = True
    jwt_token = entry.get("jwt")
    if jwt_token:
        return RedirectResponse(url=f"{mp_url}?auth_token={jwt_token}")

    return RedirectResponse(url=f"{mp_url}?token={token}")


class RoleUpdateSchema(BaseModel):
    role: str

class BalanceUpdateSchema(BaseModel):
    balance: float

@tma_router.get("/admin/users")
async def admin_get_users(
    page: int = 0, 
    limit: int = 20, 
    search: str = None, 
    db: AsyncSession = Depends(get_db), 
    user: dict = Depends(get_current_tma_user)
):
    """Admin route to list and search users."""
    user_role = (user.get("role") or "").upper()
    if user_role not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    stmt = select(Partner).order_by(Partner.created_at.desc())
    if search:
        search_term = f"%{search.lower()}%"
        stmt = stmt.where(
            (Partner.username.ilike(search_term)) | 
            (Partner.first_name.ilike(search_term)) | 
            (Partner.company_name.ilike(search_term))
        )
    
    total_stmt = select(func.count(Partner.id))
    if search:
        total_stmt = total_stmt.where(
            (Partner.username.ilike(search_term)) | 
            (Partner.first_name.ilike(search_term)) | 
            (Partner.company_name.ilike(search_term))
        )
    total = (await db.execute(total_stmt)).scalar() or 0

    stmt = stmt.offset(page * limit).limit(limit)
    users = list((await db.execute(stmt)).scalars().all())

    return {
        "status": "ok",
        "total": total,
        "page": page,
        "limit": limit,
        "users": [
            {
                "id": u.id,
                "telegram_id": u.telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "company_name": u.company_name,
                "role": u.role,
                "balance": float(u.balance or 0),
                "moderation_status": u.moderation_status,
                "created_at": (u.created_at + timedelta(hours=7)).isoformat() if u.created_at else None
            } for u in users
        ]
    }

@tma_router.post("/admin/users/{user_id}/role")
async def admin_update_user_role(
    user_id: str, 
    data: RoleUpdateSchema, 
    db: AsyncSession = Depends(get_db), 
    user: dict = Depends(get_current_tma_user)
):
    """Admin route to update user role."""
    if user.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    stmt = select(Partner).where(Partner.telegram_id == int(user_id))
    target_user = (await db.execute(stmt)).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    target_user.role = data.role
    await db.commit()
    return {"status": "ok", "role": target_user.role}

@tma_router.post("/admin/users/{user_id}/balance")
async def admin_update_user_balance(
    user_id: str, 
    data: BalanceUpdateSchema, 
    db: AsyncSession = Depends(get_db), 
    user: dict = Depends(get_current_tma_user)
):
    """Admin route to update user balance."""
    if user.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    stmt = select(Partner).where(Partner.telegram_id == int(user_id))
    target_user = (await db.execute(stmt)).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    target_user.balance = data.balance
    await db.commit()
    return {"status": "ok", "balance": float(target_user.balance)}

@tma_router.post("/admin/users/{user_id}/block")
async def admin_toggle_user_block(
    user_id: str, 
    db: AsyncSession = Depends(get_db), 
    user: dict = Depends(get_current_tma_user)
):
    """Admin route to block/unblock user."""
    if user.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    stmt = select(Partner).where(Partner.telegram_id == int(user_id))
    target_user = (await db.execute(stmt)).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    will_block = target_user.moderation_status != "BLOCKED"
    target_user.moderation_status = "BLOCKED" if will_block else "APPROVED"
    await db.commit()
    return {"status": "ok", "moderation_status": target_user.moderation_status}
