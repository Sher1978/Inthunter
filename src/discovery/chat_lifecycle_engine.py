import asyncio
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal
from src.db.models import DiscoveredChat, MonitoredChannel, BlacklistedChat, UserActivityLog, Lead
from src.discovery.chat_discovery import (
    run_passive_regex_discovery,
    run_global_keyword_search,
    run_recursive_monitored_channels_mining
)

logger = logging.getLogger("intent_hunter.chat_lifecycle")


class ChatLifecycleEngine:
    """
    Unified Chat Lifecycle & Recycling Combine Engine.
    Integrates 3 continuous background phases:
    1. Performance Recycling (48-hour efficiency audit): Auto-prunes/recycles channels that produced 0 messages in 48 hours.
    2. Multi-GEO Active Discovery: Queries Grok AI & Pyrogram MTProto global search across Moscow, Dubai, Bali, Vietnam, Thailand.
    3. AI Quality Audit & Instant Promotion: Evaluates pending candidates and promotes approved ones into active monitoring.
    """

    @staticmethod
    async def run_48h_performance_recycling(session: AsyncSession, threshold_hours: int = 48) -> Dict[str, Any]:
        """
        Scans all JOINED channels. If a channel has produced 0 messages for >= 48 hours,
        it is pruned to free up capacity for active candidate groups.
        """
        now_utc = datetime.now(timezone.utc)
        cutoff_48h = now_utc - timedelta(hours=threshold_hours)

        res = await session.execute(
            select(MonitoredChannel).where(MonitoredChannel.status == "JOINED")
        )
        channels = list(res.scalars().all())
        recycled_channels = []

        for ch in channels:
            # Calculate channel age in hours
            created_dt = ch.created_at.replace(tzinfo=timezone.utc) if ch.created_at and ch.created_at.tzinfo is None else (ch.created_at or now_utc)
            age_hours = (now_utc - created_dt).total_seconds() / 3600.0

            if age_hours < threshold_hours:
                continue

            clean_u = (ch.username_or_link or "").lstrip("@")
            title_key = (ch.title or clean_u or "___").strip()

            # Check messages count in last 48 hours
            msg_count = (await session.execute(
                select(func.count(UserActivityLog.id)).where(
                    (UserActivityLog.chat_title.ilike(f"%{title_key}%")) |
                    (UserActivityLog.chat_title.ilike(f"%{clean_u}%")),
                    UserActivityLog.timestamp >= cutoff_48h
                )
            )).scalar() or 0

            # If 0 messages in last 48 hours, recycle channel
            if msg_count == 0:
                reason = f"48-часовой авто-отсев неактивных каналов: 0 сообщений за {int(age_hours)} ч."
                logger.info(f"♻️ RECYCLING channel {ch.username_or_link} [{ch.location_code}]: {reason}")

                from src.discovery.chat_discovery import blacklist_channel_permanently
                await blacklist_channel_permanently(
                    session,
                    username_or_link=ch.username_or_link,
                    title=ch.title,
                    reason=f"48-часовой авто-отсев (0 сообщений за {int(age_hours)}ч).",
                    score=30
                )

                recycled_channels.append({
                    "username": ch.username_or_link,
                    "title": ch.title or ch.username_or_link,
                    "location_code": ch.location_code or "global",
                    "reason": reason
                })

                await session.delete(ch)

        if recycled_channels:
            await session.commit()
            try:
                from src.bot.alert_bot import notify_superadmins_system_alert
                items_str = "\n".join([f"• 🗑 <b>{c['title']}</b> ({c['username']}) [{c['location_code'].upper()}]" for c in recycled_channels[:10]])
                card = (
                    f"♻️ <b>АВТОМАТИЧЕСКАЯ РОТАЦИЯ КАНАЛОВ (48 ЧАСОВ БЕЗ СООБЩЕНИЙ)</b>\n"
                    f"───────────────────────────\n\n"
                    f"Выведено молчащих каналов: <b>{len(recycled_channels)}</b> шт.\n\n"
                    f"{items_str}\n\n"
                    f"🔄 <i>Освободившиеся слоты автоматически замещаются активными целевыми группами из ИИ-поиска.</i>"
                )
                asyncio.create_task(notify_superadmins_system_alert(card))
            except Exception as notify_err:
                logger.warning(f"Recycling notice dispatch error: {notify_err}")

        return {
            "recycled_count": len(recycled_channels),
            "recycled_items": recycled_channels
        }

    @classmethod
    async def run_unified_lifecycle_cycle(cls) -> Dict[str, Any]:
        """
        Executes complete unified lifecycle pass:
        1. 7-Day Performance Recycling
        2. Passive & Active Grok Multi-GEO Discovery
        3. AI Quality Audit & Scraper Loop Restart
        """
        logger.info("🚀 Starting Unified Chat Lifecycle & Recycling Combine cycle...")
        from src.discovery.chat_manager import ChatDiscoveryManager

        async with AsyncSessionLocal() as session:
            # 1. 48-Hour Efficiency Filter & Recycling
            recycle_res = await cls.run_48h_performance_recycling(session)

            # 2. Passive & Active Discovery Across Target GEOs
            passive_count = await run_passive_regex_discovery(session)
            mined_count = await run_recursive_monitored_channels_mining(session)
            active_count = await run_global_keyword_search(session)

        # 3. AI Quality Audit for Candidate Queue
        audit_res = await ChatDiscoveryManager.process_pending_audits(limit=25)

        logger.info(
            f"🔄 Unified Lifecycle Pass Complete: "
            f"Recycled={recycle_res['recycled_count']}, "
            f"Discovered={active_count + passive_count + mined_count}, "
            f"Audited={audit_res}"
        )

        return {
            "status": "ok",
            "recycled_stats": recycle_res,
            "passive_discovered": passive_count,
            "mined_discovered": mined_count,
            "active_discovered": active_count,
            "audited_stats": audit_res
        }
