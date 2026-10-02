import asyncio
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import Lead

async def check():
    async with AsyncSessionLocal() as session:
        stmt = select(Lead).where(Lead.intent_summary.ilike('%Владивосток%')).limit(1)
        res = await session.execute(stmt)
        l = res.scalars().first()
        if l:
            print(f"ID: {l.id}")
            print(f"Reasoning: {repr(l.reasoning)}")
            print(f"Sales Hook: {repr(l.sales_hook)}")
        else:
            print("Lead not found")

asyncio.run(check())
