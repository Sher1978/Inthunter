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

class ScraperNode:
    def __init__(self, db_id: int, session_string: str, max_daily_joins: int, daily_join_count: int, flood_until: Optional[datetime]):
        self.db_id = db_id
        self.session_string = session_string
        self.app = None
        self.status = "NOT_CONFIGURED"
        self.user_handle = None
        self.last_ping = None
        self.flood_until = flood_until
        self.daily_join_count = daily_join_count
        self.max_daily_joins = max_daily_joins
        self.last_join_at: Optional[datetime] = None
        self.daily_join_reset_date: Optional[str] = None
        self.min_join_interval_seconds: int = 720
        self.joined_groups_today: List[Dict[str, Any]] = []

    def can_perform_mtproto_join(self, is_night_mode: bool, circuit_breaker_until: Optional[datetime] = None) -> tuple:
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        if circuit_breaker_until and now_utc < circuit_breaker_until:
            rem_m = round((circuit_breaker_until - now_utc).total_seconds() / 60, 1)
            return False, f"Anti-Burn Circuit Breaker active ({rem_m}m pause)"

        if self.status in ("BANNED", "DISABLED", "ERROR"):
            return False, f"Node status is {self.status}"

        if self.daily_join_reset_date != today_str:
            self.daily_join_reset_date = today_str
            self.daily_join_count = 0
            self.joined_groups_today = []


        if self.flood_until and now_utc < self.flood_until:
            rem_sec = int((self.flood_until - now_utc).total_seconds())
            return False, f"FloodWait active ({rem_sec}s)"

        if is_night_mode:
            return False, "Night Mode active"

        if self.daily_join_count >= self.max_daily_joins:
            return False, f"Daily limit reached ({self.daily_join_count}/{self.max_daily_joins})"

        if self.last_join_at:
            elapsed = (now_utc - self.last_join_at).total_seconds()
            if elapsed < self.min_join_interval_seconds:
                wait_m = round((self.min_join_interval_seconds - elapsed) / 60, 1)
                return False, f"Pacing active ({wait_m}m)"
        return True, "OK"

class TelegramIngestor:
    """
    Pyrogram / Telethon passive userbot message listener.
    Captures chat activity, updates user profiles, and triggers intent scoring.
    """

    def __init__(self):
        self.scrapers = []
        self._is_running = False
        self.last_scraped_at = None
        self.last_check_at = None
        self.scraped_count = 0
        self.public_scraper_task = None
        self.watchdog_task = None
        self.dead_man_switch_task = None
        self.retention_task = None
        self.discovery_task = None
        self.ai_batch_worker_task = None
        self._ai_batch_queue = []
        self._ai_batch_lock = asyncio.Lock()
        self.banned_spammer_user_ids = set()
        self.group_chat_302_count = 0           # total lifetime 302s (kept for legacy compat)
        self.group_chat_302_session_count = 0   # total 302 hits since last report (legacy)
        self.group_chat_302_session_channels: set = set()  # UNIQUE channels returning 302 this hour
        self.group_chat_302_total_count = 0     # 302s seen this scraper session (for report)
        self._scraper_cycle_count = 0           # how many full scraper loop passes have run
        self.swarm_circuit_breaker_until: Optional[datetime] = None

    def _is_night_mode(self) -> bool:
        """Returns True if current local time is within human night sleeping hours (01:00 - 07:00)."""
        now = datetime.now()
        return 1 <= now.hour < 7

    async def setup(self):
        """Initializes Pyrogram Userbot Swarm from ScraperAccount DB table."""
        from src.db.models import ScraperAccount
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            res = await session.execute(select(ScraperAccount).where(ScraperAccount.status == 'ACTIVE'))
            accounts = list(res.scalars().all())

        # Support legacy USERBOT_SESSION_STRING from .env as a fallback node if no DB accounts exist
        session_str = (settings.USERBOT_SESSION_STRING or "").strip()
        if not accounts and session_str:
            logger.info("ℹ️ No active ScraperAccounts in DB. Using legacy USERBOT_SESSION_STRING from .env")
            legacy_node = ScraperNode(db_id=0, session_string=session_str, max_daily_joins=20, daily_join_count=0, flood_until=None)
            self.scrapers.append(legacy_node)
        elif accounts:
            logger.info(f"⚡ Setting up Pyrogram Userbot Swarm with {len(accounts)} active accounts...")
            for acc in accounts:
                node = ScraperNode(db_id=acc.id, session_string=acc.session_string, max_daily_joins=acc.max_daily_joins, daily_join_count=acc.daily_join_count, flood_until=acc.flood_until)
                self.scrapers.append(node)

        if not self.scrapers:
            logger.info("ℹ️ No scraper sessions found. Operating in Zero-Auth Public Scraper mode.")
            return

        try:
            from pyrogram import Client, filters
            from pyrogram.types import Message
            from functools import partial

            for node in self.scrapers:
                try:
                    node.app = Client(
                        name=f"intent_hunter_scraper_{node.db_id}",
                        api_id=settings.TELEGRAM_API_ID,
                        api_hash=settings.TELEGRAM_API_HASH,
                        session_string=node.session_string,
                        in_memory=True
                    )
                    
                    @node.app.on_message(filters.group | filters.channel)
                    async def _on_pyrogram_message(client, message: Message):
                        try:
                            if not message:
                                return
                            msg_text = message.text or message.caption
                            if not msg_text:
                                return
                            user_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 0)
                            username = message.from_user.username if message.from_user else None
                            first_name = message.from_user.first_name if message.from_user else (message.chat.title if message.chat else None)
                            last_name = message.from_user.last_name if message.from_user else None
                            chat_id = message.chat.id if message.chat else 0
                            raw_title = message.chat.title if message.chat else (username or "Telegram Group")
                            thread_id = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_top_message_id", None)
                            chat_title = f"{raw_title} [Топик #{thread_id}]" if thread_id else raw_title
                            msg_id = message.id

                            await self.process_incoming_message(
                                user_id=user_id,
                                username=username,
                                first_name=first_name,
                                last_name=last_name,
                                chat_id=chat_id,
                                chat_title=chat_title,
                                message_id=msg_id,
                                text=msg_text,
                                channel_username=f"@{message.chat.username}" if message.chat and getattr(message.chat, "username", None) else None
                            )
                        except Exception as msg_err:
                            logger.error(f"Error in Pyrogram live message handler: {msg_err}")
                            
                    node.status = "CONFIGURED"
                except Exception as node_err:
                    logger.error(f"Failed to setup node {node.db_id}: {node_err}")
                    node.status = "ERROR"

            logger.info(f"✅ Pyrogram Userbot Swarm setup complete ({len(self.scrapers)} nodes).")
        except Exception as e:
            logger.error(f"❌ Error setting up Pyrogram Swarm: {e}")

    def get_userbot_status(self) -> Dict:
        """Returns live metrics and connection health state of the Userbot Swarm."""
        nodes_status = []
        is_night = self._is_night_mode()
        
        for node in self.scrapers:
            in_flood = node.flood_until is not None and datetime.now(timezone.utc) < node.flood_until
            rem_s = int((node.flood_until - datetime.now(timezone.utc)).total_seconds()) if in_flood else 0
            can_join, join_reason = node.can_perform_mtproto_join(is_night)
            
            nodes_status.append({
                "db_id": node.db_id,
                "status": "FLOOD_WAIT" if in_flood else node.status,
                "connected": node.app is not None and getattr(node.app, "is_connected", False),
                "user_handle": node.user_handle,
                "last_ping_at": node.last_ping.isoformat() if node.last_ping else None,
                "flood_wait_seconds": rem_s,
                "daily_joins_used": node.daily_join_count,
                "joined_groups_today": getattr(node, "joined_groups_today", []),
                "can_join": can_join,
                "join_reason": join_reason
            })

            
        return {
            "status": "SWARM_ACTIVE" if self.scrapers else "NOT_CONFIGURED",
            "nodes_count": len(self.scrapers),
            "group_chats_302_count": self.group_chat_302_count,
            "group_chats_302_session_count": self.group_chat_302_session_count,
            "group_chats_302_total_count": self.group_chat_302_total_count,
            "scraper_cycle_count": self._scraper_cycle_count,
            "scraped_count": self.scraped_count,
            "nodes": nodes_status
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
        channel_username: Optional[str] = None,
        db_session: Optional[AsyncSession] = None
    ):
        if not user_id or not text.strip():
            return

        from src.services.module_manager import module_manager
        if not module_manager.is_enabled("reader"):
            logger.debug("💬 Reader notice: Message ingestion is PAUSED via module_manager.")
            return

        # =====================================================================
        # AI SCOUT: PHASE 1 - REGEX INVITE INTERCEPTOR
        # =====================================================================
        # Extract private group invites from ALL incoming messages (even unmonitored chats)
        if module_manager.is_enabled("scout_regex_extract"):
            import re
            invite_match = re.search(r'https?://t\.me/(?:\+|\bjoinchat/)[A-Za-z0-9_-]+', text)
            if invite_match:
                invite_link = invite_match.group(0)
                # Send to background task to save to DiscoveredChat so we don't block the ingestor
                asyncio.create_task(self._register_discovered_invite(invite_link, chat_title or channel_username or "Unknown Intercept"))
        # =====================================================================

        # Security & Spam Filter: Drop messages from channels that were deleted from MonitoredChannels
        # This prevents the system from continuing to ingest logs from spam groups after the user clicked "Delete Channel".
        if getattr(self, 'monitored_channels_cache', None):
            is_monitored = False
            clean_ct = (chat_title or "").strip().lower()
            clean_cu = (channel_username or "").strip().lower().replace("@", "").replace("https://t.me/", "")
            
            for ch in self.monitored_channels_cache:
                ch_title_db = (ch.get("title") or "").strip().lower()
                ch_uname_db = (ch.get("username_or_link") or "").strip().lower().replace("@", "").replace("https://t.me/", "")
                
                if (clean_ct and ch_title_db and clean_ct == ch_title_db) or \
                   (clean_cu and ch_uname_db and clean_cu == ch_uname_db):
                    is_monitored = True
                    break
                    
            if not is_monitored:
                # Silently ignore messages from ghost/deleted channels
                return

        from datetime import datetime, timezone
        update_last_message_time()

        self.last_scraped_at = datetime.now(timezone.utc)
        self.scraped_count += 1

        # Upgrade 4: Global Spammer Blacklist Filter
        if user_id and user_id in self.banned_spammer_user_ids:
            logger.debug(f"🚫 Gatekeeper: Dropped message from globally blacklisted spammer user_id={user_id}")
            return

        # Gatekeeper Fast Pre-Filter: Protect HR vacancies, B2B posts, and leads. Only drop extreme promo dumps (>2200 chars) without valuable keywords.
        txt_low = text.lower()
        valuable_keywords = [
            "hiring", "vacancy", "вакансия", "требуется", "ищем", "cv", "резюме", "job", "recruitment", "career", "работа", "оклад", "зарплат",
            "b2b", "услуг", "закупк", "ищу", "нужен", "нужна", "куплю", "сдам", "сниму", "аренд", "недвиж", "вилл", "квартир", "авто", "машин",
            "переезд", "район", "жк", "внж", "виза", "школ", "садик", "детсад", "мамы", "мамочки", "дети", "ребенок", "няня", "налог", "посоветуйте", "подскажите", "инвест", "планиру",
            "nursery", "school", "kindergarten", "parents", "family", "housewives"
        ]
        has_valuable_kw = any(kw in txt_low for kw in valuable_keywords)

        if not has_valuable_kw and len(text) > 2200 and ("http://" in txt_low or "https://" in txt_low or text.count("#") >= 7):
            logger.debug(f"🚫 Gatekeeper: Dropped extreme promo dump ({len(text)} chars) from user_id={user_id}")
            asyncio.create_task(self._log_dropped_to_ai(user_id, username, first_name, chat_title, text, "Отклонено пре-фильтром (Gatekeeper): Длинный спам-пост", channel_username))
            return

        logger.info(f"Received message from user_id={user_id} in [{chat_title}]: \"{text[:40]}...\"")
        try:
            from src.services.process_logger import process_logger
            process_logger.add_log(
                category="USERBOT",
                level="info",
                title=f"⚡ ЮЗЕРБОТ [Слушатель]: Сообщение от @{username or user_id} в [{chat_title}]",
                details=f"«{text[:120]}»"
            )
        except Exception:
            pass

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
                                    loc = "dubai"
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

            # 2. Record UserActivityLog with location_code lookup
            msg_location = "global"
            clean_ct = (chat_title or "").strip().lower()
            clean_username = (channel_username or "").strip().lstrip("@").lower()
            if clean_ct or clean_username:
                try:
                    from src.db.models import MonitoredChannel
                    from sqlalchemy import func
                    ch_loc = (await session.execute(
                        select(MonitoredChannel.location_code).where(
                            (func.lower(MonitoredChannel.title) == clean_ct) |
                            (func.lower(MonitoredChannel.username_or_link) == f"@{clean_username}") |
                            (func.lower(MonitoredChannel.username_or_link) == clean_username)
                        )
                    )).scalars().first()
                    if ch_loc:
                        msg_location = ch_loc
                except Exception as loc_err:
                    logger.debug(f"Notice fetching channel location_code: {loc_err}")

            activity = UserActivityLog(
                user_id=user_id,
                chat_id=chat_id,
                chat_title=chat_title,
                message_id=message_id,
                message_text=text,
                channel_username=channel_username,
                location_code=msg_location
            )
            session.add(activity)

            # Update MonitoredChannel last_scraped_at timestamp in DB
            if clean_ct:
                try:
                    from src.db.models import MonitoredChannel
                    from sqlalchemy import update, func
                    await session.execute(
                        update(MonitoredChannel)
                        .where(
                            (func.lower(MonitoredChannel.title) == clean_ct) |
                            (func.lower(MonitoredChannel.username_or_link) == f"@{clean_ct}") |
                            (func.lower(MonitoredChannel.username_or_link) == clean_ct)
                        )
                        .values(last_scraped_at=datetime.now(timezone.utc))
                    )
                except Exception as ch_up_err:
                    logger.warning(f"Notice updating MonitoredChannel last_scraped_at: {ch_up_err}")

            await session.commit()

            # 2.5 Fetch recent messages for context
            stmt_msgs = select(UserActivityLog).where(
                UserActivityLog.user_id == user_id
            ).order_by(UserActivityLog.timestamp.desc()).limit(20)
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

            if intent_type == 'VENDOR_OFFER' and vqs_score >= 40:
                profile.is_b2b_vendor = True
                profile.vendor_quality_score = max(profile.vendor_quality_score or 0, vqs_score)
                profile.messages_seen_count = (profile.messages_seen_count or 0) + 1
                logger.info(f"💎 Funnel 2 (Vendor B2B): Qualified Vendor offer ({vqs_reason}, seen_count={profile.messages_seen_count}) for @{username or user_id}")

                from src.services.process_logger import process_logger
                process_logger.add_log(
                    "USERBOT",
                    "success",
                    f"💎 ВЕНДОР КВАЛИФИЦИРОВАН: @{username or user_id} (VQS: {vqs_score}/100)",
                    f"Ниша: Недвижимость B2B | Оффер: {vqs_reason[:100]}"
                )

                # Trigger auto-outreach queue when vendor reaches 5+ messages or high VQS >= 70
                if profile.messages_seen_count >= 5 or vqs_score >= 70:
                    process_logger.add_log(
                        "USERBOT",
                        "lead",
                        f"✉️ АВТО-АУТРИЧ: Вендор @{username or user_id} поставлен в очередь контакта",
                        f"Посещений: {profile.messages_seen_count} | VQS: {vqs_score}/100"
                    )
                    asyncio.create_task(self._register_vendor_prospect(user_id, username, first_name, text, chat_title, vqs_score))

            # Only trigger AI scoring if the message passed the free VQS/Gatekeeper filter.
            # If it's TRASH, bypass AI entirely to save tokens and avoid 429 limits.
            if intent_type != 'TRASH':
                asyncio.create_task(self._trigger_ai_scoring(user_id, messages))
            else:
                asyncio.create_task(self._log_dropped_to_ai(user_id, username, first_name, chat_title, text, vqs_reason, channel_username))


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

    async def _log_dropped_to_ai(self, user_id: int, username: Optional[str], first_name: Optional[str], chat_title: str, text: str, reason: str, channel_username: Optional[str] = None):
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
                    channel_username=channel_username,
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
        """Queues ALL user messages for 100% pure LLM evaluation asynchronously in background queue."""
        from src.services.module_manager import module_manager
        if not module_manager.is_enabled("ai_scorer"):
            logger.debug("🧠 AI Scorer notice: AI scoring is PAUSED via module_manager.")
            return

        try:
            async with self._ai_batch_lock:
                from src.ai.scorer import build_timeline_string
                timeline_str = build_timeline_string(messages)
                if not timeline_str or len(timeline_str.strip()) < 2:
                    return
                
                self._ai_batch_queue.append({
                    "user_id": user_id,
                    "timeline_str": timeline_str,
                    "messages": messages
                })
        except Exception as e:
            logger.error(f"Error queueing for AI scoring: {e}")
            
    async def _ai_batch_worker(self):
        """Background worker that processes AI batches with controlled queue pacing (Capacity: 40 users/batch, Interval: 10s)."""
        from src.ai.batch_scorer import evaluate_batch
        from src.bot.alert_bot import broadcast_lead_alert
        
        logger.info("🧠 AI Fast-Batch Worker Loop Started (Capacity: 40 users/batch, Paced Interval: 10s).")
        while getattr(self, "_is_running", True):
            try:
                batch = []
                async with self._ai_batch_lock:
                    if len(self._ai_batch_queue) > 0:
                        batch = self._ai_batch_queue[:40]
                        self._ai_batch_queue = self._ai_batch_queue[len(batch):]
                
                if batch:
                    logger.info(f"🧠 AI Batch Worker processing {len(batch)} queued timelines via LLM (Queue remaining: {len(self._ai_batch_queue)})...")
                    try:
                        async with AsyncSessionLocal() as session:
                            results = await evaluate_batch(batch, session)
                            
                            # If results is None, it indicates a global API failure (all keys exhausted/rate limited).
                            # We requeue the entire batch without penalty and sleep to allow cooldown.
                            if results is None:
                                logger.warning("🛑 System AI failure (API exhaustion). Pausing batch worker for 60s and re-queueing.")
                                async with self._ai_batch_lock:
                                    self._ai_batch_queue = batch + self._ai_batch_queue
                                await asyncio.sleep(60)
                                continue

                            items_to_retry = []
                            for item in batch:
                                uid = item["user_id"]
                                msgs = item.get("messages", [])
                                last_m = msgs[-1] if msgs else None
                                m_text = getattr(last_m, "message_text", "") if last_m else ""
                                uname = getattr(last_m, "username", None) if last_m else None
                                fname = getattr(last_m, "first_name", None) if last_m else None
                                c_title = getattr(last_m, "chat_title", None) if last_m else "Telegram Group"

                                lead_result = results.get(uid) if results else None
                                
                                if lead_result is None:
                                    retries = item.get("retries", 0)
                                    if retries < 3:
                                        item["retries"] = retries + 1
                                        items_to_retry.append(item)
                                        continue
                                    else:
                                        try:
                                            from src.bot.alert_bot import bot
                                            from src.db.models import Partner
                                            from sqlalchemy import select
                                            res_sa = await session.execute(select(Partner.telegram_id).where(Partner.role == "SUPERADMIN"))
                                            for sa_id in res_sa.scalars().all():
                                                await bot.send_message(sa_id, f"🚨 <b>Критический сбой ИИ</b>\n\nСообщение от @{uname or uid} пропущено после 3 неудачных попыток анализа (Сбой API Groq).", parse_mode="HTML")
                                        except Exception:
                                            pass

                                is_l = lead_result.is_lead if lead_result else False
                                reason_txt = lead_result.reasoning if (lead_result and lead_result.reasoning) else "🚨 ОШИБКА ИИ: Сбой API или парсинга ответа. Требуется ручная перепроверка!"
                                niche_val = (lead_result.niche_code if lead_result else None) or "dropped"
                                conf_val = lead_result.confidence_score if lead_result else 0.0

                                try:
                                    from src.db.models import AIEvaluationLog
                                    eval_log = AIEvaluationLog(
                                        user_id=uid,
                                        username=uname or f"user_{uid}",
                                        first_name=fname or f"User_{uid}",
                                        chat_title=c_title,
                                        message_text=m_text,
                                        channel_username=getattr(last_m, "channel_username", None) if last_m else None,
                                        is_lead=is_l,
                                        reasoning=reason_txt,
                                        niche_code=niche_val,
                                        temperature="🔥 HOT" if is_l else ("⚠️ ОШИБКА" if not lead_result else "❄️ Не лид"),
                                        confidence_score=conf_val
                                    )
                                    session.add(eval_log)
                                    await session.commit()
                                except Exception as db_log_err:
                                    logger.warning(f"Notice saving AIEvaluationLog in batch worker: {db_log_err}")

                                if lead_result and lead_result.is_lead:
                                    if getattr(lead_result, "is_job_seeker", False) or lead_result.rubric_name == "JOB_SEEKER":
                                        asyncio.create_task(self._register_job_seeker_prospect(
                                            user_id=uid,
                                            username=uname,
                                            first_name=fname,
                                            raw_text=m_text,
                                            chat_title=c_title,
                                            conf_score=conf_val * 100
                                        ))
                                    else:
                                        await broadcast_lead_alert(uid, lead_result, msgs)

                            if items_to_retry:
                                async with self._ai_batch_lock:
                                    self._ai_batch_queue.extend(items_to_retry)
                                logger.info(f"🔄 Re-queued {len(items_to_retry)} items for retry (API failures).")

                            await asyncio.sleep(10)
                    except Exception as e:
                        logger.error(f"AI Batch Error: {e}")
                        await asyncio.sleep(10)
                else:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AI Batch Worker error: {e}")
                await asyncio.sleep(5)
            
            await asyncio.sleep(1)

            
            await asyncio.sleep(1)

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

    async def _register_job_seeker_prospect(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        raw_text: str,
        chat_title: str,
        conf_score: float
    ):
        """Registers a JOB_SEEKER into OutreachLead queue."""
        try:
            async with AsyncSessionLocal() as session:
                from src.db.models import OutreachLead
                from datetime import datetime, timezone
                author_uname = username.replace("@", "") if username else None
                author_fname = first_name or f"Seeker_{user_id}"

                dup_stmt = select(OutreachLead).where(
                    (OutreachLead.telegram_id == user_id) |
                    (OutreachLead.author_username == author_uname)
                ) if author_uname else select(OutreachLead).where(OutreachLead.telegram_id == user_id)

                existing_outreach = (await session.execute(dup_stmt)).scalars().first()

                if not existing_outreach:
                    s_hook = "Предложить соискателю вступить в наш Telegram-канал с актуальными вакансиями в Дубае"
                    new_outreach = OutreachLead(
                        author_username=author_uname,
                        author_first_name=author_fname,
                        telegram_id=user_id,
                        niche_code="job_seeker",
                        location_code="global",
                        confidence_score=conf_score,
                        status="READY_FOR_OUTREACH",
                        raw_ad_text=raw_text[:500],
                        sales_hook=s_hook,
                        chat_title=chat_title,
                        messages_history=[{"chat_title": chat_title, "message_text": raw_text, "timestamp": datetime.now(timezone.utc).isoformat()}]
                    )
                    session.add(new_outreach)
                    await session.commit()
                    logger.info(f"🚀 Auto-registered JOB SEEKER Prospect @{author_uname or user_id} into OutreachLead queue!")
        except Exception as e:
            logger.warning(f"Notice registering job seeker prospect: {e}")

    async def join_channel(self, username_or_link: str):
        """
        Attempts to add target chat/channel using Zero-Auth Public Scraper bypass first (0 MTProto calls),
        or Pyrogram Userbot with strict Anti-Ban rate limiting quotas.
        """
        clean_raw = username_or_link.strip().replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "")
        clean_user = clean_raw.split('/')[0].strip() if not clean_raw.startswith("+") else clean_raw
        clean_target = f"@{clean_user}" if not clean_user.startswith("+") else clean_user

        # 1. Zero-Auth Public Channel Pre-check: Resolve Title
        title = None
        try:
            from src.ingestion.public_scraper import PublicTelegramScraper
            scraper = PublicTelegramScraper()
            if clean_user and not clean_target.startswith("+"):
                url = f"https://t.me/s/{clean_user}"
                import httpx, re
                async with httpx.AsyncClient(headers=scraper.headers, follow_redirects=False, timeout=8.0) as client:
                    res = await client.get(url)
                    if res.status_code == 200:
                        title_match = re.search(r'<div class="tgme_header_title"[^>]*>\s*<span[^>]*>(.*?)</span>', res.text, re.DOTALL)
                        title = scraper._strip_html(title_match.group(1)) if title_match else f"@{clean_user}"
        except Exception as web_err:
            logger.debug(f"Public scraper pre-check notice for {clean_target}: {web_err}")

        # 2. Enforce MTProto Userbot Join so Telegram supergroups add userbots to group members and emit live events
        is_night = self._is_night_mode()
        available_node = None
        for node in self.scrapers:
            can_join, _ = node.can_perform_mtproto_join(is_night, self.swarm_circuit_breaker_until)
            if can_join and node.app and (getattr(node.app, "is_connected", False) or node.status in ("CONNECTED", "CONFIGURED")):
                available_node = node
                break

        if not available_node:
            logger.info(f"🛡️ Anti-Ban Rate Limiter: Deferring MTProto join for {clean_target} (No free nodes in Swarm)")
            return True, title or clean_target, "Anti-Ban Pacing: Deferred join"

        # 3. Perform MTProto Userbot join if client active & quota permits
        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        if available_node.app and self._is_running:
            try:
                chat = await available_node.app.join_chat(clean_target)
                title = getattr(chat, "title", None) or getattr(chat, "username", None) or username_or_link
                
                # Update Anti-Ban Rate Limiter state
                available_node.last_join_at = now_utc
                available_node.daily_join_count += 1
                if not hasattr(available_node, "joined_groups_today") or available_node.joined_groups_today is None:
                    available_node.joined_groups_today = []
                available_node.joined_groups_today.append({
                    "title": title or clean_target,
                    "link": clean_target,
                    "time": now_utc.strftime("%H:%M")
                })
                self.last_mtproto_join_at = now_utc


                # Persist DB join count update & MonitoredChannel JOINED status
                try:
                    from src.db.models import ScraperAccount, MonitoredChannel
                    from sqlalchemy import update, func
                    async with AsyncSessionLocal() as session:
                        if available_node.db_id > 0:
                            await session.execute(
                                update(ScraperAccount)
                                .where(ScraperAccount.id == available_node.db_id)
                                .values(daily_join_count=available_node.daily_join_count, last_join_at=now_utc)
                            )
                        clean_ct = clean_target.replace("@", "").lower()
                        await session.execute(
                            update(MonitoredChannel)
                            .where(
                                (func.lower(MonitoredChannel.username_or_link) == f"@{clean_ct}") |
                                (func.lower(MonitoredChannel.username_or_link) == clean_ct) |
                                (func.lower(MonitoredChannel.username_or_link).ilike(f"%{clean_ct}%"))
                            )
                            .values(status="JOINED", last_scraped_at=now_utc)
                        )
                        await session.commit()
                except Exception as db_err:
                    logger.warning(f"Notice updating ScraperAccount & MonitoredChannel join count in DB: {db_err}")

                # Publish Event to Live Process Monitoring Terminal
                try:
                    from src.services.process_logger import process_logger
                    process_logger.add_log(
                        category="USERBOT",
                        level="success",
                        title=f"⚡ ВСТУПЛЕНИЕ В ГРУППУ: Юзербот #{available_node.db_id} вступил в [{title}] ({clean_target})",
                        details=f"Использовано вступлений сегодня: {available_node.daily_join_count}/{available_node.max_daily_joins}"
                    )
                except Exception:
                    pass

                logger.info(f"✅ Userbot {available_node.db_id} successfully joined group chat: {title} ({clean_target}). MTProto quota today: {available_node.daily_join_count}/{available_node.max_daily_joins}")
                return True, title, None
            except Exception as e:
                err_str = str(e)
                err_type = type(e).__name__
                if "FloodWait" in err_type or "FLOOD_WAIT" in err_str:
                    wait_sec = getattr(e, "value", 60)
                    available_node.status = "FLOOD_WAIT"
                    available_node.flood_until = now_utc + timedelta(seconds=wait_sec)
                    
                    # Halve max daily joins for recovery
                    available_node.max_daily_joins = max(5, available_node.max_daily_joins - 5)
                    logger.warning(f"⚠️ Pyrogram FloodWait caught during join on node {available_node.db_id}: {wait_sec}s until {available_node.flood_until.isoformat()}. Adjusted daily join quota to {available_node.max_daily_joins}.")

                    return False, None, f"FloodWait ({wait_sec}s)"
                elif any(b_tag in err_str for b_tag in ["UserDeactivated", "USER_DEACTIVATED", "AuthKeyUnregistered", "AUTH_KEY_UNREGISTERED", "SessionRevoked", "SESSION_REVOKED", "Unauthorized", "401"]):
                    # 🚨 EMERGENCY ANTI-BURN CIRCUIT BREAKER ACTIVATED
                    available_node.status = "BANNED"
                    logger.error(f"🚨 EMERGENCY: Userbot #{available_node.db_id} was BANNED / DEACTIVATED by Telegram! Disconnecting node and triggering 30m Swarm Freeze...")
                    
                    if available_node.db_id > 0:
                        try:
                            from src.db.models import ScraperAccount
                            from sqlalchemy import update
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(ScraperAccount)
                                    .where(ScraperAccount.id == available_node.db_id)
                                    .values(status="BANNED", error_log=f"Account banned: {err_str[:300]}")
                                )
                                await session.commit()
                        except Exception:
                            pass
                    
                    # 1. Freeze MTProto joins across ALL nodes for 30 mins to protect remaining accounts
                    self.swarm_circuit_breaker_until = now_utc + timedelta(minutes=30)
                    
                    # 2. Send Urgent System Alert to Superadmin
                    try:
                        from src.bot.alert_bot import notify_superadmins_system_alert
                        await notify_superadmins_system_alert(
                            f"🚨 <b>АВАРИЙНАЯ ЗАЩИТА: СРАБОТАЛ CIRCUIT BREAKER!</b>\n\n"
                            f"⚠️ Аккаунт Юзербот <b>#{available_node.db_id}</b> заблокирован Telegram (<code>{err_type}</code>).\n\n"
                            f"🛡️ <b>Принятые автоматические меры:</b>\n"
                            f"1. Аккаунт <b>#{available_node.db_id}</b> мгновенно отключен от пула.\n"
                            f"2. Запущена <b>30-минутная заморозка</b> всех новых вступлений для защиты остальных аккаунтов!\n\n"
                            f"<i>Сканер продолжает работу через Public Scraper.</i>"
                        )
                    except Exception:
                        pass
                        
                    return False, None, f"Account Banned ({err_type})"
                else:
                    logger.warning(f"Pyrogram Userbot {available_node.db_id} join error for {clean_target}: {e}")
                    return False, None, f"MTProto Error: {e}"

        return False, None, "Не удалось подключиться: закрытый чат или отсутствует сессия юзербота."

    async def sync_monitored_channels(self):
        """
        Continuous background worker that processes pending channels under strict Anti-Ban pacing.
        Checks for PENDING/FAILED channels continuously every 2 minutes.
        """
        import random
        from src.db.models import MonitoredChannel
        while self._is_running:
            try:
                async with AsyncSessionLocal() as session:
                    # Fetch all channels ordered by least recently scraped or pending
                    res = await session.execute(
                        select(MonitoredChannel).where(
                            MonitoredChannel.status.in_(["PENDING", "FAILED"])
                        ).order_by(
                            MonitoredChannel.last_scraped_at.asc().nullsfirst(),
                            MonitoredChannel.created_at.desc()
                        )
                    )
                    channels = list(res.scalars().all())

                    if channels:
                        logger.info(f"🔄 Auto-Joiner & History Sync: Checking {len(channels)} monitored channels.")
                        for channel in channels:
                            if not self._is_running:
                                break

                            # Clean username/link (strip topic /12)
                            raw_link = channel.username_or_link or ""
                            clean_link = raw_link.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "").split('/')[0].strip()
                            clean_target = f"@{clean_link}" if not clean_link.startswith("+") else clean_link

                            success, title, error = await self.join_channel(clean_target)
                            if success:
                                channel.status = "JOINED"
                                if title:
                                    channel.title = title
                                channel.error_message = None
                                channel.last_scraped_at = datetime.now(timezone.utc)
                                await session.commit()
                                logger.info(f"✅ Auto-Joiner: MonitoredChannel {title or clean_target} synced & joined.")
                            elif error and "Anti-Ban Pacing" in error:
                                logger.info(f"🛡️ Auto-Joiner: Pacing quota deferred processing for remaining channels ({error}).")
                                break
                            elif error:
                                # Fatal Pyrogram errors indicating dead/blind chats
                                fatal_keywords = ["UsernameNotOccupied", "UsernameInvalid", "ChannelPrivate", "InviteHashExpired", "ChatRestricted", "PeerIdInvalid"]
                                if any(kw in error for kw in fatal_keywords):
                                    logger.warning(f"❌ Auto-Joiner: Fatal error for {clean_target} ({error}). Purging dead chat from system.")
                                    try:
                                        from src.ingestion.public_scraper import purge_dead_channel
                                        await purge_dead_channel(clean_target, reason=f"Pyrogram {error}")
                                    except Exception as purge_err:
                                        logger.error(f"Error purging dead chat {clean_target}: {purge_err}")
                                else:
                                    channel.status = "FAILED"
                                    channel.error_message = error
                                    channel.last_scraped_at = datetime.now(timezone.utc)
                                    await session.commit()

                            if getattr(self, "last_mtproto_join_at", None) and (datetime.now(timezone.utc) - self.last_mtproto_join_at).total_seconds() < 5:
                                jitter_s = random.randint(15, 45)  # Fast join pacing across userbot swarm
                                await asyncio.sleep(jitter_s)
            except Exception as loop_err:
                logger.error(f"Error in sync_monitored_channels loop: {loop_err}")

            await asyncio.sleep(60)  # Re-check DB for pending channels every 60 seconds

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

                if posts is not None and getattr(channel, "status", None) != "JOINED":
                    try:
                        from src.db.models import MonitoredChannel
                        from sqlalchemy import update
                        await session.execute(
                            update(MonitoredChannel)
                            .where(MonitoredChannel.id == channel.id)
                            .values(status="JOINED")
                        )
                        await session.commit()
                    except Exception:
                        pass

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
                        channel_username=channel.username_or_link,
                        db_session=session
                    )
                    await asyncio.sleep(0.05)

                title = (posts_list[0]["chat_title"] if posts_list else None) or channel.title or channel.username_or_link
                
                # Save CollectorLog telemetry entry
                try:
                    from src.db.models import CollectorLog
                    from src.services.process_logger import process_logger
                    
                    if self.scrapers:
                        assigned_idx = abs(hash(target)) % len(self.scrapers)
                        node = self.scrapers[assigned_idx]
                        ub_name = getattr(client, 'user_handle', None) or getattr(node, 'user_handle', None) or f"Pyrogram Userbot #{node.db_id}"
                        worker_tag = f"Userbot: ⚡ {ub_name}"
                    else:
                        worker_tag = "Userbot: 📡 Zero-Auth Web Scraper (25s)"

                    detail_msg = f"{worker_tag} | Проверено: {total_fetched} постов, новых: {new_posts_found}"

                    # Real-time live process terminal ticker emit
                    process_logger.add_log(
                        category="USERBOT" if "Userbot" in worker_tag else "SCRAPER",
                        level="success" if new_posts_found > 0 else "info",
                        title=f"📡 Опрос чата {title} ({target}) — {new_posts_found} новых сообщений",
                        details=detail_msg
                    )

                    # Only insert 0-message check log entries if last check was > 10m ago to prevent log spam
                    cutoff_10m = datetime.now(timezone.utc) - timedelta(minutes=10)
                    recent_log_stmt = select(CollectorLog).where(
                        CollectorLog.username_or_link == target,
                        CollectorLog.created_at >= cutoff_10m
                    ).limit(1)
                    existing_recent = (await session.execute(recent_log_stmt)).scalars().first()

                    if new_posts_found > 0 or not existing_recent:
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

                    # Also clear every 50 full cycles so old posts aren't permanently skipped
                    self._scraper_cycle_count += 1
                    if self._scraper_cycle_count % 50 == 0:
                        processed_posts.clear()
                        logger.info(f"🔄 processed_posts cache cleared at cycle #{self._scraper_cycle_count} (periodic re-evaluation pass).")

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
                        # Cache for process_incoming_message filter (Userbot spam prevention)
                        self.monitored_channels_cache = [{"title": ch.title, "username_or_link": ch.username_or_link} for ch in channels]

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

                await asyncio.sleep(15)  # Relaxed 15-second interval to avoid LLM rate limits

    async def restart_scraper_loop(self):
        logger.info("🔄 Restarting Telegram Public Scraper Loop & Userbot Sync...")
        from datetime import datetime, timezone
        self.last_check_at = datetime.now(timezone.utc)

        if self.public_scraper_task and not self.public_scraper_task.done():
            self.public_scraper_task.cancel()

        self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())

        if getattr(self, 'scout_task', None) and not self.scout_task.done():
            self.scout_task.cancel()
        self.scout_task = asyncio.create_task(self._scout_validator_worker())

        if self.scrapers:
            try:
                await self.sync_monitored_channels()
            except Exception as e:
                logger.warning(f"Userbot channel sync notice during restart: {e}")

        logger.info("✅ Telegram Public Scraper Loop & Userbot restarted successfully.")

    async def _scout_validator_worker(self):
        """Background worker that validates PENDING DiscoveredChats."""
        logger.info("🕵️ AI SCOUT: Validator Worker Started (Checking pending invites...)")
        from src.db.models import DiscoveredChat, MonitoredChannel
        from sqlalchemy import select, update
        from pyrogram.raw.functions.messages import CheckChatInvite
        from pyrogram.raw.types import ChatInviteAlready, ChatInvite, ChatInvitePeek
        import random

        while self._is_running:
            try:
                # Need at least one userbot
                if not self.scrapers:
                    await asyncio.sleep(30)
                    continue
                
                async with AsyncSessionLocal() as session:
                    from sqlalchemy import case
                    stmt = (
                        select(DiscoveredChat)
                        .where(DiscoveredChat.audit_status == "PENDING")
                        .order_by(
                            case(
                                (DiscoveredChat.source == "MASS_IMPORT", 0),
                                else_=1
                            ),
                            DiscoveredChat.discovered_at.asc()
                        )
                        .limit(1)
                    )
                    chat_cand = (await session.execute(stmt)).scalars().first()
                    
                    if not chat_cand:
                        await asyncio.sleep(60) # Sleep if nothing to do
                        continue
                    
                    chat_username = chat_cand.chat_username
                    chat_cand.audit_status = "AUDITING"
                    await session.commit()
                    
                    logger.info(f"🕵️ AI SCOUT: Auditing {chat_username}...")
                    
                    connected_nodes = [n for n in getattr(self, "scrapers", []) if n.app and n.app.is_connected]
                    if not connected_nodes:
                        chat_cand.audit_status = "PENDING"
                        await session.commit()
                        await asyncio.sleep(60)
                        continue
                        
                    node = random.choice(connected_nodes)

                    # Extract hash
                    invite_hash = chat_username.split("/")[-1].replace("+", "")
                    
                    try:
                        # Step 1: Meta-Check
                        is_channel = False
                        participants_count = 0
                        chat_title = chat_cand.title or chat_username
                        
                        if chat_username.startswith("@") or "joinchat" not in chat_username and "+" not in chat_username:
                            # It's a public username
                            chat_info = await node.app.get_chat(chat_username)
                            is_channel = chat_info.type.name == "CHANNEL"
                            participants_count = chat_info.members_count or 1000
                            chat_title = chat_info.title or chat_title
                        else:
                            # It's a private invite link hash
                            invite_info = await node.app.invoke(CheckChatInvite(hash=invite_hash))
                            if isinstance(invite_info, ChatInviteAlready):
                                is_channel = getattr(invite_info.chat, "broadcast", False)
                                participants_count = getattr(invite_info.chat, "participants_count", 1000)
                                chat_title = getattr(invite_info.chat, "title", chat_title)
                            elif isinstance(invite_info, ChatInvite):
                                is_channel = getattr(invite_info, "broadcast", getattr(invite_info, "channel", False))
                                participants_count = getattr(invite_info, "participants_count", 0)
                                chat_title = getattr(invite_info, "title", chat_title)
                        
                        if is_channel or participants_count < 100 or participants_count > 50000:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} (Meta-Fail: Channel={is_channel}, Users={participants_count})")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"Meta-Fail: Channel={is_channel}, Users={participants_count}"
                            await session.commit()
                            continue

                        # Step 2: Math-Audit (Test Run)
                        logger.info(f"🕵️ AI SCOUT: Joining {chat_username} for Math-Audit...")
                        try:
                            chat_obj = await node.app.join_chat(chat_username)
                        except Exception as join_err:
                            logger.warning(f"🕵️ AI SCOUT: Failed to join {chat_username}: {join_err}")
                            chat_cand.audit_status = "FAILED"
                            chat_cand.verdict_reason = f"Join Failed: {join_err}"
                            await session.commit()
                            continue

                        real_chat_id = chat_obj.id
                        chat_title = chat_obj.title or chat_title

                        messages = []
                        try:
                            async for msg in node.app.get_chat_history(real_chat_id, limit=200):
                                if msg.text or msg.caption:
                                    messages.append(msg)
                        except Exception as hist_err:
                            pass

                        if len(messages) < 50:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} (Dead chat, {len(messages)} msgs)")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"Dead chat, only {len(messages)} messages found."
                            try:
                                await node.app.leave_chat(real_chat_id)
                            except: pass
                            await session.commit()
                            continue

                        replies = sum(1 for m in messages if getattr(m, "reply_to_message_id", None))
                        reply_ratio = replies / len(messages)
                        
                        links = sum(1 for m in messages if "http" in (m.text or m.caption or "") or "@" in (m.text or m.caption or ""))
                        link_density = links / len(messages)

                        if reply_ratio < 0.15 or link_density > 0.40:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} (Spam-Board: ReplyRatio={reply_ratio:.2f}, LinkDensity={link_density:.2f})")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"Spam-Board: ReplyRatio={reply_ratio:.2f}, LinkDensity={link_density:.2f}"
                            try:
                                await node.app.leave_chat(real_chat_id)
                            except: pass
                            await session.commit()
                            continue

                        # Step 3: Fast AI Audit
                        logger.info(f"🕵️ AI SCOUT: Math passed for {chat_username}. Running AI Audit...")
                        sample_msgs = random.sample(messages, min(20, len(messages)))
                        sample_text = "\n".join([m.text or m.caption for m in sample_msgs])
                        
                        from src.ai.scorer import evaluate_single_message_groq_json
                        import litellm
                        from src.core.config import settings
                        try:
                            response = await litellm.acompletion(
                                model="groq/llama-3.1-70b-versatile",
                                api_key=settings.GROQ_API_KEY,
                                messages=[
                                    {"role": "system", "content": "Analyze the following 20 Telegram messages. Is this a live human chat (A) or a spam/ad board (B)? Answer ONLY with A or B."},
                                    {"role": "user", "content": sample_text[:3000]}
                                ],
                                max_tokens=10,
                                temperature=0.1
                            )
                            ai_answer = response.choices[0].message.content.strip().upper()
                        except:
                            ai_answer = "A" # Fallback to Math if AI fails

                        if "A" in ai_answer:
                            logger.info(f"🕵️ AI SCOUT: APPROVED {chat_username}! (AI: {ai_answer})")
                            chat_cand.audit_status = "APPROVED"
                            chat_cand.verdict_reason = f"AI Approved ({ai_answer})"
                            chat_cand.title = chat_title
                            
                            # Add to MonitoredChannel
                            existing = (await session.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == chat_username))).scalars().first()
                            if not existing:
                                new_mon = MonitoredChannel(
                                    title=chat_title,
                                    username_or_link=chat_username,
                                    niche_code="community",
                                    location_code="dubai",
                                    status="JOINED"
                                )
                                session.add(new_mon)
                                
                            await session.commit()
                        else:
                            logger.info(f"🕵️ AI SCOUT: Rejected {chat_username} by AI ({ai_answer})")
                            chat_cand.audit_status = "REJECTED"
                            chat_cand.verdict_reason = f"AI Rejected: {ai_answer}"
                            try:
                                await node.app.leave_chat(real_chat_id)
                            except: pass
                            await session.commit()

                    except Exception as e:
                        logger.error(f"🕵️ AI SCOUT: Exception validating {chat_username}: {e}")
                        await session.rollback()
                        chat_cand.audit_status = "FAILED"
                        chat_cand.verdict_reason = f"Exception: {str(e)[:200]}"
                        await session.commit()
                        
            except Exception as outer_e:
                logger.error(f"Error in scout validator loop: {outer_e}")
                
            await asyncio.sleep(45) # Rate limit protection

    async def process_and_score_posts_now(self, channel_obj, posts: List[Dict]):
        """
        Immediately ingests up to 20 recent messages from a newly added channel and runs AI evaluation on them.
        """
        if not posts:
            return
        
        import zlib
        target = getattr(channel_obj, "username_or_link", "") if not isinstance(channel_obj, dict) else channel_obj.get("username_or_link", "")
        ch_title_obj = getattr(channel_obj, "title", None) if not isinstance(channel_obj, dict) else channel_obj.get("title")
        
        det_chat_id = (zlib.crc32((target or "").encode("utf-8")) & 0x7FFFFFFF)
        chat_title = (posts[0].get("chat_title") if posts else None) or ch_title_obj or target

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

            # Emit Watchdog heartbeat event to live process terminal and touch activity timestamp
            try:
                from src.services.process_logger import process_logger
                process_logger.touch()
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

            # 2. Check if scraper loop task crashed or stopped unexpectedly
            if self.public_scraper_task is None or self.public_scraper_task.done():
                exc = None
                try:
                    if self.public_scraper_task and self.public_scraper_task.done():
                        exc = self.public_scraper_task.exception()
                except Exception:
                    pass

                logger.error(f"⚠️ Scanner Watchdog: Public scraper task died/stopped (exc={exc}). Auto-restarting loop now...")
                try:
                    from src.bot.alert_bot import notify_superadmins_system_alert
                    await notify_superadmins_system_alert(
                        f"⚠️ <b>ВНИМАНИЕ: СБОЙ СКАНИРОВАНИЯ!</b>\n\n"
                        f"Фоновая задача сборщика сообщений остановилась: <code>{exc or 'Task stopped'}</code>.\n"
                        f"🔄 <i>Выполняется автоматический экстренный перезапуск сборщика...</i>"
                    )
                except Exception:
                    pass

                self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())
                continue

            last_check = getattr(self, "last_check_at", None) or getattr(self, "last_heartbeat_at", None) or self.last_scraped_at
            if last_check:
                idle_time = (datetime.now(timezone.utc) - last_check).total_seconds()
                if idle_time > 90:  # 90s threshold
                    logger.warning(f"⚠️ Scanner Watchdog Alert: Loop idle for {int(idle_time)}s. Auto-restarting scraper...")
                    try:
                        from src.bot.alert_bot import notify_superadmins_system_alert
                        await notify_superadmins_system_alert(
                            f"⚠️ <b>ВНИМАНИЕ: СБОЙ / ЗАВИСАНИЕ СКАНИРОВАНИЯ!</b>\n\n"
                            f"Опрос каналов остановился на <b>{int(idle_time)} сек</b> (порог: 90с).\n"
                            f"🔄 <i>Запущен автоматический экстренный перезапуск сканера...</i>"
                        )
                    except Exception:
                        pass
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
        """Automated background worker for discovering new Telegram groups. DISABLED by user directive."""
        logger.info("🛑 Automated Telegram Group Discovery Loop is DISABLED by user directive.")
        return

        # --- Priority 1: Geo/Lifestyle community chats (REAL buyer demand lives here) ---
        community_keywords = [
            # Dubai district communities
            "Dubai Marina community", "JLT community", "JVC community chat",
            "Downtown Dubai group", "Business Bay community", "Palm Jumeirah chat",
            "Dubai Hills community", "The Springs Dubai", "Arabian Ranches chat",
            "Al Barsha community", "Jumeirah community", "Mirdif chat",
            "Dubai South community", "Deira community", "Bur Dubai chat",
            # Expat & relocation communities
            "Наши в Дубае", "Дубай Общение", "Русские в Дубае",
            "Expats in Dubai", "Russians in Dubai", "Dubai relocation",
            "Релокация Дубай", "Переезд в Дубай", "UAE expat chat",
            "New to Dubai", "Dubai newcomers",
            # Lifestyle communities with high-income residents
            "Dubai tennis community", "Dubai padel", "Dubai running club",
            "Dubai cycling", "Dubai yacht club", "Dubai fitness",
            "Dubai cars community", "Авто Дубай", "Dubai moms",
            "Dubai families", "Dubai pets", "Dubai foodies",
            # Real demand keywords
            "Дубай аренда", "Дубай жилье", "Dubai rent apartment",
            "Дубай usdt", "Дубай авто", "Дубай работа",
            "Бали аренда", "Бали виллы", "Bali expats",
        ]

        await asyncio.sleep(60)  # 1 minute grace before first pass
        await self._seed_community_chats()  # Pre-seed curated Dubai/Bali community chats

        while self._is_running:
            try:
                from src.services.module_manager import module_manager
                if not module_manager.is_enabled("scout_global_search"):
                    await asyncio.sleep(60)
                    continue
                    
                active_node = next((n for n in self.scrapers if getattr(n, 'app', None) and getattr(n.app, 'is_connected', False)), None)
                if active_node:
                    for kw in community_keywords:
                        try:
                            results = await active_node.app.search_public_chats(kw)
                            async with AsyncSessionLocal() as session:
                                from src.db.models import MonitoredChannel, ChannelCandidate
                                for chat_item in results[:10]:
                                    if getattr(chat_item, "username", None):
                                        uname = f"@{chat_item.username}"
                                        ch_db = (await session.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == uname))).scalar_one_or_none()
                                        if not ch_db:
                                            cand_db = (await session.execute(select(ChannelCandidate).where(ChannelCandidate.username_or_link == uname))).scalar_one_or_none()
                                            if not cand_db:
                                                loc = "bali" if any(b in kw.lower() for b in ["бали", "bali"]) else "dubai"
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
                        await asyncio.sleep(15)  # 15s pacing between keyword searches
            except Exception as d_err:
                logger.error(f"Error in Telegram auto discovery loop: {d_err}")

            await asyncio.sleep(7200)  # Run discovery cycle every 2 hours

    async def _seed_community_chats(self):
        """Pre-seeds curated Dubai community, expat and lifestyle chats into ChannelCandidate on first startup."""
        seed_chats = [
            # === DUBAI DISTRICT COMMUNITY CHATS ===
            ("@dubaimarinachat", "Dubai Marina Community", "dubai"),
            ("@jlt_community", "JLT Community Chat", "dubai"),
            ("@jvc_community", "JVC Community", "dubai"),
            ("@downtowndubai_chat", "Downtown Dubai Community", "dubai"),
            ("@businessbay_chat", "Business Bay Community", "dubai"),
            ("@palmjumeirah_community", "Palm Jumeirah Chat", "dubai"),
            ("@dubaihills_community", "Dubai Hills Community", "dubai"),
            ("@thesprings_dubai", "The Springs Dubai", "dubai"),
            ("@arabianranches_chat", "Arabian Ranches Community", "dubai"),
            ("@albarsha_community", "Al Barsha Community", "dubai"),
            ("@mirdif_chat", "Mirdif Community", "dubai"),
            ("@jumeirah_community", "Jumeirah Community", "dubai"),
            ("@dubaisouth_chat", "Dubai South Community", "dubai"),
            ("@deira_community", "Deira Community", "dubai"),
            ("@dubaimarina_residents", "Dubai Marina Residents", "dubai"),
            ("@jbr_community", "JBR Community Chat", "dubai"),
            ("@discoverygardens_dubai", "Discovery Gardens Dubai", "dubai"),
            ("@siliconoasis_community", "Silicon Oasis Community", "dubai"),
            ("@internationalcity_dubai", "International City Dubai", "dubai"),
            ("@motorscity_dubai", "Motor City Dubai", "dubai"),
            # === EXPAT & RELOCATION CHATS ===
            ("@nashivdubae", "Наши в Дубае", "dubai"),
            ("@dubai_obshhenie", "Дубай Общение", "dubai"),
            ("@russiansindubai", "Russians in Dubai", "dubai"),
            ("@dubai_expats", "Expats in Dubai", "dubai"),
            ("@uae_expats_chat", "UAE Expats Chat", "dubai"),
            ("@dubai_relocation", "Dubai Relocation Community", "dubai"),
            ("@dubai_newcomers", "New to Dubai", "dubai"),
            ("@pereezddubai", "Переезд в Дубай", "dubai"),
            ("@dubai_russian_chat", "Русские в Дубае — Чат", "dubai"),
            ("@uae_chat_ru", "UAE Чат RU", "dubai"),
            # === LIFESTYLE & HIGH-NET-WORTH COMMUNITIES ===
            ("@dubaitennis", "Dubai Tennis Community", "dubai"),
            ("@dubai_padel", "Dubai Padel", "dubai"),
            ("@dubairunners", "Dubai Running Club", "dubai"),
            ("@dubaicycling", "Dubai Cycling", "dubai"),
            ("@dubaifitness", "Dubai Fitness Community", "dubai"),
            ("@dubaicars", "Dubai Cars Community", "dubai"),
            ("@dubai_auto_ru", "Авто Дубай", "dubai"),
            ("@dubaimoms", "Dubai Moms", "dubai"),
            ("@dubaifamilies", "Dubai Families", "dubai"),
            ("@dubaifoodies", "Dubai Foodies", "dubai"),
            ("@dubaipets", "Dubai Pets Community", "dubai"),
            ("@dubai_freelancers", "Dubai Freelancers", "dubai"),
            ("@dubaidigitalnomads", "Dubai Digital Nomads", "dubai"),
            # === BALI COMMUNITY ===
            ("@bali_expats", "Bali Expats", "bali"),
            ("@russiansInBali", "Russians in Bali", "bali"),
            ("@bali_chat_ru", "Бали Чат", "bali"),
            ("@balivillas_rent", "Bali Villas Rent", "bali"),
        ]

        try:
            from src.db.models import MonitoredChannel, ChannelCandidate
            async with AsyncSessionLocal() as session:
                added = 0
                for username, title, loc in seed_chats:
                    ch_db = (await session.execute(select(MonitoredChannel).where(MonitoredChannel.username_or_link == username))).scalar_one_or_none()
                    if not ch_db:
                        cand_db = (await session.execute(select(ChannelCandidate).where(ChannelCandidate.username_or_link == username))).scalar_one_or_none()
                        if not cand_db:
                            session.add(ChannelCandidate(
                                username_or_link=username,
                                title=title,
                                source="SEED_COMMUNITY",
                                location_code=loc,
                                status="DISCOVERED",
                                member_count=0
                            ))
                            added += 1
                await session.commit()
                if added:
                    logger.info(f"🌱 Seeded {added} Dubai/Bali community chats into ChannelCandidate for review.")
        except Exception as seed_err:
            logger.warning(f"Notice seeding community chats: {seed_err}")


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
        
        if not self.scrapers:
            await self.setup()

        if self.scrapers:
            for node in self.scrapers:
                if getattr(node, 'app', None):
                    try:
                        logger.info(f"🚀 Starting Pyrogram Userbot {node.db_id}...")
                        await node.app.start()
                        me = await node.app.get_me()
                        node.user_handle = f"@{me.username}" if me.username else str(me.id)
                        node.status = "CONNECTED"
                        node.last_ping = datetime.now(timezone.utc)
                        logger.info(f"✅ Pyrogram Userbot {node.db_id} connected as {node.user_handle}")
                        
                        # Auto-sync all existing groups/dialogs joined by this userbot into Scout & MonitoredChannels
                        asyncio.create_task(self.sync_userbot_joined_dialogs(node.app))
                    except Exception as e:
                        err_msg = str(e)
                        logger.warning(f"⚠️ Pyrogram Userbot {node.db_id} start error: {err_msg}")
                        node.status = "DISCONNECTED"
                        if any(k in err_msg for k in ["AUTH_KEY_DUPLICATED", "406", "SESSION_REVOKED", "Unauthorized", "AuthKeyUnregistered"]):
                            node.status = "AUTH_ERROR"
                            node.app = None
                            try:
                                if node.db_id > 0:
                                    from src.db.models import ScraperAccount
                                    from sqlalchemy import update
                                    async with AsyncSessionLocal() as session:
                                        await session.execute(update(ScraperAccount).where(ScraperAccount.id == node.db_id).values(status='BANNED', error_log=err_msg))
                                        await session.commit()
                            except Exception:
                                pass
                            
                            try:
                                from src.bot.alert_bot import notify_superadmins_system_alert
                                asyncio.create_task(notify_superadmins_system_alert(
                                    f"❌ <b>КРИТИЧЕСКАЯ ОШИБКА СКАНИРУЮЩЕГО УЗЛА (ID: {node.db_id})</b>\n"
                                    f"───────────────────────────\n\n"
                                    f"⚠️ <b>Сессия юзербота недействительна или забанена Telegram!</b>\n"
                                    f"📄 <b>Причина:</b> <code>{err_msg}</code>\n"
                                    f"💡 <b>Действие:</b> Аккаунт помечен как BANNED и исключен из пула сканеров."
                                ))
                            except Exception:
                                pass
            
            # Auto-sync dialogs and auto-join pending channels with Anti-Ban pacing
            asyncio.create_task(self.sync_monitored_channels())

        self.public_scraper_task = asyncio.create_task(self.run_public_scraper_loop())
        self.watchdog_task = asyncio.create_task(self.run_watchdog_loop())
        self.dead_man_switch_task = asyncio.create_task(self.run_dead_man_switch_loop())
        self.retention_task = asyncio.create_task(self.run_log_retention_cleanup())
        self.discovery_task = asyncio.create_task(self.run_auto_discovery_loop())
        self.ai_batch_worker_task = asyncio.create_task(self._ai_batch_worker())

        # Notify Superadmins on listener startup (Emergency channel alert)
        try:
            from src.bot.alert_bot import notify_superadmins_system_alert
            asyncio.create_task(notify_superadmins_system_alert(
                "⚡ <b>Слушатель запущен.</b> Инициализация и переподключение сокетов Telegram & WebScraper...\n"
                "🛡️ <i>Кнопка мертвеца (Dead Man's Switch) активирована (порог 5 мин).</i>"
            ))
        except Exception as notify_err:
            logger.warning(f"Notice sending listener startup Telegram alert: {notify_err}")

    async def sync_userbot_joined_dialogs(self, app=None) -> int:
        """Disabled auto-import of userbot joined dialogs by user directive."""
        logger.info("🛑 Auto-scanning userbot joined dialogs is DISABLED by user directive.")
        return 0
        target_app = app
        if not target_app:
            for node in self.scrapers:
                if getattr(node, 'app', None) and getattr(node.app, 'is_connected', False):
                    target_app = node.app
                    break
        
        if not target_app:
            logger.info("ℹ️ No connected Pyrogram userbot app for dialogs sync.")
            return 0

        imported_count = 0
        try:
            from pyrogram.enums import ChatType
            from src.db.models import MonitoredChannel, DiscoveredChat
            from sqlalchemy import select

            import re
            SPAM_PATTERNS = [
                r'[\u4e00-\u9fff]',  # Chinese
                r'[\uac00-\ud7af]',  # Korean
                r'trader', r'cricket', r'crypto', r'pump', r'casino', r'baccarat', r'betting',
                r'担保', r'公群', r'开房', r'记录', r'사기'
            ]

            async with AsyncSessionLocal() as session:
                async for dialog in target_app.get_dialogs():
                    chat = dialog.chat
                    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                        chat_title = chat.title or "Telegram Group"
                        username = chat.username
                        username_or_link = f"@{username}" if username else f"https://t.me/c/{str(chat.id).replace('-100', '')}"

                        # Skip Asian / Cricket / Trader / Crypto spam titles
                        if any(re.search(pat, chat_title, re.IGNORECASE) for pat in SPAM_PATTERNS) or \
                           any(re.search(pat, username_or_link, re.IGNORECASE) for pat in SPAM_PATTERNS):
                            continue

                        # Check existing monitored_channels
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
                            u_low = (username_or_link or "").lower()
                            t_low = (chat_title or "").lower()
                            if "phuket" in u_low or "пхукет" in t_low or "phuket" in t_low:
                                inf_loc = "phuket"
                            elif "bali" in u_low or "бали" in t_low or "bali" in t_low:
                                inf_loc = "bali"
                            elif "danang" in u_low or "nhatrang" in u_low or "нячанг" in t_low or "дананг" in t_low:
                                inf_loc = "nhatrang"
                            elif "tbilisi" in u_low or "тбилиси" in t_low:
                                inf_loc = "tbilisi"
                            elif "moscow" in u_low or "москва" in t_low or "мск" in u_low:
                                inf_loc = "moscow"
                            elif "bangkok" in u_low or "бангкок" in t_low or "thailand" in u_low:
                                inf_loc = "thailand"
                            elif "dubai" in u_low or "дубай" in t_low or "uae" in u_low:
                                inf_loc = "dubai"
                            else:
                                inf_loc = "global"

                            new_ch = MonitoredChannel(
                                title=chat_title,
                                username_or_link=username_or_link,
                                niche_code="community",
                                location_code=inf_loc,
                                status="JOINED",
                                created_at=datetime.now(timezone.utc)
                            )
                            session.add(new_ch)

                            # Also register into DiscoveredChat as APPROVED
                            ex_disc = (await session.execute(
                                select(DiscoveredChat).where(DiscoveredChat.chat_username.ilike(username_or_link))
                            )).scalars().first()
                            if not ex_disc:
                                session.add(DiscoveredChat(
                                    chat_username=username_or_link,
                                    title=chat_title,
                                    source="USERBOT_JOINED_AUTO_IMPORT",
                                    audit_status="APPROVED",
                                    score=90,
                                    verdict_reason="Авто-импорт из подписок подключенного юзербота",
                                    location_code="dubai",
                                    detected_niches=["real_estate"],
                                    audited_at=datetime.now(timezone.utc)
                                ))

                            try:
                                await session.commit()
                                imported_count += 1
                            except Exception as db_err:
                                await session.rollback()
                                logger.info(f"Skipping already existing userbot chat: {username_or_link}")

        except Exception as e:
            logger.warning(f"Notice during userbot joined dialogs sync: {e}")

        if imported_count > 0:
            logger.info(f"🎉 AUTO-IMPORTED {imported_count} existing userbot groups directly into Scout & MonitoredChannels!")

        return imported_count

    async def _register_discovered_invite(self, invite_link: str, source_chat: str):
        """Asynchronously registers a captured invite link into DiscoveredChat for AI Scout validation."""
        try:
            from src.db.models import DiscoveredChat
            async with AsyncSessionLocal() as session:
                # Deduplication check
                stmt = select(DiscoveredChat).where(DiscoveredChat.chat_username == invite_link)
                exists = (await session.execute(stmt)).scalars().first()
                if not exists:
                    new_invite = DiscoveredChat(
                        chat_username=invite_link,
                        source="REGEX_EXTRACT",
                        audit_status="PENDING",
                        location_code="dubai",
                        verdict_reason=f"Intercepted from {source_chat}"
                    )
                    session.add(new_invite)
                    await session.commit()
                    logger.info(f"🕵️ AI SCOUT: Intercepted new private invite {invite_link} (Queued for validation).")
        except Exception as e:
            logger.debug(f"Notice saving discovered invite {invite_link}: {e}")

    async def stop(self):
        self._is_running = False
        if self.public_scraper_task:
            self.public_scraper_task.cancel()
        if getattr(self, 'scout_task', None):
            self.scout_task.cancel()
        if self.watchdog_task:
            self.watchdog_task.cancel()
        if hasattr(self, 'dead_man_switch_task') and self.dead_man_switch_task:
            self.dead_man_switch_task.cancel()
        if self.retention_task:
            self.retention_task.cancel()
        if hasattr(self, 'discovery_task') and self.discovery_task:
            self.discovery_task.cancel()
        
        # Stop all scraper clients
        if hasattr(self, 'scrapers'):
            for scraper in self.scrapers:
                try:
                    if scraper.client:
                        await scraper.client.stop()
                except Exception:
                    pass
