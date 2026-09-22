import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel
from sqlalchemy import select

async def migrate():
    async with AsyncSessionLocal() as session:
        print("Cleaning up Excel import artifacts (removing @_ prefixes)...")
        channels = (await session.execute(select(MonitoredChannel))).scalars().all()
        for ch in channels:
            dirty = False
            if ch.title and ch.title.startswith('@_'):
                ch.title = '@' + ch.title[2:]
                dirty = True
            if ch.username_or_link and ch.username_or_link.startswith('@_'):
                ch.username_or_link = '@' + ch.username_or_link[2:]
                dirty = True
            
            if dirty:
                print(f"Cleaned channel: {ch.title} ({ch.username_or_link})")
        
        await session.commit()
        print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
