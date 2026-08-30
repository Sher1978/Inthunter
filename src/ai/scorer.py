import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import UserActivityLog, UserProfile, Lead
from src.ai.schemas import LeadScoringResult

logger = logging.getLogger("intent_hunter.ai")

# Concurrency semaphore to throttle concurrent LLM API calls & eliminate peak load bursts
# Set to 1 because all user keys likely share the same Google Project (15-20 RPM limit)
_ai_scoring_semaphore = asyncio.Semaphore(1)

SYSTEM_PROMPT = """Ты — интеллектуальный классификатор сообщений для сервиса LeadRadar.win.
Твоя задача — проанализировать входное сообщение из Telegram-чата от автора `[TARGET_USER]` и определить тип автора: BUYER (Покупатель), SELLER (Продавец / B2B-лид для нашего аутрича LeadRadar) или IGNORE (Флуд / Спам / Нецелевое).

---

### ПРАВИЛА КЛАССИФИКАЦИИ:

1. **Категория: BUYER (Покупатель / Интент на покупку или услугу)**
   - **Признаки:** Человек ищет услугу/товар для себя, просит рекомендации специалистов/компаний, задает вопросы о стоимости, выборе, визах, аренде, ремонте или трансфере.
   - **Ключевые маркеры:** «Ищу», «Нужен», «Нужна», «Посоветуйте», «Порекомендуйте», «Кто сдаст», «Куплю», «Сниму», «Сколько стоит», «Подскажите проверенного», «Кто поможет с», «Где лучше заказать».
   - **Температура:**
     - **HOT**: Прямой срочный запрос на покупку/аренду с параметрами, датами или бюджетом.
     - **WARM**: Запрос рекомендаций, подбора специалиста, консультации или сравнения поставщиков услуг.
   - Set `is_lead: true`, `category: "BUYER"`.

3. **Категория: SELLER (Продавец / B2B-клиент для нашего аутрича LeadRadar.win)**
   - **Признаки:** Автор сам предлагает услуги, продает товары, публикует прайс-листы, рекламирует свой бизнес, приглашает в свой канал или ЛС за покупкой.
   - **Ключевые маркеры:** «Предлагаем», «Сдаем», «В наличии», «Услуги под ключ», «Пишите в ЛС», наличие прайс-листа, рекламных хэштегов (#аренда #недвижимость).
   - Set `is_lead: false`, `category: "SELLER"`.
   - **СТРОГИЕ ТРЕБОВАНИЯ К SELLER:**
     1. `sales_hook` ОБЯЗАН быть четким персональным питчем (1-2 предложения): как LeadRadar.win поможет ЭТОМУ продавцу получать целевых клиентов из чатов в его нише! Фраза "Продавец целевых услуг" СТРОГО ЗАПРЕЩЕНА.
     2. Ниша `niche_code` ДОЛЖНА быть строго определена: `real_estate`, `bike_rent`, `currency_exchange`, `legal_services`, `hr_hiring`, `marketing_smm`, `tours_travel`, `beauty_health`. Если ниша размыта или неизвестна, ставь `category: "IGNORE"`.

4. **Категория: IGNORE (Флуд / Спам / Нецелевое / Размытая ниша)**
   - Бытовые диалоги, мемы, новости, бессмысленные сообщения или продавцы с неопределенной нишей.
   - Set `is_lead: false`, `category: "IGNORE"`.

---

### ОПРЕДЕЛЕНИЕ НИШИ (ДЛЯ BUYER И SELLER):
Присвой подходящую нишу в `niche_code` как для Покупателя (BUYER), так и для Продавца (SELLER):
- `real_estate` (Недвижимость, Аренда/Покупка жилья, ВНЖ, Ипотека)
- `bike_rent` (Аренда байков, скутеров, авто)
- `currency_exchange` (Обмен валют, Cash, USDT, SWIFT)
- `legal_services` (Юристы, Легализация, Открытие счетов/компаний)
- `hr_hiring` (Работодатели, Публикация вакансий, Поиск сотрудников, Рекрутинг)
- `marketing_smm` (Маркетинг, SMM, Reels, Таргет, Дизайн)
- `other_b2b` (Другой бизнес / прочие целевые коммерческие услуги)

---

### ⚠️ СТРОГОЕ ПРАВИЛО ПО ОБМЕНУ ВАЛЮТ (CURRENCY EXCHANGE):
1. Если автор сам ПОДАЕТ ОБЪЯВЛЕНИЕ ОБ ОБМЕНЕ ("обмен рублей на рупии", "меняем USDT", "меняю рубли на донги/дирхамы", "наш курс", "в наличии наличные рубли/доллары", "обмен во всех городах Индии/Вьетнама"), это ПРОДАВЕЦ УСЛУГ (SELLER)!
   - Указывай: `"category": "SELLER"`, `"is_lead": false`.
2. ЛИДОМ ПО ОБМЕНУ ВАЛЮТ (BUYER) считается ИСКЛЮЧИТЕЛЬНО ПОКУПАТЕЛЬ, КОТОРЫЙ ИЩЕТ ГДЕ ОБМЕНЯТЬ ИЛИ ПРОСИТ РУПИИ/ДОНГИ/ДИРХАМЫ ("нужен обмен", "кто меняет рубли", "ищу рупии", "нужно 1000$ в Нячанге").
   - Указывай: `"category": "BUYER"`, `"is_lead": true`.

---

### ⚠️ СТРОГОЕ ПРАВИЛО ПО ЛИСТИНГАМ И ОБЪЯВЛЕНИЯМ О ПРОДАЖЕ/АРЕНДЕ НЕДВИЖИМОСТИ:
Объявления от агентов, риелторов или застройщиков о продаже/аренде объектов ("Exclusive Villa For Sale", "Akoya Oxygen", "For Sale", "Selling @", "Handover in", "Plot Size", "3 BR Villa For Sale", "Сдаётся квартира", "Продаётся вилла") НЕ ЯВЛЯЮТСЯ ЛИДАМИ И НЕ ЯВЛЯЮТСЯ B2B-ЛИДАМИ!
Строго присваивай им:
- `"category": "IGNORE"`
- `"is_lead": false`
- `"action_required": "DISCARD"`
- `"confidence_score": 0.0`

ЛИДОМ ПО НЕДВИЖИМОСТИ СЧИТАЕТСЯ ИСКЛЮЧИТЕЛЬНО ПОКУПАТЕЛЬ/АРЕНДАТОР, КОТОРЫЙ ИЩЕТ СЕБЕ ЖИЛЬЕ ИЛИ УСЛУГУ ("Сниму квартиру", "Ищу виллу в аренду", "Хотим купить 2BR в Дубае").

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
- **Предложение 2:** Четкий вердикт классификации в формате: «Вердикт: ЛИД (покупательский запрос на [ниша]).» или «Вердикт: НЕ ЛИД (предложение услуг / листинг недвижимости / бытовой разговор).»

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

Example 2 (Hard Negative - Real Estate Sale Listing): "[TARGET_USER] Agent: Exclusive Villa For Sale Akoya Aster 5 bedrooms Full Golf Course View 2580sft Handover in March 2020 Selling @ 1.7net Thanks"
Example 2 (Output):
{
  "reasoning": "Это объявление о продаже виллы от агента (листинг недвижимости 'Villa For Sale'). Отсутствует покупательский запрос (нет 'сниму', 'ищу', 'купим'). Вердикт: НЕ ЛИД (листинг недвижимости / ИГНОР).",
  "validation_check": {
    "is_author_seeking_service": false,
    "is_author_offering_service": true,
    "is_time_relevant": false
  },
  "category": "IGNORE",
  "is_lead": false,
  "niche_code": "real_estate",
  "rubric_name": "🏠 Недвижимость",
  "temperature": null,
  "confidence_score": 0.0,
  "intent_summary": null,
  "sales_hook": null
}

Example 3 (Hard Negative - Opinion / Flood): "[TARGET_USER] Alex: Да уж, цены на недвижимость сейчас конечно космические, хрен что купишь."
Example 3 (Output):
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

Example 4 (Hard Negative - Seller / Agent Offer): "[TARGET_USER] Agency: Делаю КАСКО и ОСАГО по лучшим ценам в городе! Пишите в ЛС, скидка 15%."
Example 4 (Output):
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

Example 5 (Hard Negative - Job / Partnership Seeker): "[TARGET_USER] Dmitry: Ищу работу риелтором в Дубае, есть опыт продаж 5 лет в премиум-сегменте."
Example 5 (Output):
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

Example 6 (Hard Negative - Realtor / Landlord Rental Offering): "[TARGET_USER] Agent: Сдаётся 1-к квартира в Muong Thanh Grand на 3 месяца, 8 млн VND (вид на море, залог 1 месяц)."
Example 6 (Output):
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

async def build_dynamic_system_prompt(session: AsyncSession, target_niche: str = None) -> str:
    """
    Builds SYSTEM_PROMPT dynamically using a balanced budget limit:
    3 latest positive exemplars + 3 latest Hard Negatives for the target niche.
    Also injects summarized rules from DynamicNicheRule if available.
    """
    prompt = SYSTEM_PROMPT
    try:
        from src.db.models import AIStudyExemplar, DynamicNicheRule
        
        # 1. Load Summarized Rules
        if target_niche:
            res_rules = await session.execute(select(DynamicNicheRule).where(DynamicNicheRule.niche_code == target_niche))
            rule_entry = res_rules.scalars().first()
            if rule_entry:
                prompt += f"""\n\n### ВАЖНЫЕ ПРАВИЛА ДЛЯ НИШИ {target_niche.upper()}:\n{rule_entry.summarized_rules}\n"""

        # 2. Load Exemplars
        stmt_pos = select(AIStudyExemplar).where(AIStudyExemplar.is_lead == True)
        stmt_neg = select(AIStudyExemplar).where(AIStudyExemplar.is_lead == False)
        
        if target_niche:
            stmt_pos = stmt_pos.where(AIStudyExemplar.niche_code == target_niche)
            stmt_neg = stmt_neg.where(AIStudyExemplar.niche_code == target_niche)
            
        res_pos = await session.execute(stmt_pos.order_by(AIStudyExemplar.created_at.desc()).limit(3))
        pos_exemplars = list(res_pos.scalars().all())

        res_neg = await session.execute(stmt_neg.order_by(AIStudyExemplar.created_at.desc()).limit(3))
        neg_exemplars = list(res_neg.scalars().all())

        all_exemplars = pos_exemplars + neg_exemplars
        all_exemplars.sort(key=lambda x: x.created_at, reverse=True)

        if all_exemplars:
            extra_lines = ["\n\n### 5. DYNAMIC FEW-SHOT EXAMPLES (BALANCED CAP FOR RLHF):"]
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
    if any(k in combined for k in ["moscow", "москва", "мск", "подмосковье", "руб", "рублей", "сити", "арбат", "тверская"]):
        return "moscow"
    elif any(k in combined for k in ["dubai", "дубай", "оаэ", "uae", "jbr", "marina", "downtown", "jvc", "дирхам", "aed", "creek harbour", "пальм"]):
        return "dubai"
    elif any(k in combined for k in ["nhatrang", "нячанг", "камрань", "cam ranh", "северный пляж", "вьетнам", "vietnam", "дананг", "danang", "фукуок", "муйне", "донги", "vnd"]):
        return "nhatrang"
    elif any(k in combined for k in ["phuket", "пхукет", "таиланд", "thailand", "паттайя", "pattaya", "бангкок", "bangkok", "бат", "thb"]):
        return "phuket"
    elif any(k in combined for k in ["bali", "бали", "индонезия", "indonesia", "рупия", "idr", "убуд", "семиньяк", "чангу", "кута"]):
        return "bali"
    return "global"


async def _determine_message_niche(text: str) -> str:
    """Level 1 Memory Routing: Fast LLM classification of the message niche."""
    try:
        from src.ai.rotator_engine import ai_rotator
        sys_prompt = '''You are a fast router. Determine the niche of the message.
Available niches: real_estate, bike_rent, currency_exchange, legal_services, hr_hiring, marketing_smm, other_b2b, community.
If unsure, return "community".
Output strictly JSON: {"niche_code": "..."}'''
        
        res = await ai_rotator.generate_json(
            system_prompt=sys_prompt,
            user_prompt=text,
            temperature=0.0,
            timeout=5.0
        )
        if res and "niche_code" in res:
            return res["niche_code"]
    except Exception as e:
        logger.warning(f"Error in fast niche routing: {e}")
    return "community"

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

    timeline_str = "\n".join(timeline_lines)
    latest_msg_text = messages[-1].message_text if messages else ""

    # LEVEL 1 MEMORY ROUTING
    target_niche = await _determine_message_niche(latest_msg_text)

    # Build dynamic prompt with /study exemplars (LEVEL 2 MEMORY)
    active_system_prompt = await build_dynamic_system_prompt(session, target_niche)

    scoring_result: Optional[LeadScoringResult] = None
    from src.ai.rotator_engine import _extract_keys
    has_gemini_key = bool(_extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy"))

    # Acquire concurrency semaphore to ensure maximum 2 parallel LLM API evaluations across all workers
    async with _ai_scoring_semaphore:
        # Micro-stagger (8.5s) to strictly respect <7 RPM Free Tier limits
        await asyncio.sleep(8.5)

        # ── PRIMARY: AIRotatorEngine Multi-Provider Cascade (SambaNova -> Cerebras -> Groq Pool -> Gemini -> OpenRouter)
        from src.ai.rotator_engine import ai_rotator
        json_schema = LeadScoringResult.model_json_schema()
        sys_p = active_system_prompt or SYSTEM_PROMPT
        
        prompt_sys = (
            f"{sys_p}\n\n"
            f"Please output your evaluation in valid json format adhering strictly to this JSON schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )
        user_p = f"User Messages Timeline:\n{timeline_str}"

        raw_json_dict = await ai_rotator.generate_json(
            system_prompt=prompt_sys,
            user_prompt=user_p,
            temperature=0.1,
            timeout=12.0
        )

        if raw_json_dict:
            try:
                scoring_result = LeadScoringResult(**raw_json_dict)
            except Exception as parse_err:
                logger.warning(f"Error parsing LeadScoringResult from AIRotator Engine dict: {parse_err}")
                scoring_result = None

        # ── ATTEMPT 2: Silent Cooldown Wait & Retry (No Telegram Alert Spam) ─────────────────────
        if scoring_result is None:
            logger.warning(f"⏳ All LLM APIs are cooling or rate-limited for user {user_id}. Retrying after 30s...")
            await asyncio.sleep(30)
            
            raw_json_dict = await ai_rotator.generate_json(
                system_prompt=prompt_sys,
                user_prompt=user_p,
                temperature=0.1,
                timeout=12.0
            )

            if raw_json_dict:
                try:
                    scoring_result = LeadScoringResult(**raw_json_dict)
                except Exception as parse_err:
                    logger.warning(f"Error parsing LeadScoringResult from AIRotator Engine dict on retry: {parse_err}")
                    scoring_result = None

    if scoring_result is None:
        logger.warning(f"Notice: All LLM models temporarily cooling down for user {user_id}. Skipping LLM scoring.")
        return None


    # ── DETERMINISTIC HARD GUARD FOR REAL ESTATE LISTINGS ─────────────────
    if scoring_result:
        raw_text_check = (timeline_str or "").lower()
        prop_listing_patterns = [
            "for sale", "exclusive villa", "villa for sale", "apartment for sale", "flat for sale", "unit for sale",
            "resale unit", "handover in", "plot size", "selling @", "ask - aed", "1bhk", "2bhk", "3bhk", "4bhk", "5bhk",
            "5 bedrooms", "4 bedrooms", "3 bedrooms", "2 bedrooms", "aed 1.", "aed 2.", "aed 3.", "aed 4.", "aed 5.", "aed 6.",
            "продам квартиру", "продам виллу", "продается вилла", "продается квартира", "сдается квартира"
        ]
        buyer_keywords = ["сниму", "ищу", "купим", "хочу купить", "нужен подбор", "looking to buy", "looking for rent", "looking to rent", "want to buy", "want to rent", "need apartment", "need villa"]

        has_listing_pattern = any(p in raw_text_check for p in prop_listing_patterns)
        has_buyer_pattern = any(b in raw_text_check for b in buyer_keywords)

        if has_listing_pattern and not has_buyer_pattern:
            logger.info(f"🚫 HARD GUARD TRIPPED: Real estate sale/rent listing detected for user {user_id}. Forcing category=IGNORE, is_lead=False & Blacklisting Spammer User.")
            scoring_result.is_lead = False
            scoring_result.category = "IGNORE"
            try:
                from src.db.models import BlacklistedUser
                ex_b = (await session.execute(select(BlacklistedUser).where(BlacklistedUser.user_id == user_id))).scalar_one_or_none()
                if not ex_b:
                    session.add(BlacklistedUser(user_id=user_id, reason="Авто-черный список: рекламный листинг / спам-бот"))
                    await session.commit()
                # Sync in-memory Gatekeeper set
                import sys
                app_module = sys.modules.get("src.api.app")
                ingestor = getattr(app_module, "ingestor", None) if app_module else None
                if ingestor and hasattr(ingestor, "banned_spammer_user_ids"):
                    ingestor.banned_spammer_user_ids.add(user_id)
            except Exception as blk_u_err:
                logger.debug(f"Notice blacklisting spammer user {user_id}: {blk_u_err}")

    # ── B2B SELLER OUTREACH LEAD TRACK ──────────────────────────────────────
    if scoring_result and scoring_result.category != "IGNORE" and (scoring_result.category == "SELLER" or getattr(scoring_result, "action_required", None) in ["AUTO_SAVE", "NEED_APPROVAL"]):
        niche = (scoring_result.niche_code or "other_b2b").lower().strip()
        invalid_b2b_niches = {"unknown", "none", "", "прочее", "real_estate", "bike_rent", "auto_kasko", "other"}
        conf = float(scoring_result.confidence_score or 0.0)
        if conf <= 1.0:
            conf = conf * 100.0

        last_m = messages[-1] if messages else None
        ext_data = getattr(scoring_result, "extracted_data", None)
        raw_text = (getattr(last_m, "message_text", "") or (ext_data.raw_ad_text if ext_data else "")).lower()

        prop_keywords = ["for sale", "1bhk", "2bhk", "3bhk", "ask - aed", "aed ", "villa for sale", "handover in", "plot size", "selling @", "exclusive villa", "apartment for sale"]
        is_prop_listing = any(k in raw_text for k in prop_keywords)

        if niche == "hr_hiring" or "вакансия" in raw_text or "ищем сотрудника" in raw_text or "требуется " in raw_text:
            # Route to B2C HR-Radar System!
            try:
                from src.db.models import HRVacancy, UserProfile
                from src.bot.hr_bot import route_new_vacancy

                v_title = (scoring_result.sales_hook or raw_text_orig[:80]).strip()
                # UPSERT UserProfile for B2B Vendor CRM
                p_stmt = select(UserProfile).where(UserProfile.user_id == user_id)
                user_prof = (await session.execute(p_stmt)).scalar_one_or_none()
                if user_prof:
                    user_prof.is_b2b_vendor = True
                    user_prof.vendor_niche = scoring_result.niche_code
                    user_prof.vendor_quality_score = max(user_prof.vendor_quality_score or 0, 75)
                    user_prof.messages_seen_count = (user_prof.messages_seen_count or 0) + 1
                    user_prof.vendor_sales_hook = s_hook
                    await session.commit()

                new_vac = HRVacancy(
                    title=v_title[:250],
                    company_name=author_fname,
                    location_code=seller_loc,
                    niche_code=scoring_result.niche_code or "hr_hiring",
                    salary_text="По договоренности",
                    description=raw_text_orig,
                    hr_contact=f"@{author_uname}" if author_uname else f"ID: {user_id}",
                    author_username=author_uname,
                    author_telegram_id=user_id,
                    status="PUBLISHED"
                )
                session.add(new_vac)
                await session.commit()
                await session.refresh(new_vac)
                logger.info(f"💼 HR-RADAR B2C Vacancy Created! ID={new_vac.id}, Title='{new_vac.title}'")
                asyncio.create_task(route_new_vacancy(new_vac))
            except Exception as hr_err:
                logger.warning(f"Notice routing HR vacancy: {hr_err}")

        if niche in invalid_b2b_niches or conf < 60.0 or is_prop_listing:
            logger.info(f"🚫 DISCARDING B2B Seller lead for user {user_id}: niche='{scoring_result.niche_code}', conf={conf}%, is_prop={is_prop_listing}.")
        else:
            action = "AUTO_SAVE" if conf >= 85.0 else "NEED_APPROVAL"
            author_uname = getattr(last_m, "username", None) or (ext_data.author_username if ext_data else None)
            author_fname = getattr(last_m, "first_name", None) or f"User_{user_id}"
            raw_text_orig = getattr(last_m, "message_text", "") or (ext_data.raw_ad_text if ext_data else "")
            
            # Multi-Tier location code determination for seller
            seller_loc = "global"
            chat_titles = list(set([getattr(m, "chat_title", "") for m in messages if getattr(m, "chat_title", None)]))
            if chat_titles:
                from src.db.models import MonitoredChannel
                for ct in chat_titles:
                    ch_rec = (await session.execute(
                        select(MonitoredChannel).where(MonitoredChannel.title.ilike(f"%{ct}%"))
                    )).scalars().first()
                    if ch_rec and ch_rec.location_code and ch_rec.location_code != "global":
                        seller_loc = ch_rec.location_code
                        break

            if seller_loc == "global":
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
                NICHE_SPECIFIC_HOOKS = {
                    "real_estate": "Предложите риелтору/агентству подбор целевых клиентов на покупку и аренду жилья в Дубае через LeadRadar.",
                    "bike_rent": "Предложите прокату авто и байков горячие заявки туристов на аренду транспорта.",
                    "currency_exchange": "Предложите пункту обмена валют прямых клиентов на обмен USDT и наличных дирхамов/рублей.",
                    "legal_services": "Предложите юристу клиентов, ищущих оформление виз, ВНЖ и открытие счетов в ОАЭ.",
                    "hr_hiring": "Предложите работодателю автоматизацию поиска кандидатов и рекрутинга через LeadRadar.",
                    "marketing_smm": "Предложите SMM-специалисту клиентов на продвижение бизнеса и настройку рекламы.",
                    "tours_travel": "Предложите туроператору заявки от туристов на бронирование экскурсий и туров.",
                    "beauty_health": "Предложите мастеру бьюти-сферы новых клиентов на запись на услуги."
                }

                s_hook = (scoring_result.sales_hook or "").strip()
                if not s_hook or s_hook == "Продавец целевых услуг":
                    s_hook = NICHE_SPECIFIC_HOOKS.get(
                        scoring_result.niche_code,
                        f"Предложите поставщику услуг в нише '{scoring_result.niche_code}' готовый поток целевых клиентов через LeadRadar.win"
                    )

                new_outreach = OutreachLead(
                    author_username=author_uname,
                    author_first_name=author_fname,
                    telegram_id=user_id,
                    niche_code=scoring_result.niche_code,
                    location_code=seller_loc,
                    confidence_score=conf,
                    status=outreach_status,
                    raw_ad_text=raw_text_orig[:500],
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
                        f"💼 <b>ОБНАРУЖЕН B2B-ПРОДАВЕЦ (КАНДИДАТ В АУТРИЧ)!</b>\n"
                        f"───────────────────────────\n\n"
                        f"📍 <b>ГЕО:</b> {loc_flag}\n"
                        f"🏷️ <b>Ниша продавца:</b> {scoring_result.niche_code}\n"
                        f"👤 <b>Автор:</b> @{author_uname or 'без_юзернейма'} ({html.escape(author_fname)})\n"
                        f"💬 <b>Текст предложения:</b> «{html.escape(raw_text[:200])}»\n"
                        f"🎯 <b>Питч ИИ-Менеджера:</b> {html.escape(s_hook)}\n"
                        f"📊 <b>Уверенность ИИ:</b> {conf}%\n"
                        f"⚡ <b>Статус:</b> {outreach_status}\n\n"
                        f"ℹ️ <i>Продавец целевых услуг. Будет направлен в авто-аутрич Екатерины для продажи подписки LeadRadar.win.</i>"
                    )
                    from src.bot.keyboards import get_outreach_approval_keyboard
                    kb = get_outreach_approval_keyboard(new_outreach.id)
                    
                    superadmins_res = await session.execute(select(Partner).where((Partner.role == "SUPERADMIN") | (Partner.role == "ADMIN")))
                    superadmins = list(superadmins_res.scalars().all())
                    from src.bot.alert_bot import auto_publish_lead_after_5m
                    for sa in superadmins:
                        try:
                            if bot:
                                sent_msg = await bot.send_message(chat_id=sa.telegram_id, text=card_txt, parse_mode="HTML", reply_markup=kb)
                                if sent_msg and hasattr(sent_msg, "message_id"):
                                    asyncio.create_task(auto_publish_lead_after_5m(new_outreach.id, sa.telegram_id, sent_msg.message_id, is_outreach=True))
                        except Exception:
                            pass
                except Exception as b2b_alert_err:
                    logger.warning(f"B2B Seller Lead Superadmin notification notice: {b2b_alert_err}")


    if scoring_result and scoring_result.is_lead:
        logger.info(f"🔥 HOT/WARM Lead detected for user {user_id} in niche {scoring_result.niche_code} [{scoring_result.rubric_name}]")
        
        # Multi-Tier Geolocation Determination Hierarchy:
        # Tier 1: Source MonitoredChannel location_code lookup
        loc_code = "global"
        chat_titles = list(set([getattr(m, "chat_title", "") for m in messages if getattr(m, "chat_title", None)]))
        if chat_titles:
            from src.db.models import MonitoredChannel
            for ct in chat_titles:
                ch_rec = (await session.execute(
                    select(MonitoredChannel).where(MonitoredChannel.title.ilike(f"%{ct}%"))
                )).scalars().first()
                if ch_rec and ch_rec.location_code and ch_rec.location_code != "global":
                    loc_code = ch_rec.location_code
                    break

        # Tier 2: AI Scorer inferred location_code schema field
        if loc_code == "global" and getattr(scoring_result, "location_code", None) and scoring_result.location_code != "global":
            loc_code = scoring_result.location_code

        # Tier 3: Infer from message timeline text & chat titles
        if loc_code == "global":
            for m in messages:
                ch_name = getattr(m, "chat_title", "") or ""
                msg_txt = getattr(m, "message_text", "") or ""
                inferred = infer_location_code(ch_name + " " + msg_txt)
                if inferred != "global":
                    loc_code = inferred
                    break

        # Lock to protect against concurrent lead creation race conditions
        global _lead_creation_lock
        if '_lead_creation_lock' not in globals():
            _lead_creation_lock = asyncio.Lock()

        async with _lead_creation_lock:
            from src.db.models import Lead
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

                c_score = float(scoring_result.confidence_score or 0.85)
                if c_score > 1.0:
                    c_score = c_score / 100.0
                c_score = round(min(1.0, max(0.0, c_score)), 2)
                scoring_result.confidence_score = c_score

                # Save lead to Database
                lead = Lead(
                    user_id=user_id,
                    niche_code=scoring_result.niche_code,
                    location_code=loc_code,
                    temperature=scoring_result.temperature,
                    confidence_score=c_score,
                    intent_summary=final_summary,
                    sales_hook=scoring_result.sales_hook,
                    reasoning=scoring_result.reasoning,
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
        if 'lead' in locals() and lead:
            try:
                await session.refresh(lead)
            except Exception:
                pass

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
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
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


_gemini_cooldown_until = 0.0

async def _eval_with_gemini(timeline_str: str, active_prompt: Optional[str] = None) -> Optional[LeadScoringResult]:
    """Scores timeline using Google Gemini API (via google-genai SDK or httpx REST)."""
    global _gemini_cooldown_until
    if time.time() < _gemini_cooldown_until:
        logger.debug(f"⏳ Gemini API is on 5-minute cooldown ({int(_gemini_cooldown_until - time.time())}s remaining). Bypassing Gemini call.")
        return None

    from src.ai.rotator_engine import _extract_keys
    gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
    if not gemini_keys:
        return None
    sys_p = active_prompt or SYSTEM_PROMPT
    prompt = f"{sys_p}\n\nUser Messages Timeline:\n{timeline_str}"

    # 1. Try official google-genai SDK across available keys
    try:
        from google import genai
        from google.genai import types

        for key in gemini_keys:
            key_sfx = key[-4:] if len(key) >= 4 else key
            try:
                client = genai.Client(api_key=key)
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
                    logger.info(f"Successfully evaluated intent via Gemini SDK ({settings.GEMINI_MODEL}) Key=...{key_sfx}")
                    return LeadScoringResult(**json.loads(cleaned))
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "quota" in err_str.lower() or "resource" in err_str.lower():
                    logger.info(f"⏳ Gemini SDK Key ...{key_sfx} hit rate limit (429). Trying next key...")
                else:
                    logger.debug(f"Gemini SDK call on key ...{key_sfx} notice: {e}")

    except Exception as e:
        logger.debug(f"Gemini SDK call error: {e}. Trying httpx REST fallback...")

    # 2. Try direct HTTP REST API to Gemini across available keys
    try:
        import httpx
        json_schema = LeadScoringResult.model_json_schema()
        prompt_sys = f"{sys_p}\nRespond ONLY with valid JSON matching:\n{json.dumps(json_schema, ensure_ascii=False)}\n\nTimeline:\n{timeline_str}"
        payload = {
            "contents": [{"parts": [{"text": prompt_sys}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
        }

        for key in gemini_keys:
            key_sfx = key[-4:] if len(key) >= 4 else key
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={key}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(url, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                        cleaned = clean_json_text(text)
                        logger.info(f"Successfully evaluated intent via Gemini REST ({settings.GEMINI_MODEL}) Key=...{key_sfx}")
                        return LeadScoringResult(**json.loads(cleaned))
                    else:
                        logger.warning(f"Gemini REST API Key ...{key_sfx} returned HTTP {res.status_code}: {res.text[:100]}")
            except Exception as e:
                logger.error(f"Error calling Gemini REST API on Key ...{key_sfx}: {e}")

    except Exception as e:
        logger.error(f"Error in Gemini REST API fallback: {e}")

    return None





async def extract_stopwords_background(text: str, niche_code: str):
    """Extracts stop words from a false-positive (spam) message using LLM to automatically update the Gatekeeper."""
    try:
        from src.ai.rotator_engine import ai_rotator
        from src.db.session import async_session_maker
        from src.db.models import DynamicStopword
        
        sys_p = '''Extract 1 to 3 highly specific spam keywords or phrases from the text that indicate it is an ad or spam (e.g. "VPN", "залив", "прокси"). Output JSON: {"keywords": ["word1", "word2"]}'''
        res = await ai_rotator.generate_json(system_prompt=sys_p, user_prompt=text, temperature=0.0, timeout=10.0)
        
        if res and "keywords" in res and isinstance(res["keywords"], list):
            async with async_session_maker() as session:
                for kw in res["keywords"]:
                    kw_clean = kw.strip().lower()
                    if len(kw_clean) < 3: continue
                    # check if exists
                    existing = await session.execute(select(DynamicStopword).where(DynamicStopword.keyword == kw_clean))
                    if not existing.scalars().first():
                        session.add(DynamicStopword(keyword=kw_clean, niche_code=niche_code))
                await session.commit()
    except Exception as e:
        logger.error(f"Error extracting stopwords: {e}")

