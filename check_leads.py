import asyncio
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import Lead

async def check():
    async with AsyncSessionLocal() as session:
        stmt = select(Lead).order_by(Lead.created_at.desc()).limit(10)
        res = await session.execute(stmt)
        leads = res.scalars().all()
        for l in leads:
            print(f"ID: {l.id}, Created: {l.created_at}, Reasoning: '{l.reasoning}', Sales Hook: '{l.sales_hook}'")

asyncio.run(check())
