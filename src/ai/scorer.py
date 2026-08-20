import json
import logging
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import UserActivityLog, UserProfile, Lead
from src.ai.schemas import LeadScoringResult

logger = logging.getLogger("intent_hunter.ai")

SYSTEM_PROMPT = """You are an elite B2B Lead Qualification Intelligence Engine for "RADAR LeadScanner".
Your sole job is to analyze Telegram chat message timelines and detect strictly qualified purchasing/renting intents for target niches.

### 1. ROLE & OBJECTIVE:
You act as a strict B2B lead qualification analyst. Analyze messages from the target user marked with tag `[TARGET_USER]`.
Your goal is to reject all noise, job seekers, seller advertisements, historical anecdotes, and unspecific chatter, identifying ONLY genuine client buyers or tenants.

### 2. CHAIN-OF-THOUGHT & DECLARATIVE VALIDATION MATRIX:
You MUST execute the analysis in sequence:
1. First, generate `reasoning` (1-2 sentences of logical analysis evaluating the intent of `[TARGET_USER]`).
2. Second, evaluate `validation_check`:
   - `is_author_seeking_service`: Is `[TARGET_USER]` actively seeking to buy, rent, or use a service/property?
   - `is_author_offering_service`: Is `[TARGET_USER]` a seller, landlord, realtor, agent, or service provider offering a service/property?
   - `is_time_relevant`: Is this request relevant NOW / recently, or is it an old story/anecdote?
3. Third, determine `is_lead`: Set `is_lead: true` ONLY IF `is_author_seeking_service` is true AND `is_author_offering_service` is false AND `is_time_relevant` is true. If `is_author_offering_service` is true, `is_lead` MUST BE FORCED TO FALSE.

---

### 3. TARGET NICHES & TRIGGER MATRIX:
- Niche: "real_estate"
  • HOT Trigger: Asks for realtor recommendations, apartment search assistance, urgent rent/buy, price inquiry for specific properties, Muong Thanh, Gold Coast, 1BR/2BR.
  • WARM Trigger: Questions about neighborhood infrastructure, mortgage rates discussion, pros/cons of specific developers.

- Niche: "bike_rent"
  • HOT Trigger: Asking for scooter/bike rental (Honda NVX, PCX, Vision), car rental, Cam Ranh airport transfer with specific date/time.
  • WARM Trigger: Questions about traffic fines, international driving license rules, fuel costs.

- Niche: "currency_exchange"
  • HOT Trigger: Asking for instant currency exchange rates (USDT -> Cash VND, RUB -> VND), delivery of cash, exchanging specific amounts ($1500, 100k RUB).
  • WARM Trigger: Questions about bank fees, ATM withdrawal limits.

- Niche: "services_visa"
  • HOT Trigger: Asking for visa run (Laos/Cambodia), visa extension services, urgent passport/visa agent contacts.
  • WARM Trigger: Asking about visa policy updates, stay duration rules.

- Niche: "auto_kasko"
  • HOT Trigger: Asking for insurance agent contacts, instant KASKO/OSAGO calculation, cheapest broker offers.
  • WARM Trigger: Asking about insurance company payout experiences, coverage details.

- Niche: "medical_services"
  • HOT Trigger: Asking for recommended clinics, specific doctor recommendations (dentist, cosmetologist), urgent checkup pricing.
  • WARM Trigger: Asking about recovery time, general procedure feedback.

---

### 4. FEW-SHOT EXAMPLES (INCLUDING HARD NEGATIVES):

Example 1 (Input): "[TARGET_USER] Maxim: Ребят, посоветуйте проверенного риелтора в Дубае, нужно срочно подобрать 1BR под инвестиции!"
Example 1 (Output):
{
  "reasoning": "Пользователь [TARGET_USER] спрашивает рекомендации по выбору проверенного риелтора в Дубае для срочной покупки 1BR под инвест.",
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
  "intent_summary": "Пользователь ищет проверенного риелтора в Дубае для срочного подбора 1BR под инвест.",
  "sales_hook": "Спешит с покупкой (инвестиции). В первом сообщении покажите 2 готовых варианта с высокой доходностью и предложите созвон."
}

Example 2 (Hard Negative - Opinion / Flood): "[TARGET_USER] Alex: Да уж, цены на недвижимость сейчас конечно космические, хрен что купишь."
Example 2 (Output):
{
  "reasoning": "Пользователь делитcя эмоциональным мнением о высоких ценах на жилье. Запроса на подбор или услугу нет.",
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
  "reasoning": "Пользователь сам является агентом/продавцом страховых полисов КАСКО/ОСАГО. Это реклама услуг.",
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

Example 5 (Hard Negative - Past Experience Discussion): "[TARGET_USER] Elena: В 2022 году брала КАСКО в Ингосе, тогда всё выплатили нормально, проблем не было."
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
        user_name = getattr(m, "first_name", None) or (m.user.first_name if (hasattr(m, "user") and m.user) else None) or f"User_{m.user_id}"
        time_str = m.timestamp.strftime('%Y-%m-%d %H:%M') if m.timestamp else ""
        timeline_lines.append(f"[{time_str}] {user_tag} {user_name}: {m.message_text}")
    timeline_str = "\n".join(timeline_lines)

    scoring_result: Optional[LeadScoringResult] = None
    provider = settings.AI_PROVIDER.lower()

    # 1. Try Groq API if requested or in auto mode with valid Groq key
    if (provider == "groq" or provider == "auto") and settings.GROQ_API_KEY and settings.GROQ_API_KEY != "gsk_your_groq_api_key_here":
        scoring_result = await _eval_with_groq(timeline_str, active_system_prompt)

    # 2. Try Gemini API if requested or fallback in auto mode
    if scoring_result is None and (provider == "gemini" or provider == "auto") and settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "mock_key_for_testing":
        scoring_result = await _eval_with_gemini(timeline_str, active_system_prompt)

    # 3. Rule-based fallback heuristic if no AI API key is configured or API calls failed
    if scoring_result is None:
        logger.info("Using Rule-Based Heuristic Scorer for timeline evaluation...")
        scoring_result = _fallback_heuristic_eval(messages)

    if scoring_result and scoring_result.is_lead:
        logger.info(f"🔥 HOT/WARM Lead detected for user {user_id} in niche {scoring_result.niche_code} [{scoring_result.rubric_name}]")
        
        # Save lead to Database
        lead = Lead(
            user_id=user_id,
            niche_code=scoring_result.niche_code,
            temperature=scoring_result.temperature,
            confidence_score=scoring_result.confidence_score,
            intent_summary=scoring_result.intent_summary,
            sales_hook=scoring_result.sales_hook,
            status="AVAILABLE",
            price=800.00 if scoring_result.temperature == "HOT" else 500.00
        )
        session.add(lead)

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
        if is_new_rubric:
            try:
                from src.bot.alert_bot import notify_superadmins_new_rubric
                await notify_superadmins_new_rubric(rubric_code=rubric_code, rubric_name=rubric_title)
            except Exception as e:
                logger.error(f"Error notifying superadmins of new rubric: {e}")

    return scoring_result


async def _eval_with_groq(timeline_str: str, active_prompt: Optional[str] = None) -> Optional[LeadScoringResult]:
    """Scores timeline using Groq Cloud API (Free tier)."""
    try:
        from groq import AsyncGroq
        
        client = AsyncGroq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=8.0)
        json_schema = LeadScoringResult.model_json_schema()
        sys_p = active_prompt or SYSTEM_PROMPT
        
        prompt_sys = (
            f"{sys_p}\n\n"
            f"You MUST respond ONLY with valid JSON matching this schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )
        
        completion = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": prompt_sys},
                {"role": "user", "content": f"User Messages Timeline:\n{timeline_str}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        content = completion.choices[0].message.content
        if content:
            logger.info(f"Successfully evaluated intent via Groq ({settings.GROQ_MODEL})")
            return LeadScoringResult(**json.loads(content))

    except Exception as e:
        logger.error(f"Error calling Groq API: {e}")
    
    return None


async def _eval_with_gemini(timeline_str: str, active_prompt: Optional[str] = None) -> Optional[LeadScoringResult]:
    """Scores timeline using Google Gemini API (via google-genai SDK or httpx REST)."""
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
            logger.info(f"Successfully evaluated intent via Gemini SDK ({settings.GEMINI_MODEL})")
            return LeadScoringResult(**json.loads(response.text))

    except Exception as e:
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
                logger.info(f"Successfully evaluated intent via Gemini REST ({settings.GEMINI_MODEL})")
                return LeadScoringResult(**json.loads(text))
            else:
                logger.warning(f"Gemini REST API returned HTTP {res.status_code}: {res.text[:100]}")

    except Exception as e:
        logger.error(f"Error calling Gemini REST API: {e}")

    return None


def _fallback_heuristic_eval(messages: List[UserActivityLog]) -> LeadScoringResult:
    """Heuristic fallback engine to classify BUYER intent and exclude REALTOR/SELLER offers."""
    combined_text = " ".join([m.message_text.lower() for m in messages])

    # 1. Strict Seller / Realtor / Landlord Offer Exclusions
    seller_offer_triggers = [
        "сдаётся", "сдается", "сдам", "предлагаю", "предлагаем", "аренда:", "депозит", 
        "в наличии", "оплата за", "контракт от", "договор на", "млн/мес", "млн вьетнамских",
        "площадь:", "планировка:", "подходит для", "полностью меблирован", "тв, холодильник",
        "залог 2", "залог 1", "депозит 2", "депозит 1", "агентов не беспокоить", "комиссия",
        "обмен по курсу", "наши курсы", "принимаем рубли", "доставка от $", "наш прайс", "услуги гида",
        "визаран от $"
    ]

    buyer_intent_triggers = [
        "сниму", "ищу", "нужна", "нужен", "подскажите", "где снять", "посоветуйте",
        "хочу снять", "кто сдает", "кто сдаст", "снял бы", "снимаю", "ищем", "ищу квартиру",
        "где обменять", "кто меняет", "почем курс", "нужно обменять", "нужен байк",
        "где взять байк", "как сделать визу", "кто делает визаран"
    ]

    has_seller_offer = any(s in combined_text for s in seller_offer_triggers)
    has_buyer_intent = any(b in combined_text for b in buyer_intent_triggers)

    if has_seller_offer and not has_buyer_intent:
        logger.info("Filtered out seller/realtor offer announcement in heuristic scorer.")
        return LeadScoringResult(
            is_lead=False,
            niche_code="real_estate",
            temperature="COLD",
            confidence_score=0.0,
            intent_summary="Объявление от риэлтора/продавца (предложение аренды).",
            sales_hook=""
        )

    # 2. Buyer Intent Keyword Matching
    re_buyer = ["сниму", "ищу квартиру", "ищу жилье", "аренда квартиры", "нужен дом", "кондо", "муонг тхань", "голд кост", "студи"]
    bike_buyer = ["аренда байка", "нужен байк", "возьму байк", "скутер", "аренда авто", "трансфер", "камрань"]
    currency_buyer = ["где обменять", "обмен рублей", "нужны донги", "usdt нал", "кто меняет"]
    visa_buyer = ["нужен визаран", "кто делает визу", "продление визы", "визаран"]
    kasko_buyer = ["нужна страховка", "каско", "осаго"]

    if any(k in combined_text for k in re_buyer) or (has_buyer_intent and "дом" in combined_text):
        return LeadScoringResult(
            is_lead=True,
            niche_code="real_estate",
            temperature="HOT",
            confidence_score=0.92,
            intent_summary="Клиент ищет аренду жилья / апартаментов в Нячанге.",
            sales_hook="Запросите даты заезда, бюджет и район (Центр / Север / Муонг Тхань) и предложите варианты."
        )
    elif any(k in combined_text for k in bike_buyer):
        return LeadScoringResult(
            is_lead=True,
            niche_code="bike_rent",
            temperature="HOT",
            confidence_score=0.89,
            intent_summary="Клиент ищет аренду байка / авто или трансфер в аэропорт Камрань.",
            sales_hook="Уточните модель (NVX, PCX, Vision), срок аренды и необходимость доставки к отелю."
        )
    elif any(k in combined_text for k in currency_buyer):
        return LeadScoringResult(
            is_lead=True,
            niche_code="currency_exchange",
            temperature="HOT",
            confidence_score=0.95,
            intent_summary="Клиенту требуется обмен валюты (RUB/VND/USDT) в Нячанге.",
            sales_hook="Предложите актуальный выгодный курс и курьерскую доставку."
        )
    elif any(k in combined_text for k in visa_buyer):
        return LeadScoringResult(
            is_lead=True,
            niche_code="services_visa",
            temperature="WARM",
            confidence_score=0.85,
            intent_summary="Клиент ищет услуги визарана или продления визы.",
            sales_hook="Предложите даты ближайшего визарана в Лаос/Камбоджу."
        )
    elif any(k in combined_text for k in kasko_buyer):
        return LeadScoringResult(
            is_lead=True,
            niche_code="auto_kasko",
            temperature="WARM",
            confidence_score=0.80,
            intent_summary="Пользователь интересуется автострахованием.",
            sales_hook="Предложите расчет стоимости полиса."
        )
    else:
        return LeadScoringResult(
            is_lead=False,
            niche_code="unknown",
            temperature="WARM",
            confidence_score=0.0,
            intent_summary="Обычное общение без явного намерения совершить покупку.",
            sales_hook=""
        )
