import logging
import asyncio
import random
from datetime import datetime, timezone
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def sync_leads_to_prospects(self, db: AsyncSession):
        """
        Syncs approved OutreachLead records from main platform into B2BProspect queue.
        """
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
                    lead.status = "SENT" # Marked synced
            await db.commit()
        except Exception as e:
            logger.error(f"Error syncing OutreachLead to B2BProspect: {e}")

    async def process_next_batch(self):
        """
        Fetches next batch of READY_FOR_OUTREACH prospects and dispatches DMs.
        """
        async with AsyncSessionLocal() as db:
            # First sync approved leads
            await self.sync_leads_to_prospects(db)

            # Get next ready prospect
            prospect_stmt = (
                select(B2BProspect)
                .where(B2BProspect.status == "READY_FOR_OUTREACH")
                .order_by(B2BProspect.created_at.asc())
                .limit(1)
            )
            prospect = (await db.execute(prospect_stmt)).scalars().first()
            if not prospect:
                return False

            # Select available outreach account
            account = await AccountManager.get_available_account(db)
            if not account:
                logger.info("⏸ No active outreach accounts available or all accounts hit daily limit.")
                return False

            # Target username / user_id
            target = prospect.username or prospect.telegram_id
            if not target:
                prospect.status = "FAILED"
                prospect.error_log = "No username or telegram_id present"
                await db.commit()
                return True

            logger.info(f"🚀 Preparing outreach DM for prospect @{prospect.username} (ID: {prospect.id}) via Account #{account.id} ({account.phone_number})...")

            # Fetch matching OutreachLead to get messages_history if available
            lead_res = await db.execute(
                select(OutreachLead).where(
                    (OutreachLead.author_username == prospect.username) |
                    (OutreachLead.telegram_id == prospect.telegram_id)
                )
            )
            matching_lead = lead_res.scalars().first()
            msg_hist = matching_lead.messages_history if matching_lead else []

            # Generate AI message with manager persona & history context
            dm_text = await generate_outreach_dm(
                username=prospect.username or "клиент",
                niche=prospect.niche,
                raw_ad_text=prospect.raw_ad_text,
                sales_hook=prospect.sales_hook,
                manager_name=account.manager_name or "Екатерина",
                manager_role=account.manager_role or "Руководитель B2B развития LeadRadar",
                messages_history=msg_hist
            )
            prospect.generated_message = dm_text
            prospect.assigned_account_id = account.id

            # Initialize Pyrogram Client
            app = AccountManager.create_pyrogram_client(account)
            
            # Register incoming message handler for AI persona replies via Gemini
            from src.outreach.listener import register_incoming_message_handler
            register_incoming_message_handler(app, account)

            try:
                await app.start()
                # Send direct message
                target_dest = f"@{prospect.username.replace('@','')}" if prospect.username else prospect.telegram_id
                sent_msg = await app.send_message(chat_id=target_dest, text=dm_text)
                
                # Update Prospect
                prospect.status = "SENT"
                prospect.sent_at = datetime.now(timezone.utc)
                
                # Update Account Stats
                account.daily_sent_count += 1
                account.last_used_at = datetime.now(timezone.utc)
                
                await db.commit()
                logger.info(f"✅ DM successfully sent to @{prospect.username} as Manager '{account.manager_name}'! Sent today from Acc #{account.id}: {account.daily_sent_count}/{account.max_daily_limit}")

            except Exception as send_err:
                err_summary = await AccountManager.handle_account_error(account.id, db, send_err)
                prospect.status = "FAILED"
                prospect.error_log = err_summary
                await db.commit()
                logger.error(f"❌ Failed outreach send to @{prospect.username}: {err_summary}")

            finally:
                try:
                    await app.stop()
                except Exception:
                    pass

            return True

    async def run_loop(self):
        """
        Background loop executing continuous outreach queue processing with humanized delays.
        """
        self.is_running = True
        logger.info("🟢 LeadRadar Outreach Engine Worker Loop Started.")
        
        while self.is_running:
            try:
                had_work = await self.process_next_batch()
                if had_work:
                    # Apply random humanized delay between messages (e.g., 3-7 mins)
                    delay = random.randint(self.delay_min_s, self.delay_max_s)
                    logger.info(f"☕ Humanizer delay: Sleeping {delay} seconds before next dispatch...")
                    await asyncio.sleep(delay)
                else:
                    # Idle sleep if queue is empty or no accounts available
                    await asyncio.sleep(30)
            except asyncio.CancelledError:
                logger.info("Outreach worker loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Unhandled error in outreach worker loop: {e}")
                await asyncio.sleep(30)


outreach_worker_instance = OutreachWorker(delay_min_s=180, delay_max_s=420)
