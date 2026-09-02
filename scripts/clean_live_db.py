import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import select, delete
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel

async def clean_live_db():
    print("Starting Live Database Cleanup...")
    
    good_tgstat_links = [
        "@dubaiprofi",
        "@dubaisk_8",
        "@madubai",
        "@kstati_dubai",
        "@dubai_uae_hub"
    ]
    
    spam_links = [
        "@monicavallejo1",
        "@victoriacakeshaven",
        "@sabrinandreina46",
        "@urbe_bikini",
        "@senorita_sara8",
        "@dreddxxx_0",
        "@ashleyxfox2122",
        "@yessybernalucra",
        "@anitajimaok",
        "@lana_lrvin",
        "@jossbolivar",
        "@adrianaolivarez15",
        "@analy_bazanof",
        "@elizabecommunityxx",
        "@saral_seva_bharti_strugglers",
        "@spjinimart"
    ]
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(MonitoredChannel))
        channels = result.scalars().all()
        
        total_channels = len(channels)
        print(f"Total monitored channels in DB: {total_channels}")
        
        to_keep = []
        to_delete = []
        
        for ch in channels:
            keep = False
            username_lower = (ch.username_or_link or "").lower().replace('https://t.me/', '@')
            
            # If it's a known spam link, delete it immediately
            if username_lower in spam_links:
                keep = False
            # Rule 1: Real leads
            elif ch.leads_count > 0:
                keep = True
            # Rule 2: In the 5 good TGSTAT links
            elif username_lower in good_tgstat_links:
                keep = True
            # Rule 3: Userbot joined recently
            elif ch.status == 'JOINED':
                keep = True
            
            if keep:
                to_keep.append(ch)
            else:
                to_delete.append(ch)
                
        print(f"Keeping {len(to_keep)} channels.")
        print(f"Deleting {len(to_delete)} channels...")
        
        if to_delete:
            delete_ids = [ch.id for ch in to_delete]
            chunk_size = 500
            for i in range(0, len(delete_ids), chunk_size):
                chunk = delete_ids[i:i + chunk_size]
                await session.execute(
                    delete(MonitoredChannel).where(MonitoredChannel.id.in_(chunk))
                )
            await session.commit()
            print("Cleanup finished and committed!")
        else:
            print("Nothing to delete.")

if __name__ == "__main__":
    asyncio.run(clean_live_db())
