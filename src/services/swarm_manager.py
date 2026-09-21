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
    last_rebalance_time: Optional[datetime] = None

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
    async def get_swarm_telemetry(cls, db: AsyncSession) -> Dict[str, Any]:
        """
        Returns high-level telemetry for the Swarm Panel.
        """
        now_utc = datetime.now(timezone.utc)
        
        # 1. Listeners
        stmt_listeners = select(func.count(ScraperAccount.id)).where(
            or_(ScraperAccount.account_role == "LISTENER", ScraperAccount.account_role.is_(None))
        )
        total_listeners = (await db.execute(stmt_listeners)).scalar() or 0
        
        stmt_active_listeners = select(func.count(ScraperAccount.id)).where(
            or_(ScraperAccount.account_role == "LISTENER", ScraperAccount.account_role.is_(None)),
            ScraperAccount.status == "ACTIVE"
        )
        active_listeners = (await db.execute(stmt_active_listeners)).scalar() or 0
        
        # 2. Workers
        # Wait, OutreachAccount is imported, maybe we should use it?
        # But for now, we can use ScraperAccount with role WORKER or just check OutreachAccount
        # In this project, workers might be OutreachAccount.
        # Let's count OutreachAccount for workers.
        # Let's verify OutreachAccount existence in this db.
        try:
            from src.db.models import OutreachAccount
            stmt_workers = select(func.count(OutreachAccount.id))
            total_workers = (await db.execute(stmt_workers)).scalar() or 0
            
            stmt_active_workers = select(func.count(OutreachAccount.id)).where(
                OutreachAccount.status == "ACTIVE"
            )
            active_workers = (await db.execute(stmt_active_workers)).scalar() or 0
        except Exception:
            total_workers = 0
            active_workers = 0
            
        # 3. Channels & Bindings
        stmt_bindings = select(func.count(UserbotChatBinding.id)).where(
            UserbotChatBinding.binding_status == "ACTIVE"
        )
        active_bindings = (await db.execute(stmt_bindings)).scalar() or 0
        
        stmt_channels = select(func.count(MonitoredChannel.id)).where(
            MonitoredChannel.status.in_(["JOINED", "PUBLIC_ACTIVE", "ACTIVE", "PENDING"])
        )
        total_channels = (await db.execute(stmt_channels)).scalar() or 0
        
        # Estimate Quorum coverage (For now, 0 if no data)
        quorum_pct = 0
        if total_channels > 0:
            quorum_pct = min(100, int((active_bindings / (total_channels * 2)) * 100))
        
        # 4. Balancer
        balancer_sec = 60
        if cls.last_rebalance_time:
            elapsed = int((now_utc - cls.last_rebalance_time).total_seconds())
            balancer_sec = max(0, 60 - elapsed)
            
        return {
            "listeners": {"active": active_listeners, "total": total_listeners},
            "workers": {"active": active_workers, "total": total_workers},
            "channels_telemetry": {
                "quorum_coverage_pct": quorum_pct,
                "active_bindings_count": active_bindings
            },
            "balancer": {"next_scan_seconds": balancer_sec}
        }

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

        public_channels = [c for c in channels if c.status == "PUBLIC_ACTIVE"]
        public_channels_cnt = len(public_channels)
        userbot_required = [c for c in channels if c.status != "PUBLIC_ACTIVE"]
        userbot_required_cnt = len(userbot_required)

        high_yield_channels = [c for c in channels if cls.get_target_quorum(c) == 2]
        total_high_yield = len(high_yield_channels)

        # Count active bindings per channel
        active_bind_rows = await session.execute(
            select(UserbotChatBinding.channel_id, func.count(UserbotChatBinding.id))
            .join(ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id)
            .where(
                UserbotChatBinding.binding_status == "ACTIVE",
                ScraperAccount.status == "ACTIVE"
            )
            .group_by(UserbotChatBinding.channel_id)
        )
        bound_channel_map = {row[0]: row[1] for row in active_bind_rows.all()}

        covered_2x_count = sum(1 for hy_c in high_yield_channels if bound_channel_map.get(hy_c.id, 0) >= 2)
        quorum_percentage = (covered_2x_count / total_high_yield * 100.0) if total_high_yield > 0 else 0.0

        lost_channels_cnt = sum(1 for c in userbot_required if bound_channel_map.get(c.id, 0) == 0)
        is_loss_detected = lost_channels_cnt > 0

        # Active total bindings count
        active_bindings_cnt = (await session.execute(
            select(func.count(UserbotChatBinding.id)).where(UserbotChatBinding.binding_status == "ACTIVE")
        )).scalar() or 0

        now_utc = datetime.now(timezone.utc)
        balancer_sec = 60
        if cls.last_rebalance_time:
            elapsed = int((now_utc - cls.last_rebalance_time).total_seconds())
            balancer_sec = max(0, 60 - elapsed)

        pending_cnt = len([c for c in channels if c.status in ("PENDING", "FAILED")])

        return {
            "status": "ok",
            "balancer": {
                "next_scan_seconds": balancer_sec,
                "next_scan_formatted": f"Через {balancer_sec}с"
            },
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
                "public_api_channels_count": public_channels_cnt,
                "userbot_required_count": userbot_required_cnt,
                "channels_with_listener_count": userbot_required_cnt - lost_channels_cnt,
                "lost_channels_count": lost_channels_cnt,
                "is_channel_loss_detected": is_loss_detected,
                "high_yield_count": total_high_yield,
                "covered_2x_count": covered_2x_count,
                "quorum_coverage_pct": round(quorum_percentage, 1),
                "active_bindings_count": active_bindings_cnt,
                "pending_count": pending_cnt
            }
        }

    @classmethod
    async def audit_and_reconcile_dialogs(cls, ingestor=None) -> Dict[str, Any]:
        """
        Live MTProto Dialog Audit & Reconciliation Pass:
        1. Queries active Pyrogram userbot clients (ingestor.scrapers) using get_dialogs().
        2. Compares actual Telegram group memberships with DB monitored_channels & userbot_chat_bindings.
        3. Auto-heals desynchronizations:
           - Creates ACTIVE binding if userbot is in Telegram group but missing DB binding.
           - Marks binding DISCONNECTED and resets channel to PENDING if DB binding exists, but userbot is absent in Telegram.
        """
        if not ingestor or not hasattr(ingestor, "scrapers") or not ingestor.scrapers:
            logger.info("ℹ️ MTProto Audit: Ingestor or scraper nodes not available for live dialog check.")
            return {"status": "skipped", "reason": "no_ingestor_nodes"}

        logger.info("🔍 MTProto Audit: Starting live Pyrogram userbot dialog reconciliation...")
        reconciled_created = 0
        reconciled_disconnected = 0

        async with AsyncSessionLocal() as session:
            # Fetch all monitored channels
            ch_res = await session.execute(select(MonitoredChannel))
                      # Build fast lookup map for channels by clean username, clean title, and telegram_id
            channel_map: Dict[str, MonitoredChannel] = {}
            channel_map_by_id: Dict[str, MonitoredChannel] = {}
            for ch in all_channels:
                if ch.id:
                    channel_map_by_id[str(ch.id)] = ch
                if ch.telegram_id:
                    channel_map_by_id[str(ch.telegram_id)] = ch
                if ch.username_or_link:
                    clean_u = ch.username_or_link.strip().lower().replace("@", "").replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").split("/")[0]
                    if clean_u:
                        channel_map[clean_u] = ch
                if ch.title:
                    clean_t = ch.title.strip().lower()
                    if clean_t:
                        channel_map[clean_t] = ch

            for node in ingestor.scrapers:
                if getattr(node, "status", None) == "BANNED" or not node.app:
                    continue
                if not getattr(node.app, "is_connected", False):
                    continue

                account_id = node.db_id
                if account_id <= 0:
                    continue

                try:
                    logger.info(f"📱 MTProto Audit: Querying get_dialogs() for Userbot #{account_id}...")
                    actual_joined_channel_ids = set()

                    async for dialog in node.app.get_dialogs():
                        chat = dialog.chat
                        chat_title = (getattr(chat, "title", None) or "").strip().lower()
                        chat_uname = (getattr(chat, "username", None) or "").strip().lower()
                        chat_id_str = str(getattr(chat, "id", ""))

                        matched_channel = (
                            channel_map_by_id.get(chat_id_str) or 
                            channel_map.get(chat_uname) or 
                            channel_map.get(chat_title)
                        )

                        # If userbot is in a chat not yet in MonitoredChannel, auto-upsert MonitoredChannel
                        if not matched_channel and (chat_title or chat_uname):
                            try:
                                raw_link = f"@{chat_uname}" if chat_uname else f"https://t.me/c/{abs(chat.id)}"
                                matched_channel = MonitoredChannel(
                                    title=getattr(chat, "title", None) or chat_uname or f"Chat {chat.id}",
                                    username_or_link=raw_link,
                                    platform="telegram",
                                    telegram_id=chat.id,
                                    status="JOINED",
                                    last_scraped_at=datetime.now(timezone.utc)
                                )
                                session.add(matched_channel)
                                await session.flush()
                                channel_map_by_id[str(matched_channel.id)] = matched_channel
                                if chat.id:
                                    channel_map_by_id[str(chat.id)] = matched_channel
                                if chat_uname:
                                    channel_map[chat_uname] = matched_channel
                                logger.info(f"✨ MTProto Audit: Auto-created MonitoredChannel for dialog: {matched_channel.title}")
                            except Exception as ch_create_err:
                                logger.warning(f"Notice auto-creating channel during MTProto audit: {ch_create_err}")

                        if matched_channel:
                            actual_joined_channel_ids.add(matched_channel.id)
                            # Ensure active binding exists in DB
                            bind_stmt = select(UserbotChatBinding).where(
                                UserbotChatBinding.account_id == account_id,
                                UserbotChatBinding.channel_id == matched_channel.id
                            )
                            binding = (await session.execute(bind_stmt)).scalar_one_or_none()
                            now_utc = datetime.now(timezone.utc)
                            if not binding:
                                new_b = UserbotChatBinding(
                                    account_id=account_id,
                                    channel_id=matched_channel.id,
                                    binding_status="ACTIVE",
                                    joined_at=now_utc,
                                    last_activity_at=now_utc
                                )
                                session.add(new_b)
                                reconciled_created += 1
                                logger.info(f"✅ MTProto Audit: Created missing binding for Userbot #{account_id} -> {matched_channel.title or matched_channel.username_or_link}")
                            elif binding.binding_status != "ACTIVE":
                                binding.binding_status = "ACTIVE"
                                binding.last_activity_at = now_utc
                                reconciled_created += 1

                            if matched_channel.status != "JOINED":
                                matched_channel.status = "JOINED"

                    # Check DB bindings for this node that were NOT in actual_joined_channel_ids
                    db_binds_stmt = select(UserbotChatBinding).where(
                        UserbotChatBinding.account_id == account_id,
                        UserbotChatBinding.binding_status == "ACTIVE"
                    )
                    db_binds = list((await session.execute(db_binds_stmt)).scalars().all())

                    for b in db_binds:
                        if b.channel_id not in actual_joined_channel_ids:
                            b.binding_status = "DISCONNECTED"
                            reconciled_disconnected += 1
                            # Reset MonitoredChannel status to PENDING if 0 active bindings remaining
                            unbound_ch = (await session.execute(
                                select(MonitoredChannel).where(MonitoredChannel.id == b.channel_id)
                            )).scalar_one_or_none()
                            if unbound_ch and unbound_ch.status != "PUBLIC_ACTIVE":
                                unbound_ch.status = "PENDING"
                                unbound_ch.error_message = f"Отключен слушатель роя #{account_id} (отсутствует в диалогах Telegram)"
                            logger.warning(f"⚠️ MTProto Audit: Marked Userbot #{account_id} binding as DISCONNECTED for channel_id={b.channel_id} (Absent in Telegram dialogs)")

                except Exception as node_audit_err:
                    logger.warning(f"Notice auditing dialogs for Userbot #{account_id}: {node_audit_err}")

            if reconciled_created > 0 or reconciled_disconnected > 0:
                await session.commit()

        logger.info(f"✅ MTProto Audit Complete: Created/Restored {reconciled_created} bindings, Disconnected {reconciled_disconnected} stale bindings.")
        return {
            "status": "ok",
            "reconciled_created": reconciled_created,
            "reconciled_disconnected": reconciled_disconnected
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

    @classmethod
    async def get_next_scheduled_join_info(cls, ingestor=None) -> Dict[str, Any]:
        """
        Returns real-time status of next scheduled join:
        - module_enabled (bool)
        - next_join_seconds (int)
        - next_join_formatted (str)
        - recent_joins (list of dicts with account_id, phone, channel_title, tg_url, joined_at)
        """
        from src.services.module_manager import module_manager
        is_enabled = module_manager.is_enabled("userbot_joiner")

        now_utc = datetime.now(timezone.utc)
        min_seconds = 0

        async with AsyncSessionLocal() as session:
            # Self-healing check to ensure accurate pending_count
            try:
                active_bind_subq = (
                    select(UserbotChatBinding.channel_id)
                    .join(ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id)
                    .where(
                        UserbotChatBinding.binding_status == "ACTIVE",
                        ScraperAccount.status == "ACTIVE"
                    )
                )
                stuck_stmt = (
                    update(MonitoredChannel)
                    .where(
                        MonitoredChannel.platform == "telegram",
                        MonitoredChannel.status.in_(["JOINED", "ACTIVE"]),
                        MonitoredChannel.id.not_in(active_bind_subq)
                    )
                    .values(status="PENDING", error_message="В очереди: ожидание привязки слушателя роя")
                )
                res_stuck = await session.execute(stuck_stmt)
                if res_stuck.rowcount and res_stuck.rowcount > 0:
                    await session.commit()
            except Exception:
                pass

            pending_count = (await session.execute(
                select(func.count(MonitoredChannel.id)).where(MonitoredChannel.status == "PENDING")
            )).scalar() or 0

        if ingestor and hasattr(ingestor, "scrapers"):
            earliest_time = None
            has_ready_node = False

            for node in ingestor.scrapers:
                if getattr(node, "account_role", "LISTENER") != "LISTENER":
                    continue
                if getattr(node, "status", None) in ("BANNED", "DISABLED", "ERROR"):
                    continue
                
                can_j, _ = node.can_perform_mtproto_join(False)
                if can_j:
                    has_ready_node = True
                    break

                if getattr(node, "flood_until", None) and node.flood_until > now_utc:
                    if not earliest_time or node.flood_until < earliest_time:
                        earliest_time = node.flood_until
                elif getattr(node, "last_join_at", None) and getattr(node, "min_join_interval_seconds", 0) > 0:
                    next_avail = node.last_join_at + timedelta(seconds=node.min_join_interval_seconds)
                    if next_avail > now_utc:
                        if not earliest_time or next_avail < earliest_time:
                            earliest_time = next_avail

            if pending_count > 0:
                if has_ready_node:
                    min_seconds = 0
                    next_formatted = "00:00 (готов)"
                elif earliest_time:
                    min_seconds = max(0, int((earliest_time - now_utc).total_seconds()))
                    m, s = divmod(min_seconds, 60)
                    h, m = divmod(m, 60)
                    if h > 0:
                        next_formatted = f"{h:02d}:{m:02d}:{s:02d}"
                    else:
                        next_formatted = f"{m:02d}:{s:02d}"
                else:
                    min_seconds = 0
                    next_formatted = "00:00 (готов)"
            else:
                min_seconds = 0
                next_formatted = "Очередь пуста"
        else:
            if pending_count > 0:
                min_seconds = 0
                next_formatted = "00:00 (готов)"
            else:
                min_seconds = 0
                next_formatted = "Очередь пуста"

        if pending_count == 0 and min_seconds == 0:
            next_formatted = "Очередь пуста"


        recent_joins = []
        async with AsyncSessionLocal() as session:
            stmt = select(
                UserbotChatBinding,
                ScraperAccount.phone_number,
                MonitoredChannel.title,
                MonitoredChannel.username_or_link
            ).join(
                ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id
            ).outerjoin(
                MonitoredChannel, UserbotChatBinding.channel_id == MonitoredChannel.id
            ).where(
                UserbotChatBinding.binding_status == "ACTIVE"
            ).order_by(
                UserbotChatBinding.joined_at.desc()
            ).limit(15)


            res = await session.execute(stmt)
            for b, phone, title, link in res.all():
                raw_link = link or ""
                clean_link = raw_link.replace("@", "").strip()
                if clean_link and not clean_link.startswith("http"):
                    tg_url = f"https://t.me/{clean_link}"
                else:
                    tg_url = raw_link or "#"

                recent_joins.append({
                    "id": b.id,
                    "account_id": b.account_id,
                    "phone": phone or f"Юзербот #{b.account_id}",
                    "channel_title": title or link or b.channel_id,
                    "channel_link": link or "",
                    "tg_url": tg_url,
                    "joined_at": b.joined_at.isoformat() if b.joined_at else None
                })

        now_utc = datetime.now(timezone.utc)
        balancer_sec = 60
        if cls.last_rebalance_time:
            elapsed = int((now_utc - cls.last_rebalance_time).total_seconds())
            balancer_sec = max(0, 60 - elapsed)

        return {
            "status": "ok",
            "module_enabled": is_enabled,
            "next_join_seconds": min_seconds,
            "next_join_formatted": next_formatted,
            "balancer_next_scan_seconds": balancer_sec,
            "balancer_next_scan_formatted": f"Через {balancer_sec}с",
            "recent_joins": recent_joins
        }

    @classmethod
    async def rebalance_and_dispatch_joins(cls, ingestor=None) -> Dict[str, Any]:
        """
        4-Tier Priority Swarm Balancer & Auto-Join Scheduler.
        Scans all monitored channels and active LISTENER userbots, categorizing channels into 4 priorities:
        P1: Fresh manually added / PENDING channels
        P2: Channels with 0 active userbots (dead/evacuated userbots)
        P3: High-yield channels needing 2x quorum (currently only 1 listener)
        P4: All other monitored channels needing re-check/join

        Dispatches MTProto join requests via TelegramIngestor pacing anti-ban rate limits.
        """
        from src.services.module_manager import module_manager
        if not module_manager.is_enabled("userbot_joiner"):
            logger.info("⏸ Swarm Balancer: Auto-Join module 'userbot_joiner' is PAUSED.")
            return {"status": "paused", "dispatched": 0, "message": "Модуль авто-вступлений приостановлен"}

        logger.info("🔄 Swarm Manager: Starting 4-Tier Priority Swarm Rebalance & Auto-Join scan...")
        now_utc = datetime.now(timezone.utc)
        cls.last_rebalance_time = now_utc

        # 0. Live MTProto Dialog Audit Pass & Auto-reconciliation
        if ingestor:
            try:
                await cls.audit_and_reconcile_dialogs(ingestor=ingestor)
            except Exception as audit_err:
                logger.warning(f"Notice running MTProto audit pass before rebalance: {audit_err}")
        
        async with AsyncSessionLocal() as session:
            # 1. Fetch active LISTENER userbots
            scrapers_res = await session.execute(
                select(ScraperAccount).where(
                    ScraperAccount.status == "ACTIVE",
                    or_(ScraperAccount.account_role == "LISTENER", ScraperAccount.account_role.is_(None))
                )
            )
            active_scrapers = list(scrapers_res.scalars().all())
            if not active_scrapers:
                logger.info("ℹ️ Swarm Balancer: No active LISTENER userbots found in DB.")
                return {"status": "no_listeners", "dispatched": 0}

            # 2. Fetch all monitored channels
            channels_res = await session.execute(
                select(MonitoredChannel).where(MonitoredChannel.platform == "telegram")
            )
            channels = list(channels_res.scalars().all())
            if not channels:
                logger.info("ℹ️ Swarm Balancer: No monitored channels found.")
                return {"status": "no_channels", "dispatched": 0}

            # 3. Fetch active bindings matrix
            bindings_res = await session.execute(
                select(UserbotChatBinding).join(
                    ScraperAccount, UserbotChatBinding.account_id == ScraperAccount.id
                ).where(
                    UserbotChatBinding.binding_status == "ACTIVE",
                    ScraperAccount.status == "ACTIVE"
                )
            )
            active_bindings = list(bindings_res.scalars().all())

            # Map active bindings by channel_id
            channel_listeners_map: Dict[str, List[int]] = {}
            for b in active_bindings:
                channel_listeners_map.setdefault(b.channel_id, []).append(b.account_id)

            # 3.1 Self-Healing Pass: Clean up corrupted usernames in DB and reset FAILED channels back to PENDING
            import re
            cleaned_cnt = 0
            for ch in channels:
                if ch.username_or_link:
                    raw_u = ch.username_or_link.strip()
                    cleaned_u = re.sub(r'^[_\s\-\*\•\"\'\«\»\>\#]+', '', raw_u)
                    cleaned_u = cleaned_u.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/s/", "").replace("http://t.me/", "").replace("t.me/", "")
                    cleaned_u = re.sub(r'^[_\s\-\*\•\"\'\«\»\>\#]+', '', cleaned_u).lstrip('@').strip()
                    cleaned_u = cleaned_u.split('/')[0].split('?')[0].strip()
                    
                    if cleaned_u:
                        formatted_link = f"@{cleaned_u}" if not cleaned_u.startswith("+") else cleaned_u
                        if ch.username_or_link != formatted_link:
                            logger.info(f"🧹 Self-Healing DB Pass: Cleaned channel username '{ch.username_or_link}' -> '{formatted_link}'")
                            ch.username_or_link = formatted_link
                            cleaned_cnt += 1
                        if ch.status == "FAILED":
                            ch.status = "PENDING"
                            ch.error_message = None
                            cleaned_cnt += 1

                bound_accounts = channel_listeners_map.get(ch.id, [])
                if len(bound_accounts) == 0 and ch.status in ("JOINED", "ACTIVE") and ch.status != "PUBLIC_ACTIVE":
                    ch.status = "PENDING"
                    ch.error_message = "В очереди: ожидание привязки слушателя роя"
                    cleaned_cnt += 1

            if cleaned_cnt > 0:
                await session.commit()
                logger.info(f"🔧 Self-Healing Pass: Sanitized {cleaned_cnt} channel usernames/statuses in DB.")


            # Categorize channels into 4 priority queues
            p1_fresh_manual: List[MonitoredChannel] = []
            p2_zero_listeners: List[MonitoredChannel] = []
            p3_quorum_deficit: List[MonitoredChannel] = []
            p4_general_backlog: List[MonitoredChannel] = []

            for ch in channels:
                bound_accounts = channel_listeners_map.get(ch.id, [])
                active_count = len(bound_accounts)
                target_q = cls.get_target_quorum(ch)
                needed = max(0, target_q - active_count)

                if needed <= 0 and ch.status in ("JOINED", "PUBLIC_ACTIVE", "ACTIVE"):
                    continue  # Full quorum satisfied

                if ch.status == "PENDING":
                    p1_fresh_manual.append(ch)
                elif active_count == 0:
                    p2_zero_listeners.append(ch)
                elif active_count < target_q:
                    p3_quorum_deficit.append(ch)
                else:
                    p4_general_backlog.append(ch)

            total_queued = len(p1_fresh_manual) + len(p2_zero_listeners) + len(p3_quorum_deficit) + len(p4_general_backlog)
            logger.info(
                f"📊 Swarm Balancer Priority Breakdown (Total Queued: {total_queued}):\n"
                f"   [P1] Fresh Manual Queue: {len(p1_fresh_manual)}\n"
                f"   [P2] Dead Userbots (0 active): {len(p2_zero_listeners)}\n"
                f"   [P3] Quorum Deficit (1/2): {len(p3_quorum_deficit)}\n"
                f"   [P4] General Backlog: {len(p4_general_backlog)}"
            )

            if total_queued == 0:
                logger.info("✅ Swarm Balancer: All monitored channels have full listener quorum.")
                return {"status": "fully_balanced", "dispatched": 0, "queued": 0}

            # Resolve global ingestor instance if not passed
            if not ingestor:
                try:
                    from src.api.app import ingestor as global_ingestor
                    ingestor = global_ingestor
                except Exception:
                    ingestor = None

            prioritized_channels = p1_fresh_manual + p2_zero_listeners + p3_quorum_deficit + p4_general_backlog
            dispatched_count = 0

            if ingestor and hasattr(ingestor, "join_channel"):
                for ch in prioritized_channels:
                    target_link = ch.username_or_link
                    if not target_link:
                        continue
                    try:
                        success, title, error = await ingestor.join_channel(target_link, channel_id=ch.id)
                        if success:
                            dispatched_count += 1
                        elif error and "Anti-Ban Pacing" in str(error):
                            logger.info(f"🛡️ Swarm Balancer: Quota limit reached during rebalance ({error}). Pacing for next pass.")
                            break
                        elif error:
                            # Permanent error (e.g. 400 USERNAME_NOT_OCCUPIED, non-existent username)
                            # Mark channel as FAILED in DB so it doesn't block the rest of the queue
                            ch.status = "FAILED"
                            ch.error_message = str(error)
                            await session.commit()
                            logger.warning(f"⚠️ Swarm Balancer: Channel {target_link} failed ({error}). Marked as FAILED to unblock queue.")
                    except Exception as join_err:
                        logger.warning(f"Notice auto-joining channel {target_link} during rebalance: {join_err}")

            return {
                "status": "ok",
                "dispatched": dispatched_count,
                "queued": total_queued,
                "breakdown": {
                    "p1": len(p1_fresh_manual),
                    "p2": len(p2_zero_listeners),
                    "p3": len(p3_quorum_deficit),
                    "p4": len(p4_general_backlog)
                }
            }

    @classmethod
    async def run_hourly_rebalance_loop(cls, ingestor=None):
        """
        Background worker running 4-tier priority swarm rebalance pass every 60 seconds.
        """
        import asyncio
        logger.info("⏰ Swarm Manager: Auto-Rebalance & Priority Join worker started (60s interval).")
        while True:
            try:
                await cls.rebalance_and_dispatch_joins(ingestor=ingestor)
            except Exception as err:
                logger.error(f"Error in swarm rebalance worker: {err}")
            await asyncio.sleep(60)

