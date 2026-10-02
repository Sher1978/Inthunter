import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check_channels_status():
    async with AsyncSessionLocal() as session:
        res = await session.execute(text("SELECT status, COUNT(*) FROM monitored_channels GROUP BY status"))
        for status, count in res.all():
            print(f"{status}: {count}")

if __name__ == "__main__":
    asyncio.run(check_channels_status())
