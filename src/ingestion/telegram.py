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
        self.last_scraped_at = None
        self.last_check_at = None
        self.scraped_count = 0
        self.public_scraper_task = None
        self.watchdog_task = None
        self.retention_task = None

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

        from datetime import datetime, timezone
        self.last_scraped_at = datetime.now(timezone.utc)
        self.scraped_count += 1

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

            # Broadcast real-time scan card to Superadmins in test mode
            from src.bot.alert_bot import broadcast_debug_scan
            asyncio.create_task(broadcast_debug_scan(
                chat_title=chat_title,
                user_id=user_id,
                first_name=first_name,
                username=username,
                text=text,
                total_messages=len(messages)
            ))

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
        """Attempts to auto-join target chat/channel using Pyrogram Userbot or Zero-Auth Public Scraper."""
        clean_target = username_or_link.strip().replace("https://t.me/s/", "").replace("https://t.me/", "@").replace("http://t.me/", "@")
        if not clean_target.startswith("@") and not clean_target.startswith("+"):
            clean_target = f"@{clean_target}"

        # 1. Attempt Pyrogram Userbot if active
        if self.app and self._is_running:
            try:
                chat = await self.app.join_chat(clean_target)
                title = getattr(chat, "title", None) or getattr(chat, "username", None) or username_or_link
                logger.info(f"✅ Userbot successfully joined target chat: {title} ({clean_target})")
                return True, title, None
            except Exception as e:
                logger.warning(f"Pyrogram Userbot join error for {clean_target}: {e}. Trying Public Web Scraper fallback...")

        # 2. Fallback to Zero-Auth Public Scraper
        try:
            from src.ingestion.public_scraper import PublicTelegramScraper
            scraper = PublicTelegramScraper()
            posts = await scraper.fetch_latest_messages(clean_target)
            if posts:
                title = posts[0].get("chat_title") or clean_target
                logger.info(f"✅ Zero-Auth Public Scraper verified channel {title} ({clean_target})")
                return True, title, None

            # Check HTTP preview for channel title even if no posts returned
            clean_user = scraper._clean_username(clean_target)
            if clean_user:
                url = f"https://t.me/s/{clean_user}"
                import httpx, re
                async with httpx.AsyncClient(headers=scraper.headers, follow_redirects=True, timeout=10.0) as client:
                    res = await client.get(url)
                    if res.status_code == 200:
                        title_match = re.search(r'<div class="tgme_header_title"[^>]*>\s*<span[^>]*>(.*?)</span>', res.text, re.DOTALL)
                        title = scraper._strip_html(title_match.group(1)) if title_match else f"@{clean_user}"
                        logger.info(f"✅ Zero-Auth Public Scraper found header for {title} ({clean_target})")
                        return True, title, None
        except Exception as e:
            logger.error(f"Error in public scraper fallback for {clean_target}: {e}")

        return False, None, "Не удалось подключиться: закрытый чат или неверная ссылка."

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

    async def _scrape_single_channel_task(self, channel, scraper, client, semaphore, processed_posts):
        """Scrapes a single channel asynchronously with concurrency semaphore controls."""
        async with semaphore:
            target = channel.username_or_link
            posts = await scraper.fetch_latest_messages(target, client=client)

            new_max_id = channel.last_scraped_msg_id or 0
            new_posts_found = 0

            for post in posts:
                msg_id = post["message_id"]
                post_key = f"{target}:{msg_id}"

                if (channel.last_scraped_msg_id and msg_id <= channel.last_scraped_msg_id) or post_key in processed_posts:
                    continue

                processed_posts.add(post_key)
                if msg_id > new_max_id:
                    new_max_id = msg_id
                new_posts_found += 1

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

            title = posts[0]["chat_title"] if posts else None
            return channel.id, new_posts_found, new_max_id, title

    async def run_public_scraper_loop(self):
        """High-concurrency async task for scraping 300+ Telegram channels with HTTP connection pooling."""
        import httpx
        from src.db.models import MonitoredChannel
        from src.ingestion.public_scraper import PublicTelegramScraper

        scraper = PublicTelegramScraper()
        logger.info("📡 Starting High-Concurrency Public Telegram Scraper Loop (Optimized for 300+ channels)...")

        processed_posts = set()
        CONCURRENCY_LIMIT = 15  # Up to 15 concurrent channels in parallel
        sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

        limits = httpx.Limits(max_keepalive_connections=30, max_connections=50)

        async with httpx.AsyncClient(headers=scraper.headers, follow_redirects=True, timeout=10.0, limits=limits) as client:
            while self._is_running:
                try:
                    from datetime import datetime, timezone
                    self.last_check_at = datetime.now(timezone.utc)
                    async with AsyncSessionLocal() as session:
                        res = await session.execute(select(MonitoredChannel))
                        channels = list(res.scalars().all())

                    if channels:
                        tasks = [
                            self._scrape_single_channel_task(ch, scraper, client, sem, processed_posts)
                            for ch in channels
                        ]
                        results = await asyncio.gather(*tasks, return_exceptions=True)

                        # Batch update DB transaction to prevent SQLite lock contention
                        async with AsyncSessionLocal() as session:
                            for res_item in results:
                                if isinstance(res_item, tuple):
                                    ch_id, new_found, max_id, ch_title = res_item
                                    stmt = select(MonitoredChannel).where(MonitoredChannel.id == ch_id)
                                    ch_db = (await session.execute(stmt)).scalar_one_or_none()
                                    if ch_db:
                                        if max_id > (ch_db.last_scraped_msg_id or 0):
                                            ch_db.last_scraped_msg_id = max_id
                                        ch_db.status = "JOINED"
                                        if ch_title:
                                            ch_db.title = ch_title
                                        ch_db.error_message = None
                            await session.commit()

                except Exception as e:
                    logger.error(f"Error in high-concurrency public scraper loop: {e}")

                await asyncio.sleep(15)

    async def restart_scraper_loop(self):
        logger.info("🔄 Restarting Telegram Public Scraper Loop...")
        if self.public_scraper_task and not self.public_scraper_task.done():
            self.public_scraper_task.cancel()
            try:
                await self.public_scraper_task
            except Exception:
                pass
        self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())
        logger.info("✅ Telegram Public Scraper Loop restarted successfully.")

    async def run_watchdog_loop(self):
        from datetime import datetime, timezone
        logger.info("🛡️ Starting Scanner Health Watchdog Loop...")
        STALE_THRESHOLD_SECONDS = 300  # 5 minutes without check loop execution

        while self._is_running:
            await asyncio.sleep(60)

            if not self._is_running:
                break

            # Check if scraper loop task crashed unexpectedly
            if self.public_scraper_task and self.public_scraper_task.done():
                exc = self.public_scraper_task.exception()
                logger.error(f"⚠️ Scanner Watchdog: Public scraper task died unexpectedly: {exc}")
                from src.bot.alert_bot import notify_superadmins_system_alert
                await notify_superadmins_system_alert(
                    f"⚠️ <b>ВНИМАНИЕ: СБОЙ СКАНИРОВАНИЯ!</b>\n\n"
                    f"Фоновая задача сборщика сообщений завершилась с ошибкой: <code>{exc}</code>.\n"
                    f"🔄 <i>Выполняется автоматический перезапуск сборщика...</i>"
                )
                await self.restart_scraper_loop()
                continue

            check_time = self.last_check_at or self.last_scraped_at
            if not check_time:
                continue

            idle_time = (datetime.now(timezone.utc) - check_time).total_seconds()
            if idle_time > STALE_THRESHOLD_SECONDS:
                logger.warning(f"⚠️ Scanner Watchdog Alert: Loop idle for {int(idle_time)}s. Restarting scraper...")
                from src.bot.alert_bot import notify_superadmins_system_alert
                await notify_superadmins_system_alert(
                    f"⚠️ <b>ВНИМАНИЕ: ЗАВИСАНИЕ СКАНИРОВАНИЯ!</b>\n\n"
                    f"Проверка чатов приостановилась на <b>{int(idle_time // 60)} мин ({int(idle_time)} сек)</b>.\n"
                    f"🔄 <i>Выполняется автоматический перезапуск сборщика сообщений...</i>"
                )
                await self.restart_scraper_loop()

    async def run_log_retention_cleanup(self):
        """Periodically prunes old activity logs to prevent SQLite DB bloating under stress."""
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import delete
        RETENTION_DAYS = 14
        logger.info(f"🧹 Starting Log Retention Cleanup Loop (Threshold: {RETENTION_DAYS} days)...")

        while self._is_running:
            try:
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
                async with AsyncSessionLocal() as session:
                    stmt = delete(UserActivityLog).where(UserActivityLog.timestamp < cutoff_date)
                    res = await session.execute(stmt)
                    await session.commit()
                    if res.rowcount > 0:
                        logger.info(f"🧹 Log Retention Cleanup: Pruned {res.rowcount} activity logs older than {RETENTION_DAYS} days.")
            except Exception as e:
                logger.error(f"Error in log retention cleanup loop: {e}")

            # Sleep for 6 hours
            await asyncio.sleep(6 * 3600)

    async def start(self):
        self._is_running = True
        if self.app:
            logger.info("Starting Pyrogram Userbot Listener...")
            await self.app.start()
            await self.sync_monitored_channels()

        self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())
        self.watchdog_task = asyncio.create_task(self.run_watchdog_loop())
        self.retention_task = asyncio.create_task(self.run_log_retention_cleanup())

    async def stop(self):
        self._is_running = False
        if self.public_scraper_task:
            self.public_scraper_task.cancel()
        if self.watchdog_task:
            self.watchdog_task.cancel()
        if self.retention_task:
            self.retention_task.cancel()
        if self.app:
            await self.app.stop()

