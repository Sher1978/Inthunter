"""
Platform Controller Module
Manages independent platform status (Telegram, MAX Messenger, VKontakte, Odnoklassniki).
"""
import logging
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

class PlatformAutoScaler:
    @staticmethod
    async def get_active_platforms(session: AsyncSession) -> List[str]:
        """
        Returns active operational platforms.
        """
        return ["telegram", "max", "vk", "ok"]

    @staticmethod
    async def evaluate_platform_status(session: AsyncSession) -> Dict[str, Any]:
        """
        Returns telemetry status dict for all supported platforms.
        """
        return {
            "active_platforms": ["telegram", "max", "vk", "ok"],
            "platforms": {
                "telegram": {"status": "ACTIVE", "name": "Telegram", "is_mock": False},
                "max": {"status": "MOCK_PENDING", "name": "MAX Messenger", "is_mock": True},
                "vk": {"status": "MOCK_PENDING", "name": "VKontakte", "is_mock": True},
                "ok": {"status": "MOCK_PENDING", "name": "Одноклассники", "is_mock": True}
            }
        }
