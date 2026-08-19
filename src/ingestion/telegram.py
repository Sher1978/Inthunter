import asyncio
import logging
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.session import AsyncSessionLocal
from src.db.models import UserProfile, UserActivityLog
from src.ai.scorer import evaluate_user_timeline

logger = logging.getLogger("intent_hunter.ingestion")

class TelegramIngestor:
    """
    Pyrogram / Telethon passive userbot message listener.
    Captures chat activity, updates user profiles, and triggers intent scoring.
    """

    def __init__(self):
        self.app = None
        self._is_running = False

    async def setup(self):
        """Initializes Pyrogram Client if credentials exist."""
        if not settings.TELEGRAM_API_ID or settings.TELEGRAM_API_ID == 123456 or not settings.TELEGRAM_API_HASH or settings.TELEGRAM_API_HASH == "mock_hash":
            logger.warning("Telegram API_ID/HASH not configured in .env. Userbot listener paused until credentials provided.")
            return

        if not settings.USERBOT_SESSION_STRING:
            logger.warning("USERBOT_SESSION_STRING missing. To connect Userbot, generate Pyrogram session string or use Bot token.")
            return

        try:
            from pyrogram import Client, filters
            from pyrogram.types import Message

            self.app = Client(
                "intent_hunter_userbot",
                api_id=settings.TELEGRAM_API_ID,
                api_hash=settings.TELEGRAM_API_HASH,
                session_string=settings.USERBOT_SESSION_STRING
            )


            @self.app.on_message(filters.group | filters.channel | filters.text)
            async def on_new_message(client: Client, message: Message):
                await self.process_incoming_message(
                    user_id=message.from_user.id if message.from_user else 0,
                    username=message.from_user.username if message.from_user else None,
                    first_name=message.from_user.first_name if message.from_user else None,
                    last_name=message.from_user.last_name if message.from_user else None,
                    chat_id=message.chat.id,
                    chat_title=message.chat.title or message.chat.username or str(message.chat.id),
                    message_id=message.id,
                    text=message.text or message.caption or ""
                )

        except Exception as e:
            logger.error(f"Failed to set up Pyrogram client: {e}")

    async def process_incoming_message(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        chat_id: int,
        chat_title: str,
        message_id: int,
        text: str
    ):
        if not user_id or not text.strip():
            return

        logger.info(f"Received message from user_id={user_id} in [{chat_title}]: \"{text[:40]}...\"")

        async with AsyncSessionLocal() as session:
            # 1. UPSERT UserProfile
            stmt = select(UserProfile).where(UserProfile.user_id == user_id)
            result = await session.execute(stmt)
            profile = result.scalar_one_or_none()

            if not profile:
                profile = UserProfile(
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name
                )
                session.add(profile)
            else:
                profile.username = username or profile.username
                profile.first_name = first_name or profile.first_name
                profile.last_name = last_name or profile.last_name

            # 2. Record UserActivityLog
            activity = UserActivityLog(
                user_id=user_id,
                chat_id=chat_id,
                chat_title=chat_title,
                message_id=message_id,
                message_text=text
            )
            session.add(activity)
            await session.commit()

            # 3. Check trigger threshold for AI scoring
            user_msg_count_stmt = select(UserActivityLog).where(UserActivityLog.user_id == user_id)
            count_res = await session.execute(user_msg_count_stmt)
            messages = list(count_res.scalars().all())

            if len(messages) >= settings.MIN_MESSAGES_FOR_SCORING:
                asyncio.create_task(self._trigger_ai_scoring(user_id, messages))

    async def _trigger_ai_scoring(self, user_id: int, messages: List[UserActivityLog]):
        """Runs AI evaluation asynchronously in background."""
        try:
            async with AsyncSessionLocal() as session:
                lead_result = await evaluate_user_timeline(user_id, session, messages)
                if lead_result and lead_result.is_lead:
                    # Notify Alert Bot
                    from src.bot.alert_bot import broadcast_lead_alert
                    await broadcast_lead_alert(user_id, lead_result, messages)
        except Exception as e:
            logger.error(f"Error in background AI scoring: {e}")

    async def join_channel(self, username_or_link: str):
        """Attempts to auto-join target chat/channel using Pyrogram Userbot."""
        if not self.app or not self._is_running:
            logger.warning(f"Pyrogram Userbot not active. Cannot auto-join {username_or_link} immediately.")
            return False, None, "Userbot not running (check USERBOT_SESSION_STRING)"

        try:
            # Clean username/link format
            clean_target = username_or_link.strip().replace("https://t.me/", "@").replace("http://t.me/", "@")
            if not clean_target.startswith("@") and not clean_target.startswith("+"):
                clean_target = f"@{clean_target}"

            chat = await self.app.join_chat(clean_target)
            title = getattr(chat, "title", None) or getattr(chat, "username", None) or username_or_link
            logger.info(f"✅ Userbot successfully joined target chat: {title} ({clean_target})")
            return True, title, None
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Failed to join chat {username_or_link}: {err_msg}")
            return False, None, err_msg

    async def sync_monitored_channels(self):
        """Syncs all PENDING channels from DB and attempts auto-join."""
        from src.db.models import MonitoredChannel
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(MonitoredChannel).where(MonitoredChannel.status == "PENDING"))
            pending_channels = list(res.scalars().all())

            for channel in pending_channels:
                success, title, error = await self.join_channel(channel.username_or_link)
                if success:
                    channel.status = "JOINED"
                    channel.title = title
                    channel.error_message = None
                else:
                    channel.status = "FAILED"
                    channel.error_message = error
            await session.commit()

    async def run_public_scraper_loop(self):
        """Periodic background task that scrapes public Telegram channels without userbot accounts."""
        from src.db.models import MonitoredChannel
        from src.ingestion.public_scraper import PublicTelegramScraper

        scraper = PublicTelegramScraper()
        logger.info("📡 Starting Accountless Public Telegram Channel Scraper Loop...")

        processed_posts = set()

        while self._is_running:
            try:
                async with AsyncSessionLocal() as session:
                    res = await session.execute(select(MonitoredChannel))
                    channels = list(res.scalars().all())

                for channel in channels:
                    target = channel.username_or_link
                    posts = await scraper.fetch_latest_messages(target)

                    new_max_id = channel.last_scraped_msg_id or 0
                    new_posts_found = 0

                    for post in posts:
                        msg_id = post["message_id"]
                        post_key = f"{target}:{msg_id}"

                        # Skip already processed or old message IDs
                        if (channel.last_scraped_msg_id and msg_id <= channel.last_scraped_msg_id) or post_key in processed_posts:
                            continue

                        processed_posts.add(post_key)
                        if msg_id > new_max_id:
                            new_max_id = msg_id
                        new_posts_found += 1

                        # Process message through pipeline
                        await self.process_incoming_message(
                            user_id=post["user_id"],
                            username=post["username"],
                            first_name=post["first_name"],
                            last_name=post["last_name"],
                            chat_id=abs(hash(target)) % (10**9),
                            chat_title=post["chat_title"] or target,
                            message_id=post["message_id"],
                            text=post["text"]
                        )

                    # Update last_scraped_msg_id and channel status in DB
                    if new_posts_found > 0 or channel.status != "JOINED":
                        async with AsyncSessionLocal() as session:
                            stmt = select(MonitoredChannel).where(MonitoredChannel.id == channel.id)
                            ch_db = (await session.execute(stmt)).scalar_one_or_none()
                            if ch_db:
                                ch_db.last_scraped_msg_id = max(ch_db.last_scraped_msg_id or 0, new_max_id)
                                ch_db.status = "JOINED"
                                if posts:
                                    ch_db.title = posts[0]["chat_title"] or ch_db.title
                                ch_db.error_message = None
                                await session.commit()

            except Exception as e:
                logger.error(f"Error in public scraper loop: {e}")

            await asyncio.sleep(15)

    async def start(self):
        self._is_running = True
        if self.app:
            logger.info("Starting Pyrogram Userbot Listener...")
            await self.app.start()
            await self.sync_monitored_channels()

        # Always start public zero-auth scraper loop regardless of userbot credentials!
        asyncio.create_task(self.run_public_scraper_loop())

    async def stop(self):
        self._is_running = False
        if self.app:
            await self.app.stop()
