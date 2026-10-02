import os

with open('src/ingestion/telegram.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_str = '''            msg_location = "global"
            clean_ct = (chat_title or "").strip().lower()
            clean_username = (channel_username or "").strip().lstrip("@").lower()
            if clean_ct or clean_username:
                try:
                    from src.db.models import MonitoredChannel
                    from sqlalchemy import func
                    ch_loc = (await session.execute(
                        select(MonitoredChannel.location_code).where(
                            (func.lower(MonitoredChannel.title) == clean_ct) |
                            (func.lower(MonitoredChannel.username_or_link) == f"@{clean_username}") |
                            (func.lower(MonitoredChannel.username_or_link) == clean_username)
                        )
                    )).scalars().first()
                    if ch_loc:
                        msg_location = ch_loc
                except Exception as loc_err:
                    logger.debug(f"Notice fetching channel location_code: {loc_err}")'''

new_str = '''            clean_ct = (chat_title or "").split("[Топик #")[0].strip().lower()
            clean_username = (channel_username or "").strip().lstrip("@").lower()
            
            # Smart Fallback: Keyword guesser
            msg_location = "global"
            combined_text = f"{clean_ct} {clean_username} {text.lower() if text else ''}"
            
            if any(k in combined_text for k in ['пхукет', 'phuket', 'rawai', 'naiharn', 'chalong', 'kata', 'karon', 'bangtao', 'kamala', 'patong', 'раваи', 'найхарн', 'чалонг', 'ката', 'карон', 'бангтао', 'камала', 'патонг', 'самуи', 'samui']):
                msg_location = 'phuket'
            elif any(k in combined_text for k in ['нячанг', 'nha trang', 'nhatrang', 'вьетнам', 'vietnam']):
                msg_location = 'nhatrang'
            elif any(k in combined_text for k in ['дананг', 'da nang', 'danang']):
                msg_location = 'danang'
            elif any(k in combined_text for k in ['бали', 'bali', 'canggu', 'kuta', 'seminyak', 'ubud', 'чанггу', 'кута', 'семиньяк', 'убуд', 'индонези']):
                msg_location = 'bali'
            elif any(k in combined_text for k in ['дубай', 'dubai', 'оаэ', 'uae', 'эмират']):
                msg_location = 'dubai'
            elif any(k in combined_text for k in ['тбилиси', 'tbilisi', 'грузи', 'georgia', 'батуми', 'batumi']):
                msg_location = 'tbilisi'

            # Strict MonitoredChannel Lookup (Overrides guess if explicitly set)
            if clean_ct or clean_username:
                try:
                    from src.db.models import MonitoredChannel
                    from sqlalchemy import func, or_
                    conditions = []
                    if clean_ct:
                        conditions.append(func.lower(MonitoredChannel.title) == clean_ct)
                    if clean_username:
                        conditions.append(func.lower(MonitoredChannel.username_or_link) == f"@{clean_username}")
                        conditions.append(func.lower(MonitoredChannel.username_or_link) == clean_username)
                    
                    if conditions:
                        ch_loc = (await session.execute(
                            select(MonitoredChannel.location_code).where(or_(*conditions))
                        )).scalars().first()
                        if ch_loc and ch_loc != "global":
                            msg_location = ch_loc
                except Exception as loc_err:
                    logger.debug(f"Notice fetching channel location_code: {loc_err}")'''

if old_str in content:
    content = content.replace(old_str, new_str)
    
    # Also fix the MonitoredChannel update logic
    old_update_str = '''                    await session.execute(
                        update(MonitoredChannel)
                        .where(
                            (func.lower(MonitoredChannel.title) == clean_ct) |
                            (func.lower(MonitoredChannel.username_or_link) == f"@{clean_ct}") |
                            (func.lower(MonitoredChannel.username_or_link) == clean_ct)
                        )
                        .values(last_scraped_at=datetime.now(timezone.utc))
                    )'''
    
    new_update_str = '''                    conditions = [func.lower(MonitoredChannel.title) == clean_ct]
                    if clean_username:
                        conditions.append(func.lower(MonitoredChannel.username_or_link) == f"@{clean_username}")
                        conditions.append(func.lower(MonitoredChannel.username_or_link) == clean_username)
                    
                    from sqlalchemy import or_
                    await session.execute(
                        update(MonitoredChannel)
                        .where(or_(*conditions))
                        .values(last_scraped_at=datetime.now(timezone.utc))
                    )'''
    content = content.replace(old_update_str, new_update_str)

    with open('src/ingestion/telegram.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Patched telegram.py successfully')
else:
    print('String not found!')
