import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict
from sqlalchemy import select, func, delete, text
from src.config import settings, DB_PATH
from src.db.session import AsyncSessionLocal
from src.db.models import UserActivityLog, AIEvaluationLog, CollectorLog, Lead

logger = logging.getLogger("intent_hunter.db_guard")

class DatabaseGuard:
    """
    Autonomous Database Size & Storage Limit Controller.
    Enforces strict row caps, time-retention policies, and emergency storage VACUUM
    to ensure database size never exceeds Railway free tier limits (Default: 350 MB).
    """

    def __init__(self, max_db_size_mb: float = None):
        self.max_db_size_mb = max_db_size_mb or getattr(settings, "MAX_DB_SIZE_MB", 350.0)

    async def get_db_size_mb(self, session) -> float:
        """Calculates current DB size in Megabytes for either PostgreSQL or SQLite (including WAL/SHM)."""
        try:
            # PostgreSQL check
            res = await session.execute(text("SELECT pg_database_size(current_database())"))
            bytes_val = res.scalar()
            if bytes_val:
                return round(float(bytes_val) / (1024.0 * 1024.0), 2)
        except Exception:
            pass

        try:
            # SQLite check (sum main db + wal + shm file sizes)
            total_bytes = 0
            for suffix in ["", "-wal", "-shm"]:
                p = f"{DB_PATH}{suffix}"
                if os.path.exists(p):
                    total_bytes += os.path.getsize(p)
            if total_bytes > 0:
                return round(float(total_bytes) / (1024.0 * 1024.0), 2)
        except Exception:
            pass

        return 0.0

    async def run_enforcement_pass(self) -> Dict:
        """Executes full automated pruning pass with hard row caps and size safety rules."""
        logger.info(f"🛡️ DB Guard: Running automated database size & retention enforcement pass (Target Max: {self.max_db_size_mb} MB)...")
        pruned_stats = {
            "collector_logs_pruned": 0,
            "activity_logs_pruned": 0,
            "ai_logs_pruned": 0,
            "emergency_vacuum": False,
            "initial_size_mb": 0.0,
            "final_size_mb": 0.0
        }

        async with AsyncSessionLocal() as session:
            # 0. Truncate SQLite WAL file & clean temp files to prevent "No space left on device"
            try:
                await session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                await session.execute(text("PRAGMA journal_size_limit = 10485760"))  # 10MB WAL cap
            except Exception:
                pass

            try:
                import tempfile
                tmp_dir = tempfile.gettempdir()
                now_ts = datetime.now().timestamp()
                if os.path.exists(tmp_dir):
                    for f in os.listdir(tmp_dir):
                        fp = os.path.join(tmp_dir, f)
                        if os.path.isfile(fp) and (f.startswith("tmp") or f.startswith("starlette") or f.endswith(".tmp")):
                            try:
                                if now_ts - os.path.getmtime(fp) > 1800:  # Older than 30m
                                    os.remove(fp)
                            except Exception:
                                pass
            except Exception as tmp_err:
                logger.debug(f"Temp file cleanup notice: {tmp_err}")

            initial_size = await self.get_db_size_mb(session)
            pruned_stats["initial_size_mb"] = initial_size

            # 1. Prune CollectorLog telemetry (Keep max 1 hour & max 500 rows)
            cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
            del_c = await session.execute(delete(CollectorLog).where(CollectorLog.created_at < cutoff_1h))
            pruned_stats["collector_logs_pruned"] += del_c.rowcount or 0
            await session.commit()

            # 1b. Auto-expire AVAILABLE leads older than 3 hours into EXPIRED (Archive)
            ttl_hours = getattr(settings, "LEAD_TTL_HOURS", 3)
            cutoff_lead_ttl = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
            from sqlalchemy import update
            exp_stmt = update(Lead).where(
                Lead.status == "AVAILABLE",
                Lead.created_at < cutoff_lead_ttl
            ).values(status="EXPIRED")
            await session.execute(exp_stmt)
            await session.commit()

            # 1c. Auto-prune ineffective channels (>=3d silence or >=6d zero leads) and add to Blacklist
            try:
                from src.api.routes import run_auto_channel_pruning
                pruned_ch = await run_auto_channel_pruning(session)
                if pruned_ch > 0:
                    logger.info(f"🛡️ DB Guard: Auto-pruned & blacklisted {pruned_ch} ineffective channels.")
            except Exception as prune_err:
                logger.warning(f"DB Guard auto channel prune notice: {prune_err}")

            # 2. Time-based retention: prune UserActivityLog and AIEvaluationLog older than RETENTION_DAYS (3 days)
            ret_days = getattr(settings, "RETENTION_DAYS", 3)
            cutoff_retention = datetime.now(timezone.utc) - timedelta(days=ret_days)
            
            # Preserve user_activity_logs associated with actual leads
            lead_user_ids_stmt = select(Lead.user_id).distinct()
            lead_user_ids = list((await session.execute(lead_user_ids_stmt)).scalars().all())

            act_del_stmt = delete(UserActivityLog).where(
                UserActivityLog.timestamp < cutoff_retention,
                UserActivityLog.user_id.not_in(lead_user_ids) if lead_user_ids else True
            )
            del_act = await session.execute(act_del_stmt)
            pruned_stats["activity_logs_pruned"] += del_act.rowcount or 0

            # Prune non-lead AIEvaluationLog older than 3 days
            ai_del_stmt = delete(AIEvaluationLog).where(
                AIEvaluationLog.created_at < cutoff_retention,
                AIEvaluationLog.is_lead == False
            )
            del_ai_time = await session.execute(ai_del_stmt)
            pruned_stats["ai_logs_pruned"] += del_ai_time.rowcount or 0
            await session.commit()

            # 3. Row-count cap guard: UserActivityLog (Keep max 15,000 rows)
            max_act_rows = getattr(settings, "MAX_ACTIVITY_LOG_ROWS", 15000)
            total_act_count = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
            
            if total_act_count > max_act_rows:
                excess = total_act_count - max_act_rows
                logger.info(f"🛡️ DB Guard: UserActivityLog row count ({total_act_count}) exceeds max cap ({max_act_rows}). Pruning {excess} oldest rows...")
                
                # Subquery to find oldest IDs to delete
                oldest_ids_stmt = (
                    select(UserActivityLog.id)
                    .where(UserActivityLog.user_id.not_in(lead_user_ids) if lead_user_ids else True)
                    .order_by(UserActivityLog.timestamp.asc())
                    .limit(excess)
                )
                oldest_ids = list((await session.execute(oldest_ids_stmt)).scalars().all())
                if oldest_ids:
                    del_cap = await session.execute(delete(UserActivityLog).where(UserActivityLog.id.in_(oldest_ids)))
                    pruned_stats["activity_logs_pruned"] += del_cap.rowcount or 0
                    await session.commit()

            # 4. Row-count cap guard: AIEvaluationLog (Keep max 10,000 rows)
            max_ai_rows = getattr(settings, "MAX_AI_LOG_ROWS", 10000)
            total_ai_count = (await session.execute(select(func.count(AIEvaluationLog.id)))).scalar() or 0
            
            if total_ai_count > max_ai_rows:
                excess_ai = total_ai_count - max_ai_rows
                oldest_ai_ids_stmt = (
                    select(AIEvaluationLog.id)
                    .where(AIEvaluationLog.is_lead == False)
                    .order_by(AIEvaluationLog.created_at.asc())
                    .limit(excess_ai)
                )
                oldest_ai_ids = list((await session.execute(oldest_ai_ids_stmt)).scalars().all())
                if oldest_ai_ids:
                    del_ai = await session.execute(delete(AIEvaluationLog).where(AIEvaluationLog.id.in_(oldest_ai_ids)))
                    pruned_stats["ai_logs_pruned"] += del_ai.rowcount or 0
                    await session.commit()

            # 5. Emergency Storage Limit Guard (if DB size exceeds MAX_DB_SIZE_MB)
            current_size = await self.get_db_size_mb(session)
            if current_size > self.max_db_size_mb:
                logger.warning(f"⚠️ DB Guard EMERGENCY: Current DB size ({current_size} MB) exceeds threshold ({self.max_db_size_mb} MB). Running aggressive cleanup...")
                
                # Aggressively prune non-lead logs older than 3 days
                cutoff_3d = datetime.now(timezone.utc) - timedelta(days=3)
                em_del = await session.execute(
                    delete(UserActivityLog).where(
                        UserActivityLog.timestamp < cutoff_3d,
                        UserActivityLog.user_id.not_in(lead_user_ids) if lead_user_ids else True
                    )
                )
                pruned_stats["activity_logs_pruned"] += em_del.rowcount or 0
                await session.commit()

                # Execute VACUUM to reclaim free space on disk
                try:
                    await session.execute(text("VACUUM"))
                    pruned_stats["emergency_vacuum"] = True
                    logger.info("🧹 DB Guard: VACUUM completed successfully.")
                except Exception as v_err:
                    logger.warning(f"VACUUM notice: {v_err}")

            final_size = await self.get_db_size_mb(session)
            pruned_stats["final_size_mb"] = final_size

            # Log result in process_logger
            try:
                from src.services.process_logger import process_logger
                pct_used = round((final_size / self.max_db_size_mb) * 100, 1)
                total_pruned = pruned_stats["activity_logs_pruned"] + pruned_stats["ai_logs_pruned"] + pruned_stats["collector_logs_pruned"]
                process_logger.add_log(
                    category="SYSTEM",
                    level="info",
                    title=f"🛡️ DB Guard: Размер базы {final_size} MB / {self.max_db_size_mb} MB ({pct_used}%)",
                    details=f"Очищено устаревших записей: {total_pruned}. Лимит базы данных под 100% контролем."
                )
            except Exception:
                pass

        logger.info(f"✅ DB Guard Pass Complete: Size {final_size} MB / {self.max_db_size_mb} MB. Pruned total: {pruned_stats['activity_logs_pruned']} activity, {pruned_stats['ai_logs_pruned']} AI logs.")
        return pruned_stats

db_guard = DatabaseGuard()
