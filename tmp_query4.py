from src.db.session import AsyncSessionLocal
from src.db.models import ScraperAccount, MonitoredChannel, DiscoveredChat
import asyncio
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        # Check ScraperAccounts
        res_sa = await session.execute(select(ScraperAccount))
        accounts = res_sa.scalars().all()
        for a in accounts:
            print(f"Account: phone={a.phone_number}, username={getattr(a, 'account_username', '')}, status={a.status}")

        # Check MonitoredChannel added recently with location="dubai" and niche="real_estate"
        res_mc = await session.execute(select(MonitoredChannel).where(MonitoredChannel.location_code == 'dubai', MonitoredChannel.status == 'JOINED'))
        channels = res_mc.scalars().all()
        print(f"MonitoredChannels with dubai+real_estate+JOINED: {len(channels)}")

asyncio.run(main())
