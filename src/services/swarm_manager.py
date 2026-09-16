import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel, ScraperAccount, OutreachAccount, UserbotChatBinding

logger = logging.getLogger("intent_hunter.swarm_manager")


class SwarmManager:
    """
    Central Manager for Userbot Swarm Resilience:
    - Matrix accounting of Chat-to-Userbot bindings (UserbotChatBinding)
    - Role separation (LISTENER vs WORKER)
    - 2x Redundancy Quorum for high-value chats
    - Emergency Evacuation Protocol on userbot ban
    - Watchdog for silent/dead channels
    """

    @staticmethod
    def get_target_quorum(channel: MonitoredChannel) -> int:
        """
        Returns target redundancy quorum for a monitored channel.
        High-yield chats (leads/vacancies > 0) get Quorum = 2 (Double Coverage).
        Regular chats get Quorum = 1.
        """
        if (getattr(channel, "leads_count", 0) or 0) > 0 or (getattr(channel, "vacancies_count", 0) or 0) > 0:
            return 2
        return 1

    @classmethod
    async def record_binding(
        cls,
        session: AsyncSession,
        account_id: int,
        channel_id: str,
        status: str = "ACTIVE"
    ) -> UserbotChatBinding:
        """
        Creates or updates a UserbotChatBinding matrix record.
        """
        now_utc = datetime.now(timezone.utc)
        stmt = select(UserbotChatBinding).where(
            UserbotChatBinding.account_id == account_id,
            UserbotChatBinding.channel_id == channel_id
        )
        binding = (await session.execute(stmt)).scalar_one_or_none()

        if binding:
            binding.binding_status = status
            binding.last_activity_at = now_utc
            if status == "ACTIVE":
                binding.joined_at = now_utc
        else:
            binding = UserbotChatBinding(
                account_id=account_id,
                channel_id=channel_id,
                binding_status=status,
                joined_at=now_utc,
                last_activity_at=now_utc
            )
            session.add(binding)

        await session.commit()
        return binding

    @classmethod
    async def record_activity(
        cls,
        session: AsyncSession,
        account_id: int,
        channel_id: str
    ):
        """
        Updates last_activity_at timestamp for a specific account-channel binding.
        """
        now_utc = datetime.now(timezone.utc)
        stmt = update(UserbotChatBinding).where(
            UserbotChatBinding.account_id == account_id,
            UserbotChatBinding.channel_id == channel_id
        ).values(last_activity_at=now_utc, binding_status="ACTIVE")
        await session.execute(stmt)
        await session.commit()

    @classmethod
    async def evacuate_banned_userbot(
        cls,
        session: AsyncSession,
        account_id: int,
        reason: str = "Session Revoked / Banned by Telegram"
    ) -> Dict[str, Any]:
        """
        Emergency Evacuation Protocol:
        Executed immediately when a listener userbot account is banned or deactivated.
        1. Marks account status as BANNED/DISABLED.
        2. Marks all its active bindings as SESSION_REVOKED.
        3. Identifies orphaned channels whose active listener count dropped below target quorum.
        4. Resets orphaned channels status to PENDING so healthy listeners automatically re-join them.
        """
        now_utc = datetime.now(timezone.utc)
        logger.warning(f"🚨 EMERGENCY EVACUATION PROTOCOL triggered for Userbot Listener #{account_id}: {reason}")

        # 1. Update ScraperAccount status
        acc_stmt = update(ScraperAccount).where(ScraperAccount.id == account_id).values(
            status="BANNED",
            error_log=f"Evacuated: {reason[:300]}"
        )
        await session.execute(acc_stmt)

        # 2. Mark bindings as SESSION_REVOKED
        bind_stmt = select(UserbotChatBinding).where(
            UserbotChatBinding.account_id == account_id,
            UserbotChatBinding.binding_status == "ACTIVE"
        )
        revoked_bindings = list((await session.execute(bind_stmt)).scalars().all())
        affected_channel_ids = [b.channel_id for b in revoked_bindings]

        for b in revoked_bindings:
            b.binding_status = "SESSION_REVOKED"

        # 3. Check quorum for affected channels and trigger re-assignment
        reassigned_channels = []
        channels_without_listeners = 0
        for ch_id in affected_channel_ids:
            ch_stmt = select(MonitoredChannel).where(MonitoredChannel.id == ch_id)
            channel = (await session.execute(ch_stmt)).scalar_one_or_none()
            if not channel:
                continue

            target_q = cls.get_target_quorum(channel)

            # Count remaining active listeners bound to this channel
            active_b_stmt = select(func.count(UserbotChatBinding.id)).join(
                ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id
            ).where(
                UserbotChatBinding.channel_id == ch_id,
                UserbotChatBinding.binding_status == "ACTIVE",
                ScraperAccount.status == "ACTIVE",
                ScraperAccount.account_role == "LISTENER"
            )
            remaining_active = (await session.execute(active_b_stmt)).scalar() or 0

            if remaining_active < target_q:
                if remaining_active == 0:
                    channels_without_listeners += 1
                channel.status = "PENDING"
                channel.error_message = f"Эвакуирован: потерян юзербот #{account_id} (Осталось слушателей: {remaining_active}/{target_q})"
                reassigned_channels.append({
                    "channel_id": channel.id,
                    "title": channel.title or channel.username_or_link,
                    "remaining_listeners": remaining_active,
                    "target_quorum": target_q
                })

        await session.commit()

        logger.info(
            f"⚡ Evacuation complete for Userbot #{account_id}: "
            f"Revoked {len(affected_channel_ids)} bindings, "
            f"Re-queued {len(reassigned_channels)} channels for immediate re-joining."
        )

        return {
            "account_id": account_id,
            "evacuated_bindings_count": len(affected_channel_ids),
            "reassigned_channels_count": len(reassigned_channels),
            "channels_without_listeners": channels_without_listeners,
            "reassigned_channels": reassigned_channels
        }

    @classmethod
    async def get_swarm_telemetry(cls, session: AsyncSession) -> Dict[str, Any]:
        """
        Returns real-time telemetry breakdown of the Userbot Swarm:
        - Listeners vs Workers
        - 2x Quorum coverage
        - Active bindings count
        """
        # Listeners (ScraperAccounts with role LISTENER)
        listeners_res = await session.execute(
            select(ScraperAccount).where(
                or_(ScraperAccount.account_role == "LISTENER", ScraperAccount.account_role.is_(None))
            )
        )
        listeners = list(listeners_res.scalars().all())
        total_listeners = len(listeners)
        active_listeners = len([a for a in listeners if a.status == "ACTIVE"])
        banned_listeners = len([a for a in listeners if a.status == "BANNED"])

        # Workers (OutreachAccounts / ScraperAccounts with role WORKER)
        workers_res = await session.execute(select(OutreachAccount))
        workers = list(workers_res.scalars().all())
        total_workers = len(workers)
        active_workers = len([w for w in workers if w.status == "ACTIVE"])

        # Monitored Channels & Quorum Coverage
        channels_res = await session.execute(select(MonitoredChannel))
        channels = list(channels_res.scalars().all())
        total_channels = len(channels)

        high_yield_channels = [c for c in channels if cls.get_target_quorum(c) == 2]
        total_high_yield = len(high_yield_channels)

        # Count 2x quorum covered high yield channels
        covered_2x_count = 0
        for hy_c in high_yield_channels:
            cnt_stmt = select(func.count(UserbotChatBinding.id)).join(
                ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id
            ).where(
                UserbotChatBinding.channel_id == hy_c.id,
                UserbotChatBinding.binding_status == "ACTIVE",
                ScraperAccount.status == "ACTIVE"
            )
            cnt = (await session.execute(cnt_stmt)).scalar() or 0
            if cnt >= 2:
                covered_2x_count += 1

        quorum_percentage = (covered_2x_count / total_high_yield * 100.0) if total_high_yield > 0 else 100.0

        # Active total bindings count
        active_bindings_cnt = (await session.execute(
            select(func.count(UserbotChatBinding.id)).where(UserbotChatBinding.binding_status == "ACTIVE")
        )).scalar() or 0

        return {
            "status": "ok",
            "listeners": {
                "total": total_listeners,
                "active": active_listeners,
                "banned": banned_listeners
            },
            "workers": {
                "total": total_workers,
                "active": active_workers
            },
            "channels_telemetry": {
                "total_monitored": total_channels,
                "high_yield_count": total_high_yield,
                "covered_2x_count": covered_2x_count,
                "quorum_coverage_pct": round(quorum_percentage, 1),
                "active_bindings_count": active_bindings_cnt
            }
        }

    @classmethod
    async def run_silent_chat_watchdog(cls, idle_hours: int = 3) -> Dict[str, Any]:
        """
        Silent Chat Watchdog ("Dead Man's Switch"):
        Checks monitored group chats that produced 0 messages for > idle_hours.
        Re-verifies if assigned listener userbot is still active in the chat.
        If no active binding exists, resets channel to PENDING to trigger re-join.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=idle_hours)
        recovered_count = 0

        async with AsyncSessionLocal() as session:
            stmt = select(MonitoredChannel).where(
                MonitoredChannel.platform == "telegram",
                MonitoredChannel.status == "JOINED",
                or_(
                    MonitoredChannel.last_scraped_at < cutoff,
                    MonitoredChannel.last_scraped_at.is_(None)
                )
            )
            silent_channels = list((await session.execute(stmt)).scalars().all())

            if not silent_channels:
                return {"checked": 0, "recovered": 0}

            logger.info(f"🔍 Watchdog Pass: Checking {len(silent_channels)} silent group chats (idle > {idle_hours}h)...")

            for ch in silent_channels:
                # Check active listener bindings
                bind_stmt = select(func.count(UserbotChatBinding.id)).join(
                    ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id
                ).where(
                    UserbotChatBinding.channel_id == ch.id,
                    UserbotChatBinding.binding_status == "ACTIVE",
                    ScraperAccount.status == "ACTIVE"
                )
                active_binds = (await session.execute(bind_stmt)).scalar() or 0

                if active_binds == 0:
                    logger.warning(f"⚠️ Watchdog: Silent group {ch.title or ch.username_or_link} has 0 active listener bindings! Resetting to PENDING for re-joining.")
                    ch.status = "PENDING"
                    ch.error_message = f"Watchdog: Нет активного слушателя в группе (простой > {idle_hours}ч)"
                    recovered_count += 1

            if recovered_count > 0:
                await session.commit()

        return {
            "checked": len(silent_channels),
            "recovered": recovered_count
        }
