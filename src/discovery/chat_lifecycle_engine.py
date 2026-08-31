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
    1. Performance Recycling (72-hour efficiency audit): Auto-prunes/recycles channels that produced 0 messages in 72 hours.
    2. Multi-GEO Active Discovery: Queries Grok AI & Pyrogram MTProto global search across Moscow, Dubai, Bali, Vietnam, Thailand.
    3. AI Quality Audit & Instant Promotion: Evaluates pending candidates and promotes approved ones into active monitoring.
    """

    @staticmethod
    async def run_72h_performance_recycling(session: AsyncSession, threshold_hours: int = 72) -> Dict[str, Any]:
        """
        Dual-Tier Chat Performance & Efficiency Recycling:
        Tier 1 (Inactivity): Prunes channels with 0 messages for >= 72 hours (3 days).
        Tier 2 (Zero Yield): Prunes channels that have messages but produced ZERO leads AND ZERO vacancies for >= 7 days (168 hours).
        """
        now_utc = datetime.now(timezone.utc)
        cutoff_72h = now_utc - timedelta(hours=threshold_hours)
        cutoff_7d = now_utc - timedelta(days=7)

        res = await session.execute(
            select(MonitoredChannel).where(MonitoredChannel.status == "JOINED")
        )
        channels = list(res.scalars().all())
        recycled_channels = []

        # Fetch active chat_titles from last 72h
        act_res = await session.execute(
            select(UserActivityLog.chat_title).where(
                UserActivityLog.timestamp >= cutoff_72h
            ).group_by(UserActivityLog.chat_title)
        )
        active_titles_72h = [ (r[0] or "").strip().lower() for r in act_res.all() if r[0] ]

        # Fetch lead-producing chat_titles from last 7 days
        lead_titles_res = await session.execute(
            select(UserActivityLog.chat_title)
            .join(Lead, Lead.user_id == UserActivityLog.user_id)
            .where(Lead.created_at >= cutoff_7d)
            .group_by(UserActivityLog.chat_title)
        )
        lead_titles_7d = set([ (r[0] or "").strip().lower() for r in lead_titles_res.all() if r[0] ])

        # Fetch vacancy author usernames from last 7 days
        vac_res = await session.execute(
            select(HRVacancy.author_username).where(HRVacancy.created_at >= cutoff_7d)
        )
        vac_authors_7d = set([ (r[0] or "").lstrip("@").strip().lower() for r in vac_res.scalars().all() if r[0] ])

        for ch in channels:
            created_dt = ch.created_at.replace(tzinfo=timezone.utc) if ch.created_at and ch.created_at.tzinfo is None else (ch.created_at or now_utc)
            age_hours = (now_utc - created_dt).total_seconds() / 3600.0

            clean_u = (ch.username_or_link or "").lstrip("@").strip().lower()
            title_key = (ch.title or clean_u or "___").strip().lower()

            # ── TIER 1: Inactivity Filter (3 days / 72h with 0 messages) ──
            if age_hours >= threshold_hours:
                is_active_72h = any((title_key in t or clean_u in t) for t in active_titles_72h if t)
                if not is_active_72h:
                    reason = f"3-дневный авто-отсев неактивных каналов: 0 сообщений за {int(age_hours)}ч."
                    logger.info(f"♻️ RECYCLING inactive channel {ch.username_or_link} [{ch.location_code}]: {reason}")

                    from src.discovery.chat_discovery import blacklist_channel_permanently
                    await blacklist_channel_permanently(
                        session,
                        username_or_link=ch.username_or_link,
                        title=ch.title,
                        reason=f"3-дневный авто-отсев (0 сообщений за {int(age_hours)}ч).",
                        score=30
                    )
                    recycled_channels.append({
                        "username": ch.username_or_link,
                        "title": ch.title or ch.username_or_link,
                        "location_code": ch.location_code or "global",
                        "reason": reason
                    })
                    await session.delete(ch)
                    continue

            # ── TIER 2: Zero-Yield Efficiency Filter (7 days / 168h with 0 leads & 0 vacancies) ──
            if age_hours >= 168:  # 7 days old
                has_leads = (
                    any((title_key in t or clean_u in t) for t in lead_titles_7d if t) or
                    (ch.last_lead_at and ch.last_lead_at >= cutoff_7d) or
                    (ch.leads_count or 0) > 0
                )
                has_vacancies = (
                    clean_u in vac_authors_7d or
                    (ch.vacancies_count or 0) > 0
                )

                if not has_leads and not has_vacancies:
                    reason = f"7-дневный авто-отсев неэффективных каналов: сообщения идут, но 0 лидов и 0 вакансий за 7 дней ({int(age_hours)}ч)."
                    logger.info(f"♻️ RECYCLING zero-yield channel {ch.username_or_link} [{ch.location_code}]: {reason}")

                    from src.discovery.chat_discovery import blacklist_channel_permanently
                    await blacklist_channel_permanently(
                        session,
                        username_or_link=ch.username_or_link,
                        title=ch.title,
                        reason="7-дневный авто-отсев нулевой эффективности (0 лидов и 0 вакансий за 7 дней).",
                        score=40
                    )
                    recycled_channels.append({
                        "username": ch.username_or_link,
                        "title": ch.title or ch.username_or_link,
                        "location_code": ch.location_code or "global",
                        "reason": reason
                    })
                    await session.delete(ch)
                    continue

        if recycled_channels:
            await session.commit()
            try:
                from src.bot.alert_bot import notify_superadmins_system_alert
                items_str = "\n".join([f"• 🗑 <b>{c['title']}</b> ({c['username']}) [{c['location_code'].upper()}]" for c in recycled_channels[:10]])
                card = (
                    f"♻️ <b>АВТОМАТИЧЕСКАЯ РОТАЦИЯ КАНАЛОВ (3 ДНЯ БЕЗ СООБЩЕНИЙ / 7 ДНЕЙ БЕЗ ЛИДОВ И ВАКАНСИЙ)</b>\n"
                    f"───────────────────────────\n\n"
                    f"Выведено неэффективных каналов: <b>{len(recycled_channels)}</b> шт.\n\n"
                    f"{items_str}\n\n"
                    f"🔄 <i>Освободившиеся слоты автоматически замещаются новыми целевыми группами из ИИ-поиска.</i>"
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
        1. 3-Day (72h) Efficiency Filter & Recycling
        2. Passive & Active Grok Multi-GEO Discovery
        3. AI Quality Audit & Scraper Loop Restart
        """
        logger.info("🚀 Starting Unified Chat Lifecycle & Recycling Combine cycle...")
        from src.discovery.chat_manager import ChatDiscoveryManager

        async with AsyncSessionLocal() as session:
            # 1. 3-Day (72h) Efficiency Filter & Recycling
            recycle_res = await cls.run_72h_performance_recycling(session)

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
