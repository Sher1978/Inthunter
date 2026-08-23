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

SEARCH_KEYWORDS = [
    'Дубай бизнес', 'Dubai real estate', 'Дубай авто', 'Нячанг услуги',
    'Дубай аренда', 'Dubai expats', 'Дубай ВНЖ', 'Нячанг бизнес',
    'Пхукет чат', 'Бали бизнес', 'Дананг услуги'
]


def extract_chats_from_text(text: str) -> List[str]:
    """
    Extracts public Telegram group @usernames from any arbitrary text blob using Regex.
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
    title: Optional[str] = None
) -> Optional[DiscoveredChat]:
    """
    Checks if a username exists in monitored_channels, blacklisted_chats, or discovered_chats.
    If not, creates a new DiscoveredChat entry in PENDING audit status.
    """
    clean_u = username_or_link.strip()
    if not clean_u.startswith("@") and not clean_u.startswith("http"):
        clean_u = f"@{clean_u}"

    clean_lower = clean_u.lower()

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
        audit_status="PENDING",
        discovered_at=datetime.now(timezone.utc)
    )
    session.add(new_disc)
    await session.commit()
    await session.refresh(new_disc)

    logger.info(f"✨ Registered new candidate chat for AI Audit: {clean_u} (Source: {source})")
    return new_disc


async def run_passive_regex_discovery(session: AsyncSession, limit: int = 200) -> int:
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


async def run_global_keyword_search(session: AsyncSession) -> int:
    """
    Executes keyword-based active discovery via PublicTelegramScraper.
    """
    scraper = PublicTelegramScraper()
    found_count = 0

    for kw in SEARCH_KEYWORDS:
        try:
            # Generate potential slug from keyword
            slug = kw.lower().replace(' ', '_').replace('дубай', 'dubai').replace('нячанг', 'nhatrang').replace('бизнес', 'biz')
            candidate_usernames = [f"@{slug}", f"@{slug}_chat", f"@{slug}_group", f"@{slug}_community"]

            for u in candidate_usernames:
                posts = await scraper.fetch_latest_messages(u)
                if posts:
                    title = posts[0].get("chat_title") or u
                    res = await register_discovered_chat(session, u, source="GLOBAL_SEARCH", title=title)
                    if res:
                        found_count += 1
        except Exception as e:
            logger.warning(f"Notice during global search for '{kw}': {e}")

    return found_count
