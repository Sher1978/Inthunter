import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.config import settings
from src.db.session import get_db
from src.db.models import Partner

security = HTTPBearer(auto_error=False)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=30)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt

def decode_access_token(token: str):
    try:
        decoded_data = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return decoded_data
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    token = credentials.credentials
    token_data = decode_access_token(token)
    
    partner_id = token_data.get("partner_id")
    role = token_data.get("role", "DEMO")
    if not partner_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token data")
    
    if partner_id == "legacy_admin":
        return Partner(id="legacy_admin", telegram_id=260669598, company_name="Admin", role="SUPERADMIN", moderation_status="APPROVED", balance=1000.0)
        
    stmt = select(Partner).where(Partner.id == partner_id)
    partner = (await db.execute(stmt)).scalar_one_or_none()
    
    if not partner:
        if role in ["SUPERADMIN", "ADMIN"]:
            return Partner(id=partner_id, telegram_id=260669598, company_name="Admin", role=role, moderation_status="APPROVED", balance=1000.0)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        
    return partner

async def get_optional_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> Optional[Partner]:
    if not credentials:
        return None
    try:
        token = credentials.credentials
        token_data = decode_access_token(token)
        partner_id = token_data.get("partner_id")
        role = token_data.get("role", "DEMO")
        if not partner_id:
            return None
        if partner_id == "legacy_admin":
            return Partner(id="legacy_admin", telegram_id=260669598, company_name="Admin", role="SUPERADMIN", moderation_status="APPROVED", balance=1000.0)
        stmt = select(Partner).where(Partner.id == partner_id)
        partner = (await db.execute(stmt)).scalar_one_or_none()
        if not partner and role in ["SUPERADMIN", "ADMIN"]:
            return Partner(id=partner_id, telegram_id=260669598, company_name="Admin", role=role, moderation_status="APPROVED", balance=1000.0)
        return partner
    except Exception:
        return None

async def require_admin(current_user: Partner = Depends(get_current_user)):
    if current_user.role not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return current_user

async def require_superadmin(current_user: Partner = Depends(get_current_user)):
    if current_user.role != "SUPERADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin privileges required")
    return current_user
