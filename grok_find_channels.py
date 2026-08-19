import asyncio
import sys
import argparse
import logging
from sqlalchemy import select

# UTF-8 Encoding support for Windows stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.db.session import init_db, AsyncSessionLocal
from src.db.models import MonitoredChannel
from src.ai.grok_channel_finder import GrokChannelFinder

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("grok_cli")

async def run_interactive_grok_cli(keywords: str):
    print("\n" + "=" * 65)
    print("🤖 GROK TELEGRAM CHANNEL & GROUP DISCOVERY SUBSCRIPT")
    print("=" * 65)
    print(f"Target Keywords: '{keywords}'\n")

    # 1. Initialize Database Schema
    await init_db()

    # 2. Run Grok Search
    finder = GrokChannelFinder()
    print(f"⏳ Grok AI is searching Telegram channels and public groups (chats)...")
    candidates = await finder.search_channels_and_groups(keywords=keywords, limit=8)

    if not candidates:
        print("❌ No matching Telegram channels or groups found. Try different keywords.")
        return

    print(f"\n🎯 Grok found {len(candidates)} candidate channels and groups for review:\n")

    approved_count = 0
    skipped_count = 0

    async with AsyncSessionLocal() as session:
        for idx, item in enumerate(candidates, 1):
            type_icon = "👥 ГРУППА (ЧАТ)" if item["chat_type"] == "group" else "📢 КАНАЛ"
            username = item["username"]
            title = item["title"]
            members = item.get("estimated_members", "N/A")
            desc = item.get("description", "")

            print("-" * 65)
            print(f"[{idx}/{len(candidates)}] {type_icon}: {title}")
            print(f"   Ссылка/Username: {username}")
            print(f"   Участники (оценка): {members}")
            print(f"   Обоснование Grok: {desc}")
            print("-" * 65)

            # Check if already monitored in DB
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == username)
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                print(f"   ℹ️  Уже присутствует в базе данных (Статус: {existing.status})\n")
                continue

            # Prompt user for approval
            while True:
                choice = input(f"👉 Утвердить {username} и добавить в лист прослушки? [y=Да / n=Пропустить / q=Выход]: ").strip().lower()
                if choice in ["y", "yes", "н", "да"]:
                    channel = MonitoredChannel(
                        username_or_link=username,
                        title=title,
                        chat_type=item["chat_type"],
                        niche_code=item.get("niche_code", "general"),
                        status="PENDING"
                    )
                    session.add(channel)
                    await session.commit()
                    approved_count += 1
                    print(f"   ✅ {username} успешно добавлен в лист слушания (БД: monitored_channels)!\n")
                    break
                elif choice in ["n", "no", "т", "нет"]:
                    skipped_count += 1
                    print(f"   ❌ {username} пропущен.\n")
                    break
                elif choice in ["q", "quit", "exit"]:
                    print("\n🛑 Процесс завершен пользователем.")
                    print(f"📊 Итог: Утверждено: {approved_count}, Пропущено: {skipped_count}\n")
                    return
                else:
                    print("   Введите 'y', 'n' или 'q'.")

    print("=" * 65)
    print(f"🎉 Просмотр завершен! Добавлено {approved_count} новых чатов/каналов в лист прослушки.")
    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Grok Telegram Channel & Group Discovery Subscript")
    parser.add_argument("--keywords", "-k", type=str, help="Search keywords (e.g. 'нячанг аренда квартир')")
    args = parser.parse_args()

    keywords = args.keywords
    if not keywords:
        keywords = input("🔑 Введите ключевые слова для поиска чатов и каналов: ").strip()

    if not keywords:
        print("❌ Ключевые слова не заданы.")
        sys.exit(1)

    asyncio.run(run_interactive_grok_cli(keywords))

if __name__ == "__main__":
    main()
