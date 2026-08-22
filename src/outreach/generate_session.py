import asyncio
from pyrogram import Client

async def main():
    print("=" * 60)
    print("🚀 LEADRADAR - PYROGRAM STRING SESSION GENERATOR")
    print("=" * 60)
    
    api_id_input = input("Введите Telegram API ID (с сайта my.telegram.org) [по умолчанию press Enter]: ").strip()
    api_hash_input = input("Введите Telegram API HASH (с сайта my.telegram.org) [по умолчанию press Enter]: ").strip()

    from src.config import settings
    api_id = int(api_id_input) if api_id_input else settings.TELEGRAM_API_ID
    api_hash = api_hash_input if api_hash_input else settings.TELEGRAM_API_HASH

    print(f"\nИспользуется API ID: {api_id}")
    print("Сейчас Telegram попросит номер телефона и SMS-код авторизации...\n")

    async with Client(":memory:", api_id=api_id, api_hash=api_hash) as app:
        session_str = await app.export_session_string()
        me = await app.get_me()
        print("\n" + "🟢" * 30)
        print(f"✅ УСПЕШНО АВТОРИЗОВАН! Аккаунт: {me.first_name} (@{me.username or 'без_юзернейма'}, ID: {me.id})")
        print("=" * 60)
        print("ВАША СТРОКА СЕССИИ (Pyrogram StringSession):")
        print("=" * 60)
        print(session_str)
        print("=" * 60)
        print("📋 Скопируйте всю строку выше и вставьте в поле 'Pyrogram StringSession' в панели управления!")
        print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
