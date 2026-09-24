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
    elif provider == "xAI_Grok":
        return _extract_keys(getattr(settings, "XAI_API_KEYS", ""), getattr(settings, "XAI_API_KEY", ""))
    return []

async def _get_next_key(provider: str, keys: List[str], cooldown_sec: float) -> Optional[str]:
    now = time.time()
    ready_count = sum(1 for k in keys if _key_cooldowns.get(k, 0.0) <= now)
    
    # The pacing applied here is the COOLDOWN FOR THE SPECIFIC KEY, not the global delay!
    # For Gemini, each free key allows 15 RPM -> 4.0 seconds per request minimum.
    # We must NOT decrease this below the provider's per-key limit, otherwise the key gets rate-limited instantly.
    base_pacing = 4.5 if provider == "Gemini" else 1.5
    
    key = await acquire_key_with_pacing(provider, keys, base_pacing)
    if key:
        return key
        
    now = time.time()
    min_wait = min([_key_cooldowns.get(k, 0) - now for k in keys], default=999.0)
    if min_wait > 86000.0:
        logger.debug(f"⏳ All {provider} keys on 24h/long cooldown ({min_wait:.1f}s remaining). Skipping provider.")
        return None
    elif min_wait > 0 and min_wait <= 360.0:
        jitter_wait = min(min_wait, 10.0) + random.uniform(0.1, 0.5)
        logger.info(f"⏳ System Capacity Adapt: All {provider} keys on cooldown ({min_wait:.1f}s). Pausing {jitter_wait:.1f}s for key recovery...")
        await asyncio.sleep(jitter_wait)
        return await acquire_key_with_pacing(provider, keys, base_pacing)
        
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
                    cooldown_len = 35.0  # 35s rate limit RPM reset window
                    logger.warning(f"⏳ {provider} Rate Limit (429) on Key=...{key_sfx}. Setting {int(cooldown_len)}s RPM cooldown.")
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
        "Ты B2B ИИ-Анализатор Лидов системы Radar AI.\n"
        "СТРОГИЕ ПРАВИЛА КЛАССИФИКАЦИИ ИНТЕНТА:\n\n"
        "1. BUYER (КЛИЕНТСКИЙ ЛИД, is_lead: true, is_vendor: false):\n"
        "   Пользователь, который СПРАШИВАЕТ, ИЩЕТ или хочет ЗАКАЗАТЬ услугу/товар для себя.\n"
        "   - Обмен валют/крипты: 'Кто меняет USDT?', 'Где обменять рубли на донги?', 'Нужен обмен $1000 USDT', 'Подскажите проверенный обменник'.\n"
        "   - Недвижимость: 'Сниму квартиру', 'Ищу студию', 'Хочу купить виллу'.\n"
        "   - Услуги: 'Нужен визаран', 'Ищу юриста', 'Нужен трансфер'.\n\n"
        "2. SELLER / B2B_PARTNER (ОБМЕННИК / ВЕНДОР / ИСПОЛНИТЕЛЬ, is_lead: false, is_vendor: true):\n"
        "   Обменник, сервис, риелтор, агентство или бизнес, ПРЕДЛАГАЮЩИЙ или РЕКЛАМИРУЮЩИЙ свои услуги/продажу.\n"
        "   - Обмен валют/крипты: 'Меняем USDT на наличные', 'Продам USDT', 'Вывод криптовалюты 24/7', 'Лучший курс обмена USDT/рубли', 'Доставка наличных', 'Криптообменник в центре', 'Купим/продам USDT пишите в ЛС'.\n"
        "   - Недвижимость: 'Сдается квартира', 'Продам виллу', 'Агентство недвижимости'.\n"
        "   - Услуги: 'Оформление виз', 'Услуги юриста', 'Трансфер в аэропорт'.\n\n"
        "3. HR_HIRING (is_lead: false, is_vacancy: true): Работодатель ищет сотрудника (вакансия).\n"
        "4. JOB_SEEKER (is_lead: false, is_job_seeker: true): Соискатель ищет работу.\n"
        "5. TRASH (is_lead: false): Спам, реклама казино, флуд.\n\n"
        "КРИТИЧЕСКИ ВАЖНО: Если автор ПРЕДЛАГАЕТ или РЕКЛАМИРУЕТ услуги обмена валют/USDT (ВЕНДОР/ОБМЕННИК), то is_lead = false, is_vendor = true! is_lead = true ТОЛЬКО когда пользователь ИЩЕТ или СПРАШИВАЕТ, где обменять.\n\n"
        "Определяй нишу (niche): real_estate, bike_rent, currency_exchange, legal_services, auto_kasko, visa, job_seeker и т.д.\n"
        "Ответь строго JSON-словарем, где ключ - это ID, а значение - объект:\n"
        '{"123": {"type": "BUYER", "niche": "currency_exchange", "is_lead": true, "reasoning": "ищет где обменять 1000 USDT", "confidence_score": 0.95, "intent_summary": "Обмен 1000 USDT"}}'
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
    
    # Tier 1: xAI Grok (if available)
    xai_keys = _get_active_keys("xAI_Grok")
    if xai_keys and not parsed_result:
        model = getattr(settings, "XAI_GROK_MODEL", "grok-2-latest")
        candidate_models = list(dict.fromkeys([model, "grok-2-latest", "grok-beta"]))
        for _ in range(min(len(xai_keys), 3)):
            parsed_result = await _eval_batch_with_provider(
                "xAI_Grok", "https://api.x.ai/v1/chat/completions", candidate_models,
                lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
                openai_payload, xai_keys, 2.0
            )
            if parsed_result: break

    # Tier 2: Groq Cloud Pool
    groq_keys = _get_active_keys("Groq")
    if groq_keys and not parsed_result:
        model = getattr(settings, "SAFE_GROQ_MODEL", "openai/gpt-oss-120b")
        candidate_models = list(dict.fromkeys([model, "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "groq/compound", "groq/compound-mini"]))
        for _ in range(min(len(groq_keys), 3)):
            parsed_result = await _eval_batch_with_provider(
                "Groq", "https://api.groq.com/openai/v1/chat/completions", candidate_models,
                lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
                openai_payload, groq_keys, 1.5
            )
            if parsed_result: break

    # Tier 3: Google AI Studio (Gemini REST)
    gemini_keys = _get_active_keys("Gemini")
    if gemini_keys and not parsed_result:
        gem_m = getattr(settings, "SAFE_GEMINI_MODEL", "gemini-2.5-flash")
        candidate_models = list(dict.fromkeys([gem_m, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-2.5-pro"]))
        for _ in range(max(len(gemini_keys), 3)):
            parsed_result = await _eval_batch_with_provider(
                "Gemini", "https://generativelanguage.googleapis.com/v1beta", candidate_models,
                lambda k: {"Content-Type": "application/json"},
                gemini_payload, gemini_keys, 4.0
            )
            if parsed_result: break

    # Tier 4 Fallback: If all fast tiers are temporarily rate-limited, wait 5s and retry via AIRotatorEngine
    if not parsed_result:
        logger.warning(f"⏳ Primary AI Batch Tiers rate-limited/busy for {len(batch)} users. Waiting 5s for fallback retry via AIRotator Engine...")
        await asyncio.sleep(5.0)
        try:
            from src.ai.rotator_engine import ai_rotator
            parsed_result = await ai_rotator.generate_json(
                system_prompt=sys_p,
                user_prompt=f"Классифицируй массив:\n{batch_json}",
                temperature=0.1,
                timeout=15.0
            )
        except Exception as fallback_err:
            logger.warning(f"Notice: AIRotator Engine batch fallback failed: {fallback_err}")

    if not parsed_result:
        logger.error(f"❌ ALL BATCH SCORING TIERS FAILED for {len(batch)} users!")
        return None

    if isinstance(parsed_result, list):
        logger.info(f"ℹ️ AI returned JSON list of {len(parsed_result)} items instead of dict. Normalizing into dict...")
        dict_map = {}
        for idx, item in enumerate(parsed_result):
            if isinstance(item, dict):
                item_id = str(item.get("id") or item.get("user_id") or (batch[idx]["user_id"] if idx < len(batch) else idx))
                dict_map[item_id] = item
        parsed_result = dict_map

    if not isinstance(parsed_result, dict):
        logger.error(f"❌ AI returned non-dict response! ({type(parsed_result)})")
        return None

    # 3. Map results & apply Deterministic Hard Guards
    prop_listing_patterns = [
        "for sale", "exclusive villa", "villa for sale", "apartment for sale", "flat for sale", "unit for sale",
        "resale unit", "handover in", "plot size", "selling @", "ask - aed",
        "aed 1.", "aed 2.", "aed 3.", "aed 4.", "aed 5.", "aed 6.",
        "продам квартиру", "продам виллу", "продается вилла", "продается квартира", "сдается квартира"
    ]
    crypto_vendor_patterns = [
        # General exchange terms
        "обмен валют", "криптообменник", "наш обменник", "меняем usdt", "меняем рубли", "меняем валюту",
        "вывод usdt", "выводим usdt", "лучший курс", "доставка наличных", "наличные в наличии",
        "обменяем ваши usdt", "принимаем usdt", "выдаем нал", "обмен usdt 24/7", "по лучшему курсу",
        "продам usdt", "продам юсдт", "продам криптовалюту", "продам usdt/рубли", "купим/продам usdt",
        "обмен usdt/рубли", "обмен usdt/донги", "быстрый обмен usdt", "меняю usdt на", "меняем usdt на",
        "обмениваем usdt", "обмен крипты", "купим ваши usdt", "продадим usdt", "безнал/нал usdt",
        "покупка/продажа usdt", "покупка и продажа usdt", "выдача наличных", "обмен с выездом",
        # Trader broadcast / volume / offer phrases
        "куплю usdt", "куплю юсдт", "куплю баты", "куплю донги", "куплю евро", "куплю usd", "куплю btc",
        "куплю трц20", "куплю trc20", "куплю erc20", "куплю крипту", "куплю криптовалюту",
        "usdt нужен", "нужен usdt", "нужны usdt",
        # Autoposting & Trader offer phrases
        "без лишней волокиты", "без посредников", "без лишних посредников", "без задержек", "без комиссий", "без комиссии",
        "проведем моментально", "проведём моментально", "проведем всё моментально", "проведём всё моментально",
        "личная встреча", "встретимся лично", "встречусь лично", "всё быстро и без задержек", "без задержек",
        # Rate / Margin / Volume / Trader terms
        "1к1", "1 к 1", "+1%", "+2%", "+3%", "+4%", "+5%", "-1%", "-2%", "по курсу", "по байбит", "по бинанс",
        "подъеду сам", "подъеду", "по курсу не жадничаю", "осталось", "тыс евро", "тыс дол", "тыс $", "тыс бат", "тыс руб",
        "за наличные и безналичные", "наличные и безналичные", "нал/безнал", "безналичные", "расчет на месте", "расчёт на месте"
    ]
    buyer_keywords = [
        "сниму", "ищу", "купим квартиру", "хочу купить", "нужен подбор", "looking to buy", "looking for rent",
        "looking to rent", "want to buy", "want to rent", "need apartment", "need villa",
        "кто меняет", "где обменять", "нужно обменять", "ищу обмен", "нужен обмен", "кто может обменять",
        "подскажите обменник", "подскажите где", "где лучше обменять", "кто-нибудь меняет", "посоветуйте обменник",
        "нужен нал за usdt", "хочу обменять usdt", "где со сдельным"
    ]

    final_map = {}
    for uid_str, data in parsed_result.items():
        try:
            uid = int(uid_str)
            item_text = ""
            for item in batch:
                if str(item["user_id"]) == str(uid_str):
                    item_text = (item.get("timeline_str") or "").lower()
                    break

            is_lead_val = data.get("is_lead", False) if "is_lead" in data else (data.get("type") in ["BUYER", "WARM_LEAD", "RENT_REALTY", "BUY_REALTY"])
            is_vendor_val = data.get("is_vendor", False) if "is_vendor" in data else (data.get("type") in ["SELLER", "B2B_PARTNER"])
            niche_val = data.get("niche_code") or data.get("niche")

            if is_lead_val and item_text:
                has_prop_listing = any(p in item_text for p in prop_listing_patterns)
                has_crypto_vendor = any(c in item_text for c in crypto_vendor_patterns)
                has_buyer_pattern = any(b in item_text for b in buyer_keywords)

                if (has_prop_listing or has_crypto_vendor) and not has_buyer_pattern:
                    is_lead_val = False
                    is_vendor_val = True
                    if has_crypto_vendor:
                        niche_val = "currency_exchange"
                    logger.info(f"🚫 BATCH HARD GUARD TRIPPED for user {uid}: Forced is_lead=False, is_vendor=True.")

            lead_result = LeadScoringResult(
                reasoning=data.get("reasoning", "No reasoning provided"),
                validation_check={},
                is_lead=is_lead_val,
                is_vendor=is_vendor_val,
                is_job_seeker=data.get("type") == "JOB_SEEKER",
                niche_code=niche_val,
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
