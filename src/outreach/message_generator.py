import logging
from typing import Optional
from src.config import settings

logger = logging.getLogger("intent_hunter.outreach.message_generator")

SYSTEM_PROMPT = """Ты — менеджер по работе с партнерами платформы LeadRadar.win.
Твоя задача — составить короткое, вежливое и нативное личное сообщение в Telegram (3 предложения) для владельца бизнеса/риелтора/продавца услуг.

Правила:
1. Сообщение должно быть органичным, без хэштегов и без навязчивого спам-стиля.
2. Сообщи, что видел их объявление по тематике {sales_hook}.
3. Упомяни, что в нашей системе LeadRadar за последний час появились целевые покупатели по их нише ({niche}).
4. Предложи попробовать бесплатный стартовый баланс $10 для получения первых контактов на https://leadradar.win.
5. Пиши от первого лица, дружелюбно и емко (3 предложения).
"""

async def generate_outreach_dm(username: str, niche: str, raw_ad_text: str, sales_hook: str) -> str:
    """
    Generates a personalized, AI-crafted outreach DM for a B2B prospect.
    """
    user_prompt = f"""Context:
Recipient Username: @{username or 'клиент'}
Niche: {niche}
Ad Text Captured: "{raw_ad_text[:250]}"
Sales Hook: "{sales_hook}"

Сгенерируй 3 предложения нативного первого сообщения в Telegram.
"""
    # 1. Try Groq Cloud LLM
    try:
        if settings.GROQ_API_KEY:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            resp = await client.chat.completions.create(
                model=settings.GROQ_MODEL or "llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT.format(sales_hook=sales_hook or 'услуги', niche=niche or 'бизнес')},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=250
            )
            text = resp.choices[0].message.content.strip()
            if text and len(text) >= 20:
                return text
    except Exception as e:
        logger.warning(f"Groq outreach generation fallback: {e}")

    # 2. Try Gemini LLM
    try:
        if settings.GEMINI_API_KEY:
            from google import genai
            g_client = genai.Client(api_key=settings.GEMINI_API_KEY)
            res = g_client.models.generate_content(
                model=settings.GEMINI_MODEL or "gemini-2.5-flash",
                contents=f"{SYSTEM_PROMPT.format(sales_hook=sales_hook or 'услуги', niche=niche or 'бизнес')}\n\n{user_prompt}"
            )
            text = res.text.strip()
            if text and len(text) >= 20:
                return text
    except Exception as e:
        logger.warning(f"Gemini outreach generation fallback: {e}")

    # 3. Deterministic high-converting template fallback
    clean_hook = sales_hook or "предоставление услуг"
    return (
        f"Здравствуйте! Видел ваше объявление по теме «{clean_hook}» в Telegram. "
        f"У нас на платформе LeadRadar за последний час зашли целевые клиенты по вашей нише. "
        f"Дарим вам $10 на стартовый баланс для получения первых контактов — активируйте на https://leadradar.win"
    )
