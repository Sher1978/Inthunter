import asyncio
import sys
import os
from sqlalchemy import update, select

sys.path.insert(0, os.path.abspath("."))
from src.db.session import AsyncSessionLocal
from src.db.models import ScraperAccount

async def activate_all():
    sys.stdout.reconfigure(encoding='utf-8')
    async with AsyncSessionLocal() as session:
        # Update all 15 ScraperAccount records to ACTIVE
        await session.execute(
            update(ScraperAccount)
            .where(ScraperAccount.status == "DISABLED")
            .values(status="ACTIVE", error_log=None)
        )
        await session.commit()
        
        active_list = (await session.execute(
            select(ScraperAccount).where(ScraperAccount.status == "ACTIVE")
        )).scalars().all()
        
        print(f"✅ Все {len(active_list)} аккаунтов юзерботов переведены в статус ACTIVE!")
        for acc in active_list:
            print(f"  [ID {acc.id}] Phone: {acc.phone_number} | Status: {acc.status}")

if __name__ == "__main__":
    asyncio.run(activate_all())
