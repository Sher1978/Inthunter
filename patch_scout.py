import os

TELEGRAM_PY = r"c:\Sher_AI_Studio\projects\Outreach\src\ingestion\telegram.py"

with open(TELEGRAM_PY, "r", encoding="utf-8") as f:
    content = f.read()

SCOUT_WORKER_CODE = """
    async def _scout_validator_worker(self):
        \"\"\"Background worker that validates PENDING DiscoveredChats.\"\"\"
        logger.info("🕵️ AI SCOUT: Validator Worker Started (Checking pending invites...)")
        from src.db.models import DiscoveredChat, MonitoredChannel
        from sqlalchemy import select, update
        from pyrogram.raw.functions.messages import CheckChatInvite
        from pyrogram.raw.types import ChatInviteAlready, ChatInvite, ChatInvitePeek
        import random

        while self._is_running:
            try:
                # Need at least one userbot
                if not self.scrapers:
                    await asyncio.sleep(30)
                    continue
                
                async with AsyncSessionLocal() as session:
                    # Fetch one pending chat
                    stmt = select(DiscoveredChat).where(DiscoveredChat.audit_status == "PENDING").limit(1)
                    chat_cand = (await session.execute(stmt)).scalars().first()
                    
                    if not chat_cand:
                        await asyncio.sleep(60) # Sleep if nothing to do
                        continue
                    
                    chat_username = chat_cand.chat_username
                    chat_cand.audit_status = "AUDITING"
                    await session.commit()
                    
                    logger.info(f"🕵️ AI SCOUT: Auditing {chat_username}...")
                    
                    node = random.choice(self.scrapers)
                    if not node.app or not node.app.is_connected:
                        chat_cand.audit_status = "PENDING"
                        await session.commit()
                        await asyncio.sleep(10)
                        continue

                    # Extract hash
                    invite_hash = chat_username.split("/")[-1].replace("+", "")
                    
                    try:
                        # Step 1: Meta-Check
                        invite_info = await node.app.invoke(CheckChatInvite(hash=invite_hash))
                        
                        is_channel = False
                        participants_count = 0
                        chat_title = chat_cand.title or chat_username
                        
                        if isinstance(invite_info, ChatInviteAlready):
                            is_channel = getattr(invite_info.chat, "broadcast", False)
                            participants_count = getattr(invite_info.chat, "participants_count", 1000)
                            chat_title = getattr(invite_info.chat, "title", chat_title)
                        elif isinstance(invite_info, ChatInvite):
                            is_channel = getattr(invite_info, "broadcast", getattr(invite_info, "channel", False))
                            participants_count = getattr(invite_info, "participants_count", 0)
                            chat_title = getattr(invite_info, "title", chat_title)
                        
                        if is_channel or participants_count < 100 or participants_count > 50000:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} (Meta-Fail: Channel={is_channel}, Users={participants_count})")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"Meta-Fail: Channel={is_channel}, Users={participants_count}"
                            await session.commit()
                            continue

                        # Step 2: Math-Audit (Test Run)
                        logger.info(f"🕵️ AI SCOUT: Joining {chat_username} for Math-Audit...")
                        try:
                            chat_obj = await node.app.join_chat(chat_username)
                        except Exception as join_err:
                            logger.warning(f"🕵️ AI SCOUT: Failed to join {chat_username}: {join_err}")
                            chat_cand.audit_status = "FAILED"
                            chat_cand.verdict_reason = f"Join Failed: {join_err}"
                            await session.commit()
                            continue

                        real_chat_id = chat_obj.id
                        chat_title = chat_obj.title or chat_title

                        messages = []
                        try:
                            async for msg in node.app.get_chat_history(real_chat_id, limit=200):
                                if msg.text or msg.caption:
                                    messages.append(msg)
                        except Exception as hist_err:
                            pass

                        if len(messages) < 50:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} (Dead chat, {len(messages)} msgs)")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"Dead chat, only {len(messages)} messages found."
                            try:
                                await node.app.leave_chat(real_chat_id)
                            except: pass
                            await session.commit()
                            continue

                        replies = sum(1 for m in messages if getattr(m, "reply_to_message_id", None))
                        reply_ratio = replies / len(messages)
                        
                        links = sum(1 for m in messages if "http" in (m.text or m.caption or "") or "@" in (m.text or m.caption or ""))
                        link_density = links / len(messages)

                        if reply_ratio < 0.15 or link_density > 0.40:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} (Spam-Board: ReplyRatio={reply_ratio:.2f}, LinkDensity={link_density:.2f})")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"Spam-Board: ReplyRatio={reply_ratio:.2f}, LinkDensity={link_density:.2f}"
                            try:
                                await node.app.leave_chat(real_chat_id)
                            except: pass
                            await session.commit()
                            continue

                        # Step 3: Fast AI Audit
                        logger.info(f"🕵️ AI SCOUT: Math passed for {chat_username}. Running AI Audit...")
                        sample_msgs = random.sample(messages, min(20, len(messages)))
                        sample_text = "\\n".join([m.text or m.caption for m in sample_msgs])
                        
                        # Fake evaluate using existing Groq integration, asking it if it's a lead or not is overkill. 
                        # We will just use standard LLM call if possible.
                        # For now, if Math passes, we assume it's good, but let's do a basic heuristic AI check.
                        import litellm
                        from src.core.config import settings
                        try:
                            response = await litellm.acompletion(
                                model="groq/llama-3.1-70b-versatile",
                                api_key=settings.GROQ_API_KEY,
                                messages=[
                                    {"role": "system", "content": "Analyze the following 20 Telegram messages. Is this a live human chat (A) or a spam/ad board (B)? Answer ONLY with A or B."},
                                    {"role": "user", "content": sample_text[:3000]}
                                ],
                                max_tokens=10,
                                temperature=0.1
                            )
                            ai_answer = response.choices[0].message.content.strip().upper()
                        except:
                            ai_answer = "A" # Fallback to Math if AI fails

                        if "A" in ai_answer:
                            logger.info(f"🕵️ AI SCOUT: APPROVED {chat_username}! (AI: {ai_answer})")
                            chat_cand.audit_status = "APPROVED"
                            chat_cand.verdict_reason = f"AI Approved ({ai_answer})"
                            chat_cand.title = chat_title
                            
                            # Add to MonitoredChannel
                            existing = (await session.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == chat_username))).scalars().first()
                            if not existing:
                                new_mon = MonitoredChannel(
                                    title=chat_title,
                                    username_or_link=chat_username,
                                    niche_code="community",
                                    location_code="dubai",
                                    status="JOINED"
                                )
                                session.add(new_mon)
                                
                            await session.commit()
                        else:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} by AI ({ai_answer})")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"AI Rejected: {ai_answer}"
                            try:
                                await node.app.leave_chat(real_chat_id)
                            except: pass
                            await session.commit()

                    except Exception as e:
                        logger.error(f"🕵️ AI SCOUT: Exception validating {chat_username}: {e}")
                        chat_cand.audit_status = "FAILED"
                        chat_cand.verdict_reason = f"Exception: {e}"
                        await session.commit()
                        
            except Exception as outer_e:
                logger.error(f"Error in scout validator loop: {outer_e}")
                
            await asyncio.sleep(15) # Wait before processing next

"""

# Insert _scout_validator_worker before process_and_score_posts_now
if "_scout_validator_worker" not in content:
    idx = content.find("    async def process_and_score_posts_now")
    content = content[:idx] + SCOUT_WORKER_CODE + content[idx:]
    with open(TELEGRAM_PY, "w", encoding="utf-8") as f:
        f.write(content)
    print("Injected _scout_validator_worker successfully.")

"""
# Need to start it in run_public_scraper_loop or start()
"""
with open(TELEGRAM_PY, "r", encoding="utf-8") as f:
    content = f.read()

# Start scout task
START_INJECT = """
        self.scout_task = asyncio.create_task(self._scout_validator_worker())
"""
if "self.scout_task" not in content:
    idx = content.find("self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())")
    if idx != -1:
        content = content[:idx] + START_INJECT + content[idx:]
        with open(TELEGRAM_PY, "w", encoding="utf-8") as f:
            f.write(content)
        print("Injected scout_task startup successfully.")
    else:
        print("Could not find startup injection point.")

"""
# Stop scout task
STOP_INJECT = """
        if getattr(self, 'scout_task', None):
            self.scout_task.cancel()
"""
if "self.scout_task.cancel()" not in content:
    idx = content.find("        if self.public_scraper_task:")
    if idx != -1:
        content = content[:idx] + STOP_INJECT + content[idx:]
        with open(TELEGRAM_PY, "w", encoding="utf-8") as f:
            f.write(content)
        print("Injected scout_task shutdown successfully.")
    else:
        print("Could not find shutdown injection point.")
"""
