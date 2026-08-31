import re
import logging
from sqlalchemy import select, delete
from src.db.session import AsyncSessionLocal
from src.db.models import MonitoredChannel, DiscoveredChat, ChannelCandidate

logger = logging.getLogger("intent_hunter.spam_guard")

# 1. Non-target scripts & spam keywords
SPAM_PATTERNS = [
    r'[\u4e00-\u9fff]',  # Chinese characters
    r'[\uac00-\ud7af]',  # Korean characters
    r'[\u0600-\u06ff]',  # Arabic non-GEO spam characters
    r'trader', r'cricket', r'crypto', r'pump', r'casino', r'baccarat', r'betting', r'gambling',
    r'担保', r'公群', r'开房', r'记录', r'사기', r'骗子', r'套路', r'柬埔寨',
    r'movie', r'movies', r'bollywood', r'free-content', r'free_content', r'18\+', r'adult', r'erotic', r'porn', r'sinner'
]

# 2. Strict Non-Dubai GEO Rejection (Explicitly exclude other cities/countries)
NON_DUBAI_GEO_PATTERNS = [
    r'нячанг', r'nhatrang', r'вьетнам', r'vietnam',
    r'бали', r'bali', r'индонезия', r'indonesia',
    r'пхукет', r'phuket', r'патайя', r'pattaya', r'тайланд', r'thailand',
    r'тбилиси', r'tbilisi', r'грузия', r'georgia',
    r'ереван', r'yerevan', r'армения',
    r'стамбул', r'istanbul', r'анталья', r'antalya', r'турция', r'turkey',
    r'сербия', r'белград', r'кипр', r'cyprus',
    r'черногория', r'montenegro',
    r'москва', r'moscow', r'питер', r'spb', r'петербург',
    r'минск', r'minsk', r'алматы', r'almaty', r'астана', r'astana', r'ташкент', r'tashkent'
]

# 3. Dubai / UAE GEO Matching Keywords
DUBAI_GEO_PATTERNS = [
    r'dubai', r'дубай', r'дубае', r'дубая', r'дубаю', r'дубаем',
    r'uae', r'оаэ', r'эмираты', r'emirates', r'dxb',
    r'marina', r'jlt', r'jvc', r'downtown', r'business bay', r'palm jumeirah',
    r'barsha', r'deira', r'bur dubai', r'difc', r'ras al khaimah', r'rak',
    r'abu dhabi', r'абу даби', r'sharjah', r'шарджа', r'ajman', r'аджман'
]

def is_spam_or_non_target(username_or_link: str, title: str = "") -> bool:
    """
    Returns True if the channel contains:
    1. Asian spam scripts / Adult / Betting / Crypto / Movies.
    2. Non-Dubai GEOs (Nha Trang, Bali, Phuket, Moscow, Georgia, Turkey, etc.).
    """
    text = f"{username_or_link or ''} {title or ''}".lower()

    if any(re.search(pat, text, re.IGNORECASE) for pat in SPAM_PATTERNS):
        return True

    if any(re.search(pat, text, re.IGNORECASE) for pat in NON_DUBAI_GEO_PATTERNS):
        return True

    return False

def has_dubai_geo_relevance(username_or_link: str, title: str = "") -> bool:
    """Returns True if the candidate explicitly mentions Dubai / UAE or relevant local keywords."""
    text = f"{username_or_link or ''} {title or ''}".lower()
    if is_spam_or_non_target(username_or_link, title):
        return False
    return any(re.search(pat, text, re.IGNORECASE) for pat in DUBAI_GEO_PATTERNS)

async def purge_all_database_spam():
    """Purges all non-target / non-Dubai channels directly from PostgreSQL on Railway."""
    try:
        async with AsyncSessionLocal() as session:
            # 1. Purge MonitoredChannel
            res_m = await session.execute(select(MonitoredChannel))
            mons = list(res_m.scalars().all())
            del_mons = [m.id for m in mons if is_spam_or_non_target(m.username_or_link, m.title)]

            if del_mons:
                for i in range(0, len(del_mons), 500):
                    batch = del_mons[i:i+500]
                    await session.execute(delete(MonitoredChannel).where(MonitoredChannel.id.in_(batch)))
                await session.commit()
                logger.info(f"🧹 SPAM GUARD: Purged {len(del_mons)} non-Dubai/spam channels from MonitoredChannel table!")

            # 2. Purge DiscoveredChat
            res_d = await session.execute(select(DiscoveredChat))
            discs = list(res_d.scalars().all())
            del_discs = [d.id for d in discs if is_spam_or_non_target(d.chat_username, d.title)]

            if del_discs:
                for i in range(0, len(del_discs), 500):
                    batch = del_discs[i:i+500]
                    await session.execute(delete(DiscoveredChat).where(DiscoveredChat.id.in_(batch)))
                await session.commit()
                logger.info(f"🧹 SPAM GUARD: Purged {len(del_discs)} non-Dubai/spam items from DiscoveredChat table!")

            # 3. Purge ChannelCandidate
            res_c = await session.execute(select(ChannelCandidate))
            cands = list(res_c.scalars().all())
            del_cands = [c.id for c in cands if is_spam_or_non_target(c.username_or_link, c.title)]

            if del_cands:
                for i in range(0, len(del_cands), 500):
                    batch = del_cands[i:i+500]
                    await session.execute(delete(ChannelCandidate).where(ChannelCandidate.id.in_(batch)))
                await session.commit()
                logger.info(f"🧹 SPAM GUARD: Purged {len(del_cands)} non-Dubai candidates from ChannelCandidate table!")
    except Exception as e:
        logger.error(f"Spam Guard DB Purge notice: {e}")
