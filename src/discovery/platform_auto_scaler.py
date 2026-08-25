"""
Platform Auto-Scaler & Threshold Controller
Manages automatic activation of VK and OK platforms based on MonitoredChannel thresholds.

Rules:
- Stage 1 (< 1000 channels): Active platforms = ['telegram', 'max']
- Stage 2 (>= 1000 channels): Active platforms = ['telegram', 'max', 'vk']
- Stage 3 (>= 1500 channels): Active platforms = ['telegram', 'max', 'vk', 'ok']
"""
import logging
from typing import List, Dict, Any
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import MonitoredChannel

logger = logging.getLogger(__name__)

VK_THRESHOLD = 1000
OK_THRESHOLD = 1500

class PlatformAutoScaler:
    @staticmethod
    async def get_active_platforms(session: AsyncSession) -> List[str]:
        """
        Returns list of active platforms based on current MonitoredChannel count.
        """
        count_stmt = select(func.count(MonitoredChannel.id)).where(MonitoredChannel.status == "JOINED")
        total_joined = (await session.execute(count_stmt)).scalar() or 0

        active_platforms = ["telegram", "max"]

        if total_joined >= VK_THRESHOLD:
            active_platforms.append("vk")
            logger.info(f"🚀 STAGE 2 UNLOCKED ({total_joined} channels >= {VK_THRESHOLD}): Enabled VKontakte (VK) Discovery & Ingestion.")

        if total_joined >= OK_THRESHOLD:
            active_platforms.append("ok")
            logger.info(f"🚀 STAGE 3 UNLOCKED ({total_joined} channels >= {OK_THRESHOLD}): Enabled Odnoklassniki (OK) Discovery & Ingestion.")

        return active_platforms

    @staticmethod
    async def evaluate_platform_status(session: AsyncSession) -> Dict[str, Any]:
        """
        Returns current threshold status telemetry dict.
        """
        count_stmt = select(func.count(MonitoredChannel.id)).where(MonitoredChannel.status == "JOINED")
        total_joined = (await session.execute(count_stmt)).scalar() or 0

        active = ["telegram", "max"]
        stage = 1

        if total_joined >= VK_THRESHOLD:
            active.append("vk")
            stage = 2
        if total_joined >= OK_THRESHOLD:
            active.append("ok")
            stage = 3

        return {
            "total_joined_channels": total_joined,
            "stage": stage,
            "active_platforms": active,
            "vk_unlocked": total_joined >= VK_THRESHOLD,
            "ok_unlocked": total_joined >= OK_THRESHOLD,
            "vk_threshold": VK_THRESHOLD,
            "ok_threshold": OK_THRESHOLD,
            "next_threshold": VK_THRESHOLD if total_joined < VK_THRESHOLD else (OK_THRESHOLD if total_joined < OK_THRESHOLD else None)
        }
