import json
import logging
import time
import os
import random
import asyncio
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from src.ai.scorer import LeadScoringResult, clean_json_text
from src.config import settings
from src.ai.rotator_engine import _extract_keys, acquire_key_with_pacing, _key_cooldowns
from src.ai.budget_guard import ai_budget_guard

logger = logging.getLogger("intent_hunter.ai.batch_scorer")

class BatchItemResult(BaseModel):
    user_id: int
    is_lead: bool
    niche_code: Optional[str] = None
    reasoning: str
    confidence_score: float = 0.5
    intent_summary: str = ""

def _get_active_keys(provider: str) -> List[str]:
    if provider == "Groq":
        return _extract_keys(getattr(settings, "GROQ_API_KEYS", ""), getattr(settings, "GROQ_API_KEY", ""), prefix_filter="gsk_")
    elif provider == "Gemini":
        return _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
    return []

async def _get_next_key(provider: str, keys: List[str], cooldown_sec: float) -> Optional[str]:
    now = time.time()
    ready_count = sum(1 for k in keys if _key_cooldowns.get(k, 0.0) <= now)
    
    # Dynamic adaptive pacing based on available active keys
    # Scale pacing inversely with pool size to protect small pools while maximizing throughput of large pools
    base_pacing = 4.0 if provider == "Gemini" else 1.5
    adaptive_pacing = max(0.5, base_pacing / max(1, ready_count)) if ready_count > 0 else base_pacing
    
    key = await acquire_key_with_pacing(provider, keys, adaptive_pacing)
    if key:
        return key
        
    now = time.time()
    min_wait = min([_key_cooldowns.get(k, 0) - now for k in keys], default=999.0)
    if min_wait > 60.0:
        logger.debug(f"⏳ All {provider} keys on 24h/long cooldown ({min_wait:.1f}s remaining). Skipping provider.")
        return None
    elif min_wait > 0 and min_wait <= 45.0:
        jitter_wait = min_wait + random.uniform(0.1, 0.5)
        logger.info(f"⏳ System Capacity Adapt: All {provider} keys on cooldown. Pausing {jitter_wait:.1f}s for key recovery...")
        await asyncio.sleep(jitter_wait)
        return await acquire_key_with_pacing(provider, keys, adaptive_pacing)
        
    return None

async def _eval_batch_with_provider(provider: str, base_url: str, candidate_models: List[str], headers_func, payload: dict, keys: List[str], cooldown_sec: float) -> Optional[Dict]:
    can_exec, _ = await ai_budget_guard.can_make_request(provider)
    if not can_exec:
        return None

    key = await _get_next_key(provider, keys, cooldown_sec)
    if not key:
        return None
        
    key_sfx = key[-4:] if len(key) >= 4 else key
    headers = headers_func(key)
    
    for model in candidate_models:
        # Check if Gemini REST format
        if "generativelanguage" in base_url:
            base = base_url.rstrip("/")
            if base.endswith("/models"):
                base = base[:-7]
            url = f"{base}/models/{model}:generateContent?key={key}"
        else:
            url = base_url
            payload["model"] = model
        
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    if "generativelanguage" in base_url:
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                    else:
                        text = data["choices"][0]["message"]["content"]
                    
                    cleaned = clean_json_text(text)
                    logger.info(f"✅ Successfully evaluated BATCH via {provider} ({model}) Key=...{key_sfx}")
                    
                    # Record token usage
                    in_tok = len(json.dumps(payload, ensure_ascii=False)) // 4
                    out_tok = len(text) // 4
                    await ai_budget_guard.record_usage(provider, in_tok, out_tok)
                    return json.loads(cleaned)
                elif res.status_code in (401, 402, 403) or (res.status_code == 400 and "API key not valid" in res.text):
                    cooldown_len = 86400.0  # 24 hours
                    logger.error(f"🛑 {provider} Dead/Unauthorized (HTTP {res.status_code}) on Key=...{key_sfx}. Disabling for 24h.")
                    _key_cooldowns[key] = time.time() + cooldown_len
                    break  # Key is dead/unauthorized, skip other models for this key
                elif res.status_code == 429:
                    cooldown_len = max(180.0, float(getattr(settings, "AI_KEY_COOLDOWN_SEC", 180.0)))
                    logger.warning(f"⏳ {provider} Rate Limit (429) on Key=...{key_sfx}. Setting {int(cooldown_len)}s cooldown.")
                    _key_cooldowns[key] = time.time() + cooldown_len
                    await ai_budget_guard.record_429_error(provider, key_sfx)
                    break  # Key hit rate limit, skip other models for this key
                else:
                    logger.warning(f"❌ {provider} Error {res.status_code} ({model}) on Key=...{key_sfx}: {res.text[:100]}")
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate limit" in err_str.lower():
                cooldown_len = max(180.0, float(getattr(settings, "AI_KEY_COOLDOWN_SEC", 180.0)))
                _key_cooldowns[key] = time.time() + cooldown_len
                await ai_budget_guard.record_429_error(provider, key_sfx)
                break
            logger.warning(f"Notice calling {provider} BATCH ({model}) on Key=...{key_sfx}: {e}")
            
    return None

async def evaluate_batch(batch: List[Dict[str, Any]], session: AsyncSession) -> Dict[int, LeadScoringResult]:
    if not batch:
        return {}

    # 1. Prepare JSON prompt array
    items_for_prompt = []
    for item in batch:
        items_for_prompt.append({
            "id": str(item["user_id"]),
            "text": item["timeline_str"]
        })
        
    batch_json = json.dumps(items_for_prompt, ensure_ascii=False)
    
    sys_p = (
        "Ты B2B ИИ-Анализатор Лидов. Твоя задача — найти потенциальных клиентов в массиве сообщений.\n"
        "Разрешенные типы интентов (type):\n"
        "1. BUYER: Пользователь хочет купить, снять, арендовать, заказать услугу ИЛИ просит совет, рекомендации (недвижимость, байки, авто, обмен валют, юристы, няни, туры и т.д.). ЭТО ЛИД.\n"
        "2. WARM_LEAD: Задает общие вопросы, которые могут вести к сделке (переезд, ВНЖ, налоги, советы по районам, садикам, школам). ЭТО ЛИД.\n"
        "3. SELLER: Риелтор, агентство, собственник, предлагающий услуги/продажу/аренду. ЭТО НЕ ЛИД (если только мы не ищем B2B продавцов).\n"
        "4. JOB_SEEKER: Пользователь ищет работу, предлагает свои услуги как сотрудник, рассылает резюме (соискатель). ЭТО ЛИД.\n"
        "5. TRASH: Спам, реклама рулетки/крипты, бессмысленный флуд, обсуждение новостей. ЭТО НЕ ЛИД.\n\n"
        "Определяй нишу (niche) из контекста (например: real_estate, bike_rent, currency_exchange, legal, community, auto_kasko, visa, job_seeker и т.д.).\n"
        "ОБЯЗАТЕЛЬНО возвращай поле `is_lead`: true (для BUYER, WARM_LEAD, JOB_SEEKER) или false (для SELLER и TRASH).\n"
        "Твоя задача — находить ЛЮБЫЕ зацепки. Не будь слишком строгим! Если есть малейшее подозрение, что человеку нужна помощь или услуга - ставь is_lead: true.\n"
        "Ответь строго JSON-словарем, где ключ - это ID из входящего массива, а значение - объект.\n"
        "Пример формата ответа:\n"
        '{"123": {"type": "BUYER", "niche": "bike_rent", "is_lead": true, "reasoning": "ищет байк на месяц", "confidence_score": 0.9, "intent_summary": "Аренда NMAX"}}'
    )
    
    # 2. Build standard OpenAI payload
    openai_payload = {
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": f"Классифицируй массив:\n{batch_json}"}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }
    
    # Gemini payload
    gemini_payload = {
        "contents": [{"parts": [{"text": sys_p + "\n\nМассив:\n" + batch_json}]}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
    }
    
    logger.info(f"🧠 Sending AI Batch of {len(batch)} users (Total JSON length: {len(batch_json)} chars)")

    parsed_result = None
    
    # Tier 1: Groq Cloud Pool
    groq_keys = _get_active_keys("Groq")
    if groq_keys and not parsed_result:
        model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile") or "llama-3.3-70b-versatile"
        candidate_models = list(dict.fromkeys([model, "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama3-70b-8192"]))
        for _ in range(min(len(groq_keys), 3)):
            parsed_result = await _eval_batch_with_provider(
                "Groq", "https://api.groq.com/openai/v1/chat/completions", candidate_models,
                lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
                openai_payload, groq_keys, 1.5
            )
            if parsed_result: break

    # Tier 2: Google AI Studio (Gemini REST)
    gemini_keys = _get_active_keys("Gemini")
    if gemini_keys and not parsed_result:
        gem_m = getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
        candidate_models = list(dict.fromkeys([gem_m, "gemini-3.6-flash", "gemini-2.0-flash", "gemini-1.5-flash"]))
        for _ in range(min(len(gemini_keys), 3)):
            parsed_result = await _eval_batch_with_provider(
                "Gemini", "https://generativelanguage.googleapis.com/v1beta", candidate_models,
                lambda k: {"Content-Type": "application/json"},
                gemini_payload, gemini_keys, 4.0
            )
            if parsed_result: break

    if not parsed_result:
        logger.error(f"❌ ALL BATCH SCORING TIERS FAILED for {len(batch)} users!")
        return None

    if not isinstance(parsed_result, dict):
        logger.error(f"❌ AI returned non-dict response! ({type(parsed_result)})")
        return None

    # 3. Map results
    final_map = {}
    for uid_str, data in parsed_result.items():
        try:
            uid = int(uid_str)
            lead_result = LeadScoringResult(
                reasoning=data.get("reasoning", "No reasoning provided"),
                validation_check={},
                is_lead=data.get("is_lead", False) if "is_lead" in data else (data.get("type") in ["BUYER", "WARM_LEAD", "RENT_REALTY", "BUY_REALTY", "JOB_SEEKER"]),
                is_job_seeker=data.get("type") == "JOB_SEEKER",
                niche_code=data.get("niche_code") or data.get("niche"),
                rubric_name=data.get("type"),
                confidence_score=float(data.get("confidence_score", 0.5)),
                intent_summary=data.get("intent_summary", ""),
                sales_hook=None
            )
            final_map[uid] = lead_result
        except Exception as e:
            logger.warning(f"Skipping invalid batch item {uid_str}: {e}")

    logger.info(f"✅ AI Batch Scoring complete. Evaluated {len(final_map)} / {len(batch)} users.")
    return final_map
