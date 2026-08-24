import json
import logging
import asyncio
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import UserActivityLog, UserProfile, Lead
from src.ai.schemas import LeadScoringResult

logger = logging.getLogger("intent_hunter.ai")

# Concurrency semaphore to throttle concurrent LLM API calls & eliminate peak load bursts against Groq RPM/TPM limits
_ai_scoring_semaphore = asyncio.Semaphore(2)

SYSTEM_PROMPT = """Ты — интеллектуальный классификатор сообщений для сервиса LeadRadar.win.
Твоя задача — проанализировать входное сообщение из Telegram-чата от автора `[TARGET_USER]` и определить тип автора: BUYER (Покупатель), SELLER (Продавец / B2B-лид для нашего аутрича LeadRadar) или IGNORE (Флуд / Спам / Нецелевое).

---

### ПРАВИЛА КЛАССИФИКАЦИИ:

1. **Категория: BUYER (Покупатель / Интент на покупку)**
   - **Признаки:** Человек ищет услугу/товар для себя, просит рекомендации, задает вопросы о покупке/аренде.
   - **Ключевые маркеры:** «Ищу», «Нужен», «Посоветуйте», «Кто сдаст», «Куплю», «Сниму», «Сколько стоит у вас».
   - Set `is_lead: true`, `category: "BUYER"`.

2. **Категория: SELLER (Продавец / B2B-клиент для нашего аутрича LeadRadar.win)**
   - **Признаки:** Автор сам предлагает услуги, продает товары, публикует прайс-листы, рекламирует свой бизнес, приглашает в свой канал или ЛС за покупкой.
   - **Ключевые маркеры:** «Предлагаем», «Сдаем», «В наличии», «Услуги под ключ», «Пишите в ЛС», наличие прайс-листа, рекламных хэштегов (#аренда #недвижимость).
   - Set `is_lead: false`, `category: "SELLER"`.

3. **Категория: IGNORE (Флуд / Спам / Нецелевое)**
   - Бытовые диалоги, мемы, новости, бессмысленные сообщения.
   - Set `is_lead: false`, `category: "IGNORE"`.

---

### ОПРЕДЕЛЕНИЕ НИШИ (ДЛЯ BUYER И SELLER):
Присвой подходящую нишу в `niche_code` как для Покупателя (BUYER), так и для Продавца (SELLER):
- `real_estate` (Недвижимость, Аренда/Покупка жилья, ВНЖ, Ипотека)
- `bike_rent` (Аренда байков, скутеров, авто)
- `currency_exchange` (Обмен валют, Cash, USDT, SWIFT)
- `legal_services` (Юристы, Легализация, Открытие счетов/компаний)
- `other_b2b` (Другой бизнес / прочие целевые услуги)

---

### ОЦЕНКА УВЕРЕННОСТИ (CONFIDENCE SCORE 0-100):
- **90-100:** Оффер/запрос и ниша однозначно понятны, высокая уверенность.
- **60-89:** Оффер/запрос есть, но ниша размыта или формулировка неоднозначна.
- **0-59:** Низкая уверенность (отправлять в IGNORE).

---

### ФОРМАТ ВЫХОДНОГО JSON (ЭКОНОМИЯ ТОКЕНОВ & ДВУХПРЕДЛОЖИТЕЛЬНЫЙ ВЕРДИКТ):
Отвечай СТРОГО в формате JSON без дополнительного текста.
ПЕРВОЕ И ГЛАВНОЕ ПОЛЕ В JSON — 'reasoning'. Поле 'reasoning' ОБЯЗАТЕЛЬНО состоит из СТРОГО 2 коротких предложений:
- **Предложение 1:** Короткий 1-строчный анализ текста автора с цитатой ключевых слов.
- **Предложение 2:** Четкий вердикт классификации в формате: «Вердикт: ЛИД (покупательский запрос на [ниша]).» или «Вердикт: НЕ ЛИД (предложение услуг / бытовой разговор).»

{
  "reasoning": "Пользователь [TARGET_USER] спрашивает совет участников чата о стоимости продления визы в Нячанге без прямого запроса на покупку. Вердикт: НЕ ЛИД (бытовой разговор / консультация).",
  "category": "IGNORE",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": false,
    "is_time_relevant": false
  },
  "is_lead": false,
  "niche_code": "services_visa",
  "rubric_name": "📝 Визы и документы",
  "temperature": null,
  "confidence_score": 95.0,
  "intent_summary": null,
  "sales_hook": null
}

### 4. FEW-SHOT EXAMPLES (INCLUDING HARD NEGATIVES):

Example 1 (Input - Positive Tenant Lead): "[TARGET_USER] Ekaterina: Сниму 1-к квартиру или студию в Muong Thanh Grand на 3 месяца (вид на море, бюджет до 8 млн VND)."
Example 1 (Output):
{
  "reasoning": "Пользователь [TARGET_USER] использует чёткий глагол поиска 'Сниму' для подбора квартиры в Muong Thanh Grand. Вердикт: ЛИД (горячий покупательский запрос на аренду недвижимости).",
  "validation_check": {
    "is_author_seeking_service": true,
    "is_author_offering_service": false,
    "is_time_relevant": true
  },
  "is_lead": true,
  "niche_code": "real_estate",
  "rubric_name": "🏠 Недвижимость",
  "temperature": "HOT",
  "confidence_score": 0.98,
  "intent_summary": "Сниму 1-к квартиру или студию в Muong Thanh Grand на 3 месяца (вид на море, бюджет до 8 млн VND)",
  "sales_hook": "Запросите у арендатора даты заезда и предложите готовые варианты студий с видом на море."
}

Example 2 (Hard Negative - Opinion / Flood): "[TARGET_USER] Alex: Да уж, цены на недвижимость сейчас конечно космические, хрен что купишь."
Example 2 (Output):
{
  "reasoning": "Пользователь делится эмоциональным мнением о высоких ценах на жилье без запроса на покупку. Вердикт: НЕ ЛИД (бытовой разговор / мнения в чате).",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": false,
    "is_time_relevant": false
  },
  "is_lead": false,
  "niche_code": null,
  "rubric_name": null,
  "temperature": null,
  "confidence_score": 0.10,
  "intent_summary": null,
  "sales_hook": null
}

Example 3 (Hard Negative - Seller / Agent Offer): "[TARGET_USER] Agency: Делаю КАСКО и ОСАГО по лучшим ценам в городе! Пишите в ЛС, скидка 15%."
Example 3 (Output):
{
  "reasoning": "Автор сам является агентом и рекламирует услуги КАСКО/ОСАГО со скидкой. Вердикт: НЕ ЛИД (предложение услуг продавца/риелтора SELLER).",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": true,
    "is_time_relevant": true
  },
  "is_lead": false,
  "niche_code": "auto_kasko",
  "rubric_name": "🚗 Страхование",
  "temperature": null,
  "confidence_score": 0.0,
  "intent_summary": "Пользователь является продавцом/агентом услуг страхования.",
  "sales_hook": null
}

Example 4 (Hard Negative - Job / Partnership Seeker): "[TARGET_USER] Dmitry: Ищу работу риелтором в Дубае, есть опыт продаж 5 лет в премиум-сегменте."
Example 4 (Output):
{
  "reasoning": "Пользователь ищет вакансию/работу риелтором, а не услугу подбора недвижимости.",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": true,
    "is_time_relevant": true
  },
  "is_lead": false,
  "niche_code": "real_estate",
  "rubric_name": "🏠 Недвижимость",
  "temperature": null,
  "confidence_score": 0.0,
  "intent_summary": "Поиска работы/вакансии риелтора.",
  "sales_hook": null
}

Example 5 (Hard Negative - Realtor / Landlord Rental Offering): "[TARGET_USER] Agent: Сдаётся 1-к квартира в Muong Thanh Grand на 3 месяца, 8 млн VND (вид на море, залог 1 месяц)."
Example 5 (Output):
{
  "reasoning": "Это объявление от риэлтора или собственника о сдаче квартиры в аренду (предложение жилья). Отсутствуют глаголы поиска ('сниму', 'ищу', 'нужна').",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": true,
    "is_time_relevant": true
  },
  "is_lead": false,
  "niche_code": "real_estate",
  "rubric_name": "🏠 Недвижимость",
  "temperature": null,
  "confidence_score": 0.0,
  "intent_summary": "Объявление риэлтора/агента о сдаче в аренду квартиры в Muong Thanh Grand.",
  "sales_hook": null
}

Example 6 (Hard Negative - Past Experience Discussion): "[TARGET_USER] Elena: В 2022 году брала КАСКО в Ингосе, тогда всё выплатили нормально, проблем не было."
Example 5 (Output):
{
  "reasoning": "Пользователь рассказывает о своем прошлом опыте 2022 года. Текущей потребности в покупке страхования нет.",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": false,
    "is_time_relevant": false
  },
  "is_lead": false,
  "niche_code": "auto_kasko",
  "rubric_name": "🚗 Страхование",
  "temperature": null,
  "confidence_score": 0.15,
  "intent_summary": "Обсуждение прошлого опыта страхования в 2022 году.",
  "sales_hook": null
}

Example 6 (Hard Negative - Infrastructure Question): "[TARGET_USER] Sergey: Подскажите, во сколько открывается офисный центр на Наама Бей?"
Example 6 (Output):
{
  "reasoning": "Бытовой вопрос про график работы здания. Нет запроса на покупку или аренду недвижимости.",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": false,
    "is_time_relevant": false
  },
  "is_lead": false,
  "niche_code": null,
  "rubric_name": null,
  "temperature": null,
  "confidence_score": 0.05,
  "intent_summary": null,
  "sales_hook": null
}

Example 7 (Input - Currency Exchange): "[TARGET_USER] Olga: Привет всем! Подскажите, где в центре Нячанга сейчас самый выгодный курс обмена USDT на наличные донги? Нужно поменять $1500 с доставкой."
Example 7 (Output):
{
  "reasoning": "Пользователь [TARGET_USER] просит посоветовать выгодный курс обмена $1500 USDT на наличные донги с доставкой в центре Нячанга.",
  "validation_check": {
    "is_author_seeking_service": true,
    "is_author_offering_service": false,
    "is_time_relevant": true
  },
  "is_lead": true,
  "niche_code": "currency_exchange",
  "rubric_name": "💱 Обмен валюты",
  "temperature": "HOT",
  "confidence_score": 0.98,
  "intent_summary": "Клиенту требуется обмен $1500 USDT на наличные донги с доставкой в центре Нячанга.",
  "sales_hook": "Предложите выгодный курс обмена и бесплатную доставку наличных донгов в центр."
}

Example 8 (Input - Bike Rental): "[TARGET_USER] Andrey: Нужен байк Honda NVX 155 или PCX в хорошем состоянии на месяц в районе Северного пляжа. Также нужен трансфер из аэропорта Камрань на завтра 14:00."
Example 8 (Output):
{
  "reasoning": "Пользователь [TARGET_USER] ищет аренду байка NVX 155/PCX на месяц и трансфер из аэропорта Камрань.",
  "validation_check": {
    "is_author_seeking_service": true,
    "is_author_offering_service": false,
    "is_time_relevant": true
  },
  "is_lead": true,
  "niche_code": "bike_rent",
  "rubric_name": "🛵 Аренда байков",
  "temperature": "HOT",
  "confidence_score": 0.96,
  "intent_summary": "Клиент ищет аренду байка NVX 155/PCX на месяц и трансфер из аэропорта Камрань.",
  "sales_hook": "Уточните наличие NVX/PCX, предложите скидку за месяц и встречу в аэропорту Камрань."
}

Example 9 (POSITIVE LEAD - English, High-Value Off-Market Property Buyer): "[TARGET_USER] User: I'm currently looking for a discreet, high-value off market property — something unique, not listed everywhere. Contact only with real access, DM me directly."
Example 9 (Output):
{
  "reasoning": "[TARGET_USER] is an active BUYER explicitly searching for a premium, off-market property. Key buyer signals: 'looking for', 'off market', 'not listed everywhere', 'DM me directly', 'contact only with real access'. The author is NOT offering any service — they are seeking to purchase. This is a high-value HOT lead.",
  "validation_check": {
    "is_author_seeking_service": true,
    "is_author_offering_service": false,
    "is_time_relevant": true
  },
  "is_lead": true,
  "niche_code": "real_estate",
  "rubric_name": "🏠 Недвижимость",
  "temperature": "HOT",
  "confidence_score": 0.97,
  "intent_summary": "High-value buyer seeking a discreet off-market exclusive property not listed publicly.",
  "sales_hook": "Respond privately with 1-2 exclusive off-market listings matching their profile. Emphasize discretion, direct access to owner, and unique value."
}
"""

async def build_dynamic_system_prompt(session: AsyncSession) -> str:
    """
    Builds SYSTEM_PROMPT dynamically using a balanced budget limit:
    5 latest positive exemplars (is_lead=True) + 5 latest Hard Negatives (is_lead=False).
    Prevents prompt bloat and maintains ultra-fast inference speed.
    """
    prompt = SYSTEM_PROMPT
    try:
        from src.db.models import AIStudyExemplar
        # 5 positive exemplars
        res_pos = await session.execute(
            select(AIStudyExemplar)
            .where(AIStudyExemplar.is_lead == True)
            .order_by(AIStudyExemplar.created_at.desc())
            .limit(5)
        )
        pos_exemplars = list(res_pos.scalars().all())

        # 5 hard negatives
        res_neg = await session.execute(
            select(AIStudyExemplar)
            .where(AIStudyExemplar.is_lead == False)
            .order_by(AIStudyExemplar.created_at.desc())
            .limit(5)
        )
        neg_exemplars = list(res_neg.scalars().all())

        all_exemplars = pos_exemplars + neg_exemplars
        all_exemplars.sort(key=lambda x: x.created_at, reverse=True)

        if all_exemplars:
            extra_lines = ["\n\n### 5. DYNAMIC FEW-SHOT EXAMPLES (BALANCED CAP: 5 HOT + 5 HARD NEGATIVES):"]
            for idx, ex in enumerate(all_exemplars, 1):
                val_check = {
                    "is_author_seeking_service": ex.is_lead,
                    "is_author_offering_service": not ex.is_lead,
                    "is_time_relevant": True
                }
                out_obj = {
                    "reasoning": ex.intent_summary or "Обученный пример из базы знаний.",
                    "validation_check": val_check,
                    "is_lead": ex.is_lead,
                    "niche_code": ex.niche_code,
                    "temperature": ex.temperature,
                    "confidence_score": 0.98 if ex.is_lead else 0.0,
                    "intent_summary": ex.intent_summary,
                    "sales_hook": ex.sales_hook
                }
                type_tag = "[POSITIVE LEAD]" if ex.is_lead else "[HARD NEGATIVE / SPAM]"
                extra_lines.append(f"Learned Example {idx} {type_tag} (Input): [TARGET_USER] User: {json.dumps(ex.raw_message_text, ensure_ascii=False)}")
                extra_lines.append(f"Learned Example {idx} (Output): {json.dumps(out_obj, ensure_ascii=False)}\n")
            
            prompt += "\n".join(extra_lines)
    except Exception as e:
        logger.warning(f"Error appending dynamic /study exemplars: {e}")
    return prompt


def infer_location_code(text: str) -> str:
    combined = (text or "").lower()
    if any(k in combined for k in ["dubai", "дубай", "оаэ", "uae", "jbr", "marina", "downtown", "jvc", "дирхам", "aed", "creek harbour"]):
        return "dubai"
    elif any(k in combined for k in ["nhatrang", "нячанг", "камрань", "cam ranh", "северный пляж", "вьетнам", "vietnam", "дананг", "danang", "фукуок", "муйне"]):
        return "nhatrang"
    elif any(k in combined for k in ["phuket", "пхукет", "таиланд", "thailand", "паттайя", "бат"]):
        return "phuket"
    elif any(k in combined for k in ["bali", "бали", "индонезия", "рупия"]):
        return "bali"
    return "global"


async def evaluate_user_timeline(
    user_id: int,
    session: AsyncSession,
    messages: Optional[List[UserActivityLog]] = None
) -> Optional[LeadScoringResult]:
    """
    Fetches user's message timeline and calls Groq / Gemini AI to score intent.
    If is_lead is True, saves lead to database and registers dynamic rubrics.
    """
    if messages is None:
        result = await session.execute(
            select(UserActivityLog)
            .where(UserActivityLog.user_id == user_id)
            .order_by(UserActivityLog.timestamp.desc())
            .limit(10)
        )
        messages = list(result.scalars().all())

    if not messages:
        logger.info(f"No messages found for user {user_id}")
        return None

    # Build dynamic prompt with /study exemplars
    active_system_prompt = await build_dynamic_system_prompt(session)

    # Format timeline for prompt with role tagging ([TARGET_USER] vs [OTHER_USER])
    timeline_lines = []
    for m in reversed(messages):
        user_tag = "[TARGET_USER]" if m.user_id == user_id else "[OTHER_USER]"
        user_name = getattr(m, "first_name", None) or f"User_{m.user_id}"
        time_str = m.timestamp.strftime('%Y-%m-%d %H:%M') if m.timestamp else ""
        timeline_lines.append(f"[{time_str}] {user_tag} {user_name}: {m.message_text}")
    timeline_str = "\n".join(timeline_lines)

    scoring_result: Optional[LeadScoringResult] = None
    provider = (settings.AI_PROVIDER or "auto").lower()
    has_groq_keys = bool((settings.GROQ_API_KEY or "").strip() or (getattr(settings, "GROQ_API_KEYS", "") or "").strip())
    has_gemini_key = bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.startswith("AIzaSy"))

    # Acquire concurrency semaphore to ensure maximum 2 parallel LLM API evaluations across all workers
    async with _ai_scoring_semaphore:
        # Micro-stagger (300ms) to smooth token rates per minute
        await asyncio.sleep(0.3)

        # ── ATTEMPT 1: Groq (try 1) ──────────────────────────────────────────────
        if (provider in ("groq", "auto")) and has_groq_keys:
            scoring_result = await _eval_with_groq(timeline_str, active_system_prompt)

        # ── ATTEMPT 2: Groq (try 2 after 30s pause if keys were cooling) ─────────
        if scoring_result is None and (provider in ("groq", "auto")) and has_groq_keys:
            logger.info("⏳ Groq attempt 1 returned no result. Waiting 30s then retrying Groq...")
            await asyncio.sleep(30)
            scoring_result = await _eval_with_groq(timeline_str, active_system_prompt)

        # ── ATTEMPT 3: Gemini (try 1) ─────────────────────────────────────────────
        if scoring_result is None and (provider in ("gemini", "auto")) and has_gemini_key:
            logger.info("🤖 Groq exhausted. Falling back to Gemini (attempt 1)...")
            scoring_result = await _eval_with_gemini(timeline_str, active_system_prompt)

        # ── ATTEMPT 4: Gemini (try 2 after 30s pause) ────────────────────────────
        if scoring_result is None and (provider in ("gemini", "auto")) and has_gemini_key:
            logger.info("⏳ Gemini attempt 1 returned no result. Waiting 30s then retrying Gemini...")
            await asyncio.sleep(30)
            scoring_result = await _eval_with_gemini(timeline_str, active_system_prompt)

        # ── ATTEMPT 5: Cooldown Wait & Retry (No Heuristics!) ─────────────────────
        if scoring_result is None:
            logger.warning(
                f"⏳ All LLM APIs (Groq / Gemini) are cooling or rate-limited for user {user_id}. Entering 30s Cooldown & notifying Telegram Bot..."
            )
            try:
                from src.bot.alert_bot import notify_superadmins_system_alert
                await notify_superadmins_system_alert(
                    "⏳ <b>ИИ-СКАНЕР: Вход в кулдаун API (Rate Limit)!</b>\n"
                    "───────────────────────────\n\n"
                    "⚠️ Все ключи ИИ-моделей (Groq / Gemini) временно исчерпали минутный лимит запросов.\n"
                    "⏸️ <b>Пауза:</b> 30 секунд для сброса лимита ключей.\n\n"
                    "🔄 <i>Эвристический анализ отключен по вашему требованию. Сканирование продолжится только через ИИ после паузы!</i>"
                )
            except Exception as alert_err:
                logger.warning(f"Could not send cooldown alert: {alert_err}")

            await asyncio.sleep(30)
            if (provider in ("groq", "auto")) and has_groq_keys:
                scoring_result = await _eval_with_groq(timeline_str, active_system_prompt)
            if scoring_result is None and has_gemini_key:
                scoring_result = await _eval_with_gemini(timeline_str, active_system_prompt)

    if scoring_result is None:
        logger.error(f"❌ LLM API Error: All LLM models failed for user {user_id}. Skipping scoring.")
        try:
            from src.bot.alert_bot import notify_superadmins_system_alert
            await notify_superadmins_system_alert(
                f"❌ <b>ОШИБКА ИИ-СКАНЕРА: Запрос к нейросети завершился ошибкой!</b>\n"
                f"───────────────────────────\n\n"
                f"⚠️ Не удалось получить нейросетевой вывод от ключей Groq/Gemini.\n"
                f"🚫 <i>Эвристика и шаблонные ответы полностью исключены. Сообщение пропущено до восстановления ИИ.</i>"
            )
        except Exception:
            pass
        return None


    # ── B2B SELLER OUTREACH LEAD TRACK ──────────────────────────────────────
    if scoring_result and (scoring_result.category == "SELLER" or getattr(scoring_result, "action_required", None) in ["AUTO_SAVE", "NEED_APPROVAL"]):
        conf = float(scoring_result.confidence_score or 0.0)
        action = getattr(scoring_result, "action_required", None) or ("AUTO_SAVE" if conf >= 85 or scoring_result.category == "SELLER" else ("NEED_APPROVAL" if conf >= 60 else "DISCARD"))
        
        if action in ["AUTO_SAVE", "NEED_APPROVAL"] or scoring_result.category == "SELLER":
            last_m = messages[-1] if messages else None
            ext_data = getattr(scoring_result, "extracted_data", None)
            author_uname = getattr(last_m, "username", None) or (ext_data.author_username if ext_data else None)
            author_fname = getattr(last_m, "first_name", None) or f"User_{user_id}"
            raw_text = getattr(last_m, "message_text", "") or (ext_data.raw_ad_text if ext_data else "")
            
            # Infer location code for seller
            seller_loc = "global"
            for m in messages:
                ch_title = getattr(m, "chat_title", "") or ""
                m_txt = getattr(m, "message_text", "") or ""
                seller_loc = infer_location_code(ch_title + " " + m_txt)
                if seller_loc != "global":
                    break
            
            # Build message history array
            history_items = []
            for m in messages:
                history_items.append({
                    "chat_title": getattr(m, "chat_title", "Chat"),
                    "message_text": getattr(m, "message_text", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

            from src.db.models import OutreachLead
            outreach_status = "READY_FOR_OUTREACH" if action == "AUTO_SAVE" or conf >= 85 else "NEED_APPROVAL"
            
            # Check duplicate / existing B2B lead for this author
            dup_stmt = select(OutreachLead).where(
                (OutreachLead.telegram_id == user_id) |
                (OutreachLead.author_username == author_uname)
            ) if author_uname else select(OutreachLead).where(OutreachLead.telegram_id == user_id)
            existing_outreach = (await session.execute(dup_stmt)).scalars().first()
            
            if existing_outreach:
                # Update existing seller card history
                cur_hist = existing_outreach.messages_history or []
                cur_hist.extend(history_items)
                existing_outreach.messages_history = cur_hist
                existing_outreach.raw_ad_text = raw_text[:500]
                await session.commit()
                logger.info(f"Updated existing B2B SELLER timeline history for @{author_uname} ({len(cur_hist)} messages)")
            else:
                s_hook = (scoring_result.sales_hook or "").strip() or "Продавец целевых услуг"

                new_outreach = OutreachLead(
                    author_username=author_uname,
                    author_first_name=author_fname,
                    telegram_id=user_id,
                    niche_code=scoring_result.niche_code,
                    location_code=seller_loc,
                    confidence_score=conf,
                    status=outreach_status,
                    raw_ad_text=raw_text[:500],
                    sales_hook=s_hook,
                    chat_title=getattr(last_m, "chat_title", "Telegram Chat"),
                    messages_history=history_items
                )
                session.add(new_outreach)
                await session.commit()
                await session.refresh(new_outreach)
                
                logger.info(f"🎯 NEW B2B SELLER Lead created! @{author_uname}, GEO: {seller_loc}, Niche: {scoring_result.niche_code}, Status: {outreach_status}")
                
                # Always notify Superadmins immediately on every new B2B Seller Lead / Bid
                try:
                    from src.bot.alert_bot import bot, notify_superadmins_system_alert
                    from src.db.models import Partner
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    import html
                    
                    loc_flag = {"dubai": "🇦🇪 Дубай", "nhatrang": "🇻🇳 Вьетнам", "phuket": "🇹🇭 Таиланд"}.get(seller_loc, "🌐 Глобал")
                    card_txt = (
                        f"🤖 <b>НАЙДЕН НОВЫЙ B2B-КЛИЕНТ / БИД!</b>\n"
                        f"───────────────────────────\n\n"
                        f"📍 <b>ГЕО:</b> {loc_flag}\n"
                        f"🏷️ <b>Ниша:</b> {scoring_result.niche_code}\n"
                        f"👤 <b>Автор:</b> @{author_uname or 'без_юзернейма'} ({html.quote(author_fname)})\n"
                        f"💬 <b>Текст объявления:</b> «{html.quote(raw_text[:200])}»\n"
                        f"🎯 <b>Sales Hook:</b> {html.quote(s_hook)}\n"
                        f"📊 <b>Уверенность ИИ:</b> {conf}%\n"
                        f"⚡ <b>Статус:</b> {outreach_status}"
                    )
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✅ Одобрить и отправить", callback_data=f"approve_outreach:{new_outreach.id}"),
                        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_outreach:{new_outreach.id}")
                    ]])
                    
                    superadmins_res = await session.execute(select(Partner).where((Partner.role == "SUPERADMIN") | (Partner.role == "ADMIN")))
                    superadmins = list(superadmins_res.scalars().all())
                    for sa in superadmins:
                        try:
                            if bot:
                                await bot.send_message(chat_id=sa.telegram_id, text=card_txt, parse_mode="HTML", reply_markup=kb)
                        except Exception:
                            pass
                except Exception as b2b_alert_err:
                    logger.warning(f"B2B Seller Lead Superadmin notification notice: {b2b_alert_err}")


    if scoring_result and scoring_result.is_lead:
        logger.info(f"🔥 HOT/WARM Lead detected for user {user_id} in niche {scoring_result.niche_code} [{scoring_result.rubric_name}]")
        
        # Infer location code from messages timeline
        loc_code = "global"
        for m in messages:
            ch_name = getattr(m, "chat_title", "") or ""
            msg_txt = getattr(m, "message_text", "") or ""
            loc_code = infer_location_code(ch_name + " " + msg_txt)
            if loc_code != "global":
                break

        # Lock to protect against concurrent lead creation race conditions
        global _lead_creation_lock
        if '_lead_creation_lock' not in globals():
            _lead_creation_lock = asyncio.Lock()

        async with _lead_creation_lock:
            # Check if ANY lead already exists for this user in this niche or with matching intent summary
            existing_lead_stmt = select(Lead).where(
                Lead.user_id == user_id,
                Lead.niche_code == scoring_result.niche_code
            )
            existing_lead = (await session.execute(existing_lead_stmt)).scalars().first()

            if not existing_lead and scoring_result.intent_summary:
                summary_stmt = select(Lead).where(Lead.intent_summary == scoring_result.intent_summary)
                existing_lead = (await session.execute(summary_stmt)).scalars().first()

            if existing_lead:
                logger.info(f"Lead already exists for user {user_id} in niche {scoring_result.niche_code} (ID: {existing_lead.id}). Skipping duplicate creation.")
                scoring_result.is_lead = False  # Mark as non-new lead to suppress duplicate alerts
            else:
                # Prefer exact direct quote from client's original message text
                client_quote = None
                for m in reversed(messages):
                    if getattr(m, "user_id", None) == user_id:
                        raw_txt = (getattr(m, "message_text", "") or "").strip()
                        if raw_txt:
                            client_quote = raw_txt
                            break

                final_summary = (scoring_result.intent_summary or "").strip()
                if client_quote and len(client_quote) >= 10:
                    final_summary = client_quote[:350]
                
                scoring_result.intent_summary = final_summary

                # Save lead to Database
                lead = Lead(
                    user_id=user_id,
                    niche_code=scoring_result.niche_code,
                    location_code=loc_code,
                    temperature=scoring_result.temperature,
                    confidence_score=scoring_result.confidence_score,
                    intent_summary=final_summary,
                    sales_hook=scoring_result.sales_hook,
                    status="AVAILABLE",
                    price=1.00
                )
                session.add(lead)
                await session.commit()
                await session.refresh(lead)

        # Check and register dynamic Rubric in DB
        from src.db.models import Rubric
        from src.bot.keyboards import register_dynamic_rubric, NICHE_NAMES
        
        rubric_code = scoring_result.niche_code
        rubric_title = scoring_result.rubric_name or NICHE_NAMES.get(rubric_code, "Прочее")

        rub_stmt = select(Rubric).where(Rubric.code == rubric_code)
        existing_rubric = (await session.execute(rub_stmt)).scalar_one_or_none()

        is_new_rubric = False
        if not existing_rubric and rubric_code not in NICHE_NAMES:
            is_new_rubric = True
            new_rub = Rubric(
                code=rubric_code,
                name=rubric_title,
                icon="🏷️",
                is_custom=True
            )
            session.add(new_rub)

        await session.commit()
        await session.refresh(lead)

        # Register in memory registry
        register_dynamic_rubric(rubric_code, rubric_title)

        # Notify Superadmins ONLY when a brand new rubric is created by AI
    # Log AI Scorer verdict to real-time telemetry stream
    try:
        from src.services.process_logger import process_logger
        if scoring_result:
            if scoring_result.is_lead:
                process_logger.add_log(
                    category="AI_SCORER",
                    level="lead",
                    title=f"🔥 ГОРЯЧИЙ ЛИД ОБНАРУЖЕН! Ниша: {scoring_result.rubric_name or scoring_result.niche_code} ({int((scoring_result.confidence_score or 0.85) * 100)}%)",
                    details=f"Запрос: \"{scoring_result.intent_summary}\" | Sales Hook: \"{scoring_result.sales_hook}\""
                )
            else:
                reason = scoring_result.reasoning or "Не содержит покупательского интента (Цифровой шум/Спам)"
                process_logger.add_log(
                    category="AI_SCORER",
                    level="noise",
                    title=f"🛑 ИИ-Анализатор: Квалификация сообщения завершена — НЕ ЛИД",
                    details=f"Причина: {reason[:150]}"
                )
    except Exception as log_err:
        logger.debug(f"AI Scorer process logger notice: {log_err}")

    # Record AI Evaluation Log for audit & reasoning inspection
    try:
        from src.db.models import AIEvaluationLog
        last_m = messages[-1] if messages else None
        if last_m and scoring_result:
            u_name = getattr(last_m, "username", None) or f"user_{user_id}"
            f_name = getattr(last_m, "first_name", None) or f"Пользователь {user_id}"
            cot_reasoning = (scoring_result.reasoning or "").strip() or "Квалификация ИИ завершена."

            eval_log = AIEvaluationLog(
                user_id=user_id,
                username=u_name,
                first_name=f_name,
                chat_title=last_m.chat_title,
                message_text=last_m.message_text,
                is_lead=scoring_result.is_lead,
                reasoning=cot_reasoning,
                niche_code=scoring_result.niche_code,
                temperature=scoring_result.temperature,
                confidence_score=scoring_result.confidence_score or 0.0
            )
            session.add(eval_log)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to record AIEvaluationLog: {e}")

    return scoring_result


import re

def clean_json_text(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

_groq_key_cooldowns = {}

async def _eval_with_groq(timeline_str: str, active_prompt: Optional[str] = None) -> Optional[LeadScoringResult]:
    """Scores timeline using Groq Cloud API with 45-second key cooldown management and multi-model fallback chain."""
    global _groq_key_cooldowns
    try:
        from groq import AsyncGroq
        import time
        
        # Build API Keys Pool from GROQ_API_KEYS and GROQ_API_KEY
        raw_keys = (getattr(settings, "GROQ_API_KEYS", "") or "") + "," + (settings.GROQ_API_KEY or "")
        key_pool = [k.strip() for k in re.split(r'[,\s\n]+', raw_keys) if k.strip().startswith("gsk_")]
        key_pool = list(dict.fromkeys(key_pool)) # Unique keys list

        if not key_pool:
            logger.warning("No valid Groq API keys starting with 'gsk_' found in settings.")
            return None

        now = time.time()
        # Filter keys not currently on cooldown
        ready_keys = [k for k in key_pool if _groq_key_cooldowns.get(k, 0) <= now]
        if not ready_keys:
            min_cooldown_end = min(_groq_key_cooldowns.values()) if _groq_key_cooldowns else now + 30
            wait_s = max(1.0, min_cooldown_end - now)
            if wait_s <= 30.0:
                logger.info(f"⏳ All {len(key_pool)} Groq API keys are on 30s cooldown. Waiting {wait_s:.1f}s for reset...")
                await asyncio.sleep(wait_s)
                _groq_key_cooldowns.clear()
                ready_keys = key_pool
            else:
                _groq_key_cooldowns.clear()
                ready_keys = key_pool

        json_schema = LeadScoringResult.model_json_schema()
        sys_p = active_prompt or SYSTEM_PROMPT
        
        prompt_sys = (
            f"{sys_p}\n\n"
            f"Please output your evaluation in valid json format adhering strictly to this JSON schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )

        candidate_models = []
        official_models = [
            "qwen/qwen3.6-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "groq/compound-mini"
        ]
        if settings.GROQ_MODEL and settings.GROQ_MODEL in official_models:
            candidate_models.append(settings.GROQ_MODEL)
        for m in official_models:
            if m not in candidate_models:
                candidate_models.append(m)

        for api_key in ready_keys:
            key_suffix = api_key[-4:]
            client = AsyncGroq(api_key=api_key, max_retries=0, timeout=10.0)

            for model_name in candidate_models:
                try:
                    completion = await client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": prompt_sys},
                            {"role": "user", "content": f"User Messages Timeline:\n{timeline_str}"}
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.1
                    )
                    
                    content = completion.choices[0].message.content
                    if content:
                        cleaned = clean_json_text(content)
                        logger.info(f"Successfully evaluated intent via Groq Key (...{key_suffix}) Model ({model_name})")
                        _groq_key_cooldowns.pop(api_key, None)
                        return LeadScoringResult(**json.loads(cleaned))
                except Exception as model_err:
                    err_str = str(model_err)
                    is_rate_limit = (getattr(model_err, "status_code", None) == 429) or ("rate_limit_exceeded" in err_str.lower())
                    
                    if is_rate_limit:
                        logger.warning(f"⚠️ Groq API Rate Limit (429) on Key ...{key_suffix} / Model {model_name}. Trying next key/model...")
                        _groq_key_cooldowns[api_key] = time.time() + 30.0
                        continue
                    else:
                        logger.warning(f"Groq model {model_name} on Key ...{key_suffix} notice: {err_str[:120]}. Trying next model...")

    except Exception as e:
        logger.error(f"Error in Groq Multi-Key Pool evaluation: {e}")
    
    return None


async def _eval_with_gemini(timeline_str: str, active_prompt: Optional[str] = None) -> Optional[LeadScoringResult]:
    """Scores timeline using Google Gemini API (via google-genai SDK or httpx REST)."""
    if not (settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.startswith("AIzaSy")):
        return None
    sys_p = active_prompt or SYSTEM_PROMPT
    # 1. Try official google-genai SDK
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        prompt = f"{sys_p}\n\nUser Messages Timeline:\n{timeline_str}"

        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=LeadScoringResult,
                temperature=0.1
            ),
        )
        
        if response and response.text:
            cleaned = clean_json_text(response.text)
            logger.info(f"Successfully evaluated intent via Gemini SDK ({settings.GEMINI_MODEL})")
            return LeadScoringResult(**json.loads(cleaned))

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "quota" in err_str.lower() or "resource" in err_str.lower():
            logger.warning(f"⚠️ Gemini SDK Rate Limit (429/Quota) on {settings.GEMINI_MODEL}: {err_str[:150]}")
            try:
                from src.bot.alert_bot import notify_superadmins_llm_error
                await notify_superadmins_llm_error("Gemini (SDK)", settings.GEMINI_MODEL, f"HTTP 429 / Quota Limit Exceeded: {err_str[:250]}")
            except Exception:
                pass
        else:
            logger.debug(f"Gemini SDK call failed/skipped: {e}. Trying httpx REST fallback...")

    # 2. Try direct HTTP REST API to Gemini
    try:
        import httpx
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
        
        json_schema = LeadScoringResult.model_json_schema()
        prompt_sys = f"{sys_p}\nRespond ONLY with valid JSON matching:\n{json.dumps(json_schema, ensure_ascii=False)}\n\nTimeline:\n{timeline_str}"

        payload = {
            "contents": [{"parts": [{"text": prompt_sys}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                data = res.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                cleaned = clean_json_text(text)
                logger.info(f"Successfully evaluated intent via Gemini REST ({settings.GEMINI_MODEL})")
                return LeadScoringResult(**json.loads(cleaned))
            else:
                logger.warning(f"Gemini REST API returned HTTP {res.status_code}: {res.text[:100]}")
                if res.status_code == 429:
                    try:
                        from src.bot.alert_bot import notify_superadmins_llm_error
                        await notify_superadmins_llm_error("Gemini (REST)", settings.GEMINI_MODEL, f"HTTP 429 Rate Limit Exceeded: {res.text[:250]}")
                    except Exception:
                        pass

    except Exception as e:
        logger.error(f"Error calling Gemini REST API: {e}")

    return None



