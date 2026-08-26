import asyncio
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal
from src.db.models import DiscoveredChat, MonitoredChannel, BlacklistedChat
from src.discovery.chat_discovery import (
    run_passive_regex_discovery,
    run_global_keyword_search,
    run_recursive_monitored_channels_mining
)
from src.discovery.chat_auditor import evaluate_chat_quality

logger = logging.getLogger(__name__)


class ChatDiscoveryManager:
    """
    Manages the lifecycle of discovered Telegram chats:
    Discovery -> Pre-metrics & LLM Audit -> Promotion to MonitoredChannel OR Blacklisting.
    """

    @staticmethod
    async def process_pending_audits(limit: int = 5) -> Dict[str, int]:
        """
        Picks up PENDING candidate chats from discovered_chats, evaluates quality sequentially in small batches via LLM,
        and either promotes them into monitored_channels or adds them to blacklisted_chats.
        """
        async with AsyncSessionLocal() as session:
            # 0. Auto-reset stuck AUDITING candidates (>5 minutes old)
            from datetime import datetime, timezone, timedelta
            stuck_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
            stuck_stmt = select(DiscoveredChat).where(
                DiscoveredChat.audit_status == "AUDITING",
                DiscoveredChat.discovered_at <= stuck_cutoff
            )
            stuck_chats = list((await session.execute(stuck_stmt)).scalars().all())
            for sc in stuck_chats:
                sc.audit_status = "PENDING"
            if stuck_chats:
                logger.info(f"🔄 Reset {len(stuck_chats)} stuck AUDITING candidates back to PENDING")
                await session.commit()

            stmt = (
                select(DiscoveredChat)
                .where(DiscoveredChat.audit_status == "PENDING")
                .order_by(DiscoveredChat.discovered_at.asc())
                .limit(limit)
            )
            pending_chats = list((await session.execute(stmt)).scalars().all())

            if not pending_chats:
                return {"processed": 0, "approved": 0, "rejected": 0}

            chat_datas = [{
                "id": c.id,
                "username": c.chat_username,
                "platform": getattr(c, "platform", "telegram") or "telegram",
                "source": c.source,
                "title": c.title,
                "location_code": c.location_code
            } for c in pending_chats]

            # Mark candidates as AUDITING
            for chat in pending_chats:
                chat.audit_status = "AUDITING"
            await session.commit()

        approved_count = 0
        rejected_count = 0

        for cd in chat_datas:
            chat_id = cd["id"]
            username = cd["username"]
            effective_pl = cd["platform"]
            title = cd["title"]
            loc_code = cd["location_code"]

            try:
                verdict = await evaluate_chat_quality(username, platform=effective_pl)
                score = verdict.get("score", 0)
                chat_type = verdict.get("chat_type", "LIVE_COMMUNITY")
                niches = verdict.get("detected_niches", ["community"])
                reason = verdict.get("reason", "")
                status = verdict.get("status", "REJECTED")

                async with AsyncSessionLocal() as session:
                    c_ref = (await session.execute(
                        select(DiscoveredChat).where(DiscoveredChat.id == chat_id)
                    )).scalar_one_or_none()

                    if c_ref:
                        c_ref.score = score
                        c_ref.chat_type = chat_type
                        c_ref.detected_niches = niches
                        c_ref.verdict_reason = reason
                        c_ref.audited_at = datetime.now(timezone.utc)

                        if status == "APPROVED":
                            c_ref.audit_status = "APPROVED"
                            approved_count += 1

                            MAX_MONITORED_CHANNELS = 1500
                            from sqlalchemy import func
                            cur_total = (await session.execute(select(func.count(MonitoredChannel.id)))).scalar() or 0

                            if cur_total >= MAX_MONITORED_CHANNELS:
                                logger.info(f"⚠️ System limit reached ({cur_total}/{MAX_MONITORED_CHANNELS} monitored channels). Holding approved candidate @{username} in queue until quiet channels are purged.")
                            else:
                                dup_mon = (await session.execute(
                                    select(MonitoredChannel).where(
                                        MonitoredChannel.username_or_link.ilike(username),
                                        MonitoredChannel.platform == effective_pl
                                    )
                                )).scalars().first()

                                if not dup_mon:
                                    niche_code = (niches[0] if niches else "community").lower()
                                    new_mon = MonitoredChannel(
                                        username_or_link=username,
                                        title=title or username,
                                        niche_code=niche_code,
                                        location_code=loc_code or "global",
                                        platform=effective_pl,
                                        chat_type="group",
                                        status="JOINED"
                                    )
                                    session.add(new_mon)
                                logger.info(f"✅ APPROVED chat {username} (Score {score}/100) -> Promoted to MonitoredChannels ({cur_total+1}/{MAX_MONITORED_CHANNELS})!")
                        else:
                            c_ref.audit_status = "REJECTED"
                            rejected_count += 1

                            from src.discovery.chat_discovery import blacklist_channel_permanently
                            await blacklist_channel_permanently(
                                session,
                                username_or_link=username,
                                title=title,
                                reason=f"Отсеян ИИ-аудитом (Score {score}/100): {reason[:100]}",
                                score=score
                            )
                            logger.info(f"⛔ REJECTED chat {username} (Score {score}/100) -> Blacklisted.")

                        await session.commit()

            except Exception as e:
                logger.error(f"Error auditing chat {username}: {e}")
                async with AsyncSessionLocal() as session:
                    c_ref = (await session.execute(
                        select(DiscoveredChat).where(DiscoveredChat.id == chat_id)
                    )).scalar_one_or_none()
                    if c_ref:
                        c_ref.audit_status = "FAILED"
                        c_ref.verdict_reason = f"Ошибка аудита: {str(e)[:200]}"
                        await session.commit()

            await asyncio.sleep(1)

        if approved_count > 0:
            try:
                from src.api.app import ingestor
                if ingestor:
                    asyncio.create_task(ingestor.restart_scraper_loop())
            except Exception:
                pass

        return {
            "processed": len(pending_chats),
            "approved": approved_count,
            "rejected": rejected_count
        }

    @staticmethod
    async def run_full_discovery_cycle() -> Dict[str, Any]:
        """
        Executes complete unified lifecycle pass (7-day performance recycling + AI multi-GEO discovery + audit).
        """
        from src.discovery.chat_lifecycle_engine import ChatLifecycleEngine
        return await ChatLifecycleEngine.run_unified_lifecycle_cycle()


discovery_manager_task = None

async def run_discovery_background_loop():
    """
    Background worker loop executing periodic unified lifecycle combine cycles (discovery, recycling & AI audits).
    Drains pending audit queue aggressively.
    """
    logger.info("🚀 Starting Accelerated Chat Lifecycle & Discovery Combine background loop...")
    await asyncio.sleep(10)

    while True:
        try:
            res = await ChatDiscoveryManager.run_full_discovery_cycle()
            rec_cnt = res.get("recycled_stats", {}).get("recycled_count", 0)
            logger.info(
                f"🔄 Unified Combine Cycle Finished: Recycled={rec_cnt}, "
                f"Discovered={res.get('active_discovered', 0) + res.get('passive_discovered', 0) + res.get('mined_discovered', 0)}, "
                f"Audited={res.get('audited_stats', {})}"
            )

            # Moderate pacing: audit up to 5 pending candidates per cycle
            audit_batch = await ChatDiscoveryManager.process_pending_audits(limit=5)
            logger.info(f"⚡ Audit Pass Completed: {audit_batch}")

        except Exception as e:
            logger.error(f"Error in Discovery Engine background loop: {e}")

        await asyncio.sleep(120) # Accelerated 2-minute interval between full discovery passes
