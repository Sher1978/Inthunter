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
        Unified Multi-Tier Channel Performance & Efficiency Recycling:
        Executes automated channel pruning pass via run_auto_channel_pruning.
        Prunes inactive (2-3d), zero-yield (3d+ 0 leads), FAILED, and spam channels, blacklisting them permanently.
        """
        try:
            from src.api.routes import run_auto_channel_pruning
            res = await run_auto_channel_pruning(session)
            recycled_count = res.get("pruned_count", 0)
            reasons = res.get("reasons", {})
            logger.info(f"♻️ ChatLifecycleEngine: Performance recycling complete. Pruned {recycled_count} channels. Breakdown: {reasons}")
            return {
                "recycled_count": recycled_count,
                "reasons": reasons
            }
        except Exception as e:
            logger.error(f"Error during ChatLifecycleEngine performance recycling: {e}")
            return {"recycled_count": 0, "reasons": {}}


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
