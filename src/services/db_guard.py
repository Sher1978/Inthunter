import os
import logging
import shutil
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
        self.max_db_size_mb = max_db_size_mb or getattr(settings, "MAX_DB_SIZE_MB", 400.0)

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

            emergency_disk_full = False
            try:
                import tempfile
                tmp_dir = tempfile.gettempdir()
                
                # Check system free disk space
                total, used, free = shutil.disk_usage(tmp_dir)
                free_mb = free / (1024 * 1024)
                if free_mb < 500.0 or (used / total) > 0.95:
                    emergency_disk_full = True
                    logger.critical(f"🚨 CRITICAL: System disk space is extremely low ({round(free_mb, 1)} MB free). Triggering EMERGENCY DISK CLEANUP!")

                now_ts = datetime.now().timestamp()
                if os.path.exists(tmp_dir):
                    for f in os.listdir(tmp_dir):
                        fp = os.path.join(tmp_dir, f)
                        if os.path.isfile(fp) and (f.startswith("tmp") or f.startswith("starlette") or f.endswith(".tmp")):
                            try:
                                # In emergency, delete immediately. Otherwise, wait 30m
                                if emergency_disk_full or (now_ts - os.path.getmtime(fp) > 1800):
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
            # In emergency, prune everything older than 12 hours
            ret_days = getattr(settings, "RETENTION_DAYS", 3)
            if emergency_disk_full:
                cutoff_retention = datetime.now(timezone.utc) - timedelta(hours=12)
                logger.warning(f"⚠️ EMERGENCY: Changing retention cutoff to 12 hours to free up disk space.")
            else:
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

            # Prune non-lead AIEvaluationLog older than 3 hours
            cutoff_3h = datetime.now(timezone.utc) - timedelta(hours=3)
            ai_del_stmt = delete(AIEvaluationLog).where(
                AIEvaluationLog.created_at < cutoff_3h,
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

            # 5. Emergency Storage Limit Guard (if DB size exceeds MAX_DB_SIZE_MB or disk is full)
            current_size = await self.get_db_size_mb(session)
            act_rows = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
            if current_size > 30.0 or act_rows > 3000 or emergency_disk_full:
                logger.warning(f"⚠️ DB Guard EMERGENCY: DB size {current_size}MB / Rows={act_rows}. Running TRUNCATE & VACUUM recovery...")
                
                # Execute instant TRUNCATE on high-volume log tables to immediately free physical disk space
                try:
                    from src.db.session import engine
                    async with engine.begin() as conn:
                        try:
                            await conn.execute(text("TRUNCATE TABLE user_activity_logs, ai_evaluation_logs, collector_logs;"))
                            logger.info("🧹 DB Guard EMERGENCY: Auto-truncated log tables successfully.")
                        except Exception as tr_err:
                            logger.warning(f"TRUNCATE notice: {tr_err}")
                except Exception as tr_e:
                    logger.warning(f"Engine connection for TRUNCATE notice: {tr_e}")

                # Execute autocommit VACUUM to reclaim free pages without temp file bloat
                try:
                    from src.db.session import engine
                    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
                    async with autocommit_engine.connect() as conn:
                        try:
                            await conn.execute(text("VACUUM;"))
                            await conn.execute(text("CHECKPOINT;"))
                            logger.info("🧹 DB Guard: PostgreSQL AUTOCOMMIT VACUUM & CHECKPOINT completed successfully.")
                        except Exception as vf_err:
                            logger.warning(f"VACUUM notice: {vf_err}")
                    pruned_stats["emergency_vacuum"] = True
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
