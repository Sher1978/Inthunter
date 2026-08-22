import logging
from typing import List, Dict, Optional
from src.config import settings

logger = logging.getLogger("intent_hunter.outreach.dialogue_engine")

DIALOGUE_SYSTEM_PROMPT = """Тебя зовут {manager_name}, ты {manager_role} компании LeadRadar.win (платформа мониторинга покупательского спроса и горячих лидов в Telegram).

ТВОЯ РОЛЬ И СТИЛЬ:
- Общайся нативно, вежливо и естественно от первого лица ({manager_name}).
- Ты эксперт по привлечению B2B-клиентов и лидогенерации в Telegram.
- Ответы должны быть четкими (2-4 предложения), не превращайся в навязчивого бота.

КОНТЕКСТ СОБЕСЕДНИКА:
- Ниша бизнеса собеседника: {niche}
- Исходное предложение: «{raw_ad_text}»
- Sales Hook: «{sales_hook}»

ГЛАВНАЯ ЦЕЛЬ ДИАЛОГА:
1. Ответить на вопрос или возражение клиента.
2. Объяснить, как работает LeadRadar.win: наш ИИ-сканер прослушивает 1000+ открытых Telegram-чатов и находит людей с прямым спросом (например: "сниму квартиру", "нужна аренда авто", "обменяю USDT").
3. Убедить клиента зайти на https://leadradar.win, забрать свой стартовый подарок $10 на баланс и забрать первых клиентов прямо сейчас.

ВАЖНО:
Отвечай СТРОГО на русском языке от имени {manager_name}. РАБОТАЙ ИСКЛЮЧИТЕЛЬНО ЧЕРЕЗ GEMINI AI.
"""

async def generate_dialogue_reply(
    user_message: str,
    dialogue_history: List[Dict],
    niche: str,
    raw_ad_text: str,
    sales_hook: str,
    manager_name: str = "Екатерина",
    manager_role: str = "Руководитель B2B развития LeadRadar"
) -> str:
    """
    Generates persona-based dialogue reply to a client DM using STRICTLY Gemini AI.
    """
    if not settings.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is missing! Cannot generate dialogue reply.")
        return f"Здравствуйте! Подробно посмотреть лиды по вашей нише и забрать $10 на баланс можно на нашем сайте https://leadradar.win"

    try:
        from google import genai
        g_client = genai.Client(api_key=settings.GEMINI_API_KEY)

        sys_instruction = DIALOGUE_SYSTEM_PROMPT.format(
            manager_name=manager_name or "Екатерина",
            manager_role=manager_role or "Менеджер развития LeadRadar",
            niche=niche or "бизнес",
            raw_ad_text=(raw_ad_text or "")[:200],
            sales_hook=sales_hook or "услуги"
        )

        # Build message history string
        history_prompt = "ИСТОРИЯ ДИАЛОГА:\n"
        for item in dialogue_history[-6:]:
            role_label = f"{manager_name} ({manager_role})" if item.get("role") == "manager" else "Клиент"
            history_prompt += f"{role_label}: {item.get('text', '')}\n"

        history_prompt += f"\nКлиент только что написал: «{user_message}»\n\nОтветь клиенту от лица {manager_name}:"

        res = g_client.models.generate_content(
            model=settings.GEMINI_MODEL or "gemini-2.5-flash",
            contents=f"{sys_instruction}\n\n{history_prompt}"
        )
        reply = res.text.strip() if res and res.text else ""

        if reply:
            return reply

    except Exception as e:
        logger.error(f"Error in Gemini dialogue generation: {e}")

    return f"Спасибо за отклик! Вы можете прямо сейчас зарегистрироваться на https://leadradar.win и забрать $10 на стартовый баланс, чтобы посмотреть горячих клиентов в нише {niche}."
