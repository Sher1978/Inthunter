import json
import logging
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import UserActivityLog, UserProfile, Lead
from src.ai.schemas import LeadScoringResult

logger = logging.getLogger("intent_hunter.ai")

SYSTEM_PROMPT = """You are a lead qualification intelligence engine for B2B marketplaces.
Analyze the user's chat activity timeline and identify if the user is demonstrating a real intention to purchase products/services in one of the active niches.

Target Niches:
- 'auto_kasko': Insurance, KASKO, OSAGO inquiries.
- 'real_estate': Buying/renting property, searching agents.
- 'auto_broker': Buying vehicles, car inspections.

Rules:
1. Mark 'is_lead: true' ONLY if the user asks for prices, recommendations, services, or shows clear purchasing signals.
2. If 'is_lead: true', assign temperature: 'WARM' or 'HOT'.
3. Generate 'sales_hook' - actionable advice for the salesperson on how to approach this exact lead based on their timeline.
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
    timeline_str = ""
    for msg in reversed(messages):
        chat_info = f"[{msg.chat_title or msg.chat_id}]"
        timeline_str += f"- {msg.timestamp.strftime('%Y-%m-%d %H:%M')} {chat_info}: \"{msg.message_text}\"\n"

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
        
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
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
    """Scores timeline using Google Gemini API."""
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
            logger.info(f"Successfully evaluated intent via Gemini ({settings.GEMINI_MODEL})")
            return LeadScoringResult(**json.loads(response.text))

    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}")

    return None


def _fallback_heuristic_eval(messages: List[UserActivityLog]) -> LeadScoringResult:
    """Heuristic fallback engine when AI model is offline/testing."""
    combined_text = " ".join([m.message_text.lower() for m in messages])
    
    kasko_keywords = ["каско", "осаго", "страховк", "ингос", "ресго", "альфастрах"]
    re_keywords = ["квартир", "аренд", "недвижим", "риелтор", "купить квартиру", "сниму"]
    broker_keywords = ["автоподбор", "пригнать авто", "дилер", "машин", "купить авто"]

    if any(k in combined_text for k in kasko_keywords):
        return LeadScoringResult(
            is_lead=True,
            niche_code="auto_kasko",
            temperature="HOT",
            confidence_score=0.90,
            intent_summary="Пользователь интересуется автострахованием / КАСКО в сообществе.",
            sales_hook="Предложите мгновенный расчет КАСКО с дисконтом брокера."
        )
    elif any(k in combined_text for k in re_keywords):
        return LeadScoringResult(
            is_lead=True,
            niche_code="real_estate",
            temperature="WARM",
            confidence_score=0.85,
            intent_summary="Пользователь ищет варианты по недвижимости или услуги риелтора.",
            sales_hook="Уточните параметры объекта и предложите подборку эксклюзивных вариантов."
        )
    elif any(k in combined_text for k in broker_keywords):
        return LeadScoringResult(
            is_lead=True,
            niche_code="auto_broker",
            temperature="HOT",
            confidence_score=0.88,
            intent_summary="Пользователь планирует покупку авто или ищет услуги автоброкера.",
            sales_hook="Запросите желаемую марку и бюджет, предложите бесплатную первичную консультацию."
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
