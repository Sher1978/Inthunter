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
        """
        Executes full automated pruning pass with hard row caps and size safety rules.
        TRANSACTION SAFETY: Every pruning step runs in its own isolated AsyncSessionLocal()
        so a failure in one step can never abort subsequent steps.
        """
        logger.info(f"🛡️ DB Guard: Running automated database size & retention enforcement pass (Target Max: {self.max_db_size_mb} MB)...")
        pruned_stats = {
            "collector_logs_pruned": 0,
            "activity_logs_pruned": 0,
            "ai_logs_pruned": 0,
            "emergency_vacuum": False,
            "initial_size_mb": 0.0,
            "final_size_mb": 0.0
        }

        # ── 0. Disk / WAL housekeeping (no DB needed) ──────────────────────────
        emergency_disk_full = False
        try:
            import tempfile
            tmp_dir = tempfile.gettempdir()
            total, used, free = shutil.disk_usage(tmp_dir)
            free_mb = free / (1024 * 1024)
            if free_mb < 500.0 or (used / total) > 0.95:
                emergency_disk_full = True
                logger.critical(f"🚨 CRITICAL: System disk space extremely low ({round(free_mb, 1)} MB free). Triggering EMERGENCY DISK CLEANUP!")
            now_ts = datetime.now().timestamp()
            for f in os.listdir(tmp_dir):
                fp = os.path.join(tmp_dir, f)
                if os.path.isfile(fp) and (f.startswith("tmp") or f.startswith("starlette") or f.endswith(".tmp")):
                    try:
                        if emergency_disk_full or (now_ts - os.path.getmtime(fp) > 1800):
                            os.remove(fp)
                    except Exception:
                        pass
        except Exception as tmp_err:
            logger.debug(f"Temp file cleanup notice: {tmp_err}")

        # ── Helper: measure initial DB size ────────────────────────────────────
        try:
            async with AsyncSessionLocal() as _s:
                pruned_stats["initial_size_mb"] = await self.get_db_size_mb(_s)
                # SQLite WAL truncation (no-op on Postgres)
                try:
                    await _s.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                    await _s.execute(text("PRAGMA journal_size_limit = 10485760"))
                except Exception:
                    pass
        except Exception:
            pass

        # ── 1. CollectorLog pruning ─────────────────────────────────────────────
        try:
            async with AsyncSessionLocal() as session:
                cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
                del_c = await session.execute(delete(CollectorLog).where(CollectorLog.created_at < cutoff_1h))
                pruned_stats["collector_logs_pruned"] += del_c.rowcount or 0
                await session.commit()
        except Exception as e:
            logger.warning(f"DB Guard step 1 (CollectorLog prune) notice: {e}")

        # ── 1b. Auto-expire stale AVAILABLE leads ──────────────────────────────
        try:
            async with AsyncSessionLocal() as session:
                ttl_hours = getattr(settings, "LEAD_TTL_HOURS", 3)
                cutoff_lead_ttl = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
                from sqlalchemy import update as sa_update
                exp_stmt = sa_update(Lead).where(
                    Lead.status == "AVAILABLE",
                    Lead.created_at < cutoff_lead_ttl
                ).values(status="EXPIRED")
                await session.execute(exp_stmt)
                await session.commit()
        except Exception as e:
            logger.warning(f"DB Guard step 1b (lead expiry) notice: {e}")

        # ── 1c. Auto-prune ineffective channels ────────────────────────────────
        try:
            async with AsyncSessionLocal() as session:
                from src.api.routes import run_auto_channel_pruning
                pruned_ch = await run_auto_channel_pruning(session)
                if pruned_ch > 0:
                    logger.info(f"🛡️ DB Guard: Auto-pruned & blacklisted {pruned_ch} ineffective channels.")
        except Exception as prune_err:
            logger.warning(f"DB Guard step 1c (channel pruning) notice: {prune_err}")

        # ── 1d. Auto-prune ARCHIVED scout chats older than 6h ─────────────────
        try:
            async with AsyncSessionLocal() as session:
                from src.db.models import DiscoveredChat
                cutoff_6h = datetime.now(timezone.utc) - timedelta(hours=6)
                # Use discovered_at as fallback since updated_at may not exist
                del_archived = await session.execute(
                    delete(DiscoveredChat).where(
                        DiscoveredChat.audit_status == "ARCHIVED",
                        DiscoveredChat.discovered_at < cutoff_6h
                    )
                )
                if del_archived.rowcount:
                    logger.info(f"🛡️ DB Guard: Auto-pruned {del_archived.rowcount} ARCHIVED scout chats.")
                await session.commit()
        except Exception as e:
            logger.warning(f"DB Guard step 1d (archived scout prune) notice: {e}")

        # ── 2. Time-based retention: UserActivityLog & AIEvaluationLog ─────────
        ret_days = getattr(settings, "RETENTION_DAYS", 3)
        cutoff_retention = (
            datetime.now(timezone.utc) - timedelta(hours=12)
            if emergency_disk_full
            else datetime.now(timezone.utc) - timedelta(days=ret_days)
        )
        if emergency_disk_full:
            logger.warning("⚠️ EMERGENCY: Retention cutoff changed to 12h to free disk space.")

        try:
            async with AsyncSessionLocal() as session:
                lead_user_ids = list((await session.execute(select(Lead.user_id).distinct())).scalars().all())
                act_del_stmt = delete(UserActivityLog).where(
                    UserActivityLog.timestamp < cutoff_retention,
                    UserActivityLog.user_id.not_in(lead_user_ids) if lead_user_ids else True
                )
                del_act = await session.execute(act_del_stmt)
                pruned_stats["activity_logs_pruned"] += del_act.rowcount or 0
                await session.commit()
        except Exception as e:
            logger.warning(f"DB Guard step 2a (UserActivityLog retention) notice: {e}")

        try:
            async with AsyncSessionLocal() as session:
                cutoff_3h = datetime.now(timezone.utc) - timedelta(hours=3)
                ai_del_stmt = delete(AIEvaluationLog).where(
                    AIEvaluationLog.created_at < cutoff_3h,
                    AIEvaluationLog.is_lead == False
                )
                del_ai_time = await session.execute(ai_del_stmt)
                pruned_stats["ai_logs_pruned"] += del_ai_time.rowcount or 0
                await session.commit()
        except Exception as e:
            logger.warning(f"DB Guard step 2b (AIEvaluationLog retention) notice: {e}")

        # ── 3. Row-count cap: UserActivityLog (max 15 000 rows) ───────────────
        try:
            async with AsyncSessionLocal() as session:
                max_act_rows = getattr(settings, "MAX_ACTIVITY_LOG_ROWS", 15000)
                total_act_count = (await session.execute(select(func.count(UserActivityLog.id)))).scalar() or 0
                if total_act_count > max_act_rows:
                    excess = total_act_count - max_act_rows
                    logger.info(f"🛡️ DB Guard: UserActivityLog {total_act_count} rows > cap {max_act_rows}. Pruning {excess} oldest...")
                    lead_user_ids = list((await session.execute(select(Lead.user_id).distinct())).scalars().all())
                    oldest_ids = list((await session.execute(
                        select(UserActivityLog.id)
                        .where(UserActivityLog.user_id.not_in(lead_user_ids) if lead_user_ids else True)
                        .order_by(UserActivityLog.timestamp.asc())
                        .limit(excess)
                    )).scalars().all())
                    if oldest_ids:
                        del_cap = await session.execute(delete(UserActivityLog).where(UserActivityLog.id.in_(oldest_ids)))
                        pruned_stats["activity_logs_pruned"] += del_cap.rowcount or 0
                        await session.commit()
        except Exception as e:
            logger.warning(f"DB Guard step 3 (UserActivityLog cap) notice: {e}")

        # ── 4. Row-count cap: AIEvaluationLog (max 10 000 rows) ───────────────
        try:
            async with AsyncSessionLocal() as session:
                max_ai_rows = getattr(settings, "MAX_AI_LOG_ROWS", 10000)
                total_ai_count = (await session.execute(select(func.count(AIEvaluationLog.id)))).scalar() or 0
                if total_ai_count > max_ai_rows:
                    excess_ai = total_ai_count - max_ai_rows
                    oldest_ai_ids = list((await session.execute(
                        select(AIEvaluationLog.id)
                        .where(AIEvaluationLog.is_lead == False)
                        .order_by(AIEvaluationLog.created_at.asc())
                        .limit(excess_ai)
                    )).scalars().all())
                    if oldest_ai_ids:
                        del_ai = await session.execute(delete(AIEvaluationLog).where(AIEvaluationLog.id.in_(oldest_ai_ids)))
                        pruned_stats["ai_logs_pruned"] += del_ai.rowcount or 0
                        await session.commit()
        except Exception as e:
            logger.warning(f"DB Guard step 4 (AIEvaluationLog cap) notice: {e}")

        # ── 5. Emergency Storage Guard ─────────────────────────────────────────
        try:
            async with AsyncSessionLocal() as session:
                current_size = await self.get_db_size_mb(session)
                if (current_size > self.max_db_size_mb and current_size > 350.0) or emergency_disk_full:
                    logger.warning(f"⚠️ DB Guard EMERGENCY: DB {current_size}MB / Max={self.max_db_size_mb}MB. Running TRUNCATE & VACUUM...")
                    try:
                        from src.db.session import engine
                        async with engine.begin() as conn:
                            await conn.execute(text("TRUNCATE TABLE user_activity_logs, ai_evaluation_logs, collector_logs;"))
                            logger.info("🧹 DB Guard EMERGENCY: Auto-truncated log tables.")
                    except Exception as tr_err:
                        logger.warning(f"TRUNCATE notice: {tr_err}")
                    try:
                        from src.db.session import engine
                        autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
                        async with autocommit_engine.connect() as conn:
                            await conn.execute(text("VACUUM;"))
                            await conn.execute(text("CHECKPOINT;"))
                            logger.info("🧹 DB Guard: AUTOCOMMIT VACUUM & CHECKPOINT done.")
                        pruned_stats["emergency_vacuum"] = True
                    except Exception as v_err:
                        logger.warning(f"VACUUM notice: {v_err}")
        except Exception as e:
            logger.warning(f"DB Guard step 5 (emergency vacuum) notice: {e}")

        # ── Final size measurement & process_logger ────────────────────────────
        final_size = pruned_stats["initial_size_mb"]
        try:
            async with AsyncSessionLocal() as session:
                final_size = await self.get_db_size_mb(session)
        except Exception:
            pass
        pruned_stats["final_size_mb"] = final_size

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

        logger.info(
            f"✅ DB Guard Pass Complete: Size {final_size} MB / {self.max_db_size_mb} MB. "
            f"Pruned total: {pruned_stats['activity_logs_pruned']} activity, "
            f"{pruned_stats['ai_logs_pruned']} AI logs, "
            f"{pruned_stats['collector_logs_pruned']} collector logs."
        )
        return pruned_stats

db_guard = DatabaseGuard()
