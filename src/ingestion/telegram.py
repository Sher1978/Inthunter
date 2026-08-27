import asyncio
import logging
from typing import Optional, List, Dict
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
        self.banned_spammer_user_ids = set()

    async def refresh_banned_users(self):
        """Loads globally blacklisted spammer user IDs into memory for 0ms filtering."""
        try:
            from src.db.models import BlacklistedUser
            async with AsyncSessionLocal() as session:
                res = await session.execute(select(BlacklistedUser.user_id))
                self.banned_spammer_user_ids = set(res.scalars().all())
                logger.info(f"🛡️ Loaded {len(self.banned_spammer_user_ids)} blacklisted spammer user IDs into Gatekeeper memory.")
        except Exception as e:
            logger.debug(f"Notice loading blacklisted users: {e}")

    async def process_incoming_message(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        chat_id: int,
        chat_title: str,
        message_id: int,
        text: str,
        db_session: Optional[AsyncSession] = None
    ):
        if not user_id or not text.strip():
            return

        # Upgrade 4: Global Spammer Blacklist Filter
        if user_id and user_id in self.banned_spammer_user_ids:
            logger.debug(f"🚫 Gatekeeper: Dropped message from globally blacklisted spammer user_id={user_id}")
            return

        # Upgrade 2: Gatekeeper Fast Local Pre-Filter
        # Drop long commercial ad posts (>400 chars with links/hashtags)
        txt_low = text.lower()
        if len(text) > 400 and ("http://" in txt_low or "https://" in txt_low or "t.me/" in txt_low or text.count("#") >= 4):
            logger.debug(f"🚫 Gatekeeper: Dropped long commercial ad post ({len(text)} chars) from user_id={user_id}")
            return

        from datetime import datetime, timezone
        self.last_scraped_at = datetime.now(timezone.utc)
        self.scraped_count += 1

        logger.info(f"Received message from user_id={user_id} in [{chat_title}]: \"{text[:40]}...\"")

        async def _do_process(session: AsyncSession):
            # 0. Deduplication check: skip if message already ingested into DB
            dup_stmt = select(UserActivityLog).where(
                UserActivityLog.chat_id == chat_id,
                UserActivityLog.message_id == message_id
            )
            existing_msg = (await session.execute(dup_stmt)).scalar_one_or_none()
            if existing_msg:
                return

            dup_text_stmt = select(UserActivityLog).where(
                UserActivityLog.user_id == user_id,
                UserActivityLog.message_text == text
            )
            existing_text = (await session.execute(dup_text_stmt)).scalar_one_or_none()
            if existing_text:
                return

            # Extract Telegram channel links for automatic group discovery
            try:
                import re
                links = re.findall(r'(?:https?://)?t\.me/([a-zA-Z0-9_]{5,32})|@([a-zA-Z0-9_]{5,32})', text)
                discovered_users = set()
                for m in links:
                    u = (m[0] or m[1]).strip()
                    if u and not u.endswith('_bot') and u.lower() not in ['telegram', 'joinchat', 'share', 'contact']:
                        discovered_users.add(f"@{u}")
                
                if discovered_users:
                    from src.db.models import MonitoredChannel, ChannelCandidate, BlacklistedChat
                    clean_parent_title = chat_title.replace("Обнаружен в ", "").strip()
                    blk_parent = (await session.execute(select(BlacklistedChat).where(
                        (BlacklistedChat.chat_username.ilike(f"%{clean_parent_title}%")) |
                        (BlacklistedChat.reason.ilike(f"%{clean_parent_title}%"))
                    ))).scalar_one_or_none()

                    if blk_parent:
                        logger.info(f"🚫 Parent chat [{chat_title}] is blacklisted. Skipping candidate extraction.")
                    else:
                        for cand_user in discovered_users:
                            blk_cand = (await session.execute(select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(cand_user)))).scalar_one_or_none()
                            if blk_cand:
                                continue
                            ch_exists = (await session.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == cand_user))).scalar_one_or_none()
                            if not ch_exists:
                                cand_exists = (await session.execute(select(ChannelCandidate).where(ChannelCandidate.username_or_link == cand_user))).scalar_one_or_none()
                                if not cand_exists:
                                    loc = "dubai" if ("dubai" in chat_title.lower() or "дубай" in chat_title.lower() or "оаэ" in chat_title.lower()) else "nhatrang"
                                    session.add(ChannelCandidate(
                                        username_or_link=cand_user,
                                        title=f"Обнаружен в {chat_title}",
                                        source="RECURSIVE_MENTION",
                                        location_code=loc,
                                        status="DISCOVERED"
                                    ))
                                    logger.info(f"💡 Auto-discovered Telegram candidate: {cand_user} from chat [{chat_title}]")
            except Exception as cand_err:
                logger.debug(f"Candidate extraction notice: {cand_err}")

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

            # 3. Check Intent Gatekeeper & trigger threshold for AI scoring
            user_msg_count_stmt = select(UserActivityLog).where(UserActivityLog.user_id == user_id)
            count_res = await session.execute(user_msg_count_stmt)
            messages = list(count_res.scalars().all())

            INTENT_GATEKEEPER_TRIGGERS = (
                "ищу", "нужен", "нужна", "нужны", "посоветуйте", "кто сдает", "кто сдаёт",
                "кто делает", "сколько стоит", "подскажите", "купим", "требуется", "интересует",
                "ищем", "где найти", "поможет", "поможет с", "консультация", "заказать", "аренда",
                "сниму", "подберите", "порекомендуйте", "почем", "кто может", "где можно", "кто знает",
                "риелтор", "трансфер", "гид", "меняет", "обмен", "usdt", "дирхам", "рупи", "виза",
                "need", "looking for", "rent", "buy", "exchange", "hiring", "?"
            )
            has_intent = any(kw in txt_low for kw in INTENT_GATEKEEPER_TRIGGERS)

            if has_intent and len(messages) >= settings.MIN_MESSAGES_FOR_SCORING:
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

        if db_session:
            await _do_process(db_session)
        else:
            async with AsyncSessionLocal() as session:
                await _do_process(session)

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
        from datetime import datetime, timezone
        async with semaphore:
            async with AsyncSessionLocal() as session:
                target = channel.username_or_link
                platform = getattr(channel, "platform", "telegram") or "telegram"

                from src.ingestion.vk_ok_scrapers import VKPublicScraper, OKPublicScraper, MAXPublicScraper
                if platform == "vk":
                    posts = await VKPublicScraper.fetch_latest_messages(target)
                elif platform == "ok":
                    posts = await OKPublicScraper.fetch_latest_messages(target)
                elif platform == "max":
                    posts = await MAXPublicScraper.fetch_latest_messages(target)
                else:
                    posts = await scraper.fetch_latest_messages(target, client=client)

                self.last_check_at = datetime.now(timezone.utc)

                new_max_id = channel.last_scraped_msg_id or 0
                new_posts_found = 0

                if posts is None:
                    # Channel 404 or does not exist
                    try:
                        from src.db.models import CollectorLog
                        c_log = CollectorLog(
                            chat_title=channel.title or target,
                            username_or_link=target,
                            total_fetched_count=0,
                            new_messages_count=0,
                            new_leads_count=0,
                            status="FAILED",
                            details=f"❌ Группа не найдена на {platform.upper()}"
                        )
                        session.add(c_log)
                        await session.commit()
                    except Exception:
                        pass
                    return channel.id, 0, channel.last_scraped_msg_id or 0, channel.title or target, "FAILED", f"❌ Группа не найдена на {platform.upper()}"

                posts_list = posts or []
                total_fetched = len(posts_list)

                for post in posts_list:
                    msg_id = post.get("message_id", 0)
                    post_text = post.get("message_text") or post.get("text") or ""
                    post_key = f"{platform}:{target}:{msg_id}:{hash(post_text[:50])}"

                    if post_key in processed_posts:
                        continue

                    processed_posts.add(post_key)
                    if msg_id > new_max_id:
                        new_max_id = msg_id
                    new_posts_found += 1

                    import zlib
                    det_chat_id = (zlib.crc32(f"{platform}:{target}".encode("utf-8")) & 0x7FFFFFFF)
                    await self.process_incoming_message(
                        user_id=post.get("user_id") or f"{platform}_user",
                        username=post.get("username"),
                        first_name=post.get("first_name"),
                        last_name=post.get("last_name"),
                        chat_id=det_chat_id,
                        chat_title=post.get("chat_title") or channel.title or target,
                        message_id=msg_id or 1,
                        text=post_text,
                        db_session=session
                    )
                    await asyncio.sleep(0.05)

                title = (posts_list[0]["chat_title"] if posts_list else None) or channel.title or channel.username_or_link
                
                # Save CollectorLog telemetry entry (including 0-message polling attempts)
                try:
                    from src.db.models import CollectorLog
                    from src.services.process_logger import process_logger
                    engine_label = "⚡ Pyrogram MTProto Userbot" if (self.app and getattr(self.app, "is_connected", False)) else "📡 Zero-Auth Web Scraper (25s)"
                    detail_msg = f"{engine_label} — Проверено: {total_fetched} постов, новых: {new_posts_found}" if new_posts_found > 0 else f"{engine_label} — Опрос выполнен (0 новых сообщений)"

                    # Real-time live process terminal ticker emit
                    process_logger.add_log(
                        category="USERBOT" if "Userbot" in engine_label else "SCRAPER",
                        level="success" if new_posts_found > 0 else "info",
                        title=f"📡 Опрос чата {title} ({target}) — {new_posts_found} новых сообщений",
                        details=detail_msg
                    )

                    c_log = CollectorLog(
                        chat_title=title,
                        username_or_link=target,
                        total_fetched_count=total_fetched,
                        new_messages_count=new_posts_found,
                        new_leads_count=0,
                        status="NEW" if new_posts_found > 0 else "OK",
                        details=detail_msg
                    )
                    session.add(c_log)
                    await session.commit()
                except Exception as c_err:
                    logger.warning(f"CollectorLog save notice: {c_err}")

                return channel.id, new_posts_found, new_max_id, title, "JOINED", None

    async def force_rescan_past_hour(self):
        """Forces a priority out-of-order re-scrape and AI re-evaluation of all monitored channels asynchronously."""
        logger.info("⚡ Executing manual 1-hour forced rescan and AI re-evaluation in background...")
        from datetime import datetime, timezone, timedelta
        from src.db.models import MonitoredChannel, CollectorLog, UserActivityLog
        from src.ai.scorer import evaluate_user_timeline

        async def _do_async_rescan():
            try:
                now_utc = datetime.now(timezone.utc)
                cutoff_1h = now_utc - timedelta(hours=1)
                self.last_check_at = now_utc

                # 1. Reset channels last_scraped pointers
                async with AsyncSessionLocal() as session:
                    res = await session.execute(select(MonitoredChannel))
                    channels = list(res.scalars().all())
                    for ch in channels:
                        ch.last_scraped_msg_id = 0
                    await session.commit()

                    c_log = CollectorLog(
                        chat_title="⚡ Ручной перескан за 1 час",
                        username_or_link="system:rescan_hour",
                        total_fetched_count=len(channels),
                        new_messages_count=0,
                        new_leads_count=0,
                        status="RESCAN",
                        details=f"🚀 Запущен принудительный ручной перескан за 1 час для {len(channels)} каналов"
                    )
                    session.add(c_log)
                    await session.commit()

                # 2. Re-evaluate user timelines in background
                eval_count = 0
                async with AsyncSessionLocal() as session:
                    u_stmt = select(UserActivityLog.user_id).where(UserActivityLog.timestamp >= cutoff_1h).distinct()
                    active_user_ids = list((await session.execute(u_stmt)).scalars().all())

                    for u_id in active_user_ids[:50]:
                        try:
                            msg_stmt = select(UserActivityLog).where(UserActivityLog.user_id == u_id).order_by(UserActivityLog.timestamp.desc()).limit(10)
                            user_msgs = list((await session.execute(msg_stmt)).scalars().all())
                            if user_msgs:
                                eval_res = await evaluate_user_timeline(u_id, session, user_msgs)
                                if eval_res:
                                    eval_count += 1
                        except Exception as eval_err:
                            logger.warning(f"Error re-evaluating timeline for user {u_id}: {eval_err}")

                logger.info(f"✅ AI Re-evaluation finished for {eval_count} user timelines from the past hour.")

                # 3. Trigger immediate out-of-order priority scraper loop pass
                await self.restart_scraper_loop()
            except Exception as rescan_err:
                logger.error(f"Error in background rescan task: {rescan_err}")

        asyncio.create_task(_do_async_rescan())
        return 50

    async def run_public_scraper_loop(self):
        """High-concurrency async task for scraping Telegram channels with smooth rate pacing."""
        import httpx
        from src.db.models import MonitoredChannel
        from src.ingestion.public_scraper import PublicTelegramScraper

        scraper = PublicTelegramScraper()
        logger.info("📡 Starting Accelerated Public Telegram Scraper Loop (25 concurrent workers, 5s loop interval)...")

        processed_posts = set()
        CONCURRENCY_LIMIT = 5  # Throttled concurrency pool (5 parallel channel fetches max) to protect DB pool
        sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

        limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)

        async with httpx.AsyncClient(headers=scraper.headers, follow_redirects=True, timeout=12.0, limits=limits) as client:
            while self._is_running:
                try:
                    from datetime import datetime, timezone
                    self.last_check_at = datetime.now(timezone.utc)

                    # Periodically prune processed_posts set memory & CollectorLog older than 1 hour
                    if len(processed_posts) > 10000:
                        processed_posts.clear()

                    from datetime import timedelta
                    from sqlalchemy import delete
                    from src.db.models import CollectorLog
                    cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=1)
                    async with AsyncSessionLocal() as session:
                        await session.execute(delete(CollectorLog).where(CollectorLog.created_at < cutoff_1h))
                        await session.commit()

                    async with AsyncSessionLocal() as session:
                        # Order channels so newly added or least recently scraped channels run first!
                        res = await session.execute(
                            select(MonitoredChannel).order_by(
                                MonitoredChannel.last_scraped_msg_id.asc().nullsfirst(),
                                MonitoredChannel.created_at.desc()
                            )
                        )
                        channels = list(res.scalars().all())

                    if channels:
                        tasks = [
                            self._scrape_single_channel_task(ch, scraper, client, sem, processed_posts)
                            for ch in channels
                        ]
                        try:
                            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=180.0)
                        except asyncio.TimeoutError:
                            logger.warning("⏱️ Scraper loop pass reached 180s timeout limit. Preserving completed channel results...")
                            results = [t.result() for t in tasks if t.done() and not t.cancelled() and not t.exception()]

                        # Batch update DB transaction for channel statuses and last scraped message IDs
                        async with AsyncSessionLocal() as session:
                            for res_item in results:
                                if isinstance(res_item, tuple) and len(res_item) >= 6:
                                    ch_id, new_found, max_id, ch_title, status_val, err_msg = res_item
                                    stmt = select(MonitoredChannel).where(MonitoredChannel.id == ch_id)
                                    ch_db = (await session.execute(stmt)).scalar_one_or_none()
                                    if ch_db:
                                        if max_id > (ch_db.last_scraped_msg_id or 0):
                                            ch_db.last_scraped_msg_id = max_id
                                        ch_db.last_scraped_at = datetime.now(timezone.utc)
                                        ch_db.status = status_val
                                        if ch_title:
                                            ch_db.title = ch_title
                                        ch_db.error_message = err_msg
                            await session.commit()

                except Exception as e:
                    logger.error(f"Error in public scraper loop: {e}")

                await asyncio.sleep(3)  # Accelerated 3-second interval for real-time lead ingestion

    async def restart_scraper_loop(self):
        logger.info("🔄 Restarting Telegram Public Scraper Loop & Userbot Sync...")
        from datetime import datetime, timezone
        self.last_check_at = datetime.now(timezone.utc)

        if self.public_scraper_task and not self.public_scraper_task.done():
            self.public_scraper_task.cancel()

        self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())

        if self.app:
            try:
                await self.sync_monitored_channels()
            except Exception as e:
                logger.warning(f"Userbot channel sync notice during restart: {e}")

        logger.info("✅ Telegram Public Scraper Loop & Userbot restarted successfully.")

    async def process_and_score_posts_now(self, channel_obj, posts: List[Dict]):
        """
        Immediately ingests up to 20 recent messages from a newly added channel and runs AI evaluation on them.
        """
        if not posts:
            return
        
        import zlib
        target = getattr(channel_obj, "username_or_link", "") or ""
        det_chat_id = (zlib.crc32(target.encode("utf-8")) & 0x7FFFFFFF)
        chat_title = (posts[0].get("chat_title") if posts else None) or getattr(channel_obj, "title", None) or target

        logger.info(f"⚡ Instant AI Ingestion: processing {len(posts)} recent messages from newly added channel {chat_title} ({target})...")

        max_msg_id = 0
        for p in posts:
            msg_id = p.get("message_id", 0)
            if msg_id > max_msg_id:
                max_msg_id = msg_id

            await self.process_incoming_message(
                user_id=p["user_id"],
                username=p.get("username", ""),
                first_name=p.get("first_name", ""),
                last_name=p.get("last_name", ""),
                chat_id=det_chat_id,
                chat_title=chat_title,
                message_id=msg_id,
                text=p.get("text", "")
            )
            await asyncio.sleep(0.1)

        # Update last_scraped_msg_id in DB
        from datetime import datetime, timezone
        async with AsyncSessionLocal() as session:
            stmt = select(MonitoredChannel).where(MonitoredChannel.username_or_link == target)
            ch = (await session.execute(stmt)).scalar_one_or_none()
            if ch:
                if max_msg_id > (ch.last_scraped_msg_id or 0):
                    ch.last_scraped_msg_id = max_msg_id
                ch.last_scraped_at = datetime.now(timezone.utc)
                ch.status = "JOINED"
                ch.title = chat_title
                await session.commit()

    async def scrape_channel_now(self, channel_id_or_target: str):
        """Executes an immediate, out-of-order priority scrape for a specific newly added channel."""
        logger.info(f"⚡ Executing priority out-of-order scrape for: {channel_id_or_target}")
        try:
            from src.ingestion.public_scraper import PublicTelegramScraper
            from src.db.models import MonitoredChannel
            import httpx

            scraper = PublicTelegramScraper()
            async with AsyncSessionLocal() as session:
                stmt = select(MonitoredChannel).where(
                    (MonitoredChannel.id == channel_id_or_target) |
                    (MonitoredChannel.username_or_link == channel_id_or_target)
                )
                ch = (await session.execute(stmt)).scalar_one_or_none()

                if ch:
                    async with httpx.AsyncClient(headers=scraper.headers, follow_redirects=True, timeout=12.0) as client:
                        processed_posts = set()
                        sem = asyncio.Semaphore(1)
                        res_item = await self._scrape_single_channel_task(ch, scraper, client, sem, processed_posts)
                        if isinstance(res_item, tuple) and len(res_item) >= 6:
                            ch_id, new_found, max_id, ch_title, status_val, err_msg = res_item
                            ch.status = status_val
                            if max_id > (ch.last_scraped_msg_id or 0):
                                ch.last_scraped_msg_id = max_id
                            if ch_title:
                                ch.title = ch_title
                            ch.error_message = err_msg
                            await session.commit()
                        logger.info(f"✅ Priority out-of-order scan finished for: {ch.title or ch.username_or_link}")
        except Exception as e:
            logger.error(f"Error in priority channel scrape: {e}")

    async def run_watchdog_loop(self):
        from datetime import datetime, timezone
        logger.info("🛡️ Starting Scanner Health Watchdog Loop (Stale threshold: 180s)...")
        STALE_THRESHOLD_SECONDS = 180  # 180 seconds (3 minutes) stall threshold

        while self._is_running:
            await asyncio.sleep(15)  # Check every 15 seconds

            if not self._is_running:
                break

            # Emit Watchdog heartbeat event to live process terminal
            try:
                from src.services.process_logger import process_logger
                idle_s = (datetime.now(timezone.utc) - self.last_scraped_at).total_seconds() if self.last_scraped_at else 0
                process_logger.add_log(
                    category="WATCHDOG",
                    level="warning" if idle_s > 60 else "info",
                    title=f"🛡️ Watchdog: Пинг активности сборщика — Система активна ({int(idle_s)}с простоя)",
                    details=f"Режим: {'⚡ Userbot MTProto' if (self.app and getattr(self.app, 'is_connected', False)) else '📡 Zero-Auth Web Scraper (25s)'}"
                )
            except Exception:
                pass

            # 1. Check and keep Pyrogram Userbot MTProto connection active 24/7
            if self.app:
                try:
                    if not getattr(self.app, "is_connected", False):
                        logger.warning("⚠️ Pyrogram Userbot connection dropped! Reconnecting automatically...")
                        await self.app.connect()
                        logger.info("✅ Pyrogram Userbot reconnected successfully.")
                    else:
                        # Lightweight get_me ping to maintain active socket connection
                        await self.app.get_me()
                except Exception as userbot_err:
                    err_msg = str(userbot_err)
                    if "AUTH_KEY_DUPLICATED" in err_msg or "406" in err_msg:
                        logger.warning(
                            f"⚠️ Pyrogram Userbot Auth Key Duplicated ({err_msg}). "
                            "Disabling Userbot listener and operating 100% in Zero-Auth Public Scraper mode."
                        )
                        self.app = None
                    else:
                        logger.error(f"⚠️ Pyrogram KeepAlive Error: {userbot_err}. Attempting full restart...")
                        try:
                            await self.app.restart()
                            await self.sync_monitored_channels()
                            logger.info("✅ Pyrogram Userbot restarted & resynced monitored channels.")
                        except Exception as re_err:
                            logger.error(f"❌ Failed to restart Pyrogram client: {re_err}")
                            if "AUTH_KEY_DUPLICATED" in str(re_err) or "406" in str(re_err):
                                self.app = None

            # 2. Check if scraper loop task crashed unexpectedly
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
                    f"⚠️ <b>ВНИМАНИЕ: СБОЙ / ЗАВИСАНИЕ СКАНИРОВАНИЯ!</b>\n\n"
                    f"Опрос каналов остановился на <b>{int(idle_time)} сек</b> (порог: 180с).\n"
                    f"🌐 <b>Статус:</b> Сборщик не отвечает.\n\n"
                    f"🔄 <i>Запущен автоматический экстренный перезапуск сканера...</i>"
                )
                await self.restart_scraper_loop()

    async def run_log_retention_cleanup(self):
        """Periodically prunes old activity logs and enforces strict DB size controls."""
        logger.info("🛡️ Starting Database Guard & Retention Enforcement Loop (Hourly check)...")
        from src.services.db_guard import db_guard

        # Delay initial pass by 60s to prevent startup DB table write locks
        await asyncio.sleep(60)

        while self._is_running:
            try:
                await db_guard.run_enforcement_pass()
            except Exception as e:
                logger.error(f"Error in DB Guard enforcement loop: {e}")

            # Run hourly check
            await asyncio.sleep(3600)

    async def run_auto_discovery_loop(self):
        """Automated background worker for discovering new Telegram groups via MTProto search & Web catalogs."""
        logger.info("🔍 Starting Automated Telegram Group Discovery Loop (MTProto & Directory Search)...")
        keywords = ["Дубай аренда", "Дубай жилье", "Dubai real estate", "Дубай usdt", "Дубай авто", "Дубай работа", "Бали аренда", "Бали виллы"]
        
        while self._is_running:
            try:
                await asyncio.sleep(120)  # Wait 2 minutes after startup before initial discovery pass
                
                # Check Pyrogram MTProto global search if Client active
                if self.app and getattr(self.app, "is_connected", False):
                    for kw in keywords:
                        try:
                            results = await self.app.search_public_chats(kw)
                            async with AsyncSessionLocal() as session:
                                from src.db.models import MonitoredChannel, ChannelCandidate
                                for chat_item in results[:10]:
                                    if getattr(chat_item, "username", None):
                                        uname = f"@{chat_item.username}"
                                        ch_db = (await session.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == uname))).scalar_one_or_none()
                                        if not ch_db:
                                            cand_db = (await session.execute(select(ChannelCandidate).where(ChannelCandidate.username_or_link == uname))).scalar_one_or_none()
                                            if not cand_db:
                                                loc = "bali" if ("бали" in kw.lower() or "bali" in kw.lower()) else "dubai"
                                                session.add(ChannelCandidate(
                                                    username_or_link=uname,
                                                    title=getattr(chat_item, "title", uname),
                                                    source="GLOBAL_SEARCH",
                                                    location_code=loc,
                                                    status="DISCOVERED",
                                                    member_count=getattr(chat_item, "members_count", 0) or 0
                                                ))
                                await session.commit()
                        except Exception as s_err:
                            logger.debug(f"MTProto search notice for '{kw}': {s_err}")
                        await asyncio.sleep(10)
            except Exception as d_err:
                logger.error(f"Error in Telegram auto discovery loop: {d_err}")

            await asyncio.sleep(3600)  # Run discovery cycle once per hour

    async def start(self):
        self._is_running = True
        await self.refresh_banned_users()
        if self.app:
            try:
                logger.info("Starting Pyrogram Userbot Listener...")
                await self.app.start()
                await self.sync_monitored_channels()
            except Exception as e:
                err_msg = str(e)
                logger.warning(f"⚠️ Pyrogram Userbot start notice: {err_msg}. Fallback to Zero-Auth Public Scraper.")
                if "AUTH_KEY_DUPLICATED" in err_msg or "406" in err_msg:
                    self.app = None

        self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())
        self.watchdog_task = asyncio.create_task(self.run_watchdog_loop())
        self.retention_task = asyncio.create_task(self.run_log_retention_cleanup())
        self.discovery_task = asyncio.create_task(self.run_auto_discovery_loop())

    async def stop(self):
        self._is_running = False
        if self.public_scraper_task:
            self.public_scraper_task.cancel()
        if self.watchdog_task:
            self.watchdog_task.cancel()
        if self.retention_task:
            self.retention_task.cancel()
        if hasattr(self, 'discovery_task') and self.discovery_task:
            self.discovery_task.cancel()
        if self.app:
            await self.app.stop()

