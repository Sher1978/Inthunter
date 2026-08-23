import asyncio
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal
from src.db.models import DiscoveredChat, MonitoredChannel, BlacklistedChat
from src.discovery.chat_discovery import run_passive_regex_discovery, run_global_keyword_search
from src.discovery.chat_auditor import evaluate_chat_quality

logger = logging.getLogger(__name__)


class ChatDiscoveryManager:
    """
    Manages the lifecycle of discovered Telegram chats:
    Discovery -> Pre-metrics & LLM Audit -> Promotion to MonitoredChannel OR Blacklisting.
    """

    @staticmethod
    async def process_pending_audits(limit: int = 10) -> Dict[str, int]:
        """
        Picks up PENDING candidate chats from discovered_chats, evaluates quality via LLM,
        and either promotes them into monitored_channels or adds them to blacklisted_chats.
        """
        async with AsyncSessionLocal() as session:
            stmt = (
                select(DiscoveredChat)
                .where(DiscoveredChat.audit_status == "PENDING")
                .order_by(DiscoveredChat.discovered_at.asc())
                .limit(limit)
            )
            pending_chats = list((await session.execute(stmt)).scalars().all())

            if not pending_chats:
                return {"processed": 0, "approved": 0, "rejected": 0}

            approved_count = 0
            rejected_count = 0

            for chat in pending_chats:
                chat.audit_status = "AUDITING"
                await session.commit()

                username = chat.chat_username
                logger.info(f"🔎 Auditing candidate chat: {username} (Source: {chat.source})...")

                try:
                    verdict = await evaluate_chat_quality(username)
                    chat.score = verdict.get("score", 0)
                    chat.chat_type = verdict.get("chat_type", "LIVE_COMMUNITY")
                    chat.detected_niches = verdict.get("detected_niches", ["community"])
                    chat.verdict_reason = verdict.get("reason", "")
                    chat.audited_at = datetime.now(timezone.utc)

                    if verdict.get("status") == "APPROVED":
                        chat.audit_status = "APPROVED"
                        approved_count += 1

                        # Promote to MonitoredChannel
                        dup_mon = (await session.execute(
                            select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(username))
                        )).scalar_one_or_none()

                        if not dup_mon:
                            niche_code = (chat.detected_niches[0] if chat.detected_niches else "community").lower()
                            new_mon = MonitoredChannel(
                                username_or_link=username,
                                title=chat.title or username,
                                niche_code=niche_code,
                                location_code="global",
                                chat_type="group",
                                status="JOINED"
                            )
                            session.add(new_mon)

                        logger.info(f"✅ APPROVED chat {username} (Score {chat.score}/100) -> Promoted to MonitoredChannels!")

                    else:
                        chat.audit_status = "REJECTED"
                        rejected_count += 1

                        # Add to BlacklistedChat
                        dup_blk = (await session.execute(
                            select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(username))
                        )).scalar_one_or_none()

                        if not dup_blk:
                            new_blk = BlacklistedChat(
                                chat_username=username,
                                reason=chat.verdict_reason,
                                score=chat.score
                            )
                            session.add(new_blk)

                        logger.info(f"⛔ REJECTED chat {username} (Score {chat.score}/100) -> Blacklisted ({chat.verdict_reason}).")

                    await session.commit()

                except Exception as e:
                    logger.error(f"Error auditing chat {username}: {e}")
                    chat.audit_status = "FAILED"
                    chat.verdict_reason = f"Ошибка аудита: {str(e)[:200]}"
                    await session.commit()

            # Trigger scraper loop restart if new channels were approved
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
        Executes a complete passive discovery, active search, and LLM audit cycle.
        """
        async with AsyncSessionLocal() as session:
            passive_count = await run_passive_regex_discovery(session)
            active_count = await run_global_keyword_search(session)

        audit_res = await ChatDiscoveryManager.process_pending_audits(limit=15)

        return {
            "status": "ok",
            "passive_discovered": passive_count,
            "active_discovered": active_count,
            "audited_stats": audit_res
        }


discovery_manager_task = None

async def run_discovery_background_loop():
    """
    Background worker loop executing periodic discovery & AI quality audits every 15 minutes.
    """
    logger.info("🚀 Starting Chat Discovery & AI Audit background loop...")
    await asyncio.sleep(15) # Warm-up delay

    while True:
        try:
            res = await ChatDiscoveryManager.run_full_discovery_cycle()
            logger.info(f"🔄 Discovery Cycle Finished: Passive={res['passive_discovered']}, Active={res['active_discovered']}, Audited={res['audited_stats']}")
        except Exception as e:
            logger.error(f"Error in discovery background loop: {e}")

        await asyncio.sleep(900) # 15 minutes interval
