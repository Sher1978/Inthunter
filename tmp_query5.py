from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel, DiscoveredChat
import asyncio
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        # Get count of DiscoveredChat table
        res_dc = await session.execute(select(DiscoveredChat))
        print("Total DiscoveredChat rows:", len(res_dc.scalars().all()))

        # Get all MonitoredChannels created recently
        res_mc = await session.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at.desc()).limit(50))
        channels = res_mc.scalars().all()
        for c in channels:
            print(f"{c.created_at} | {c.location_code} | {c.niche_code} | {c.title} | {c.status}")

asyncio.run(main())
