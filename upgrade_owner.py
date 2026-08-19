import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import Partner
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        p = (await session.execute(select(Partner).where(Partner.telegram_id == 260669598))).scalar_one_or_none()
        if p:
            p.role = "SUPERADMIN"
            p.moderation_status = "APPROVED"
            p.balance = 100.00
            await session.commit()
            print("✅ Telegram ID 260669598 (Ihor) successfully elevated to SUPERADMIN with $100.00 USD balance!")

if __name__ == "__main__":
    asyncio.run(main())
