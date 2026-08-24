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
    location_code: str = "global"
) -> Optional[DiscoveredChat]:
    """
    Checks if a username exists in monitored_channels, blacklisted_chats, discovered_chats, or user_profiles.
    If not a personal user and not already in DB, creates a new DiscoveredChat entry in PENDING status.
    """
    from src.db.models import UserProfile

    raw_clean = username_or_link.strip().replace("https://t.me/", "").replace("http://t.me/", "").lstrip("@")
    if not raw_clean or len(raw_clean) < 5 or raw_clean.endswith("_bot") or raw_clean in IGNORED_USERNAMES:
        return None

    clean_lower = raw_clean.lower()

    # Early reject personal profile handle suffixes
    if any(clean_lower.endswith(sfx) for sfx in PERSONAL_PROFILE_SUFFIXES):
        return None

    clean_u = f"@{raw_clean}"

    # Check if username belongs to an existing personal UserProfile in our database
    u_prof = (await session.execute(
        select(UserProfile).where(UserProfile.username.ilike(clean_lower))
    )).scalar_one_or_none()
    if u_prof:
        return None

    # Check existing monitored_channels
    m_ch = (await session.execute(
        select(MonitoredChannel).where(MonitoredChannel.username_or_link.ilike(clean_lower))
    )).scalar_one_or_none()
    if m_ch:
        return None

    # Check blacklisted_chats
    b_ch = (await session.execute(
        select(BlacklistedChat).where(BlacklistedChat.chat_username.ilike(clean_lower))
    )).scalar_one_or_none()
    if b_ch:
        return None

    # Check discovered_chats duplicate
    d_ch = (await session.execute(
        select(DiscoveredChat).where(DiscoveredChat.chat_username.ilike(clean_lower))
    )).scalar_one_or_none()
    if d_ch:
        return None

    # Create new discovered chat entry
    new_disc = DiscoveredChat(
        chat_username=clean_u,
        title=title or clean_u,
        source=source,
        location_code=location_code or "global",
        audit_status="PENDING",
        discovered_at=datetime.now(timezone.utc)
    )
    session.add(new_disc)
    try:
        await session.commit()
        await session.refresh(new_disc)
        logger.info(f"✨ Registered new candidate chat for AI Audit: {clean_u} (Source: {source}, GEO: {location_code})")
        return new_disc
    except Exception as e:
        await session.rollback()
        logger.debug(f"Notice registering candidate {clean_u}: {e}")
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
            res = await register_discovered_chat(session, u, source="REGEX_EXTRACT")
            if res:
                found_count += 1

    return found_count


async def run_recursive_monitored_channels_mining(session: AsyncSession, limit_channels: int = 30) -> int:
    """
    Recursively mines outgoing Telegram group links mentioned in recent posts of active monitored channels.
    """
    ch_res = await session.execute(
        select(MonitoredChannel.username_or_link)
        .where(MonitoredChannel.status == "JOINED")
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
                        res = await register_discovered_chat(session, u, source="RECURSIVE_MENTION")
                        if res:
                            found_count += 1
        except Exception as e:
            logger.debug(f"Mining notice for channel {ch}: {e}")

    return found_count


async def run_global_keyword_search(session: AsyncSession) -> int:
    """
    Executes active search and directory crawling for target location keywords from DiscoveryKeyword table.
    """
    from src.db.models import DiscoveryKeyword
    kw_res = await session.execute(
        select(DiscoveryKeyword).where(DiscoveryKeyword.is_active == True)
    )
    active_keywords = [(item.keyword, item.location_code or "global") for item in kw_res.scalars().all()]

    if not active_keywords:
        logger.info("No active discovery keywords configured in DB.")
        return 0

    scraper = PublicTelegramScraper()
    found_count = 0

    for kw, loc in active_keywords:
        try:
            slug = kw.lower().replace(' ', '_').replace('дубай', 'dubai').replace('нячанг', 'nhatrang').replace('бизнес', 'biz').replace('услуги', 'services').replace('аренда', 'rent')
            slug_clean = re.sub(r'[^a-zA-Z0-9_]', '', slug)
            candidate_usernames = [
                f"@{slug_clean}", f"@{slug_clean}_chat", f"@{slug_clean}_group",
                f"@{slug_clean}_community", f"@{slug_clean}_b2b", f"@{slug_clean}_market"
            ]

            for u in candidate_usernames:
                posts = await scraper.fetch_latest_messages(u)
                if posts:
                    title = posts[0].get("chat_title") or u
                    res = await register_discovered_chat(session, u, source="GLOBAL_SEARCH", title=title, location_code=loc)
                    if res:
                        found_count += 1
        except Exception as e:
            logger.warning(f"Notice during global search for '{kw}': {e}")

    return found_count


