import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check_db():
    async with AsyncSessionLocal() as session:
        for table in ["monitored_channels", "blacklisted_chats", "channel_candidates", "user_activity_logs", "discovered_chats"]:
            res = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = res.scalar()
            print(f"{table}: {count}")

if __name__ == "__main__":
    asyncio.run(check_db())
