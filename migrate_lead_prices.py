import asyncio
from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.db.models import Lead
from src.services.purchase_engine import get_lead_pricing_by_location

async def migrate_lead_prices():
    async with AsyncSessionLocal() as session:
        print("Migrating lead prices to USD based on location...")
        # Get all leads
        leads = (await session.execute(select(Lead))).scalars().all()
        updated_count = 0
        
        for lead in leads:
            # We skip leads that are already sold, or we can update their price anyway 
            # for historical accuracy? The user wants marketplace values fixed.
            if lead.status == "AVAILABLE":
                base, _ = get_lead_pricing_by_location(lead.location_code)
                if lead.price != base:
                    lead.price = base
                    updated_count += 1
        
        await session.commit()
        print(f"Migration complete! Updated prices for {updated_count} active leads.")

if __name__ == "__main__":
    asyncio.run(migrate_lead_prices())
