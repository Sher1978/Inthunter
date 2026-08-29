import re
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("intent_hunter.vendor_quality")

GARBAGE_STOPWORDS = (
    "крипта", "p2p", "арбитраж", "казино", "ставки", "onlyfans", 
    "заработок в сети", "1000$ в день", "легкий заработок", "18+",
    "100% доход", "инвестиции от 100", "слив тем", "пассивный доход",
    "подписывайтесь на наш канал", "аирдроп", "airdrop", "рефералка"
)

LEAD_TRIGGERS = (
    "ищу", "нужен", "нужна", "нужны", "подскажите", "посоветуйте", "кто знает",
    "кто делает", "сколько стоит", "купим", "требуется", "интересует", "ищем",
    "где найти", "поможет", "консультация", "заказать", "аренда", "сниму",
    "подберите", "порекомендуйте", "почем", "кто может", "где можно",
    "looking for", "need", "rent", "buy", "exchange", "hiring"
)

VENDOR_OFFER_TRIGGERS = (
    "предлагаем", "сдаем", "сдаётся", "сдается", "в наличии", "услуги под ключ",
    "оформление", "гарантия", "доставка", "пишите в лс", "скидки", "прайс",
    "цена:", "стоимость:", "аренда авто", "аренда байка", "обмен валют", "продам"
)

FOREIGN_SCRIPT_PATTERN = re.compile(r'[\u4e00-\u9fff\u0600-\u06FF\u0900-\u097F]')


_DYNAMIC_STOPWORDS = set()

async def refresh_dynamic_stopwords(session):
    global _DYNAMIC_STOPWORDS
    try:
        from src.db.models import DynamicStopword
        from sqlalchemy import select
        res = await session.execute(select(DynamicStopword).where(DynamicStopword.is_active == True))
        _DYNAMIC_STOPWORDS = {s.keyword for s in res.scalars().all()}
    except Exception as e:
        logger.warning(f"Error refreshing stopwords: {e}")

def evaluate_vendor_quality(
    message_text: str,
    is_premium: bool = False,
    username: Optional[str] = None,
    is_reply: bool = False
) -> Tuple[int, str, str]:
    """
    Evaluates message and returns tuple: (VQS_Score: int, Intent_Type: str, Reason: str)
    Intent_Type can be:
      - 'TRASH': Hard drop (stopwords, non-target alphabets, excessive emoji spam)
      - 'LEAD_REQUEST': B2C Buyer Intent ("ищу", "нужен", etc.)
      - 'VENDOR_OFFER': B2B Vendor Offer ("сдаем", "услуги", etc.)
    """
    if not message_text or not message_text.strip():
        return 0, 'TRASH', 'Пустой текст'

    text_lower = message_text.lower()

    # 1. Hard Drop: Check Garbage Stopwords
    for sw in GARBAGE_STOPWORDS:
        if sw in text_lower:
            return 0, 'TRASH', f'Мусорное стоп-слово: {sw}'
            
    for dsw in _DYNAMIC_STOPWORDS:
        if dsw in text_lower:
            return 0, 'TRASH', f'Динамическое стоп-слово (ИИ-Обучение): {dsw}'

    # Check foreign alphabets (Asian/Arabic/Hindi)
    if FOREIGN_SCRIPT_PATTERN.search(message_text):
        return 0, 'TRASH', 'Инородный алфавит'

    # Check excessive emoji spam (>10 emoji symbols penalty / drop)
    emoji_count = len(re.findall(r'[\U00010000-\U0010ffff\u2600-\u27ff🔥🚀👇✅💯‼❗🎯💎⚡]', message_text))
    if emoji_count >= 10:
        return 0, 'TRASH', f'Избыточный эмодзи-спам ({emoji_count} эмодзи)'

    # 2. Check for explicit Vendor Offer (Funnel 2)
    has_vendor_trigger = any(trigger in text_lower for trigger in VENDOR_OFFER_TRIGGERS)

    vqs = 0
    if is_reply:
        vqs += 50
    if is_premium:
        vqs += 30
    if username:
        vqs += 20
    
    has_portfolio_link = any(p in text_lower for p in ["instagram.com/", "t.me/", "http://", "https://", "vk.com/"])
    has_scam_link = any(s in text_lower for s in ["bot", "claim", "airdrop", "ref", "spin"])
    if has_portfolio_link and not has_scam_link:
        vqs += 20

    if emoji_count >= 5:
        vqs -= 30

    if has_vendor_trigger or (has_portfolio_link and len(message_text) > 300) or vqs >= 60:
        intent = 'VENDOR_OFFER' if vqs >= 40 else 'TRASH'
        reason = f"VQS={vqs} ({'Качественный подрядчик' if vqs >= 40 else 'Спам от подрядчика'})"
        return max(0, vqs), intent, reason

    # 3. Default to LEAD_REQUEST for AI Evaluation (Funnel 1)
    # Если это не явный мусор и не явный подрядчик — отправляем на проверку нейросети!
    return 100, 'LEAD_REQUEST', 'Потенциальный лид (Передано на проверку ИИ)'


def calculate_vendor_quality_score(
    text: str,
    username: Optional[str] = None,
    is_reply: bool = False,
    is_premium: bool = False
) -> Dict[str, Any]:
    """
    Backwards-compatible wrapper returning dict for calculate_vendor_quality_score.
    """
    score, intent, reason = evaluate_vendor_quality(
        message_text=text,
        is_premium=is_premium,
        username=username,
        is_reply=is_reply
    )
    should_drop = intent == 'TRASH' or score < 40
    return {
        "score": score,
        "intent_type": intent,
        "should_drop": should_drop,
        "reason": reason
    }
