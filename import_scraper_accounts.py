import asyncio
import sys
import os
from sqlalchemy import select

sys.path.insert(0, os.path.abspath("."))
from src.db.session import AsyncSessionLocal
from src.db.models import ScraperAccount

async def import_accounts(session_strings: list, max_daily_joins: int = 15):
    """
    Imports a list of Pyrogram session strings into ScraperAccount database table.
    """
    async with AsyncSessionLocal() as session:
        added_count = 0
        skipped_count = 0
        for s_str in session_strings:
            s_clean = s_str.strip().strip('"').strip("'")
            if not s_clean:
                continue
            
            # Check if session string already exists
            existing = (await session.execute(
                select(ScraperAccount).where(ScraperAccount.session_string == s_clean)
            )).scalar_one_or_none()
            
            if not existing:
                account = ScraperAccount(
                    session_string=s_clean,
                    status="ACTIVE",
                    max_daily_joins=max_daily_joins,
                    daily_join_count=0
                )
                session.add(account)
                added_count += 1
            else:
                skipped_count += 1
                
        await session.commit()
        print(f"✅ Успешно добавлено {added_count} аккаунтов юзерботов в базу! (Пропущено дубликатов: {skipped_count})")
        
        # Display current active pool count
        total_active = (await session.execute(
            select(ScraperAccount).where(ScraperAccount.status == "ACTIVE")
        )).scalars().all()
        print(f"📊 Всего активных аккаунтов в пуле Swarm: {len(total_active)} шт.")

if __name__ == "__main__":
    print("=== Импорт пула Юзербот-аккаунтов (Swarm) ===")
    if len(sys.argv) > 1:
        # Pass session strings via args or input file
        input_path = sys.argv[1]
        if os.path.exists(input_path):
            with open(input_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            asyncio.run(import_accounts(lines))
        else:
            asyncio.run(import_accounts([sys.argv[1]]))
    else:
        print("💡 Инструкция:")
        print("Вы можете создать файл `sessions.txt` (1 сессия на строчку) и запустить:")
        print("python import_scraper_accounts.py sessions.txt")
