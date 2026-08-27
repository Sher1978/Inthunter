#!/usr/bin/env python3
"""
Interactive CLI Script for Generating Pyrogram USERBOT_SESSION_STRING.
Usage:
    python scripts/generate_session.py
"""

import sys
import os
import asyncio

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from pyrogram import Client
except ImportError:
    print("❌ Pyrogram is not installed! Run: pip install pyrogram tgcrypto")
    sys.exit(1)

def update_env_file(key: str, value: str, env_path: str = ".env"):
    """Helper to write or update a key=value pair in the .env file."""
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}=") or line.strip().startswith(f"export {key}="):
                lines[i] = f'{key}="{value}"\n'
                found = True
                break

    if not found:
        lines.append(f'\n{key}="{value}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"✅ Successfully written {key} to {env_path}")


async def main():
    print("==========================================================")
    print("🔑 INTENT HUNTER — PYROGRAM USERBOT SESSION GENERATOR")
    print("==========================================================")

    # Try loading default API ID and HASH from settings/config
    api_id = os.getenv("TELEGRAM_API_ID", "33842717")
    api_hash = os.getenv("TELEGRAM_API_HASH", "370212aabacfec01a554788aeda7cf0e")

    print(f"📌 Using API ID: {api_id}")
    print(f"📌 Using API Hash: {api_hash[:6]}...")
    print("----------------------------------------------------------")

    app = Client(
        "temp_userbot_session",
        api_id=int(api_id),
        api_hash=api_hash,
        in_memory=True
    )

    print("🚀 Connecting to Telegram API... Follow instructions below:")
    await app.start()
    
    session_string = await app.export_session_string()
    me = await app.get_me()
    
    print("\n----------------------------------------------------------")
    print(f"🎉 SUCCESS! Authorized as @{me.username or me.id} ({me.first_name})")
    print("----------------------------------------------------------")
    print("\n📦 YOUR USERBOT SESSION STRING:")
    print(f"\n{session_string}\n")
    print("----------------------------------------------------------")

    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    update_env_file("USERBOT_SESSION_STRING", session_string, env_path=env_path)

    await app.stop()
    print("✨ Process completed! You can now start the main application.")

if __name__ == "__main__":
    asyncio.run(main())
