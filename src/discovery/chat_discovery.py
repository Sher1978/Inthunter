import re
import logging
from typing import List, Optional, Set
from datetime import datetime, timezone
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DiscoveredChat, MonitoredChannel, BlacklistedChat, UserActivityLog
from src.ingestion.public_scraper import PublicTelegramScraper

logger = logging.getLogger(__name__)

# Excluded system keywords / bots
IGNORED_USERNAMES = {
    'telegram', 'joinchat', 'share', 'contact', 'addstickers', 'proxy',
    'c', 's', 'login', 'bot', 'admin', 'help', 'support', 'channel'
}

PERSONAL_PROFILE_SUFFIXES = (
    '_hr', '_recruiter', '_manager', '_admin', '_moderator', '_owner',
    '_ceo', '_contact', '_support', '_help', '_agent', '_realtor',
    '_broker', '_seller', '_boss', '_dev', '_vip', '_lead', '_buyer'
)

SEARCH_KEYWORDS = [
    'Дубай бизнес', 'Dubai real estate', 'Дубай авто аренда', 'Дубай услуги чат',
    'Дубай аренда жилья', 'Dubai expats community', 'Дубай ВНЖ визы', 'Дубай обмен валют',
    'Дубай работа вакансии', 'Dubai USDT exchange', 'Дубай недвижимость продажа', 'Dubai luxury villas',
    'Dubai Marina аренда', 'Business Bay Dubai', 'Бизнес Бэй аренда жилья', 'JLT Dubai chat',
    'ЖЛТ Дубай жилье', 'Downtown Dubai real estate', 'JBR Dubai community', 'Palm Jumeirah villas',
    'Dubai Hills rent', 'Дубай жилье', 'Дубай авто', 'Дубай услуги', 'Дубай коммерция',
    # Family, Moms, Housewives & Children Institutions Search Seeds
    'Дубай мамы', 'Дубай мамочки', 'Дубай родительский чат', 'Дубай дети', 'Дубай семейный чат', 'Дубай домохозяйки',
    'Dubai moms', 'Dubai parents', 'Dubai housewives', 'Dubai family chat', 'Dubai kids',
    'Дубай детский сад', 'Дубай садик', 'Дубай школа', 'Дубай детские центры',
    'Dubai nursery', 'Dubai kindergarten', 'Dubai international school',
    'GEMS Dubai school', 'Nord Anglia Dubai', 'Kings School Dubai', 'Dubai British School', 'Repton School Dubai', 'Jumeirah Primary School', 'Raffles Nursery Dubai'
]


def extract_chats_from_text(text: str) -> List[str]:
    """
    Extracts public Telegram group @usernames from any arbitrary text blob using Regex.
    Filters out bots and personal user profile handles.
    """
    if not text:
        return []

    pattern = r'(?:https?://)?(?:t\.me/|@)([a-zA-Z0-9_]{5,32})'
    matches = re.findall(pattern, text)

    extracted = []
    seen: Set[str] = set()

    for raw in matches:
        clean_u = raw.strip().lower()
        if (
            clean_u
            and not clean_u.endswith('_bot')
            and not clean_u.endswith('bot')
            and '_bot_' not in clean_u
            and not any(clean_u.endswith(sfx) for sfx in PERSONAL_PROFILE_SUFFIXES)
            and clean_u not in IGNORED_USERNAMES
            and len(clean_u) >= 5
        ):
            formatted = f"@{clean_u}"
            if formatted not in seen:
                seen.add(formatted)
                extracted.append(formatted)

    return extracted


async def blacklist_channel_permanently(
    session: AsyncSession,
    username_or_link: str,
    title: Optional[str] = None,
    reason: str = "Отсеян навсегда",
    score: int = 10
) -> Optional[BlacklistedChat]:
    """
    Saves a chat permanently into blacklisted_chats so it can NEVER be recommended or re-added.
    Normalizes username handles (with and without '@').
    """
    if not username_or_link:
        return None

    clean = username_or_link.strip().replace("https://t.me/", "@").replace("http://t.me/", "@")
    if not clean.startswith("@") and not clean.startswith("+") and not clean.isdigit() and len(clean) < 30:
        clean = f"@{clean}"

    variants = [clean, clean.lstrip("@"), f"@{clean.lstrip('@')}"]

    existing = None
    for var in variants:
        res = await session.execute(
            select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(var))
        )
        existing = res.scalars().first()
        if existing:
            break

    if not existing:
        new_blk = BlacklistedChat(
            chat_username=clean,
            reason=f"{title + ' | ' if title else ''}{reason}",
            score=score,
            blacklisted_at=datetime.now(timezone.utc)
        )
        session.add(new_blk)
        try:
            await session.commit()
            logger.info(f"🚫 PERMANENTLY BLACKLISTED chat {clean} ({title or 'no title'}): {reason}")
            return new_blk
        except Exception as err:
            await session.rollback()
            logger.debug(f"Notice blacklisting {clean}: {err}")
            return None
    return existing


async def register_discovered_chat(
    session: AsyncSession,
    username_or_link: str,
    source: str = "PASSIVE_SCAN",
    title: Optional[str] = None,
    location_code: Optional[str] = None,
    platform: str = "telegram"
) -> Optional[DiscoveredChat]:
    """
    Registers a newly discovered chat handle into candidate queue for AI Audit.
    Checks existing MonitoredChannels and BlacklistedChats to prevent duplicates.
    """
    from src.ingestion.platform_detector import detect_platform_and_clean_target
    detected_pl, clean_target = detect_platform_and_clean_target(username_or_link)
    effective_platform = platform if platform != "telegram" else detected_pl

    if not clean_target or len(clean_target) < 3 or clean_target.endswith("_bot") or clean_target in IGNORED_USERNAMES:
        return None

    from src.services.spam_guard import is_spam_or_non_target
    if is_spam_or_non_target(clean_target, title or ""):
        return None

    clean_lower = clean_target.lower()

    # Early reject personal profile handle suffixes for Telegram
    if effective_platform == "telegram" and any(clean_lower.endswith(sfx) for sfx in PERSONAL_PROFILE_SUFFIXES):
        return None

    # Check existing monitored_channels
    m_ch = (await session.execute(
        select(MonitoredChannel).where(
            MonitoredChannel.username_or_link.ilike(clean_target),
            MonitoredChannel.platform == effective_platform
        )
    )).scalars().first()
    if m_ch:
        return None

    # Check blacklisted_chats by handle variants (e.g. @name and name)
    variants = [clean_target, clean_target.lstrip("@"), f"@{clean_target.lstrip('@')}"]
    for var in variants:
        b_ch = (await session.execute(
            select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(var))
        )).scalars().first()
        if b_ch:
            return None

    # Check discovered_chats duplicate
    d_ch = (await session.execute(
        select(DiscoveredChat).where(
            DiscoveredChat.chat_username.ilike(clean_target),
            DiscoveredChat.platform == effective_platform
        )
    )).scalars().first()
    if d_ch:
        return None

    # Create new discovered chat entry
    new_disc = DiscoveredChat(
        chat_username=clean_target,
        title=title or clean_target,
        source=source,
        location_code=location_code or "global",
        platform=effective_platform,
        audit_status="PENDING",
        discovered_at=datetime.now(timezone.utc)
    )
    session.add(new_disc)
    try:
        await session.commit()
        await session.refresh(new_disc)
        logger.info(f"✨ Registered new candidate target for AI Audit: {clean_target} [{effective_platform.upper()}] (Source: {source}, GEO: {location_code})")
        return new_disc
    except Exception as e:
        await session.rollback()
        logger.debug(f"Notice registering candidate {clean_target}: {e}")
        return None


async def run_passive_regex_discovery(session: AsyncSession, limit: int = 300) -> int:
    """
    Scans recent UserActivityLog messages for embedded Telegram group links.
    """
    logs_res = await session.execute(
        select(UserActivityLog.message_text)
        .order_by(UserActivityLog.timestamp.desc())
        .limit(limit)
    )
    texts = list(logs_res.scalars().all())

    found_count = 0
    for txt in texts:
        extracted = extract_chats_from_text(txt)
        for u in extracted:
            res = await register_discovered_chat(session, u, source="REGEX_EXTRACT", platform="telegram")
            if res:
                found_count += 1

    return found_count


async def run_recursive_monitored_channels_mining(session: AsyncSession, limit_channels: int = 30) -> int:
    """
    Recursively mines outgoing Telegram group links mentioned in recent posts of active monitored channels.
    """
    ch_res = await session.execute(
        select(MonitoredChannel.username_or_link)
        .where(MonitoredChannel.status == "JOINED", MonitoredChannel.platform == "telegram")
        .order_by(MonitoredChannel.last_scraped_at.desc().nulls_last())
        .limit(limit_channels)
    )
    channels = list(ch_res.scalars().all())

    scraper = PublicTelegramScraper()
    found_count = 0

    for ch in channels:
        try:
            posts = await scraper.fetch_latest_messages(ch)
            if posts:
                for p in posts:
                    txt = p.get("message_text") or ""
                    extracted = extract_chats_from_text(txt)
                    for u in extracted:
                        res = await register_discovered_chat(session, u, source="RECURSIVE_MENTION", platform="telegram")
                        if res:
                            found_count += 1
        except Exception as e:
            logger.debug(f"Mining notice for channel {ch}: {e}")

    return found_count


async def run_global_keyword_search(session: AsyncSession) -> int:
    """
    Executes active search for target location keywords using Grok AI discovery engine,
    Pyrogram live search (if userbot active), and syncs MTProto candidates across Telegram, VK, OK, and MAX.
    """
    from src.db.models import DiscoveryKeyword, ChannelCandidate
    from src.ai.grok_channel_finder import GrokChannelFinder

    kw_res = await session.execute(
        select(DiscoveryKeyword).where(DiscoveryKeyword.is_active == True)
    )
    active_keywords = [(item.keyword, item.location_code or "global") for item in kw_res.scalars().all()]

    if not active_keywords:
        logger.info("No active discovery keywords configured in DB. Using default location search.")
        active_keywords = [
            ("Dubai Marina Expats", "dubai"),
            ("Business Bay Dubai community", "dubai"),
            ("JLT Dubai chat neighbors", "dubai"),
            ("Дубай Бизнес Клуб предприниматели", "dubai"),
            ("Dubai IT Tech Freelance", "dubai"),
            ("Дубай Аренда Жилья квартиры", "dubai"),
            ("Дубай Авто Аренда трансфер", "dubai"),
            ("Дубай Обмен Валют USDT", "dubai"),
            ("Downtown Dubai residents", "dubai"),
            ("JBR Dubai community", "dubai"),
            ("Palm Jumeirah residents", "dubai"),
            ("Dubai Hills community", "dubai"),
            ("Дубай Работа Вакансии", "dubai"),
            ("Бали Бизнес Комьюнити", "bali"),
            ("Бали Аренда Виллы жилье", "bali")
        ]

    # Collect ALL active monitored, blacklisted, and discovered usernames for AI prompt exclusion
    m_handles = list((await session.execute(select(MonitoredChannel.username_or_link))).scalars().all())
    b_handles = list((await session.execute(select(BlacklistedChat.chat_username))).scalars().all())
    d_handles = list((await session.execute(select(DiscoveredChat.chat_username))).scalars().all())
    exclude_pool = list(set([h.lower().strip() for h in (m_handles + b_handles + d_handles) if h]))

    found_count = 0
    finder = GrokChannelFinder()

    # 1. Sync MTProto Auto-Discovery candidates from ChannelCandidate table into DiscoveredChat
    try:
        cand_res = await session.execute(
            select(ChannelCandidate).where(ChannelCandidate.status == "DISCOVERED").limit(30)
        )
        candidates_db = list(cand_res.scalars().all())
        for c_item in candidates_db:
            res = await register_discovered_chat(
                session,
                c_item.username_or_link,
                source=c_item.source or "RECURSIVE_MENTION",
                title=c_item.title,
                location_code=c_item.location_code or "global",
                platform="telegram"
            )
            if res:
                found_count += 1
            c_item.status = "PROCESSED"
        if candidates_db:
            await session.commit()
    except Exception as sync_err:
        logger.debug(f"ChannelCandidate sync notice: {sync_err}")

    # 2. AI Grok Discovery Cascade per active keyword
    for kw, loc in active_keywords:
        try:
            logger.info(f"🔎 Discovery Engine active search for keyword: '{kw}' (GEO: {loc})...")
            ai_candidates = await finder.search_channels_and_groups(kw, niche_code=loc, limit=15, exclude_usernames=exclude_pool)
            for cand in ai_candidates:
                u_name = cand.get("username")
                if u_name:
                    c_title = cand.get("title") or u_name
                    res = await register_discovered_chat(
                        session,
                        u_name,
                        source="GLOBAL_SEARCH",
                        title=c_title,
                        location_code=loc,
                        platform="telegram"
                    )
                    if res:
                        found_count += 1
        except Exception as e:
            logger.warning(f"Notice during Grok global search for '{kw}': {e}")

    # 3. Live Pyrogram Global Search if Userbot active
    try:
        import sys
        app_module = sys.modules.get("src.api.app")
        ingestor = getattr(app_module, "ingestor", None) if app_module else None
        if ingestor and getattr(ingestor, "app", None) and getattr(ingestor, "_is_running", False):
            logger.info("📡 Userbot active: Querying Pyrogram MTProto global Telegram search...")
            for kw, loc in active_keywords[:5]:
                try:
                    pyro_results = await ingestor.app.search_public_chats(kw)
                    for item in (pyro_results or [])[:5]:
                        u_name = f"@{item.username}" if getattr(item, "username", None) else None
                        if u_name:
                            c_title = getattr(item, "title", None) or u_name
                            res = await register_discovered_chat(
                                session,
                                u_name,
                                source="GLOBAL_SEARCH",
                                title=c_title,
                                location_code=loc,
                                platform="telegram"
                            )
                            if res:
                                found_count += 1
                except Exception as p_err:
                    logger.debug(f"Pyrogram search notice for '{kw}': {p_err}")
    except Exception as p_outer_err:
        logger.debug(f"Userbot MTProto discovery search skipped: {p_outer_err}")

    return found_count


