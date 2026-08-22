import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pyrogram import Client
from pyrogram.errors import (
    PeerFlood, UserBannedInChannel, UserPrivacyRestricted,
    FloodWait, AuthKeyUnregistered, SessionRevoked, UserDeactivated
)

from src.config import settings
from src.db.models import OutreachAccount

logger = logging.getLogger("intent_hunter.outreach.account_manager")

def parse_proxy_url(proxy_url: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parses a proxy URL string into Pyrogram proxy dict format:
    http://user:pass@host:port or socks5://user:pass@host:port
    """
    if not proxy_url or not proxy_url.strip():
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url.strip())
        scheme = parsed.scheme.lower()
        
        scheme_map = {
            "http": "http",
            "https": "http",
            "socks4": "socks4",
            "socks5": "socks5"
        }
        ptype = scheme_map.get(scheme, "http")
        
        proxy_dict = {
            "scheme": ptype,
            "hostname": parsed.hostname,
            "port": parsed.port or (80 if ptype == "http" else 1080)
        }
        if parsed.username:
            proxy_dict["username"] = parsed.username
        if parsed.password:
            proxy_dict["password"] = parsed.password
            
        return proxy_dict
    except Exception as e:
        logger.error(f"Error parsing proxy URL '{proxy_url}': {e}")
        return None


class AccountManager:
    """
    Manages Telegram outreach account rotation, Pyrogram Client initialization with proxy,
    health checks, rate limiting, and daily cooldowns.
    """
    
    @staticmethod
    async def get_available_account(session: AsyncSession) -> Optional[OutreachAccount]:
        """
        Selects an ACTIVE account with daily_sent_count < max_daily_limit and oldest last_used_at.
        """
        # First auto-recover accounts whose COOL_DOWN has expired (>= 24h)
        cooldown_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        await session.execute(
            update(OutreachAccount)
            .where(
                OutreachAccount.status == "COOL_DOWN",
                OutreachAccount.last_used_at <= cooldown_threshold
            )
            .values(status="ACTIVE", daily_sent_count=0)
        )
        await session.commit()

        # Query best active account
        stmt = (
            select(OutreachAccount)
            .where(
                OutreachAccount.status == "ACTIVE",
                OutreachAccount.daily_sent_count < OutreachAccount.max_daily_limit
            )
            .order_by(OutreachAccount.last_used_at.asc().nulls_first())
            .limit(1)
        )
        account = (await session.execute(stmt)).scalars().first()
        return account

    @staticmethod
    def create_pyrogram_client(account: OutreachAccount, name: Optional[str] = None) -> Client:
        """
        Creates a Pyrogram Client instance using session_string and bound proxy.
        """
        client_name = name or f"outreach_acc_{account.id}"
        proxy = parse_proxy_url(account.proxy_url)
        
        app = Client(
            name=client_name,
            api_id=settings.TELEGRAM_API_ID or 6,
            api_hash=settings.TELEGRAM_API_HASH or "eb6e0a7f5ee9d3b00d644d715d0130a0",
            session_string=account.session_string,
            proxy=proxy,
            in_memory=True
        )
        return app

    @staticmethod
    async def handle_account_error(account_id: int, db: AsyncSession, error: Exception) -> str:
        """
        Handles Pyrogram execution errors and updates account status.
        Returns detailed error string for logging.
        """
        err_type = type(error).__name__
        err_msg = str(error)
        
        account = (await db.execute(select(OutreachAccount).where(OutreachAccount.id == account_id))).scalar_one_or_none()
        if not account:
            return f"Account {account_id} not found"

        if isinstance(error, (PeerFlood, UserBannedInChannel)):
            account.status = "COOL_DOWN"
            account.error_log = f"PeerFlood / Ban detected: {err_msg}"
            logger.warning(f"⚠️ Account #{account.id} ({account.phone_number}) placed in COOL_DOWN (24h) due to {err_type}")

        elif isinstance(error, (AuthKeyUnregistered, SessionRevoked, UserDeactivated)):
            account.status = "BANNED"
            account.error_log = f"Session invalidated/banned: {err_msg}"
            logger.error(f"❌ Account #{account.id} ({account.phone_number}) marked BANNED due to {err_type}")

        elif isinstance(error, FloodWait):
            wait_s = getattr(error, "value", 300)
            account.status = "COOL_DOWN"
            account.last_used_at = datetime.now(timezone.utc) + timedelta(seconds=wait_s)
            account.error_log = f"FloodWait ({wait_s}s): {err_msg}"
            logger.warning(f"⏳ Account #{account.id} FloodWait {wait_s}s, cooling down.")

        elif isinstance(error, UserPrivacyRestricted):
            account.error_log = f"User Privacy Restricted: {err_msg}"
            logger.info(f"Target user privacy settings prevented message sending.")
        else:
            account.error_log = f"Other error ({err_type}): {err_msg}"

        await db.commit()
        return f"{err_type}: {err_msg}"

    @staticmethod
    async def reset_daily_limits(db: AsyncSession):
        """
        Cron task to reset daily message counts for all active outreach accounts at midnight UTC.
        """
        await db.execute(
            update(OutreachAccount)
            .where(OutreachAccount.status.in_(["ACTIVE", "COOL_DOWN"]))
            .values(daily_sent_count=0)
        )
        await db.commit()
        logger.info("🔄 Outreach Account daily limits reset to 0.")
