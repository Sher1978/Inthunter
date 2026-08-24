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
    1. Performance Recycling (7-day efficiency audit): Auto-prunes/recycles channels that produced 0 leads in 7 days.
    2. Multi-GEO Active Discovery: Queries Grok AI & Pyrogram MTProto global search across Moscow, Dubai, Bali, Vietnam, Thailand.
    3. AI Quality Audit & Instant Promotion: Evaluates pending candidates and promotes approved ones into active monitoring.
    """

    @staticmethod
    async def run_7day_performance_recycling(session: AsyncSession, threshold_days: int = 7) -> Dict[str, Any]:
        """
        Scans all JOINED channels. If a channel has been monitored for >= threshold_days
        and produced 0 leads (or zero message activity for 7 days), it is recycled/pruned.
        """
        now_utc = datetime.now(timezone.utc)
        cutoff_7d = now_utc - timedelta(days=threshold_days)

        res = await session.execute(
            select(MonitoredChannel).where(MonitoredChannel.status == "JOINED")
        )
        channels = list(res.scalars().all())

        recycled_channels = []

        for ch in channels:
            # Calculate channel age
            created_dt = ch.created_at.replace(tzinfo=timezone.utc) if ch.created_at and ch.created_at.tzinfo is None else (ch.created_at or now_utc)
            age_days = (now_utc - created_dt).days
            if age_days < threshold_days:
                continue

            title_key = (ch.title or ch.username_or_link or "").strip()

            # Check messages count in last 7 days
            msg_count = (await session.execute(
                select(func.count(UserActivityLog.id)).where(
                    UserActivityLog.chat_title.ilike(f"%{title_key}%"),
                    UserActivityLog.timestamp >= cutoff_7d
                )
            )).scalar() or 0

            # Check leads count produced by users from this channel in last 7 days
            user_ids = list((await session.execute(
                select(UserActivityLog.user_id).where(
                    UserActivityLog.chat_title.ilike(f"%{title_key}%")
                ).distinct()
            )).scalars().all())

            lead_count = 0
            if user_ids:
                lead_count = (await session.execute(
                    select(func.count(Lead.id)).where(
                        Lead.user_id.in_(user_ids),
                        Lead.created_at >= cutoff_7d
                    )
                )).scalar() or 0

            # If 0 leads after 7+ days or completely idle (< 3 messages in 7d), recycle channel
            if lead_count == 0 or msg_count < 3:
                reason = f"7-дневный авто-отсев: {lead_count} лидов, {msg_count} сообщений за {age_days} дней."
                logger.info(f"♻️ RECYCLING channel {ch.username_or_link} [{ch.location_code}]: {reason}")

                from src.discovery.chat_discovery import blacklist_channel_permanently
                await blacklist_channel_permanently(
                    session,
                    username_or_link=ch.username_or_link,
                    title=ch.title,
                    reason=f"7-дневный авто-отсев (0 лидов за {age_days}д). Отсеян навсегда.",
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
            # Notify Superadmins of automated recycling pass
            try:
                from src.bot.alert_bot import notify_superadmins_system_alert
                items_str = "\n".join([f"• 🗑 <b>{c['title']}</b> ({c['username']}) [{c['location_code'].upper()}]" for c in recycled_channels[:10]])
                card = (
                    f"♻️ <b>АВТОМАТИЧЕСКАЯ РОТАЦИЯ КАНАЛОВ (7 ДНЕЙ БЕЗ ЛИДОВ)</b>\n"
                    f"───────────────────────────\n\n"
                    f"Выведено неэффективных каналов: <b>{len(recycled_channels)}</b> шт.\n\n"
                    f"{items_str}\n\n"
                    f"🔄 <i>Освободившиеся слоты автоматически замещаются новыми целевыми каналами из ИИ-поиска.</i>"
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
            # 1. 7-Day Efficiency Filter & Recycling
            recycle_res = await cls.run_7day_performance_recycling(session)

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
