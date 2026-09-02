import asyncio
import sys
import os
import time
from sqlalchemy import select, func, not_

sys.path.insert(0, os.path.abspath("."))
from src.db.session import AsyncSessionLocal
from src.db.models import UserActivityLog, Lead
from src.ai.batch_scorer import evaluate_batch
from src.bot.alert_bot import broadcast_lead_alert

async def retro_rescore():
    async with AsyncSessionLocal() as session:
        # 1. Get user_ids who are already leads to exclude them
        leads_stmt = select(Lead.user_id)
        existing_leads_res = await session.execute(leads_stmt)
        existing_leads = {row[0] for row in existing_leads_res}
        
        # 2. Get recent activity logs for users who are NOT leads yet
        # We'll grab the latest messages from the past 48 hours.
        cutoff_stmt = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc()).limit(1000)
        logs_res = await session.execute(cutoff_stmt)
        logs = logs_res.scalars().all()
        
        user_messages = {}
        for log in logs:
            if log.user_id in existing_leads:
                continue
            if log.user_id not in user_messages:
                user_messages[log.user_id] = []
            user_messages[log.user_id].append(log)
            
        print(f"Found {len(user_messages)} unique non-lead users in recent history to retro-score.")
        
        # 3. Batch and score them
        batch_size = 20
        user_ids = list(user_messages.keys())
        total_found_leads = 0
        
        for i in range(0, len(user_ids), batch_size):
            batch_uids = user_ids[i:i+batch_size]
            
            batch_payload = []
            for uid in batch_uids:
                msgs = user_messages[uid]
                # sort oldest to newest for timeline
                msgs = sorted(msgs, key=lambda x: getattr(x, 'timestamp', 0) if getattr(x, 'timestamp', None) else 0)
                timeline_str = " | ".join([getattr(m, "message_text", "") for m in msgs])
                batch_payload.append({
                    "user_id": uid,
                    "timeline_str": timeline_str,
                    "messages": msgs
                })
            
            print(f"Scoring batch {i//batch_size + 1}...")
            results = await evaluate_batch(batch_payload, session)
            
            if results:
                for uid, lead_result in results.items():
                    if getattr(lead_result, "is_lead", False):
                        msgs = user_messages[uid]
                        last_m = msgs[-1]
                        print(f"🌟 RETRO-FOUND LEAD: {uid} - Type: {lead_result.rubric_name} - Reason: {lead_result.reasoning}")
                        total_found_leads += 1
                        
                        # Add to DB
                        new_lead = Lead(
                            user_id=uid,
                            username=getattr(last_m, "username", None),
                            first_name=getattr(last_m, "first_name", None),
                            last_name=getattr(last_m, "last_name", None),
                            source_chat_id=getattr(last_m, "chat_id", None),
                            source_chat_title=getattr(last_m, "chat_title", "Unknown"),
                            niche_code=lead_result.niche_code or "real_estate",
                            rubric_name=lead_result.rubric_name,
                            initial_message=getattr(last_m, "message_text", ""),
                            ai_reasoning=lead_result.reasoning,
                            confidence_score=lead_result.confidence_score,
                            intent_summary=lead_result.intent_summary,
                            is_warm=(lead_result.rubric_name == "WARM_LEAD")
                        )
                        session.add(new_lead)
                        await session.commit()
                        
                        # Send alert
                        try:
                            await broadcast_lead_alert(new_lead)
                        except Exception as e:
                            print(f"Alert failed: {e}")
                            
            time.sleep(2) # rate limit pause
            
        print(f"✅ Retro-Scoring Complete! Found {total_found_leads} new leads.")

if __name__ == "__main__":
    asyncio.run(retro_rescore())
