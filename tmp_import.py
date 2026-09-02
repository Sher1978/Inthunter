import sys
import os
import asyncio
import re

sys.path.insert(0, os.path.abspath("."))
from pyrogram import Client
from pyrogram.enums import ChatType
from src.db.session import AsyncSessionLocal
from src.db.models import ScraperAccount, MonitoredChannel, DiscoveredChat
from sqlalchemy import select
from datetime import datetime, timezone

SPAM_PATTERNS = [
    r'[\u4e00-\u9fff]',
    r'[\uac00-\ud7af]',
    r'trader', r'cricket', r'crypto', r'pump', r'casino', r'baccarat', r'betting',
    r'क', r'ख', r'ग', r'घ', r'च',
    r'担保', r'公群', r'开房', r'记录', r'사기'
]

async def main():
    async with AsyncSessionLocal() as session:
        acc = (await session.execute(
            select(ScraperAccount).where(ScraperAccount.account_username == '@Sherlock_cars_uae')
        )).scalars().first()
        
        if not acc:
            print("Account not found")
            return

        print(f"Connecting to userbot {acc.phone_number}...")
        
        from src.config import settings
        client = Client(
            name="importer_bot",
            api_id=settings.TELEGRAM_API_ID,
            api_hash=settings.TELEGRAM_API_HASH,
            session_string=acc.session_string,
            in_memory=True
        )

        await client.connect()
        print("Connected.")

        imported_count = 0
        total = 0
        groups = 0
        seen_usernames = set()

        async for dialog in client.get_dialogs():
            total += 1
            chat = dialog.chat
            if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                groups += 1
                chat_title = chat.title or "Telegram Group"
                username = chat.username
                username_or_link = f"@{username}" if username else f"https://t.me/c/{str(chat.id).replace('-100', '')}"

                if username_or_link in seen_usernames:
                    continue
                seen_usernames.add(username_or_link)

                if any(re.search(pat, chat_title, re.IGNORECASE) for pat in SPAM_PATTERNS) or \
                   any(re.search(pat, username_or_link, re.IGNORECASE) for pat in SPAM_PATTERNS):
                    continue

                ch_query = select(MonitoredChannel)
                if username:
                    ch_query = ch_query.where(
                        (MonitoredChannel.title.ilike(chat_title)) |
                        (MonitoredChannel.username_or_link.ilike(f"%{username}%"))
                    )
                else:
                    ch_query = ch_query.where(MonitoredChannel.title.ilike(chat_title))

                ex_ch = (await session.execute(ch_query)).scalars().first()

                if not ex_ch:
                    new_ch = MonitoredChannel(
                        title=chat_title,
                        username_or_link=username_or_link,
                        niche_code="real_estate",
                        location_code="dubai",
                        status="JOINED",
                        created_at=datetime.now(timezone.utc)
                    )
                    session.add(new_ch)

                    ex_disc = (await session.execute(
                        select(DiscoveredChat).where(DiscoveredChat.chat_username.ilike(username_or_link))
                    )).scalars().first()
                    if not ex_disc:
                        session.add(DiscoveredChat(
                            chat_username=username_or_link,
                            title=chat_title,
                            source="USERBOT_JOINED_AUTO_IMPORT",
                            audit_status="APPROVED",
                            score=90,
                            verdict_reason="Авто-импорт из юзербота @Sherlock_cars_uae",
                            location_code="dubai",
                            detected_niches=["real_estate"],
                            audited_at=datetime.now(timezone.utc)
                        ))

                    imported_count += 1

        if imported_count > 0:
            await session.commit()
            print(f"Successfully imported {imported_count} new groups directly into Scout & MonitoredChannels!")
        else:
            print("No new groups imported. All passed filter groups already exist.")
        
        print(f"Total dialogs: {total}")
        print(f"Total groups/channels: {groups}")
        print(f"Passed spam filter & unique & new: {imported_count}")

        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
