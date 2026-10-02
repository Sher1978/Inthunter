import asyncio
import sys
from datetime import datetime, timezone, timedelta
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.db.session import AsyncSessionLocal
from src.db.models import UserActivityLog, MonitoredChannel
from sqlalchemy import select, func

async def restore_chats_from_logs():
    print("Starting restoration of channels from activity logs in the last 5 days...")
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)
    
    async with AsyncSessionLocal() as session:
        # Get distinct channel usernames/titles from activity logs
        stmt = select(
            UserActivityLog.channel_username, 
            func.max(UserActivityLog.chat_title)
        ).where(
            UserActivityLog.timestamp >= cutoff,
            UserActivityLog.channel_username.isnot(None)
        ).group_by(UserActivityLog.channel_username)
        
        result = await session.execute(stmt)
        active_channels = result.all()
        
        print(f"Found {len(active_channels)} unique channels in activity logs in the last 5 days.")
        
        restored_count = 0
        for username, title in active_channels:
            username = username.strip().replace("https://t.me/", "").replace("@", "")
            if not username:
                continue
                
            formatted_username = f"@{username}" if not username.startswith("+") else username
            
            # Check if it's already in monitored channels
            check_stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == formatted_username)
            exists = (await session.execute(check_stmt)).scalar_one_or_none()
            
            if not exists:
                new_ch = MonitoredChannel(
                    title=title or formatted_username,
                    username_or_link=formatted_username,
                    status="PENDING",
                    error_message="Restored from activity logs"
                )
                session.add(new_ch)
                restored_count += 1
                
        await session.commit()
        print(f"Successfully restored {restored_count} channels to PENDING queue.")

if __name__ == "__main__":
    asyncio.run(restore_chats_from_logs())
