import re
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("intent_hunter.vendor_quality")

GARBAGE_STOPWORDS = (
    "крипта", "p2p", "арбитраж", "казино", "ставки", "onlyfans", 
    "заработок в сети", "1000$ в день", "легкий заработок", "18+",
    "100% доход", "инвестиции от 100", "слив тем", "пассивный доход"
)

FOREIGN_SCRIPT_PATTERN = re.compile(r'[\u4e00-\u9fff\u0600-\u06FF\u0900-\u097F]')


def calculate_vendor_quality_score(
    text: str,
    username: Optional[str] = None,
    is_reply: bool = False,
    is_premium: bool = False
) -> Dict[str, Any]:
    """
    Calculates Vendor Quality Score (VQS) for commercial service offer messages.
    Returns dict: {"score": int, "should_drop": bool, "reason": str}
    """
    if not text:
        return {"score": 0, "should_drop": True, "reason": "Пустой текст"}

    txt_low = text.lower()

    # 1. Check Garbage Stopwords
    for sw in GARBAGE_STOPWORDS:
        if sw in txt_low:
            return {"score": -100, "should_drop": True, "reason": f"Мусорное стоп-слово: {sw}"}

    # 2. Check Non-target Foreign Alphabets (Chinese, Arabic, Hindi)
    if FOREIGN_SCRIPT_PATTERN.search(text):
        return {"score": -100, "should_drop": True, "reason": "Инородный алфавит (азиатский/арабский)"}

    # 3. Check Emoji Density (Excessive emoji spam)
    emoji_count = len(re.findall(r'[\U00010000-\U0010ffff\u2600-\u27ff]', text))
    letter_count = len(re.findall(r'[a-zA-Zа-яА-ЯёЁ]', text))
    if emoji_count >= 12 and letter_count < 30:
        return {"score": -50, "should_drop": True, "reason": f"Избыточные эмодзи ({emoji_count} эмодзи на {letter_count} букв)"}

    score = 0

    # 🟢 Positive Signals
    if is_reply:
        score += 50  # Contextual Hunter: replied to a real user!

    if is_premium:
        score += 30  # Telegram Premium User

    if username:
        score += 20  # Public @username present

    # Portfolio / Channel link
    has_portfolio_link = any(p in txt_low for p in ["instagram.com/", "behance.net/", "t.me/", "http://", "https://"])
    has_scam_link = any(s in txt_low for s in ["bot", "claim", "airdrop", "ref", "spin"])
    if has_portfolio_link and not has_scam_link:
        score += 20

    # Minimum threshold: VQS >= 40 to pass to LLM Vendor Profiler
    should_drop = score < 40

    return {
        "score": score,
        "should_drop": should_drop,
        "reason": f"VQS={score} ({'Проходит к ИИ' if not should_drop else 'Ниже порога 40'})"
    }
