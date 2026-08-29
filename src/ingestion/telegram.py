import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional, List, Dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.session import AsyncSessionLocal
from src.db.models import UserProfile, UserActivityLog
from src.ai.scorer import evaluate_user_timeline

logger = logging.getLogger("intent_hunter.ingestion")

# 🛡️ Global Dead Man's Switch State Tracking
LAST_MESSAGE_TIME: Optional[datetime] = datetime.now(timezone.utc)

def update_last_message_time():
    """Updates global timestamp whenever ANY message is captured by listener/scrapers."""
    global LAST_MESSAGE_TIME
    LAST_MESSAGE_TIME = datetime.now(timezone.utc)

def get_last_message_time() -> Optional[datetime]:
    """Returns timestamp of last captured message across all channels."""
    return LAST_MESSAGE_TIME


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
        self.dead_man_switch_task = None
        self.retention_task = None
        self.banned_spammer_user_ids = set()
        
        # Userbot status & telemetry tracking
        self.userbot_status = "NOT_CONFIGURED"
        self.userbot_user_handle = None
        self.userbot_last_ping = None
        self.userbot_flood_wait_seconds = 0
        self.userbot_flood_until = None
        self.group_chat_302_count = 0
        
        # Smart Anti-Ban Rate Limiter state (Adaptive 12-15 min interval, 20 joins/day limit)
        self.last_mtproto_join_at: Optional[datetime] = None
        self.daily_join_count: int = 0
        self.daily_join_reset_date: Optional[str] = None
        self.max_daily_joins: int = 20
        self.min_join_interval_seconds: int = 720  # 12 minutes minimum interval between MTProto joins

    def _is_night_mode(self) -> bool:
        """Returns True if current local time is within human night sleeping hours (01:00 - 07:00)."""
        now = datetime.now()
        return 1 <= now.hour < 7

    def _can_perform_mtproto_join(self) -> tuple:
        """
        Checks if MTProto join_chat can be executed safely under Anti-Ban rate quotas.
        Returns (can_join: bool, reason: str).
        """
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        # 1. Reset daily counter at midnight UTC
        if self.daily_join_reset_date != today_str:
            self.daily_join_reset_date = today_str
            self.daily_join_count = 0

        # 2. Check FloodWait active cooldown
        if self.userbot_flood_until and now_utc < self.userbot_flood_until:
            rem_sec = int((self.userbot_flood_until - now_utc).total_seconds())
            return False, f"FloodWait active ({rem_sec}s remaining)"

        # 3. Check Night Mode
        if self._is_night_mode():
            return False, "Night Mode active (01:00-07:00, human sleeping emulation)"

        # 4. Check Daily Join Quota Limit (max 20/day)
        if self.daily_join_count >= self.max_daily_joins:
            return False, f"Daily Anti-Ban limit reached ({self.daily_join_count}/{self.max_daily_joins} today)"

        # 5. Check Minimum Interval (12-15 minutes spacing)
        if self.last_mtproto_join_at:
            elapsed = (now_utc - self.last_mtproto_join_at).total_seconds()
            if elapsed < self.min_join_interval_seconds:
                wait_m = round((self.min_join_interval_seconds - elapsed) / 60, 1)
                return False, f"Anti-Ban pacing active (next join in {wait_m} min)"

        return True, "OK"

    async def setup(self):
        """Initializes Pyrogram Userbot MTProto client if session string is configured."""
        session_str = (settings.USERBOT_SESSION_STRING or "").strip()
        if not session_str:
            logger.info("ℹ️ USERBOT_SESSION_STRING is empty. Operating in Zero-Auth Public Scraper mode.")
            self.userbot_status = "NOT_CONFIGURED"
            return

        try:
            from pyrogram import Client, filters
            from pyrogram.types import Message

            logger.info("⚡ Setting up Pyrogram Userbot MTProto Client...")
            self.app = Client(
                name="intent_hunter_userbot",
                api_id=settings.TELEGRAM_API_ID,
                api_hash=settings.TELEGRAM_API_HASH,
                session_string=session_str,
                in_memory=True
            )

            # Register real-time incoming message handler for group chats and channels
            @self.app.on_message(filters.group | filters.channel)
            async def _on_pyrogram_message(client, message: Message):
                try:
                    if not message or not message.text:
                        return

                    user_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 0)
                    username = message.from_user.username if message.from_user else None
                    first_name = message.from_user.first_name if message.from_user else (message.chat.title if message.chat else None)
                    last_name = message.from_user.last_name if message.from_user else None
                    chat_id = message.chat.id if message.chat else 0
                    chat_title = message.chat.title if message.chat else (username or "Telegram Group")
                    msg_id = message.id

                    await self.process_incoming_message(
                        user_id=user_id,
                        username=username,
                        first_name=first_name,
                        last_name=last_name,
                        chat_id=chat_id,
                        chat_title=chat_title,
                        message_id=msg_id,
                        text=message.text
                    )
                except Exception as msg_err:
                    logger.error(f"Error in Pyrogram live message handler: {msg_err}")

            logger.info("✅ Pyrogram Userbot Client setup complete with live on_message listener.")
        except Exception as e:
            logger.error(f"❌ Error setting up Pyrogram Userbot: {e}")
            self.userbot_status = "ERROR"
            self.app = None

    def get_userbot_status(self) -> Dict:
        """Returns live metrics and connection health state of the Userbot engine."""
        now_utc = datetime.now(timezone.utc)
        in_flood = self.userbot_flood_until is not None and now_utc < self.userbot_flood_until
        remaining_s = int((self.userbot_flood_until - now_utc).total_seconds()) if in_flood else 0
        can_join, join_reason = self._can_perform_mtproto_join()
        
        return {
            "status": "FLOOD_WAIT" if in_flood else self.userbot_status,
            "connected": self.app is not None and getattr(self.app, "is_connected", False),
            "user_handle": self.userbot_user_handle,
            "last_ping_at": self.userbot_last_ping.isoformat() if self.userbot_last_ping else None,
            "flood_wait_seconds": remaining_s if in_flood else self.userbot_flood_wait_seconds,
            "group_chats_302_count": self.group_chat_302_count,
            "scraped_count": self.scraped_count,
            "rate_limiter": {
                "daily_joins_used": self.daily_join_count,
                "daily_joins_limit": self.max_daily_joins,
                "last_mtproto_join_at": self.last_mtproto_join_at.isoformat() if self.last_mtproto_join_at else None,
                "night_mode_active": self._is_night_mode(),
                "can_join_mtproto": can_join,
                "join_status_reason": join_reason
            }
        }

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

        from datetime import datetime, timezone
        update_last_message_time()
        self.last_scraped_at = datetime.now(timezone.utc)
        self.scraped_count += 1

        # Upgrade 4: Global Spammer Blacklist Filter
        if user_id and user_id in self.banned_spammer_user_ids:
            logger.debug(f"🚫 Gatekeeper: Dropped message from globally blacklisted spammer user_id={user_id}")
            return

        # Upgrade 2: Gatekeeper Fast Local Pre-Filter
        # Drop long commercial ad posts (>800 chars with links/hashtags)
        txt_low = text.lower()
        if len(text) > 800 and ("http://" in txt_low or "https://" in txt_low or "t.me/" in txt_low or text.count("#") >= 4):
            logger.debug(f"🚫 Gatekeeper: Dropped long commercial ad post ({len(text)} chars) from user_id={user_id}")
            asyncio.create_task(self._log_dropped_to_ai(user_id, username, first_name, chat_title, text, "Отклонено пре-фильтром (Gatekeeper): Длинный рекламный пост"))
            return

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

            # 2.5 Fetch recent messages for context
            stmt_msgs = select(UserActivityLog).where(
                UserActivityLog.user_id == user_id
            ).order_by(UserActivityLog.created_at.desc()).limit(20)
            res_msgs = await session.execute(stmt_msgs)
            messages = list(res_msgs.scalars().all())
            messages.reverse()

            # 3. Dual-Funnel Router (Splitter) & Vendor Quality Score (VQS) Filter
            from src.ingestion.vendor_quality import evaluate_vendor_quality
            vqs_score, intent_type, vqs_reason = evaluate_vendor_quality(
                message_text=text,
                is_premium=False,
                username=username,
                is_reply=False
            )

            if intent_type == 'TRASH':
                logger.debug(f"🚫 Gatekeeper/Splitter: Dropped TRASH message ({vqs_reason}) from user_id={user_id}")
                asyncio.create_task(self._log_dropped_to_ai(user_id, username, first_name, chat_title, text, f"Отклонено пре-фильтром (VQS): {vqs_reason}"))
                return

            if intent_type == 'LEAD_REQUEST':
                if len(messages) >= settings.MIN_MESSAGES_FOR_SCORING:
                    asyncio.create_task(self._trigger_ai_scoring(user_id, messages))
            elif intent_type == 'VENDOR_OFFER':
                asyncio.create_task(self._log_dropped_to_ai(user_id, username, first_name, chat_title, text, f"Отклонено пре-фильтром (B2B Подрядчик): {vqs_reason}"))
                if vqs_score >= 40:
                    profile.is_b2b_vendor = True
                    profile.vendor_quality_score = max(profile.vendor_quality_score or 0, vqs_score)
                    profile.messages_seen_count = (profile.messages_seen_count or 0) + 1
                    await session.commit()

                    logger.info(f"💎 Funnel 2 (Vendor B2B): Qualified Vendor offer ({vqs_reason}, seen_count={profile.messages_seen_count}) for @{username or user_id}")

                    # Trigger auto-outreach queue when vendor reaches 5+ messages or high VQS >= 70
                    if profile.messages_seen_count >= 5 or vqs_score >= 70:
                        asyncio.create_task(self._register_vendor_prospect(user_id, username, first_name, text, chat_title, vqs_score))


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

    async def _log_dropped_to_ai(self, user_id: int, username: Optional[str], first_name: Optional[str], chat_title: str, text: str, reason: str):
        """Logs Gatekeeper/VQS dropped messages to AIEvaluationLog for UI visibility."""
        try:
            async with AsyncSessionLocal() as session:
                from src.db.models import AIEvaluationLog
                eval_log = AIEvaluationLog(
                    user_id=user_id,
                    username=username or f"user_{user_id}",
                    first_name=first_name or f"Пользователь {user_id}",
                    chat_title=chat_title,
                    message_text=text,
                    is_lead=False,
                    reasoning=reason,
                    niche_code="dropped",
                    temperature="❄️ Спам/Шум",
                    confidence_score=0.0
                )
                session.add(eval_log)
                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to record dropped AIEvaluationLog: {e}")

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

    async def _register_vendor_prospect(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        raw_text: str,
        chat_title: str,
        vqs_score: int
    ):
        """
        Registers qualified vendor into OutreachLead and B2BProspect queues for automated B2B outreach by Ekaterina.
        """
        try:
            async with AsyncSessionLocal() as session:
                from src.db.models import OutreachLead
                from datetime import datetime, timezone
                author_uname = username.replace("@", "") if username else None
                author_fname = first_name or f"Vendor_{user_id}"

                dup_stmt = select(OutreachLead).where(
                    (OutreachLead.telegram_id == user_id) |
                    (OutreachLead.author_username == author_uname)
                ) if author_uname else select(OutreachLead).where(OutreachLead.telegram_id == user_id)

                existing_outreach = (await session.execute(dup_stmt)).scalars().first()

                if not existing_outreach:
                    s_hook = f"Предложите поставщику услуг (@{author_uname or user_id}) готовый поток целевых клиентов через LeadRadar.win (VQS={vqs_score})"
                    new_outreach = OutreachLead(
                        author_username=author_uname,
                        author_first_name=author_fname,
                        telegram_id=user_id,
                        niche_code="other_b2b",
                        location_code="global",
                        confidence_score=float(vqs_score),
                        status="READY_FOR_OUTREACH",
                        raw_ad_text=raw_text[:500],
                        sales_hook=s_hook,
                        chat_title=chat_title,
                        messages_history=[{"chat_title": chat_title, "message_text": raw_text, "timestamp": datetime.now(timezone.utc).isoformat()}]
                    )
                    session.add(new_outreach)
                    await session.commit()
                    logger.info(f"🚀 VQS Splitter: Auto-registered Vendor Prospect @{author_uname or user_id} (VQS={vqs_score}) into OutreachLead queue!")
        except Exception as e:
            logger.warning(f"Notice registering vendor prospect: {e}")


    async def join_channel(self, username_or_link: str):
        """
        Attempts to add target chat/channel using Zero-Auth Public Scraper bypass first (0 MTProto calls),
        or Pyrogram Userbot with strict Anti-Ban rate limiting quotas.
        """
        clean_target = username_or_link.strip().replace("https://t.me/s/", "").replace("https://t.me/", "@").replace("http://t.me/", "@")
        if not clean_target.startswith("@") and not clean_target.startswith("+"):
            clean_target = f"@{clean_target}"

        # 1. Zero-Auth Public Channel Bypass: Check if readable via Web Preview without MTProto join!
        try:
            from src.ingestion.public_scraper import PublicTelegramScraper
            scraper = PublicTelegramScraper()
            clean_user = scraper._clean_username(clean_target)
            if clean_user and not clean_target.startswith("+"):
                url = f"https://t.me/s/{clean_user}"
                import httpx, re
                async with httpx.AsyncClient(headers=scraper.headers, follow_redirects=False, timeout=8.0) as client:
                    res = await client.get(url)
                    if res.status_code == 200:
                        title_match = re.search(r'<div class="tgme_header_title"[^>]*>\s*<span[^>]*>(.*?)</span>', res.text, re.DOTALL)
                        title = scraper._strip_html(title_match.group(1)) if title_match else f"@{clean_user}"
                        logger.info(f"✅ Zero-Auth Public Scraper verified public channel {title} ({clean_target}). Bypassing MTProto join (0 API calls used)!")
                        return True, title, None
                    elif res.status_code in (301, 302, 307, 308):
                        logger.info(f"💬 Chat {clean_target} is a GROUP CHAT (302 Redirect). Requires MTProto Userbot join.")
        except Exception as web_err:
            logger.debug(f"Public scraper pre-check notice for {clean_target}: {web_err}")

        # 2. Group Chat / Private Chat: Check Anti-Ban Quota before using MTProto
        can_join, reason = self._can_perform_mtproto_join()
        if not can_join:
            logger.info(f"🛡️ Anti-Ban Rate Limiter: Deferring MTProto join for {clean_target}: {reason}")
            return False, None, f"Anti-Ban Pacing: {reason}"

        # 3. Perform MTProto Userbot join if client active & quota permits
        now_utc = datetime.now(timezone.utc)
        if self.app and self._is_running:
            try:
                chat = await self.app.join_chat(clean_target)
                title = getattr(chat, "title", None) or getattr(chat, "username", None) or username_or_link
                
                # Update Anti-Ban Rate Limiter state
                self.last_mtproto_join_at = now_utc
                self.daily_join_count += 1
                
                logger.info(f"✅ Userbot successfully joined group chat: {title} ({clean_target}). MTProto quota today: {self.daily_join_count}/{self.max_daily_joins}")
                return True, title, None
            except Exception as e:
                err_str = str(e)
                if "FloodWait" in type(e).__name__ or "FLOOD_WAIT" in err_str:
                    wait_sec = getattr(e, "value", 60)
                    already_flooded = self.userbot_flood_until and now_utc < self.userbot_flood_until
                    self.userbot_status = "FLOOD_WAIT"
                    self.userbot_flood_wait_seconds = wait_sec
                    self.userbot_flood_until = now_utc + timedelta(seconds=wait_sec)
                    
                    # Halve max daily joins for recovery
                    self.max_daily_joins = max(5, self.max_daily_joins - 5)
                    logger.warning(f"⚠️ Pyrogram FloodWait caught during join: {wait_sec}s until {self.userbot_flood_until.isoformat()}. Adjusted daily join quota to {self.max_daily_joins}.")

                    if not already_flooded:
                        try:
                            from src.bot.alert_bot import notify_superadmins_system_alert
                            await notify_superadmins_system_alert(
                                f"⚠️ <b>ВНИМАНИЕ: PYROGRAM FLOOD WAIT!</b>\n"
                                f"───────────────────────────\n\n"
                                f"Telegram ограничил действия юзербота на <b>{wait_sec} сек</b> (~{round(wait_sec/3600, 1)} ч).\n"
                                f"🛡️ <i>Умный рейт-лимитер снизил суточную квоту до {self.max_daily_joins}. Публичные каналы опрашиваются через Zero-Auth Web Scraper.</i>"
                            )
                        except Exception:
                            pass
                    return False, None, f"FloodWait ({wait_sec}s)"
                else:
                    logger.warning(f"Pyrogram Userbot join error for {clean_target}: {e}")
                    return False, None, f"MTProto Error: {e}"

        return False, None, "Не удалось подключиться: закрытый чат или отсутствует сессия юзербота."

    async def sync_monitored_channels(self):
        """
        Background worker that processes pending channels under strict Anti-Ban pacing.
        Processes max 1 pending group chat per check pass, spacing joins 12-15 min apart.
        """
        import random
        from src.db.models import MonitoredChannel
        if not self._is_running:
            return

        async with AsyncSessionLocal() as session:
            res = await session.execute(select(MonitoredChannel).where(MonitoredChannel.status.in_(["PENDING", "FAILED"])))
            pending_channels = list(res.scalars().all())

            if not pending_channels:
                return

            logger.info(f"🔄 Auto-Joiner: {len(pending_channels)} channels pending processing.")

            for channel in pending_channels:
                if not self._is_running:
                    break

                success, title, error = await self.join_channel(channel.username_or_link)
                if success:
                    channel.status = "JOINED"
                    channel.title = title
                    channel.error_message = None
                    await session.commit()
                    logger.info(f"✅ Auto-Joiner: MonitoredChannel {title or channel.username_or_link} updated to JOINED.")
                elif error and "Anti-Ban Pacing" in error:
                    # Defer remaining pending channels to next check pass
                    logger.info(f"🛡️ Auto-Joiner: Pacing quota deferred processing for remaining {len(pending_channels)} channels ({error}).")
                    break
                else:
                    channel.status = "FAILED"
                    channel.error_message = error
                    await session.commit()

                # If an MTProto join was actually executed, pause for randomized anti-ban interval (12-15 min)
                if self.last_mtproto_join_at and (datetime.now(timezone.utc) - self.last_mtproto_join_at).total_seconds() < 5:
                    jitter_s = random.randint(720, 900)  # 12 to 15 minutes jitter
                    logger.info(f"⏳ Anti-Ban Pacer: MTProto join completed. Pausing join loop for {round(jitter_s/60, 1)} minutes...")
                    await asyncio.sleep(jitter_s)

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
        logger.info("📡 Starting Accelerated Public Telegram Scraper Loop (20 concurrent workers, batched polling)...")

        processed_posts = set()
        CONCURRENCY_LIMIT = 20  # Optimized concurrency pool (20 parallel channel fetches)
        sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

        limits = httpx.Limits(max_keepalive_connections=30, max_connections=60)

        async with httpx.AsyncClient(headers=scraper.headers, follow_redirects=True, timeout=12.0, limits=limits) as client:
            while self._is_running:
                try:
                    from datetime import datetime, timezone
                    self.last_check_at = datetime.now(timezone.utc)
                    update_last_message_time()

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
                        # Order channels so least recently scraped channels run first in fair round-robin!
                        res = await session.execute(
                            select(MonitoredChannel).order_by(
                                MonitoredChannel.last_scraped_at.asc().nullsfirst(),
                                MonitoredChannel.created_at.desc()
                            )
                        )
                        channels = list(res.scalars().all())

                    if channels:
                        # Process channels in paginated chunks of 50 to ensure no timeout starvation
                        chunk_size = 50
                        for i in range(0, len(channels), chunk_size):
                            if not self._is_running:
                                break

                            channel_chunk = channels[i:i + chunk_size]
                            tasks = [
                                self._scrape_single_channel_task(ch, scraper, client, sem, processed_posts)
                                for ch in channel_chunk
                            ]
                            try:
                                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=45.0)
                            except asyncio.TimeoutError:
                                logger.warning(f"⏱️ Scraper chunk pass ({i}-{i+chunk_size}) reached 45s timeout limit. Preserving completed channel results...")
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
        logger.info("🛡️ Starting Database Guard & Retention Enforcement Loop (15 min check)...")
        from src.services.db_guard import db_guard

        # Delay initial pass by 60s to prevent startup DB table write locks
        await asyncio.sleep(60)

        while self._is_running:
            try:
                await db_guard.run_enforcement_pass()
            except Exception as e:
                logger.error(f"Error in DB Guard enforcement loop: {e}")

            # Run 15-minute check (900 seconds)
            await asyncio.sleep(900)

    async def run_auto_discovery_loop(self):
        """Automated background worker for discovering new Telegram groups via MTProto search & Web catalogs."""
        logger.info("🔍 Starting Automated Telegram Group Discovery Loop (MTProto & Directory Search)...")
        keywords = ["Дубай аренда", "Дубай жилье", "Dubai real estate", "Дубай usdt", "Дубай авто", "Дубай работа", "Бали аренда", "Бали виллы"]
        
        while self._is_running:
            try:
                await asyncio.sleep(120)  # Wait 2 minutes after startup before initial discovery pass
                
                # Check Pyrogram MTProto global search if Client active and not in FloodWait
                if self.app and getattr(self.app, "is_connected", False) and (not self.userbot_flood_until or datetime.now(timezone.utc) >= self.userbot_flood_until):
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

    async def run_dead_man_switch_loop(self):
        """
        🛡️ Dead Man's Switch (Кнопка мертвеца):
        Monitors LAST_MESSAGE_TIME and last_check_at every minute.
        If both scraper polling and message ingestion have stopped for > 1800 seconds (30 minutes),
        forces hard suicide (os._exit(1)) so infrastructure (Docker / PM2 / Railway)
        instantly restarts the process and reconnects fresh Telegram WebSockets.
        """
        timeout_seconds = int(os.getenv("DEAD_MAN_TIMEOUT_SECONDS", "1800"))
        logger.info(f"💀 Dead Man's Switch active (Stale timeout threshold: {timeout_seconds}s / {timeout_seconds//60}m)...")

        # Initial startup grace period (wait 3 minutes before checking inactivity)
        await asyncio.sleep(180)

        while self._is_running:
            await asyncio.sleep(60)  # Check every 60 seconds
            if not self._is_running:
                break

            now_utc = datetime.now(timezone.utc)

            # Check both last message time AND scraper polling activity time
            last_msg_at = get_last_message_time()
            last_check_at = self.last_check_at

            idle_seconds_msg = (now_utc - last_msg_at).total_seconds() if last_msg_at else 999999
            idle_seconds_check = (now_utc - last_check_at).total_seconds() if last_check_at else 999999

            # Only trigger Dead Man's Switch if BOTH message capture AND scraper loop polling have stalled beyond threshold!
            effective_idle = min(idle_seconds_msg, idle_seconds_check)

            if effective_idle > timeout_seconds:
                idle_minutes = round(effective_idle / 60, 1)
                crit_msg = (
                    f"🚨 <b>DEAD MAN'S SWITCH TRIGGERED! (КНОПКА МЕРТВЕЦА)</b>\n"
                    f"───────────────────────────\n\n"
                    f"⚠️ <b>Процесс слушателя полностью заблокирован/завис!</b>\n"
                    f"⏱️ <b>Время молчания:</b> <b>{idle_minutes} мин</b> ({int(effective_idle)} сек) (порог: {timeout_seconds//60} мин).\n"
                    f"💀 <b>Действие:</b> Выполняется экстренная остановка процесса <code>os._exit(1)</code> для сброса зависших WebSockets...\n\n"
                    f"🔄 <i>Инфраструктура (Docker / PM2 / Railway) мгновенно поднимет чистый процесс через 1-2 сек.</i>"
                )
                logger.critical(f"🚨 DEAD MAN'S SWITCH: No activity for {idle_minutes}m ({int(effective_idle)}s > {timeout_seconds}s). Executing os._exit(1) hard exit!")

                try:
                    from src.bot.alert_bot import notify_superadmins_system_alert
                    await notify_superadmins_system_alert(crit_msg)
                    await asyncio.sleep(2)
                except Exception as alert_err:
                    logger.error(f"Error sending Dead Man's Switch alert: {alert_err}")

                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(1)


    async def start(self):
        self._is_running = True
        await self.refresh_banned_users()
        
        if not self.app and settings.USERBOT_SESSION_STRING:
            await self.setup()

        if self.app:
            try:
                logger.info("🚀 Starting Pyrogram Userbot MTProto Listener...")
                await self.app.start()
                me = await self.app.get_me()
                self.userbot_user_handle = f"@{me.username}" if me.username else str(me.id)
                self.userbot_status = "CONNECTED"
                self.userbot_last_ping = datetime.now(timezone.utc)
                logger.info(f"✅ Pyrogram Userbot connected as {self.userbot_user_handle}")
                
                # Auto-sync dialogs and auto-join pending channels with Anti-Ban pacing
                asyncio.create_task(self.sync_monitored_channels())
            except Exception as e:
                err_msg = str(e)
                logger.warning(f"⚠️ Pyrogram Userbot start error: {err_msg}. Fallback to Zero-Auth Public Scraper.")
                self.userbot_status = "DISCONNECTED"
                if any(k in err_msg for k in ["AUTH_KEY_DUPLICATED", "406", "SESSION_REVOKED", "Unauthorized"]):
                    self.userbot_status = "AUTH_ERROR"
                    self.app = None
                    # Send alert card to Superadmin
                    try:
                        from src.bot.alert_bot import notify_superadmins_system_alert
                        asyncio.create_task(notify_superadmins_system_alert(
                            "⚠️ <b>ВНИМАНИЕ: СБОЙ СЕССИИ ЮЗЕРБОТА!</b>\n"
                            "───────────────────────────\n\n"
                            f"❌ <b>Ошибка авторизации:</b> <code>{err_msg}</code>\n\n"
                            "🔑 <i>Требуется обновить строку сессии с помощью скрипта:</i>\n"
                            "<code>python scripts/generate_session.py</code>\n\n"
                            "📡 <i>Система временно работает в режиме Zero-Auth Web Scraper.</i>"
                        ))
                    except Exception:
                        pass

        self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())
        self.watchdog_task = asyncio.create_task(self.run_watchdog_loop())
        self.dead_man_switch_task = asyncio.create_task(self.run_dead_man_switch_loop())
        self.retention_task = asyncio.create_task(self.run_log_retention_cleanup())
        self.discovery_task = asyncio.create_task(self.run_auto_discovery_loop())

        # Notify Superadmins on listener startup (Emergency channel alert)
        try:
            from src.bot.alert_bot import notify_superadmins_system_alert
            asyncio.create_task(notify_superadmins_system_alert(
                "⚡ <b>Слушатель запущен.</b> Инициализация и переподключение сокетов Telegram & WebScraper...\n"
                "🛡️ <i>Кнопка мертвеца (Dead Man's Switch) активирована (порог 5 мин).</i>"
            ))
        except Exception as notify_err:
            logger.warning(f"Notice sending listener startup Telegram alert: {notify_err}")

    async def stop(self):
        self._is_running = False
        if self.public_scraper_task:
            self.public_scraper_task.cancel()
        if self.watchdog_task:
            self.watchdog_task.cancel()
        if hasattr(self, 'dead_man_switch_task') and self.dead_man_switch_task:
            self.dead_man_switch_task.cancel()
        if self.retention_task:
            self.retention_task.cancel()
        if hasattr(self, 'discovery_task') and self.discovery_task:
            self.discovery_task.cancel()
        if self.app:
            await self.app.stop()

