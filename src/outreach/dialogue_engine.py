import logging
from typing import List, Dict, Optional
from src.config import settings

logger = logging.getLogger("intent_hunter.outreach.dialogue_engine")

DEFAULT_DIALOGUE_SYSTEM_PROMPT = """Тебя зовут {manager_name}, ты {manager_role}{company_context}.

ТВОЯ РОЛЬ И СТИЛЬ:
- Общайся нативно, вежливо и естественно от первого лица ({manager_name}).
- Ты опытный менеджер и эксперт в сфере {niche}.
- Ответы должны быть четкими, лаконичными (2-4 предложения), не превращайся в навязчивого бота.
- Не используй штампы вроде «Я ИИ-ассистент» или шаблонные приветствия на каждое сообщение.

КОНТЕКСТ СОБЕСЕДНИКА:
- Ниша бизнеса собеседника: {niche}
- Исходное предложение: «{raw_ad_text}»
- Sales Hook: «{sales_hook}»

ГЛАВНАЯ ЦЕЛЬ ДИАЛОГА:
1. Вежливо ответить на вопрос или возражение клиента.
2. Опираться на факты из базы знаний продукта.
3. Мягко вести диалог к следующему шагу (уточнение потребностей, звонок, регистрация или тест продукта).

ВАЖНО:
Отвечай строго на языке собеседника (по умолчанию русский) от лица {manager_name}.
"""

async def generate_dialogue_reply(
    user_message: str,
    dialogue_history: List[Dict],
    niche: str,
    raw_ad_text: str,
    sales_hook: str,
    manager_name: str = "Екатерина",
    manager_role: str = "Менеджер развития",
    persona_prompt: Optional[str] = None,
    knowledge_base: Optional[str] = None,
    system_prompt: Optional[str] = None,
    product_name: Optional[str] = None
) -> str:
    """
    Generates persona-based dialogue reply to a client DM using AIRotatorEngine with Gemini & Groq cascade.
    """
    company_context = f" проекта {product_name}" if product_name else ""
    template = system_prompt if (system_prompt and len(system_prompt.strip()) > 10) else DEFAULT_DIALOGUE_SYSTEM_PROMPT
    try:
        sys_instruction = template.format(
            manager_name=manager_name or "Екатерина",
            manager_role=manager_role or "Менеджер развития",
            company_context=company_context,
            niche=niche or "бизнес",
            raw_ad_text=(raw_ad_text or "")[:200],
            sales_hook=sales_hook or "услуги",
            product_name=product_name or "наш сервис"
        )
    except Exception:
        sys_instruction = template

    if knowledge_base:
        sys_instruction += f"\n\nБАЗА ЗНАНИЙ (О ПРОДУКТЕ И ОФФЕРЕ):\n{knowledge_base}"
    if persona_prompt:
        sys_instruction += f"\n\nСТИЛЬ И ИНСТРУКЦИИ ПЕРСОНЫ:\n{persona_prompt}"

    # Build message history string
    history_prompt = "ИСТОРИЯ ДИАЛОГА:\n"
    for item in dialogue_history[-6:]:
        role_label = f"{manager_name}" if item.get("role") == "manager" else "Клиент"
        history_prompt += f"{role_label}: {item.get('text', '')}\n"

    history_prompt += f"\nКлиент только что написал: «{user_message}»\n\nОтветь клиенту от лица {manager_name}:"

    # 1. Primary: AIRotatorEngine Multi-Provider Cascade (SambaNova -> Cerebras -> Groq -> Gemini -> OpenRouter)
    try:
        from src.ai.rotator_engine import ai_rotator
        reply = await ai_rotator.generate_completion(
            system_prompt=sys_instruction,
            user_prompt=history_prompt,
            temperature=0.7,
            max_tokens=300,
            timeout=10.0
        )
        if reply and len(reply.strip()) >= 5:
            logger.info("✅ Generated dialogue reply via AIRotator Engine")
            return reply.strip()
    except Exception as rot_err:
        logger.warning(f"AIRotator dialogue reply notice: {rot_err}")

    # 2. Try Gemini SDK Fallback
    try:
        from src.ai.rotator_engine import _extract_keys
        gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
        if gemini_keys:
            from google import genai
            g_model = settings.GEMINI_MODEL or "gemini-3.6-flash"
            for key in gemini_keys:
                key_sfx = key[-4:] if len(key) >= 4 else key
                try:
                    g_client = genai.Client(api_key=key)
                    res = g_client.models.generate_content(
                        model=g_model,
                        contents=f"{sys_instruction}\n\n{history_prompt}"
                    )
                    reply = res.text.strip() if res and res.text else ""
                    if reply and len(reply) >= 5:
                        logger.info(f"✅ Generated dialogue reply via Gemini ({g_model}) Key=...{key_sfx}")
                        return reply
                except Exception as gem_err:
                    logger.warning(f"Gemini key ...{key_sfx} dialogue generation notice: {gem_err}")
    except Exception as e:
        logger.warning(f"Gemini SDK dialogue fallback notice: {e}")

    # 3. Try Groq SDK Fallback
    try:
        import re
        from groq import AsyncGroq
        raw_keys = (getattr(settings, "GROQ_API_KEYS", "") or "") + "," + (settings.GROQ_API_KEY or "")
        key_pool = [k.strip() for k in re.split(r'[,\s\n]+', raw_keys) if k.strip().startswith("gsk_")]
        key_pool = list(dict.fromkeys(key_pool))
        if key_pool:
            groq_models = [settings.SAFE_GROQ_MODEL, "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
            for key in key_pool:
                try:
                    client = AsyncGroq(api_key=key, max_retries=0, timeout=8.0)
                    for model_name in groq_models:
                        if not model_name:
                            continue
                        try:
                            resp = await client.chat.completions.create(
                                model=model_name,
                                messages=[
                                    {"role": "system", "content": sys_instruction},
                                    {"role": "user", "content": history_prompt}
                                ],
                                temperature=0.7,
                                max_tokens=250
                            )
                            text = resp.choices[0].message.content.strip()
                            if text and len(text) >= 5:
                                logger.info(f"✅ Generated dialogue reply via Groq ({model_name})")
                                return text
                        except Exception:
                            pass
                except Exception:
                    pass
    except Exception as groq_err:
        logger.warning(f"Groq dialogue fallback notice: {groq_err}")

    # 4. Safe Professional Fallback
    return "Спасибо за отклик! Буду рада подробнее рассказать обо всех условиях и ответить на ваши вопросы. Подскажите, когда вам удобно пообщаться?"
