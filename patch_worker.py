import re

file_path = r"c:\Sher_AI_Studio\projects\Outreach\src\outreach\outreach_worker.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update prospect query and account selection
old_prospect_logic = """            # Get next ready prospect
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
                return False"""

new_prospect_logic = """            # Get next ready prospect
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
                    logger.info(f"⏳ Task {task.name} is outside working hours. Sleeping.")
                    return False
            except Exception as e:
                logger.warning(f"Timezone parse error: {e}")

            # Get assigned account via OutreachTaskAccount pool
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
                logger.info(f"⏸ Account {account.id if account else 'None'} hit task limit or not found.")
                return False"""

content = content.replace(old_prospect_logic, new_prospect_logic)

# 2. Update dm_text generation
old_dm_logic = """            # Generate AI message with manager persona & history context
            dm_text = await generate_outreach_dm(
                username=prospect.username or "клиент",
                niche=prospect.niche,
                raw_ad_text=prospect.raw_ad_text,
                sales_hook=prospect.sales_hook,
                manager_name=account.manager_name or "Екатерина",
                manager_role=account.manager_role or "Руководитель B2B развития LeadRadar",
                messages_history=msg_hist
            )"""

new_dm_logic = """            # Generate AI message with manager persona & history context
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
            )"""

content = content.replace(old_dm_logic, new_dm_logic)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied successfully!")
