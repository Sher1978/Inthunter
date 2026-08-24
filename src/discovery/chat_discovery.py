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
    'Дубай бизнес', 'Dubai real estate', 'Дубай авто', 'Нячанг услуги',
    'Дубай аренда', 'Dubai expats', 'Дубай ВНЖ', 'Нячанг бизнес',
    'Пхукет чат', 'Бали бизнес', 'Дананг услуги'
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


async def register_discovered_chat(
    session: AsyncSession,
    username_or_link: str,
    source: str = "REGEX_EXTRACT",
    title: Optional[str] = None,
    location_code: str = "global",
    platform: str = "telegram"
) -> Optional[DiscoveredChat]:
    """
    Checks if a target handle exists in monitored_channels, blacklisted_chats, or discovered_chats.
    If not already in DB, creates a new DiscoveredChat entry in PENDING status.
    """
    from src.db.models import UserProfile, MonitoredChannel, BlacklistedChat

    from src.ingestion.platform_detector import detect_platform_and_clean_target
    detected_pl, clean_target = detect_platform_and_clean_target(username_or_link)
    effective_platform = platform if platform != "telegram" else detected_pl

    if not clean_target or len(clean_target) < 3 or clean_target.endswith("_bot") or clean_target in IGNORED_USERNAMES:
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

    # Check blacklisted_chats
    b_ch = (await session.execute(
        select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(clean_target))
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
    Executes active search for target location keywords across Telegram, VK, OK, and MAX Messenger.
    """
    from src.db.models import DiscoveryKeyword
    from src.ingestion.vk_ok_scrapers import VKPublicScraper, OKPublicScraper, MAXPublicScraper

    kw_res = await session.execute(
        select(DiscoveryKeyword).where(DiscoveryKeyword.is_active == True)
    )
    active_keywords = [(item.keyword, item.location_code or "global") for item in kw_res.scalars().all()]

    if not active_keywords:
        logger.info("No active discovery keywords configured in DB.")
        return 0

    tg_scraper = PublicTelegramScraper()
    found_count = 0

    for kw, loc in active_keywords:
        try:
            slug = kw.lower().replace(' ', '_').replace('дубай', 'dubai').replace('нячанг', 'nhatrang').replace('бизнес', 'biz').replace('услуги', 'services').replace('аренда', 'rent')
            slug_clean = re.sub(r'[^a-zA-Z0-9_]', '', slug)
            candidates = [
                f"{slug_clean}", f"{slug_clean}_chat", f"{slug_clean}_group",
                f"{slug_clean}_community", f"{slug_clean}_market"
            ]

            for cand in candidates:
                # 1. Telegram Check
                tg_target = f"@{cand}"
                tg_posts = await tg_scraper.fetch_latest_messages(tg_target)
                if tg_posts:
                    title = tg_posts[0].get("chat_title") or tg_target
                    res = await register_discovered_chat(session, tg_target, source="GLOBAL_SEARCH", title=title, location_code=loc, platform="telegram")
                    if res:
                        found_count += 1

                # 2. VK Check
                vk_posts = await VKPublicScraper.fetch_latest_messages(cand)
                if vk_posts:
                    title = vk_posts[0].get("chat_title") or cand
                    res = await register_discovered_chat(session, cand, source="GLOBAL_SEARCH", title=title, location_code=loc, platform="vk")
                    if res:
                        found_count += 1

                # 3. OK Check
                ok_posts = await OKPublicScraper.fetch_latest_messages(cand)
                if ok_posts:
                    title = ok_posts[0].get("chat_title") or cand
                    res = await register_discovered_chat(session, cand, source="GLOBAL_SEARCH", title=title, location_code=loc, platform="ok")
                    if res:
                        found_count += 1

                # 4. MAX Check
                max_posts = await MAXPublicScraper.fetch_latest_messages(cand)
                if max_posts:
                    title = max_posts[0].get("chat_title") or cand
                    res = await register_discovered_chat(session, cand, source="GLOBAL_SEARCH", title=title, location_code=loc, platform="max")
                    if res:
                        found_count += 1

        except Exception as e:
            logger.warning(f"Notice during multi-platform global search for '{kw}': {e}")

    return found_count


