import re
import logging
from typing import Dict, Any, Optional, Tuple, Set

logger = logging.getLogger("intent_hunter.vendor_quality")

GARBAGE_STOPWORDS = (
    "крипта", "p2p", "арбитраж", "казино", "ставки", "onlyfans",
    "заработок в сети", "1000$ в день", "легкий заработок", "18+",
    "100% доход", "инвестиции от 100", "слив тем", "пассивный доход",
    "подписывайтесь на наш канал", "аирдроп", "airdrop", "рефералка"
)

# \u4e00-\u9fff (Chinese/Kanji), \u0600-\u06FF (Arabic), \u0900-\u097F (Hindi), \u3040-\u309F (Hiragana), \u30A0-\u30FF (Katakana), \uAC00-\uD7AF (Hangul)
FOREIGN_SCRIPT_PATTERN = re.compile(r'[\u4e00-\u9fff\u0600-\u06FF\u0900-\u097F\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7AF]')

_DYNAMIC_STOPWORDS: Set[str] = set()

# Whitelist kept for backward compatibility with auditor
_VQS_WHITELIST_PATTERNS: Set[str] = set()

async def refresh_dynamic_stopwords(session):
    global _DYNAMIC_STOPWORDS
    try:
        from src.db.models import DynamicStopword
        from sqlalchemy import select
        res = await session.execute(select(DynamicStopword).where(DynamicStopword.is_active == True))
        _DYNAMIC_STOPWORDS = {s.keyword for s in res.scalars().all()}
    except Exception as e:
        logger.warning(f"Error refreshing stopwords: {e}")

def add_to_vqs_whitelist(pattern: str):
    _VQS_WHITELIST_PATTERNS.add(pattern.lower().strip())
    logger.info(f"✅ VQS Whitelist updated: added '{pattern}'")

def get_vqs_whitelist() -> Set[str]:
    return _VQS_WHITELIST_PATTERNS.copy()

def evaluate_vendor_quality(
    message_text: str,
    is_premium: bool = False,
    username: Optional[str] = None,
    is_reply: bool = False
) -> Tuple[int, str, str]:
    """
    AI-FIRST ARCHITECTURE:
    Evaluates message and returns tuple: (VQS_Score: int, Intent_Type: str, Reason: str)
    Intent_Type can be:
      - 'TRASH': Hard drop (stopwords, non-target alphabets, excessive emoji spam)
      - 'LEAD_REQUEST': Sent to AI for deep semantic parsing (Buyer, Seller, HR, Seeker)
    """
    if not message_text or not message_text.strip():
        return 0, 'TRASH', 'Пустой текст'

    text_lower = message_text.lower()

    for wp in _VQS_WHITELIST_PATTERNS:
        if wp in text_lower:
            return 100, 'LEAD_REQUEST', f'VQS Whitelist: {wp}'

    # 1. Hard Drop: Check Garbage Stopwords
    for sw in GARBAGE_STOPWORDS:
        if sw in text_lower:
            return 0, 'TRASH', f'Мусорное стоп-слово: {sw}'

    for dsw in _DYNAMIC_STOPWORDS:
        if dsw in text_lower:
            return 0, 'TRASH', f'Динамическое стоп-слово (ИИ-Обучение): {dsw}'

    # Check foreign alphabets (Asian/Arabic/Hindi)
    if FOREIGN_SCRIPT_PATTERN.search(message_text):
        return 0, 'TRASH', 'Инородный алфавит (Азиатский/Арабский)'

    # Check excessive emoji spam (>10 emoji symbols penalty / drop)
    emoji_count = len(re.findall(r'[\U00010000-\U0010ffff\u2600-\u27ff🔥🚀👇✅💯‼❗🎯💎⚡]', message_text))
    if emoji_count >= 10:
        return 0, 'TRASH', f'Избыточный эмодзи-спам ({emoji_count} эмодзи)'

    # All non-garbage messages are forwarded to the AI Router/Scorer
    return 100, 'LEAD_REQUEST', 'Потенциальный лид/В2В партнер (Передано на проверку ИИ)'

def calculate_vendor_quality_score(
    text: str,
    username: Optional[str] = None,
    is_reply: bool = False,
    is_premium: bool = False
) -> Dict[str, Any]:
    score, intent, reason = evaluate_vendor_quality(
        message_text=text,
        is_premium=is_premium,
        username=username,
        is_reply=is_reply
    )
    should_drop = intent == 'TRASH'
    return {
        "score": score,
        "intent_type": intent,
        "should_drop": should_drop,
        "reason": reason
    }
