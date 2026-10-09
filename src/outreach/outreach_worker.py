import logging
import asyncio
import random
from datetime import datetime, timezone
from sqlalchemy import select, func, update


from src.db.session import AsyncSessionLocal
from src.db.models import B2BProspect, OutreachAccount, OutreachLead
from src.outreach.account_manager import AccountManager
from src.outreach.message_generator import generate_outreach_dm

logger = logging.getLogger("intent_hunter.outreach.worker")

class OutreachWorker:
    """
    Asynchronous queue worker and dispatcher for automated B2B Telegram outreach.
    Executes non-blocking send cycles with account rotation and humanized delays.
    """
    
    def __init__(self, delay_min_s: int = 180, delay_max_s: int = 420):
        self.delay_min_s = delay_min_s
        self.delay_max_s = delay_max_s
        self.is_running = False

    async def sync_leads_to_prospects(self):
        """
        Syncs approved OutreachLead records from main platform into B2BProspect queue.
        Runs in its own isolated session so failures cannot poison the caller's session.
        """
        async with AsyncSessionLocal() as db:
            try:
                leads_res = await db.execute(
                    select(OutreachLead).where(OutreachLead.status == "READY_FOR_OUTREACH")
                )
                ready_leads = list(leads_res.scalars().all())

                for lead in ready_leads:
                    # Check if prospect already exists
                    existing = (await db.execute(
                        select(B2BProspect).where(
                            (B2BProspect.username == lead.author_username) |
                            (B2BProspect.telegram_id == lead.telegram_id)
                        )
                    )).scalars().first()

                    if not existing and (lead.author_username or lead.telegram_id):
                        prospect = B2BProspect(
                            telegram_id=lead.telegram_id,
                            username=lead.author_username,
                            niche=lead.niche_code,
                            source_chat=lead.chat_title,
                            raw_ad_text=lead.raw_ad_text,
                            sales_hook=lead.sales_hook,
                            confidence_score=int(lead.confidence_score),
                            status="READY_FOR_OUTREACH"
                        )
                        db.add(prospect)
                        lead.status = "SENT"  # Marked synced
                await db.commit()
            except Exception as e:
                await db.rollback()  # ← критично: освобождаем сломанную транзакцию
                logger.error(f"Error syncing OutreachLead to B2BProspect: {e}")

    async def process_next_batch(self):
        """
        Fetches next batch of READY_FOR_OUTREACH prospects and dispatches DMs.
        Each DB session is fully isolated: all exception paths call rollback before
        any further DB work, preventing the 'transaction is aborted' cascade.
        """
        # Sync runs in its own isolated session; failures cannot bleed into db below
        await self.sync_leads_to_prospects()

        async with AsyncSessionLocal() as db:
            try:
                # 1. Fetch next queued prospect
                prospect_stmt = (
                    select(B2BProspect)
                    .where(B2BProspect.status == "READY_FOR_OUTREACH")
                    .where(B2BProspect.task_id != None)
                    .order_by(B2BProspect.created_at.asc())
                    .limit(1)
                )
                prospect = (await db.execute(prospect_stmt)).scalars().first()
                if not prospect:
                    return False

                from src.db.models import OutreachTask, OutreachProject, OutreachTaskAccount
                task = await db.get(OutreachTask, prospect.task_id)
                if not task or task.status != "ACTIVE":
                    prospect.status = "FAILED"
                    prospect.error_log = "Task inactive or not found"
                    await db.commit()
                    return True

                project = await db.get(OutreachProject, task.project_id)

                import pytz
                try:
                    tz = pytz.timezone(task.timezone or "Asia/Dubai")
                    local_time = datetime.now(tz).time()
                    start_t = datetime.strptime(task.working_hours_start or "09:00", "%H:%M").time()
                    end_t = datetime.strptime(task.working_hours_end or "18:00", "%H:%M").time()
                    if not (start_t <= local_time <= end_t):
                        logger.info(f"Task {task.name} is outside working hours. Sleeping.")
                        return False
                except Exception as tz_err:
                    logger.warning(f"Timezone parse error: {tz_err}")

                # 2. Resolve account
                account_link = (await db.execute(
                    select(OutreachTaskAccount)
                    .where(OutreachTaskAccount.task_id == task.id)
                    .limit(1)
                )).scalars().first()

                if not account_link:
                    prospect.status = "FAILED"
                    prospect.error_log = "No accounts linked to this task pool"
                    await db.commit()
                    return True

                account = await db.get(OutreachAccount, account_link.account_id)
                if not account or account.daily_sent_count >= account_link.daily_limit:
                    acct_id = account.id if account else "None"
                    logger.info(f"Account {acct_id} hit task limit or not found.")
                    return False

                # 3. Validate target
                target = prospect.username or prospect.telegram_id
                if not target:
                    prospect.status = "FAILED"
                    prospect.error_log = "No username or telegram_id present"
                    await db.commit()
                    return True

                logger.info(
                    f"🚀 Preparing outreach DM for prospect @{prospect.username} "
                    f"(ID: {prospect.id}) via Account #{account.id} ({account.phone_number})..."
                )

                # 4. Fetch message history from matching lead
                lead_res = await db.execute(
                    select(OutreachLead).where(
                        (OutreachLead.author_username == prospect.username) |
                        (OutreachLead.telegram_id == prospect.telegram_id)
                    )
                )
                matching_lead = lead_res.scalars().first()
                msg_hist = matching_lead.messages_history if matching_lead else []

                # 5. Generate AI message
                dm_text = await generate_outreach_dm(
                    username=prospect.username or "клиент",
                    niche=prospect.niche,
                    raw_ad_text=prospect.raw_ad_text,
                    sales_hook=prospect.sales_hook,
                    manager_name=account.manager_name or "Екатерина",
                    manager_role=account.manager_role or "Руководитель B2B развития LeadRadar",
                    messages_history=msg_hist,
                    persona_prompt=task.persona_prompt,
                    knowledge_base=project.knowledge_base if project else None
                )
                prospect.generated_message = dm_text
                prospect.assigned_account_id = account.id

                # 6. Send via Platform
                try:
                    if prospect.platform == "whatsapp":
                        import aiohttp
                        instance_name = "LeadRadarWA"
                        waha_url = "http://localhost:8080/message/sendText"
                        target_dest = str(prospect.telegram_id).strip().replace("+", "")
                        if not target_dest:
                            raise ValueError("No phone number for WhatsApp")

                        async with aiohttp.ClientSession() as session:
                            resp = await session.post(
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
                        logger.info(
                            f"✅ WA DM sent to {target_dest} as '{account.manager_name}'! "
                            f"Acc #{account.id}: {account.daily_sent_count}/{account.max_daily_limit}"
                        )

                    else:
                        app = AccountManager.create_pyrogram_client(account)

                        from src.outreach.listener import register_incoming_message_handler
                        register_incoming_message_handler(app, account)

                        await app.start()
                        clean_username = prospect.username.replace("@", "") if prospect.username else None
                        target_dest = f"@{clean_username}" if clean_username else prospect.telegram_id
                        await app.send_message(chat_id=target_dest, text=dm_text)

                        prospect.status = "SENT"
                        prospect.sent_at = datetime.now(timezone.utc)
                        account.daily_sent_count += 1
                        account.last_used_at = datetime.now(timezone.utc)

                        await db.commit()
                        logger.info(
                            f"✅ TG DM sent to @{prospect.username} as '{account.manager_name}'! "
                            f"Acc #{account.id}: {account.daily_sent_count}/{account.max_daily_limit}"
                        )

                except Exception as send_err:
                    # CRITICAL: rollback aborted transaction BEFORE any further DB work
                    await db.rollback()
                    try:
                        err_summary = await AccountManager.handle_account_error(account.id, db, send_err)
                    except Exception as inner_err:
                        err_summary = (
                            f"Send failed: {send_err!r}; "
                            f"error handler also failed: {inner_err!r}"
                        )
                        logger.error(f"handle_account_error itself raised: {inner_err}")

                    prospect.status = "FAILED"
                    prospect.error_log = err_summary
                    try:
                        await db.commit()
                    except Exception as commit_err:
                        await db.rollback()
                        logger.error(
                            f"Failed to persist FAILED status for prospect {prospect.id}: {commit_err}"
                        )
                    logger.error(f"❌ Failed outreach send to @{prospect.username}: {err_summary}")

                finally:
                    try:
                        await app.stop()
                    except Exception:
                        pass

                return True

            except Exception as batch_err:
                # Top-level guard: ensures the session is never left in an aborted
                # state regardless of which inner operation raised the exception.
                await db.rollback()
                logger.error(
                    f"process_next_batch raised unexpectedly -- transaction rolled back: {batch_err}",
                    exc_info=True,
                )
                return False

    async def run_loop(self):
        """
        Background loop executing continuous outreach queue processing with humanized delays.
        Each iteration opens a brand-new DB session via process_next_batch, so a
        transaction abort in one iteration cannot infect the next.
        """
        self.is_running = True
        logger.info("🟢 LeadRadar Outreach Engine Worker Loop Started.")

        while self.is_running:
            try:
                had_work = await self.process_next_batch()
                if had_work:
                    delay = random.randint(self.delay_min_s, self.delay_max_s)
                    logger.info(f"☕ Humanizer delay: sleeping {delay}s before next dispatch...")
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(30)
            except asyncio.CancelledError:
                logger.info("Outreach worker loop cancelled.")
                break
            except Exception as e:
                # process_next_batch already rolled back its own session.
                # Log with full traceback so the root cause is always visible.
                logger.error(f"Unhandled error in outreach worker loop: {e}", exc_info=True)
                await asyncio.sleep(30)


outreach_worker_instance = OutreachWorker(delay_min_s=180, delay_max_s=420)
