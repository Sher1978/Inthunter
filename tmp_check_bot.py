import sys
import os
import asyncio
import re

sys.path.insert(0, os.path.abspath("."))
from pyrogram import Client
from pyrogram.enums import ChatType
from src.db.session import AsyncSessionLocal
from src.db.models import ScraperAccount, MonitoredChannel
from sqlalchemy import select

SPAM_PATTERNS = [
    r'[\u4e00-\u9fff]',  # Chinese
    r'[\uac00-\ud7af]',  # Korean
    r'trader', r'cricket', r'crypto', r'pump', r'casino', r'baccarat', r'betting',
    r'क', r'ख', r'ग', r'घ', r'च'
]

async def main():
    async with AsyncSessionLocal() as session:
        acc = (await session.execute(
            select(ScraperAccount).where(ScraperAccount.account_username == '@Sherlock_cars_uae')
        )).scalars().first()
        
        if not acc:
            print("Account @Sherlock_cars_uae not found in DB!")
            return

        print(f"Connecting to userbot {acc.phone_number}...")
        client = Client(
            name="checker_bot",
            api_id=2040, # Fake API ID for pyrogram if you don't have it, but wait! We need the actual API ID. Let's import from config.
            api_hash="b18441a1ff607e10a989891a5462e627",
            session_string=acc.session_string,
            in_memory=True
        )

        from src.config import settings
        client.api_id = settings.TELEGRAM_API_ID
        client.api_hash = settings.TELEGRAM_API_HASH

        await client.connect()
        print("Connected.")

        total = 0
        groups = 0
        passed_filter = 0

        async for dialog in client.get_dialogs():
            total += 1
            chat = dialog.chat
            if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                groups += 1
                chat_title = chat.title or "Telegram Group"
                username = chat.username
                username_or_link = f"@{username}" if username else f"https://t.me/c/{str(chat.id).replace('-100', '')}"

                if any(re.search(pat, chat_title, re.IGNORECASE) for pat in SPAM_PATTERNS) or \
                   any(re.search(pat, username_or_link, re.IGNORECASE) for pat in SPAM_PATTERNS):
                    continue
                
                passed_filter += 1

        print(f"Total dialogs: {total}")
        print(f"Total groups/channels: {groups}")
        print(f"Passed spam filter: {passed_filter}")

        # Check how many are in MonitoredChannel
        mc_res = await session.execute(select(MonitoredChannel))
        mcs = mc_res.scalars().all()
        print(f"Total MonitoredChannels in DB: {len(mcs)}")
        
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
