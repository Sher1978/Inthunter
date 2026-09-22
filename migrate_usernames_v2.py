import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel
from sqlalchemy import select

async def migrate():
    async with AsyncSessionLocal() as session:
        print("Cleaning up Excel import artifacts (removing _ after @ or t.me/)...")
        channels = (await session.execute(select(MonitoredChannel))).scalars().all()
        for ch in channels:
            dirty = False
            
            # Clean title
            if ch.title:
                t = ch.title
                if '@_' in t:
                    ch.title = t.replace('@_', '@')
                    dirty = True
                elif t.startswith('_'):
                    ch.title = t[1:]
                    dirty = True

            # Clean username_or_link
            if ch.username_or_link:
                u = ch.username_or_link
                if '@_' in u:
                    ch.username_or_link = u.replace('@_', '@')
                    dirty = True
                elif 't.me/_' in u:
                    ch.username_or_link = u.replace('t.me/_', 't.me/')
                    dirty = True
                elif u.startswith('_'):
                    ch.username_or_link = u[1:]
                    dirty = True
            
            if dirty:
                print(f"Cleaned channel: {ch.title} ({ch.username_or_link})")
        
        await session.commit()
        print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
