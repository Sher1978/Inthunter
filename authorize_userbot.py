import sys
import os
import json
import asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath("."))

from src.config import settings
from src.db.session import AsyncSessionLocal
from src.db.models import ScraperAccount, MonitoredChannel, DiscoveredChat
from pyrogram import Client
from pyrogram.enums import ChatType
from sqlalchemy import select

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CODE_FILE = os.path.join(BASE_DIR, "code_input.txt")
STATUS_FILE = os.path.join(BASE_DIR, "auth_status.json")

async def main():
    phone = "+971588044688"
    if len(sys.argv) > 1 and sys.argv[1].startswith("+"):
        phone = sys.argv[1].strip()

    if os.path.exists(CODE_FILE):
        try:
            os.remove(CODE_FILE)
        except Exception:
            pass

    print(f"Connecting to Telegram and sending auth code to {phone}...")
    client = Client(
        name="userbot_active_auth",
        api_id=settings.TELEGRAM_API_ID,
        api_hash=settings.TELEGRAM_API_HASH,
        in_memory=True
    )
    
    await client.connect()
    try:
        sent_code = await client.send_code(phone)
        phone_code_hash = sent_code.phone_code_hash
        print(f"SUCCESS: Authorization code sent to Telegram app for {phone}!")
        sys.stdout.flush()

        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump({"status": "WAITING_FOR_CODE", "phone": phone, "phone_code_hash": phone_code_hash}, f)

        print("WAITING_FOR_USER_INPUT_IN_FILE")
        sys.stdout.flush()

        # Poll code_input.txt for 300 seconds (5 minutes) while keeping connection alive
        auth_code = None
        for step in range(300):
            if os.path.exists(CODE_FILE):
                try:
                    with open(CODE_FILE, "r", encoding="utf-8") as cf:
                        auth_code = cf.read().strip()
                    if auth_code and len(auth_code) >= 4:
                        print(f"FOUND CODE IN FILE: '{auth_code}' at step {step}")
                        sys.stdout.flush()
                        break
                except Exception as read_err:
                    print(f"Notice reading code file: {read_err}")
            await asyncio.sleep(1)

        if not auth_code:
            print("TIMEOUT: User did not provide code in code_input.txt within 3 minutes.")
            await client.disconnect()
            return

        print(f"Signing in with code {auth_code} on SAME active connection...")
        sys.stdout.flush()
        try:
            await client.sign_in(phone, phone_code_hash, auth_code)
        except Exception as e:
            err_str = str(e)
            print(f"Sign_in exception notice: {err_str}")
            sys.stdout.flush()
            if "SESSION_PASSWORD_NEEDED" in err_str or "SessionPasswordNeeded" in err_str:
                print("2FA_PASSWORD_REQUIRED: Checking for 2fa_input.txt...")
                sys.stdout.flush()
                pwd = None
                for _ in range(60):
                    if os.path.exists("2fa_input.txt"):
                        with open("2fa_input.txt", "r", encoding="utf-8") as pf:
                            pwd = pf.read().strip()
                        if pwd:
                            break
                    await asyncio.sleep(1)
                if pwd:
                    print(f"Checking 2FA password '{pwd}'...")
                    sys.stdout.flush()
                    await client.check_password(pwd)
                else:
                    print("ERROR: 2FA Password not provided in 2fa_input.txt.")
                    sys.stdout.flush()
                    await client.disconnect()
                    return
            else:
                raise e

        me = await client.get_me()
        session_str = await client.export_session_string()
        user_label = f"@{me.username}" if me.username else str(me.id)
        print(f"SUCCESS: Logged in as {user_label} (ID: {me.id})")
        sys.stdout.flush()

        # 1. Save / Update ScraperAccount in DB
        async with AsyncSessionLocal() as session:
            stmt = select(ScraperAccount).where(
                (ScraperAccount.session_string == session_str) |
                (ScraperAccount.status == 'BANNED')
            )
            ex_acc = (await session.execute(stmt)).scalars().first()
            if ex_acc:
                ex_acc.session_string = session_str
                ex_acc.status = "ACTIVE"
                ex_acc.error_log = None
                ex_acc.flood_until = None
                ex_acc.daily_join_count = 0
                await session.commit()
                print("DB: Updated existing ScraperAccount session to ACTIVE.")
            else:
                new_acc = ScraperAccount(
                    session_string=session_str,
                    max_daily_joins=20,
                    status="ACTIVE",
                    daily_join_count=0
                )
                session.add(new_acc)
                await session.commit()
                print("DB: Created new ScraperAccount in database.")
            sys.stdout.flush()

        # 2. Auto-Import ALL Dialogs/Groups from +971588044688 into Scout
        print("Scout: Auto-scanning all joined groups of this userbot account...")
        sys.stdout.flush()
        imported_count = 0
        async with AsyncSessionLocal() as session:
            async for dialog in client.get_dialogs():
                chat = dialog.chat
                if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                    chat_title = chat.title or "Telegram Group"
                    username = chat.username
                    username_or_link = f"@{username}" if username else f"https://t.me/c/{str(chat.id).replace('-100', '')}"

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
                        session.add(MonitoredChannel(
                            title=chat_title,
                            username_or_link=username_or_link,
                            niche_code="real_estate",
                            location_code="dubai",
                            status="JOINED",
                            created_at=datetime.now(timezone.utc)
                        ))
                        session.add(DiscoveredChat(
                            chat_username=username_or_link,
                            title=chat_title,
                            source="USERBOT_JOINED_AUTO_IMPORT",
                            audit_status="APPROVED",
                            score=90,
                            verdict_reason=f"Авто-импорт из юзербота {phone}",
                            location_code="dubai",
                            audited_at=datetime.now(timezone.utc)
                        ))
                        imported_count += 1

            if imported_count > 0:
                await session.commit()
                print(f"Scout: Successfully imported {imported_count} new groups directly into Scout & MonitoredChannels!")
            else:
                print("Scout: All groups from this userbot account are already in Scout!")

        await client.disconnect()
        for fpath in [CODE_FILE, STATUS_FILE, "2fa_input.txt"]:
            if os.path.exists(fpath):
                os.remove(fpath)

        print("COMPLETE: Userbot +971588044688 successfully reconnected and all groups imported!")

    except Exception as err:
        print(f"ERROR: {err}")
        try:
            await client.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
