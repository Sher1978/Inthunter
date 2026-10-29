import logging
import asyncio
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from sqlalchemy import select, func, update, or_, and_

from src.db.session import AsyncSessionLocal
from src.db.models import (
    B2BProspect,
    OutreachAccount,
    OutreachLead,
    OutreachTask,
    OutreachProject,
    OutreachTaskAccount,
    OutreachBlacklist
)
from src.outreach.account_manager import AccountManager
from src.outreach.message_generator import generate_outreach_dm

logger = logging.getLogger("intent_hunter.outreach.worker")

class OutreachWorker:
    """
    Asynchronous multi-project queue worker and dispatcher for automated B2B Telegram outreach.
    Executes parallel task processing across multiple projects/databases with account rotation and humanized delays.
    """

    def __init__(self, delay_min_s: int = 20, delay_max_s: int = 45):
        self.delay_min_s = delay_min_s
        self.delay_max_s = delay_max_s
        self.is_running = False

    async def sync_leads_to_prospects(self):
        """
        Syncs approved OutreachLead records from the main pipeline into B2BProspect queues.
        Routes incoming leads to matching OutreachTasks based on niche and location filters.
        """
        async with AsyncSessionLocal() as db:
            try:
                leads_res = await db.execute(
                    select(OutreachLead).where(OutreachLead.status == "READY_FOR_OUTREACH")
                )
                ready_leads = list(leads_res.scalars().all())
                if not ready_leads:
                    return

                tasks_res = await db.execute(
                    select(OutreachTask).where(OutreachTask.status.in_(["RUNNING", "ACTIVE"]))
                )
                active_tasks = list(tasks_res.scalars().all())

                for lead in ready_leads:
                    if not (lead.author_username or lead.telegram_id):
                        lead.status = "SYNCED"
                        continue

                    matching_tasks: List[OutreachTask] = []
                    lead_niche = (lead.niche_code or "").lower()
                    lead_geo = (lead.location_code or "global").lower()

                    for t in active_tasks:
                        niche_match = not t.niche_code or t.niche_code.lower() in ("all", "any", lead_niche)
                        loc_match = not t.location_code or t.location_code.lower() in ("all", "global", lead_geo)
                        if niche_match and loc_match:
                            matching_tasks.append(t)

                    if not matching_tasks:
                        # Do NOT mark lead as SYNCED if no active task is configured for this niche/geo!
                        continue

                    synced_any = False
                    for target_task in matching_tasks:
                        existing = (await db.execute(
                            select(B2BProspect).where(
                                B2BProspect.task_id == target_task.id,
                                (
                                    (B2BProspect.username == lead.author_username) |
                                    (B2BProspect.telegram_id == lead.telegram_id)
                                )
                            )
                        )).scalars().first()

                        if not existing:
                            prospect = B2BProspect(
                                project_id=target_task.project_id,
                                task_id=target_task.id,
                                telegram_id=lead.telegram_id,
                                username=lead.author_username,
                                niche=lead.niche_code,
                                location_code=lead.location_code or "global",
                                source_chat=lead.chat_title,
                                raw_ad_text=lead.raw_ad_text,
                                sales_hook=lead.sales_hook,
                                confidence_score=int(lead.confidence_score or 85),
                                status="READY_FOR_OUTREACH"
                            )
                            db.add(prospect)
                            synced_any = True

                    if synced_any:
                        lead.status = "SYNCED"


                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"Error syncing OutreachLead to B2BProspect: {e}")

    def is_task_in_working_hours(self, task: OutreachTask) -> bool:
        """Checks if current time in task's timezone is within its configured working hours window."""
        import pytz
        try:
            tz = pytz.timezone(task.timezone or "Asia/Dubai")
            local_time = datetime.now(tz).time()
            start_t = datetime.strptime(task.working_hours_start or "09:00", "%H:%M").time()
            end_t = datetime.strptime(task.working_hours_end or "18:00", "%H:%M").time()
            if start_t <= end_t:
                return start_t <= local_time <= end_t
            else:
                # Overnight window (e.g. 20:00 to 04:00)
                return local_time >= start_t or local_time <= end_t
        except Exception:
            return True

    async def get_best_available_account_for_task(
        self, db, task_id: int, specific_account_id: Optional[int] = None
    ) -> Tuple[Optional[OutreachTaskAccount], Optional[OutreachAccount]]:
        """
        Selects best available active account for a task via Least-Recently-Used (LRU) rotation,
        respecting task daily limits and minimum per-account safety intervals.
        If specific_account_id is provided (e.g. Touch 2 Follow-up), preserves persona continuity.
        Falls back to any ACTIVE account if no explicit OutreachTaskAccount links exist.
        """
        now_utc = datetime.now(timezone.utc)

        # 1. Pinned account mode (e.g. follow-up touch requires the same manager)
        if specific_account_id:
            acc = (await db.execute(
                select(OutreachAccount).where(
                    OutreachAccount.id == specific_account_id,
                    OutreachAccount.status == "ACTIVE"
                )
            )).scalars().first()
            if not acc:
                return None, None

            task_acc = (await db.execute(
                select(OutreachTaskAccount).where(
                    OutreachTaskAccount.task_id == task_id,
                    OutreachTaskAccount.account_id == acc.id
                )
            )).scalars().first()
            task_limit = task_acc.daily_limit if task_acc else (acc.max_daily_limit or 15)
            effective_limit = AccountManager.get_effective_daily_limit(acc, task_limit)
            if acc.daily_sent_count >= effective_limit:
                return None, None

            if acc.last_used_at:
                lu = acc.last_used_at if acc.last_used_at.tzinfo else acc.last_used_at.replace(tzinfo=timezone.utc)
                elapsed_s = (now_utc - lu).total_seconds()
                if elapsed_s < 600:
                    return None, None
            return task_acc, acc

        # 2. General rotation mode
        stmt = (
            select(OutreachTaskAccount, OutreachAccount)
            .join(OutreachAccount, OutreachTaskAccount.account_id == OutreachAccount.id)
            .where(
                OutreachTaskAccount.task_id == task_id,
                OutreachAccount.status == "ACTIVE"
            )
        )
        rows = (await db.execute(stmt)).all()

        # Fallback to any ACTIVE OutreachAccount if no explicit OutreachTaskAccount rows exist
        if not rows:
            all_active = (await db.execute(
                select(OutreachAccount).where(OutreachAccount.status == "ACTIVE")
            )).scalars().all()
            rows = [(None, a) for a in all_active]

        if not rows:
            return None, None

        available_candidates = []
        for task_acc, acc in rows:
            limit = task_acc.daily_limit if task_acc else (acc.max_daily_limit or 15)
            effective_limit = AccountManager.get_effective_daily_limit(acc, limit)
            if acc.daily_sent_count >= effective_limit:
                continue
            # Per-account safety spacing: minimum 10 minutes between sends from the same account
            if acc.last_used_at:
                lu = acc.last_used_at if acc.last_used_at.tzinfo else acc.last_used_at.replace(tzinfo=timezone.utc)
                elapsed_s = (now_utc - lu).total_seconds()
                if elapsed_s < 600:
                    continue
            available_candidates.append((task_acc, acc))

        if not available_candidates:
            return None, None

        def _get_account_sort_key(pair):
            lu = pair[1].last_used_at
            if not lu:
                return datetime.min.replace(tzinfo=timezone.utc)
            return lu if lu.tzinfo else lu.replace(tzinfo=timezone.utc)

        available_candidates.sort(key=_get_account_sort_key)
        return available_candidates[0]

    async def dispatch_single_prospect(
        self, db, task: OutreachTask, project: Optional[OutreachProject],
        account: OutreachAccount, prospect: B2BProspect
    ) -> bool:
        """Dispatches an outreach message to a single prospect using the assigned account and task context."""
        target = prospect.username or prospect.telegram_id
        if not target:
            prospect.status = "FAILED"
            prospect.error_log = "No username or telegram_id present"
            await db.commit()
            return False

        p_id = prospect.id
        p_uname = prospect.username
        p_tgid = prospect.telegram_id

        # Check blacklist before doing any network dispatch
        bl_conds = []
        if prospect.telegram_id:
            bl_conds.append(OutreachBlacklist.telegram_id == prospect.telegram_id)
        if prospect.username:
            clean_u = prospect.username.replace("@", "").strip()
            bl_conds.append(OutreachBlacklist.username == clean_u)
            bl_conds.append(OutreachBlacklist.username == f"@{clean_u}")
        if bl_conds:
            bl_row = (await db.execute(select(OutreachBlacklist).where(or_(*bl_conds)))).scalars().first()
            if bl_row:
                prospect.status = "BLACKLISTED"
                prospect.error_log = f"Recipient is blacklisted ({bl_row.reason})"
                await db.commit()
                logger.info(f"🛑 Skipping blacklisted recipient @{prospect.username} / {prospect.telegram_id} ({bl_row.reason})")
                return False

        logger.info(
            f"🚀 [Project: {getattr(project, 'name', 'Outreach')}] Dispatching to @{prospect.username or prospect.telegram_id} "
            f"via Account #{account.id} ({account.phone_number})..."
        )

        # Fetch message history from matching lead if exists
        lead_res = await db.execute(
            select(OutreachLead).where(
                (OutreachLead.author_username == prospect.username) |
                (OutreachLead.telegram_id == prospect.telegram_id)
            )
        )
        matching_lead = lead_res.scalars().first()
        msg_hist = matching_lead.messages_history if matching_lead else []

        # Check if this is a Touch 2 Follow-up or Touch 1 Opener
        is_followup = (prospect.touch_stage == 1 and prospect.status == "SENT" and prospect.msg_2_followup)
        if is_followup:
            dm_text = prospect.msg_2_followup
        elif prospect.msg_1_opener and len(prospect.msg_1_opener.strip()) > 5:
            dm_text = prospect.msg_1_opener
        else:
            dm_text = await generate_outreach_dm(
                username=prospect.username or "партнер",
                niche=prospect.niche,
                raw_ad_text=prospect.raw_ad_text,
                sales_hook=prospect.sales_hook,
                manager_name=account.manager_name or "Екатерина",
                manager_role=account.manager_role or "Менеджер развития",
                messages_history=msg_hist,
                persona_prompt=task.persona_prompt or account.persona_prompt,
                knowledge_base=project.knowledge_base if project else None,
                system_prompt=getattr(project, "system_prompt", None) if project else None,
                product_name=project.name if project else None
            )
        prospect.generated_message = dm_text
        prospect.assigned_account_id = account.id

        if prospect.platform == "whatsapp":
            try:
                import aiohttp
                instance_name = "LeadRadarWA"
                waha_url = "http://localhost:8080/message/sendText"
                target_dest = str(prospect.telegram_id).strip().replace("+", "")
                if not target_dest:
                    raise ValueError("No phone number for WhatsApp")

                async with aiohttp.ClientSession() as http_sess:
                    resp = await http_sess.post(
                        waha_url,
                        json={"chatId": f"{target_dest}@c.us", "text": dm_text, "session": instance_name},
                        timeout=15
                    )
                    if not resp.ok:
                        raise Exception(f"WhatsApp API Error: {await resp.text()}")

                prospect.status = "SENT"
                prospect.sent_at = datetime.now(timezone.utc)
                account.daily_sent_count += 1
                account.last_used_at = datetime.now(timezone.utc)
                await db.commit()
                logger.info(f"✅ WA DM sent to {target_dest}! Acc #{account.id}: {account.daily_sent_count}")
                return True
            except Exception as wa_err:
                await db.rollback()
                prospect.status = "FAILED"
                prospect.error_log = str(wa_err)
                await db.commit()
                return False

        # Telegram Platform Dispatch
        try:
            client = await AccountManager.get_or_start_client(account)
            clean_username = prospect.username.replace("@", "") if prospect.username else None
            target_dest = f"@{clean_username}" if clean_username else prospect.telegram_id

            # Humanized typing simulation before sending
            try:
                from pyrogram.enums import ChatAction
                await client.send_chat_action(chat_id=target_dest, action=ChatAction.TYPING)
                type_duration = min(5.0, max(2.0, len(dm_text) / 45.0))
                await asyncio.sleep(type_duration)
            except Exception as act_err:
                logger.debug(f"Typing simulation notice: {act_err}")

            await client.send_message(chat_id=target_dest, text=dm_text)

            if is_followup:
                prospect.touch_stage = 2
                prospect.last_touch_at = datetime.now(timezone.utc)
                prospect.followup_after = None
                logger.info(f"🔁 Sent Touch 2 Follow-up ping to @{prospect.username}")
            else:
                prospect.status = "SENT"
                prospect.sent_at = datetime.now(timezone.utc)
                prospect.last_touch_at = datetime.now(timezone.utc)
                prospect.touch_stage = 1
                prospect.followup_after = datetime.now(timezone.utc) + timedelta(hours=24)
            account.daily_sent_count += 1
            account.last_used_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(
                f"✅ TG DM sent to @{prospect.username} as '{account.manager_name}'! "
                f"Acc #{account.id}: {account.daily_sent_count}/{account.max_daily_limit}"
            )
            return True

        except Exception as send_err:
            await db.rollback()
            err_name = type(send_err).__name__

            p_to_update = (await db.get(B2BProspect, p_id)) if p_id else prospect
            if not p_to_update:
                p_to_update = prospect

            if err_name == "UserPrivacyRestricted":
                p_to_update.status = "PRIVACY_RESTRICTED"
                p_to_update.error_log = "Target user privacy restricts incoming direct messages"
                logger.info(f"🔒 Target user @{p_uname} has privacy restrictions. Account not penalized.")
            elif err_name in ("PeerIdInvalid", "UsernameNotOccupied", "UsernameInvalid", "UserIdInvalid"):
                p_to_update.status = "NO_USERNAME_RESTRICTED"
                p_to_update.error_log = f"Cannot resolve Telegram recipient ({err_name}): user has no active username or mutual chats."
                logger.info(f"⚠️ Cannot resolve recipient @{p_uname} / {p_tgid}: {err_name}. Account not penalized.")
            elif err_name in ("PeerFlood", "FloodWait"):
                # Quarantine account, but leave prospect ready for another account to retry
                await AccountManager.handle_account_error(account.id, db, send_err)
                p_to_update.error_log = f"Temporary flood wait on Account #{account.id}: {send_err}"
            else:
                err_summary = await AccountManager.handle_account_error(account.id, db, send_err)
                p_to_update.status = "FAILED"
                p_to_update.error_log = err_summary

            try:
                await db.commit()
            except Exception:
                await db.rollback()
            return False

    async def process_parallel_tasks(self) -> int:
        """
        Executes parallel outreach passes across all active tasks and projects.
        Each product/task pool operates independently without blocking other campaigns.
        """
        await self.sync_leads_to_prospects()
        dispatched_count = 0

        async with AsyncSessionLocal() as db:
            try:
                # Auto-assign task_id to orphaned prospects if active tasks exist
                orphans = (await db.execute(
                    select(B2BProspect).where(
                        B2BProspect.status == "READY_FOR_OUTREACH",
                        B2BProspect.task_id.is_(None)
                    ).limit(20)
                )).scalars().all()

                active_tasks = (await db.execute(
                    select(OutreachTask).where(OutreachTask.status.in_(["ACTIVE", "RUNNING"]))
                )).scalars().all()

                if orphans and active_tasks:
                    for orph in orphans:
                        matched = None
                        if orph.project_id:
                            matched = next((t for t in active_tasks if t.project_id == orph.project_id), None)
                            if not matched:
                                p_rec = await db.get(OutreachProject, orph.project_id)
                                matched = OutreachTask(
                                    project_id=orph.project_id,
                                    name=f"Кампания: {p_rec.name if p_rec else 'Проект ' + str(orph.project_id)}",
                                    location_code=orph.location_code or "global",
                                    niche_code=orph.niche or "all",
                                    status="ACTIVE"
                                )
                                db.add(matched)
                                await db.flush()
                                active_tasks.append(matched)
                        else:
                            matched = next(
                                (t for t in active_tasks if (not t.niche_code or t.niche_code.lower() in ("all", (orph.niche or "").lower()))),
                                active_tasks[0]
                            )
                            orph.project_id = matched.project_id
                        orph.task_id = matched.id
                    await db.commit()

                if not active_tasks:
                    return 0

                # Process each active task independently
                for task in active_tasks:
                    if not self.is_task_in_working_hours(task):
                        continue

                    project = await db.get(OutreachProject, task.project_id)
                    now_utc = datetime.now(timezone.utc)

                    # Fetch next queued prospect: either an approved new touch, or a scheduled follow-up
                    prospect_stmt = (
                        select(B2BProspect)
                        .where(
                            B2BProspect.task_id == task.id,
                            or_(
                                and_(
                                    B2BProspect.status == "READY_FOR_OUTREACH",
                                    or_(B2BProspect.approval_status == "APPROVED", B2BProspect.approval_status.is_(None))
                                ),
                                and_(
                                    B2BProspect.status == "SENT",
                                    B2BProspect.touch_stage == 1,
                                    B2BProspect.followup_after <= now_utc,
                                    B2BProspect.msg_2_followup.isnot(None),
                                    or_(B2BProspect.approval_status == "APPROVED", B2BProspect.approval_status.is_(None))
                                )
                            )
                        )
                        .order_by(B2BProspect.created_at.asc())
                        .limit(1)
                    )
                    prospect = (await db.execute(prospect_stmt)).scalars().first()
                    if not prospect:
                        continue

                    # If this is a Touch 2 Follow-up, preserve assigned account persona continuity
                    is_followup = (prospect.touch_stage == 1 and prospect.status == "SENT" and prospect.msg_2_followup)
                    specific_account_id = prospect.assigned_account_id if is_followup else None

                    task_acc, account = await self.get_best_available_account_for_task(
                        db, task.id, specific_account_id=specific_account_id
                    )
                    if not account:
                        continue

                    success = await self.dispatch_single_prospect(db, task, project, account, prospect)
                    if success:
                        dispatched_count += 1

            except Exception as batch_err:
                await db.rollback()
                logger.error(f"Error in process_parallel_tasks: {batch_err}", exc_info=True)

        return dispatched_count

    async def run_loop(self):
        """
        Background loop executing continuous multi-task outreach passes with humanized intervals.
        """
        self.is_running = True
        logger.info("🟢 LeadRadar Multi-Project Parallel Outreach Worker Started.")

        while self.is_running:
            try:
                # Auto-recover accounts whose 24h COOL_DOWN period has elapsed
                try:
                    async with AsyncSessionLocal() as db:
                        await AccountManager.recover_cooled_down_accounts(db)
                except Exception as rec_err:
                    logger.debug(f"Cooldown recovery notice in loop: {rec_err}")

                dispatched_count = await self.process_parallel_tasks()
                if dispatched_count > 0:
                    delay = random.randint(self.delay_min_s, self.delay_max_s)
                    logger.info(f"☕ Dispatched {dispatched_count} messages. Inter-batch delay: sleeping {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(20)
            except asyncio.CancelledError:
                logger.info("Outreach worker loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Unhandled error in outreach worker loop: {e}", exc_info=True)
                await asyncio.sleep(20)


outreach_worker_instance = OutreachWorker(delay_min_s=20, delay_max_s=45)
