import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import Partner
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Partner))
        partners = list(res.scalars().all())
        print(f"Total Partners in DB: {len(partners)}")
        for p in partners:
            print(f" - ID: {p.telegram_id} | Name: {p.company_name} | Role: {p.role} | Status: {p.moderation_status}")

if __name__ == "__main__":
    asyncio.run(main())
