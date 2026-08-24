import logging
from typing import Optional, List, Dict
from src.config import settings

logger = logging.getLogger("intent_hunter.outreach.message_generator")

SYSTEM_PROMPT_TEMPLATE = """Меня зовут {manager_name}, я {manager_role} в платформе LeadRadar.win.
Твоя задача — составить короткое, вежливое и нативное личное первое сообщение в Telegram (строго 3 предложения) для потенциального B2B-партнера/продавца услуг.

Правила:
1. Сообщение должно звучать нативно от первого лица ({manager_name}), без спам-клише.
2. Сообщи, что обратил(а) внимание на их рекламную активность в Telegram по теме «{sales_hook}».
3. Учти их недавние посты и предложения: ({history_summary}).
4. Отмети, что у нас на платформе LeadRadar за последний час зашли целевые покупатели в нише {niche}.
5. Предложи получить первых 5-10 клиентов с подарочным балансом $10 на https://leadradar.win.
"""

async def generate_outreach_dm(
    username: str,
    niche: str,
    raw_ad_text: str,
    sales_hook: str,
    manager_name: str = "Екатерина",
    manager_role: str = "Руководитель B2B развития LeadRadar",
    messages_history: Optional[List[Dict]] = None
) -> str:
    """
    Generates a personalized, AI-crafted outreach DM for a B2B prospect using account persona and message history.
    """
    history_summary = "ваши публикации по услугам"
    if messages_history and len(messages_history) > 0:
        past_texts = [m.get("message_text", "") for m in messages_history[-3:] if m.get("message_text")]
        if past_texts:
            history_summary = " | ".join(past_texts)[:200]

    sys_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        manager_name=manager_name or "Екатерина",
        manager_role=manager_role or "Менеджер развития LeadRadar",
        sales_hook=sales_hook or "услуги",
        history_summary=history_summary,
        niche=niche or "бизнес"
    )

    user_prompt = f"""Context:
Recipient: @{username or 'партнер'}
Niche: {niche}
Captured Offer: "{raw_ad_text[:200]}"
Recent Posts History: "{history_summary}"
Sales Hook: "{sales_hook}"

Сгенерируй 3 предложения нативного первого сообщения от {manager_name}.
"""

    # 1. Try Gemini LLM
    try:
        if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.startswith("AIzaSy"):
            from google import genai
            g_client = genai.Client(api_key=settings.GEMINI_API_KEY)
            g_model = settings.GEMINI_MODEL or "gemini-2.5-flash"
            res = g_client.models.generate_content(
                model=g_model,
                contents=f"{sys_prompt}\n\n{user_prompt}"
            )
            text = res.text.strip() if res and res.text else ""
            if text and len(text) >= 20:
                logger.info(f"✅ Generated outreach DM via Gemini ({g_model})")
                return text
    except Exception as e:
        logger.warning(f"Gemini outreach generation notice: {e}")

    # 2. Try Groq Cloud LLM Multi-Key Pool & Active Models
    try:
        import re
        from groq import AsyncGroq
        raw_keys = (getattr(settings, "GROQ_API_KEYS", "") or "") + "," + (settings.GROQ_API_KEY or "")
        key_pool = [k.strip() for k in re.split(r'[,\s\n]+', raw_keys) if k.strip().startswith("gsk_")]
        key_pool = list(dict.fromkeys(key_pool))

        if key_pool:
            models_to_try = [settings.GROQ_MODEL, "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama3-70b-8192", "mixtral-8x7b-32768"]
            candidate_models = []
            for m in models_to_try:
                if m and m not in candidate_models:
                    candidate_models.append(m)

            for key in key_pool:
                key_suffix = key[-4:]
                try:
                    client = AsyncGroq(api_key=key, max_retries=0, timeout=8.0)
                    for model_name in candidate_models:
                        try:
                            resp = await client.chat.completions.create(
                                model=model_name,
                                messages=[
                                    {"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": user_prompt}
                                ],
                                temperature=0.7,
                                max_tokens=250
                            )
                            text = resp.choices[0].message.content.strip()
                            if text and len(text) >= 20:
                                logger.info(f"✅ Generated outreach DM via Groq Key (...{key_suffix}) Model ({model_name})")
                                return text
                        except Exception as m_err:
                            logger.debug(f"Groq model {model_name} notice on key ...{key_suffix}: {m_err}")
                except Exception as k_err:
                    logger.debug(f"Groq key ...{key_suffix} notice: {k_err}")
    except Exception as e:
        logger.warning(f"Groq outreach generation fallback notice: {e}")

    # 3. Fallback Template
    clean_hook = sales_hook or "предоставление услуг"
    return (
        f"Здравствуйте! Меня зовут {manager_name}, я {manager_role}. "
        f"Обратила внимание на ваши публикации по теме «{clean_hook}» — у нас в LeadRadar за последний час зашли целевые клиенты по вашей нише. "
        f"Дарим вам $10 на стартовый баланс для мгновенного получения контактов — забирайте на https://leadradar.win"
    )
