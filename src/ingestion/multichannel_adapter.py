import logging
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy import select

from src.db.session import AsyncSessionLocal
from src.db.models import UserProfile, UserActivityLog, Lead
from src.ai.scorer import evaluate_user_timeline
from src.bot.alert_bot import broadcast_lead_alert

logger = logging.getLogger(__name__)

PLATFORM_LABELS = {
    "telegram": "✈️ Telegram",
    "max": "💬 MAX Messenger",
    "vk": "🔵 ВКонтакте",
    "ok": "🟠 Одноклассники",
    "custom": "🌐 Custom Webhook"
}


def generate_synthetic_user_id(user_identifier: str) -> int:
    """Generates a stable 64-bit integer hash for non-numeric usernames/user_ids."""
    if isinstance(user_identifier, int):
        return abs(user_identifier)
    if isinstance(user_identifier, str) and user_identifier.isdigit():
        return abs(int(user_identifier))
    
    # Hash string to 63-bit integer for BigInteger compatibility
    hash_bytes = hashlib.md5(user_identifier.encode('utf-8')).digest()
    return int.from_bytes(hash_bytes[:8], byteorder='big') % (2**62)


class MultiChannelAdapter:
    @staticmethod
    async def process_inbound_message(
        session,
        platform: str,
        chat_title: str,
        message_text: str,
        user_id_raw: Any,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        location_code: Optional[str] = "global",
        message_id: int = 1
    ) -> Dict[str, Any]:
        """
        Universal message intake pipeline for MAX, VK, OK & Custom API.
        Normalizes inbound message, updates UserProfile, logs to UserActivityLog,
        and triggers AI Lead Evaluation.
        """
        clean_platform = (platform or "custom").lower().strip()
        numeric_user_id = generate_synthetic_user_id(str(user_id_raw or "anonymous"))
        clean_username = username.strip().lstrip('@') if username else f"{clean_platform}_user_{numeric_user_id % 10000}"
        clean_first_name = first_name or clean_username

        # 1. Upsert UserProfile
        user_res = await session.execute(select(UserProfile).where(UserProfile.user_id == numeric_user_id))
        user_profile = user_res.scalar_one_or_none()

        if not user_profile:
            user_profile = UserProfile(
                user_id=numeric_user_id,
                username=clean_username,
                first_name=clean_first_name
            )
            session.add(user_profile)
            await session.flush()

        # 2. Save UserActivityLog with platform
        log_entry = UserActivityLog(
            user_id=numeric_user_id,
            chat_id=generate_synthetic_user_id(chat_title or "general_chat"),
            chat_title=chat_title or f"{clean_platform.upper()} Group",
            message_id=message_id,
            message_text=message_text,
            platform=clean_platform,
            timestamp=datetime.now(timezone.utc)
        )
        session.add(log_entry)
        await session.commit()
        await session.refresh(log_entry)

        logger.info(f"📥 [{clean_platform.upper()}] Ingested message from @{clean_username} in '{chat_title}': '{message_text[:40]}...'")

        # 3. Fetch user recent history for AI Evaluation
        logs_res = await session.execute(
            select(UserActivityLog)
            .where(UserActivityLog.user_id == numeric_user_id)
            .order_by(UserActivityLog.timestamp.desc())
            .limit(5)
        )
        recent_logs = list(logs_res.scalars().all())

        # 4. Trigger AI Scorer
        eval_result = await evaluate_user_timeline(numeric_user_id, session, recent_logs)

        lead_created = False
        lead_id = None

        if eval_result and eval_result.is_lead:
            new_lead = Lead(
                user_id=numeric_user_id,
                niche_code=eval_result.niche_code or "community",
                location_code=location_code or "global",
                platform=clean_platform,
                temperature=eval_result.temperature or "WARM",
                confidence_score=eval_result.confidence_score or 0.85,
                intent_summary=eval_result.intent_summary or message_text[:100],
                sales_hook=eval_result.sales_hook or message_text[:150],
                price=1.00,
                created_at=datetime.now(timezone.utc)
            )
            session.add(new_lead)
            await session.commit()
            await session.refresh(new_lead)

            lead_created = True
            lead_id = new_lead.id
            logger.info(f"🎯 [{clean_platform.upper()}] NEW LEAD QUALIFIED #{new_lead.id} (@{clean_username}, Niche: {new_lead.niche_code})")

            # Push live alert to subscribers & superadmins
            try:
                await broadcast_lead_alert(numeric_user_id, eval_result, recent_logs)
            except Exception as e:
                logger.error(f"Notice pushing alert for lead {new_lead.id}: {e}")

        return {
            "status": "success",
            "platform": clean_platform,
            "lead_created": lead_created,
            "lead_id": lead_id,
            "user_id": numeric_user_id,
            "username": clean_username,
            "is_lead": eval_result.is_lead,
            "niche_code": getattr(eval_result, "niche_code", None)
        }

    @staticmethod
    def parse_max_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parses webhook payload from MAX Bot API."""
        # Handles standard MAX Bot Webhook format
        event = payload.get("event") or payload.get("type") or "message_new"
        data = payload.get("data") or payload.get("object") or payload

        msg = data.get("message") or data
        sender = data.get("sender") or data.get("user") or {}

        return {
            "platform": "max",
            "chat_title": data.get("chat_title") or data.get("chat_name") or "MAX Group Chat",
            "message_text": msg.get("text") or msg.get("body") or str(payload.get("text") or ""),
            "user_id_raw": sender.get("user_id") or sender.get("id") or payload.get("user_id") or "max_user",
            "username": sender.get("username") or sender.get("screen_name"),
            "first_name": sender.get("first_name") or sender.get("name") or "MAX User",
            "location_code": payload.get("location_code") or "global"
        }

    @staticmethod
    def parse_vk_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parses webhook payload from VK Callback API."""
        obj = payload.get("object") or {}
        msg = obj.get("message") or obj
        from_id = msg.get("from_id") or obj.get("user_id") or "vk_user"

        return {
            "platform": "vk",
            "chat_title": payload.get("chat_title") or f"VK Group (ID {msg.get('peer_id', 'chat')})",
            "message_text": msg.get("text") or "",
            "user_id_raw": from_id,
            "username": f"id{from_id}" if isinstance(from_id, int) else str(from_id),
            "first_name": "VK User",
            "location_code": payload.get("location_code") or "global"
        }

    @staticmethod
    def parse_ok_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parses webhook payload from Odnoklassniki Bot API."""
        sender = payload.get("sender") or {}
        msg = payload.get("message") or {}

        return {
            "platform": "ok",
            "chat_title": payload.get("chat_title") or "ОК Сообщество",
            "message_text": msg.get("text") or payload.get("text") or "",
            "user_id_raw": sender.get("user_id") or sender.get("id") or "ok_user",
            "username": sender.get("name") or "ok_user",
            "first_name": sender.get("name") or "OK User",
            "location_code": payload.get("location_code") or "global"
        }
