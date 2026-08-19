import json
import logging
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import UserActivityLog, UserProfile, Lead
from src.ai.schemas import LeadScoringResult

logger = logging.getLogger("intent_hunter.ai")

SYSTEM_PROMPT = """You are a strict lead qualification intelligence engine for B2B marketplaces.
Analyze the user's chat activity timeline and identify if the user is demonstrating a real BUYER or TENANT purchasing intention.

Target Niches:
- 'real_estate': Renting/buying apartments, house, Muong Thanh, Gold Coast in Nha Trang.
- 'bike_rent': Renting scooters/bikes, cars, transfer to Cam Ranh airport.
- 'currency_exchange': Exchanging RUB, VND, USDT.
- 'services_visa': Visa run, visa extension, expat services.
- 'auto_kasko': Insurance inquiries.

CRITICAL INTENT RULES:
1. BUYER/TENANT ONLY: Mark 'is_lead: true' ONLY if the user is a CLIENT LOOKING TO BUY, RENT, OR USE A SERVICE (e.g., "сниму", "ищу квартиру", "нужен байк", "где обменять", "сколько стоит").
2. REJECT ALL SELLERS / REALTORS / AGENTS / OFFER ANNOUNCEMENTS:
   If the message is a listing, rental announcement, ad, or offer from a landlord, realtor, agency, or service provider (e.g., "сдаётся", "сдам", "предлагаем", "аренда: 10 млн/мес", "депозит 1 месяц", "площадь: 100 м²", "контракт от 1 года"), YOU MUST SET 'is_lead: false'!
3. If 'is_lead: true', assign temperature: 'HOT' (urgent/specific buyer) or 'WARM' (inquiring buyer).
4. Generate 'sales_hook' - actionable advice for the salesperson on how to approach this buyer.
"""

async def evaluate_user_timeline(
    user_id: int,
    session: AsyncSession,
    messages: Optional[List[UserActivityLog]] = None
) -> Optional[LeadScoringResult]:
    """
    Fetches user's message timeline and calls Groq / Gemini AI to score intent.
    If is_lead is True, saves lead to database.
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

    # Format timeline for prompt
    timeline_str = "\n".join([
        f"[{m.timestamp.strftime('%Y-%m-%d %H:%M')}] {m.first_name or 'User'}: {m.message_text}"
        for m in reversed(messages)
    ])

    scoring_result: Optional[LeadScoringResult] = None
    provider = settings.AI_PROVIDER.lower()

    # 1. Try Groq API if requested or in auto mode with valid Groq key
    if (provider == "groq" or provider == "auto") and settings.GROQ_API_KEY and settings.GROQ_API_KEY != "gsk_your_groq_api_key_here":
        scoring_result = await _eval_with_groq(timeline_str)

    # 2. Try Gemini API if requested or fallback in auto mode
    if scoring_result is None and (provider == "gemini" or provider == "auto") and settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "mock_key_for_testing":
        scoring_result = await _eval_with_gemini(timeline_str)

    # 3. Rule-based fallback heuristic if no AI API key is configured or API calls failed
    if scoring_result is None:
        logger.info("Using Rule-Based Heuristic Scorer for timeline evaluation...")
        scoring_result = _fallback_heuristic_eval(messages)

    if scoring_result and scoring_result.is_lead:
        logger.info(f"🔥 HOT/WARM Lead detected for user {user_id} in niche {scoring_result.niche_code}")
        
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
        await session.commit()
        await session.refresh(lead)

    return scoring_result


async def _eval_with_groq(timeline_str: str) -> Optional[LeadScoringResult]:
    """Scores timeline using Groq Cloud API (Free tier)."""
    try:
        from groq import AsyncGroq
        
        client = AsyncGroq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=8.0)
        json_schema = LeadScoringResult.model_json_schema()
        
        prompt_sys = (
            f"{SYSTEM_PROMPT}\n\n"
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


async def _eval_with_gemini(timeline_str: str) -> Optional[LeadScoringResult]:
    """Scores timeline using Google Gemini API (via google-genai SDK or httpx REST)."""
    # 1. Try official google-genai SDK
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        prompt = f"{SYSTEM_PROMPT}\n\nUser Messages Timeline:\n{timeline_str}"

        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=LeadScoringResult,
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
        prompt_sys = f"{SYSTEM_PROMPT}\nRespond ONLY with valid JSON matching:\n{json.dumps(json_schema, ensure_ascii=False)}\n\nTimeline:\n{timeline_str}"

        payload = {
            "contents": [{"parts": [{"text": prompt_sys}]}],
            "generationConfig": {"response_mime_type": "application/json"}
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
