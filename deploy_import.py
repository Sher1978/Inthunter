import asyncio
import json
import sys
import os
from sqlalchemy import select

sys.path.insert(0, os.path.abspath("."))
from src.db.session import AsyncSessionLocal
from src.db.models import ScraperAccount

async def main():
    try:
        with open("payload_sessions.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("payload_sessions.json not found!")
        return

    accounts = data.get("accounts", [])
    if not accounts:
        print("No accounts found in payload.")
        return

    async with AsyncSessionLocal() as session:
        added = 0
        skipped = 0
        for acc in accounts:
            s_str = acc.get("session_string")
            phone = acc.get("phone_number")
            if not s_str: continue

            existing = (await session.execute(select(ScraperAccount).where(ScraperAccount.session_string == s_str))).scalar_one_or_none()
            if not existing:
                new_acc = ScraperAccount(
                    session_string=s_str,
                    phone_number=f"+{phone}" if phone else None,
                    status="ACTIVE",
                    max_daily_joins=20,
                    daily_join_count=0
                )
                session.add(new_acc)
                added += 1
            else:
                skipped += 1
        
        await session.commit()
        print(f"SUCCESS: Added {added} accounts to the database! (Skipped {skipped} duplicates)")

if __name__ == "__main__":
    asyncio.run(main())
