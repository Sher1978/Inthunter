import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import Partner
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        target_ids = [268669598, 260669598]
        for tid in target_ids:
            p = (await session.execute(select(Partner).where(Partner.telegram_id == tid))).scalar_one_or_none()
            if not p and tid == 268669598:
                p = Partner(
                    telegram_id=tid,
                    company_name="Компания Ihor",
                    role="SUPERADMIN",
                    moderation_status="APPROVED",
                    balance=1000.00,
                    subscribed_niches=["real_estate", "bike_rent", "currency_exchange", "services_visa", "auto_kasko"],
                    is_monitoring_active=True,
                    is_debug_monitoring=False
                )
                session.add(p)
                print(f"✅ Created new SUPERADMIN partner for Telegram ID {tid} (Ihor)")
            elif p:
                p.telegram_id = 268669598 if tid == 260669598 else tid
                p.role = "SUPERADMIN"
                p.moderation_status = "APPROVED"
                p.balance = max(float(p.balance), 1000.00)
                print(f"✅ Telegram ID {p.telegram_id} (Ihor) successfully elevated to SUPERADMIN with $1000.00 USD balance!")
        await session.commit()

if __name__ == "__main__":
    asyncio.run(main())
