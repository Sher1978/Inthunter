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
    try:
        pairs = {}
        for chunk in init_data.split("&"):
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                pairs[k] = unquote(v)
        
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            raise HTTPException(status_code=401, detail="Missing hash in initData")

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(pairs.items())
        )
        secret_key = hmac.new(
            b"WebAppData",
            settings.TELEGRAM_BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, received_hash):
            raise HTTPException(status_code=401, detail="Invalid Telegram signature")

        user_data = json.loads(pairs.get("user", "{}"))
        if not user_data.get("id"):
            raise HTTPException(status_code=401, detail="No user data in initData")
        return user_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"initData verification error: {e}")
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
    if not partner:
        display_name = first_name or username or f"Пользователь {telegram_id}"
        partner = Partner(
            telegram_id=telegram_id,
            company_name=display_name,
            role="PARTNER",
            moderation_status="APPROVED",
        )
        db.add(partner)
        await db.commit()
        await db.refresh(partner)
        logger.info(f"Auto-registered new TMA partner: {telegram_id} ({display_name})")
    return partner


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class TMAAuthSchema(BaseModel):
    init_data: str

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
    user_data = verify_telegram_init_data(data.init_data)
    telegram_id = int(user_data["id"])
    first_name = user_data.get("first_name", "")
    username = user_data.get("username", "")

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
        "company_name": partner.company_name,
        "role": partner.role,
        "balance": float(partner.balance or 0),
        "moderation_status": partner.moderation_status,
    }


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


from sqlalchemy import select, func
from src.db.models import Partner, Lead, LeadPurchase, UserActivityLog

@tma_router.get("/leads")
async def tma_leads(
    niche: str = None,
    location: str = None,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    user_role = (user.get("role") or "").upper()
    is_vip = user_role in ["VIP", "ADMIN", "SUPERADMIN"]

    cutoff_10m = datetime.now(timezone.utc) - timedelta(minutes=10)

    stmt = select(Lead).where(Lead.status == "AVAILABLE").order_by(Lead.created_at.desc()).limit(limit)
    if not is_vip:
        # Non-VIP users only see leads created at least 10 minutes ago
        stmt = stmt.where(Lead.created_at <= cutoff_10m)

    if niche and niche != "all":
        stmt = stmt.where(Lead.niche_code == niche)
    if location and location != "all":
        stmt = stmt.where(Lead.location_code == location)
    
    leads = list((await db.execute(stmt)).scalars().all())

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
            "niche_name": NICHE_NAMES_TMA.get(l.niche_code, "Прочее"),
            "location_code": getattr(l, "location_code", "global") or "global",
            "location_name": LOCATION_NAMES_TMA.get(getattr(l, "location_code", "global") or "global", "🌐 Глобал / РФ"),
            "temperature": l.temperature,
            "confidence_score": l.confidence_score,
            "intent_summary": l.intent_summary,
            "sales_hook": l.sales_hook,
            "user_message_count": max(1, msg_counts.get(l.user_id, 0)),
            "status": l.status,
            "price": float(l.price or 1.0),
            "created_at": (l.created_at + timedelta(hours=7)).isoformat() if l.created_at else None,
        }
        for l in leads
    ]


@tma_router.get("/my-purchases")
async def tma_my_purchases(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_tma_user)
):
    """Returns list of leads purchased by current partner."""
    partner_id = user.get("partner_id")
    stmt = (
        select(LeadPurchase, Lead)
        .join(Lead, LeadPurchase.lead_id == Lead.id)
        .where(LeadPurchase.partner_id == partner_id)
        .order_by(LeadPurchase.purchased_at.desc())
    )
    rows = list((await db.execute(stmt)).all())
    return [
        {
            "purchase_id": pur.id,
            "lead_id": lead.id,
            "niche_code": lead.niche_code,
            "niche_name": NICHE_NAMES_TMA.get(lead.niche_code, "Прочее"),
            "location_name": LOCATION_NAMES_TMA.get(getattr(lead, "location_code", "global") or "global", "🌐 Глобал / РФ"),
            "intent_summary": lead.intent_summary,
            "sales_hook": lead.sales_hook,
            "user_id": lead.user_id,
            "price_paid": float(pur.price_paid),
            "purchased_at": (pur.purchased_at + timedelta(hours=7)).isoformat() if pur.purchased_at else None,
        }
        for pur, lead in rows
    ]


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
