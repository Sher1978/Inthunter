import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel, UserActivityLog, Lead, HRVacancy
from sqlalchemy import select, func, or_
from datetime import datetime, timezone, timedelta

async def test_metrics():
    async with AsyncSessionLocal() as db:
        ch = (await db.execute(select(MonitoredChannel).limit(1))).scalars().first()
        if not ch:
            print("No channels")
            return
            
        print(f"Testing channel: {ch.title} | {ch.username_or_link}")
        
        raw_title = (ch.title or "").strip()
        clean_title_key = raw_title.replace("Обнаружен в ", "").strip().lower()
        username_key = (ch.username_or_link or "").strip().lower().replace("@", "").replace("https://t.me/", "")
        
        print(f"clean_title_key: {clean_title_key}")
        print(f"username_key: {username_key}")
        
        log_conditions = []
        if clean_title_key:
            log_conditions.append(UserActivityLog.chat_title.ilike(f"%{clean_title_key}%"))
        if username_key:
            log_conditions.append(UserActivityLog.chat_title.ilike(f"%{username_key}%"))
            
        match_clause = or_(*log_conditions) if log_conditions else (UserActivityLog.chat_id == 0)
        
        try:
            total_msgs_stmt = select(func.count(UserActivityLog.id)).where(match_clause)
            total_msgs = (await db.execute(total_msgs_stmt)).scalar() or 0
            print(f"total_msgs: {total_msgs}")
            
            leads_stmt = select(func.count(Lead.id)).join(
                UserActivityLog, UserActivityLog.user_id == Lead.user_id
            ).where(match_clause)
            leads_total = (await db.execute(leads_stmt)).scalar() or 0
            print(f"leads_total: {leads_total}")
            
        except Exception as e:
            print(f"Error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(test_metrics())
