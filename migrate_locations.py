import asyncio
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel, UserActivityLog, Lead
from sqlalchemy import select

def guess_loc(text):
    if not text: return None
    t = text.lower()
    if any(k in t for k in ['пхукет', 'phuket', 'rawai', 'naiharn', 'chalong', 'kata', 'karon', 'bangtao', 'kamala', 'patong', 'раваи', 'найхарн', 'чалонг', 'ката', 'карон', 'бангтао', 'камала', 'патонг', 'самуи', 'samui']): return 'phuket'
    if any(k in t for k in ['нячанг', 'nha trang', 'nhatrang', 'вьетнам', 'vietnam']): return 'nhatrang'
    if any(k in t for k in ['дананг', 'da nang', 'danang']): return 'danang'
    if any(k in t for k in ['бали', 'bali', 'canggu', 'kuta', 'seminyak', 'ubud', 'чанггу', 'кута', 'семиньяк', 'убуд', 'индонези']): return 'bali'
    if any(k in t for k in ['дубай', 'dubai', 'оаэ', 'uae', 'эмират']): return 'dubai'
    if any(k in t for k in ['тбилиси', 'tbilisi', 'грузи', 'georgia', 'батуми', 'batumi']): return 'tbilisi'
    return None

async def migrate():
    async with AsyncSessionLocal() as session:
        print("Migrating MonitoredChannels...")
        channels = (await session.execute(select(MonitoredChannel))).scalars().all()
        for ch in channels:
            combined = f"{ch.title} {ch.username_or_link}"
            guess = guess_loc(combined)
            if guess and (not ch.location_code or ch.location_code == 'global'):
                ch.location_code = guess
        
        await session.commit()
        
        # Build map of channels
        ch_map = {}
        ch_user_map = {}
        for ch in channels:
            if ch.title:
                ch_map[ch.title.strip().lower()] = ch.location_code
            if ch.username_or_link:
                ch_user_map[ch.username_or_link.strip().lstrip('@').lower()] = ch.location_code

        print("Migrating UserActivityLogs...")
        logs = (await session.execute(select(UserActivityLog))).scalars().all()
        log_loc_map = {} # user_id -> location_code
        for log in logs:
            new_loc = None
            clean_ct = (log.chat_title or "").split("[Топик #")[0].strip().lower()
            clean_un = (log.channel_username or "").strip().lstrip("@").lower()
            
            # 1. Check DB map
            if clean_ct in ch_map and ch_map[clean_ct] and ch_map[clean_ct] != 'global':
                new_loc = ch_map[clean_ct]
            elif clean_un in ch_user_map and ch_user_map[clean_un] and ch_user_map[clean_un] != 'global':
                new_loc = ch_user_map[clean_un]
            else:
                # 2. Guess from combined text
                combined = f"{clean_ct} {clean_un} {log.message_text}"
                new_loc = guess_loc(combined)
            
            if new_loc:
                log.location_code = new_loc
                if new_loc != 'global':
                    log_loc_map[log.user_id] = new_loc
                
        await session.commit()
        
        print("Migrating Leads...")
        leads = (await session.execute(select(Lead))).scalars().all()
        for lead in leads:
            if lead.user_id in log_loc_map:
                lead.location_code = log_loc_map[lead.user_id]
            else:
                # Fallback guess from intent summary
                guess = guess_loc(lead.intent_summary)
                if guess:
                    lead.location_code = guess
        
        await session.commit()
        print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
