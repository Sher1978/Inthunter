import json
import logging
import time
import os
import asyncio
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from src.ai.scorer import LeadScoringResult, clean_json_text
from src.config import settings
from src.ai.rotator_engine import _extract_keys

logger = logging.getLogger("intent_hunter.ai.batch_scorer")

# Cooldown tracking: key -> expiration timestamp
_key_cooldowns: Dict[str, float] = {}
# Round-robin tracking: provider -> next index
_key_indices: Dict[str, int] = {}
_rotator_lock = asyncio.Lock()

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
    async with _rotator_lock:
        if provider not in _key_indices:
            _key_indices[provider] = 0
            
        now = time.time()
        start_idx = _key_indices[provider]
        
        # Try to find a key that is off cooldown
        for _ in range(len(keys)):
            idx = _key_indices[provider] % len(keys)
            key = keys[idx]
            _key_indices[provider] += 1
            
            if _key_cooldowns.get(key, 0) <= now:
                _key_cooldowns[key] = now + cooldown_sec
                return key
        
        # If all keys are on cooldown, just wait for the first one in line
        # This is a basic backpressure approach
        key = keys[start_idx % len(keys)]
        wait_time = _key_cooldowns.get(key, 0) - now
        if wait_time > 0:
            logger.debug(f"⏳ All {provider} keys on cooldown. Waiting {wait_time:.1f}s for ...{key[-4:]}")
            await asyncio.sleep(wait_time)
            
        _key_cooldowns[key] = time.time() + cooldown_sec
        _key_indices[provider] = start_idx + 1
        return key

async def _eval_batch_with_provider(provider: str, base_url: str, model: str, headers_func, payload: dict, keys: List[str], cooldown_sec: float) -> Optional[Dict]:
    key = await _get_next_key(provider, keys, cooldown_sec)
    if not key:
        return None
        
    key_sfx = key[-4:] if len(key) >= 4 else key
    headers = headers_func(key)
    
    # Check if Gemini REST format
    if "generativelanguage" in base_url:
        url = f"{base_url}/{model}:generateContent?key={key}"
    else:
        url = base_url
        payload["model"] = model
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(url, json=payload, headers=headers)
            if res.status_code == 200:
                data = res.json()
                if "generativelanguage" in base_url:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    text = data["choices"][0]["message"]["content"]
                
                cleaned = clean_json_text(text)
                logger.info(f"✅ Successfully evaluated BATCH via {provider} ({model}) Key=...{key_sfx}")
                return json.loads(cleaned)
            elif res.status_code == 429:
                logger.warning(f"⏳ {provider} Rate Limit (429) on Key=...{key_sfx}. Adding penalty cooldown.")
                _key_cooldowns[key] = time.time() + (cooldown_sec * 3) # Penalty
            else:
                logger.warning(f"❌ {provider} Error {res.status_code} on Key=...{key_sfx}: {res.text[:100]}")
    except Exception as e:
        logger.error(f"Error calling {provider} BATCH on Key=...{key_sfx}: {e}")
        
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
        "Ты B2B ИИ-Анализатор. Твоя задача — классифицировать массив пользователей.\n"
        "Ответь строго JSON-словарем, где ключ - это ID из входящего массива, а значение - объект с результатами анализа.\n"
        "Пример формата ответа:\n"
        '{"123": {"is_lead": true, "niche_code": "real_estate", "reasoning": "ищет квартиру", "confidence_score": 0.9, "intent_summary": "Аренда 1-к квартиры"}}'
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
        "contents": [{"parts": [{"text": f"{sys_p}\\n\\nМассив:\\n{batch_json}"}]}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
    }
    
    logger.info(f"🧠 Sending AI Batch of {len(batch)} users (Total JSON length: {len(batch_json)} chars)")

    parsed_result = None
    
    # Tier 1: Groq (Llama 3 70B)
    groq_keys = _get_active_keys("Groq")
    if groq_keys and not parsed_result:
        model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
        if "qwen" in model or "compound" in model: model = "llama-3.3-70b-versatile"
        for _ in range(min(len(groq_keys), 3)): # Max 3 retries
            parsed_result = await _eval_batch_with_provider(
                "Groq", "https://api.groq.com/openai/v1/chat/completions", model,
                lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
                openai_payload, groq_keys, 3.5
            )
            if parsed_result: break
            


    # Tier 3: Gemini
    gemini_keys = _get_active_keys("Gemini")
    if gemini_keys and not parsed_result:
        model = getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
        for _ in range(min(len(gemini_keys), 3)):
            parsed_result = await _eval_batch_with_provider(
                "Gemini", "https://generativelanguage.googleapis.com/v1beta/models", model,
                lambda k: {"Content-Type": "application/json"},
                gemini_payload, gemini_keys, 4.0
            )
            if parsed_result: break

    if not parsed_result:
        logger.error(f"❌ ALL BATCH SCORING TIERS FAILED for {len(batch)} users!")
        return {}

    # 3. Map results
    final_map = {}
    for uid_str, data in parsed_result.items():
        try:
            uid = int(uid_str)
            lead_result = LeadScoringResult(
                reasoning=data.get("reasoning", "No reasoning provided"),
                validation_check={},
                is_lead=data.get("is_lead", False) if "is_lead" in data else (data.get("type") == "LEAD"),
                niche_code=data.get("niche_code") or data.get("niche"),
                rubric_name=None,
                confidence_score=float(data.get("confidence_score", 0.5)),
                intent_summary=data.get("intent_summary", ""),
                sales_hook=None
            )
            final_map[uid] = lead_result
        except Exception as e:
            logger.warning(f"Skipping invalid batch item {uid_str}: {e}")

    logger.info(f"✅ AI Batch Scoring complete. Evaluated {len(final_map)} / {len(batch)} users.")
    return final_map
