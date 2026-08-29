import json
import logging
import os
import re
import time
import asyncio
from typing import Dict, Any, List, Optional, Union
import httpx

from src.config import settings

logger = logging.getLogger("intent_hunter.ai.rotator")

# Cooldown state map: api_key -> expiration timestamp
_key_cooldowns: Dict[str, float] = {}

# Round-robin key rotation index per provider
_key_indices: Dict[str, int] = {}

# Throttle timestamp for system failure Telegram alerts (at most once per 15 mins)
_last_cascade_alert_time: float = 0.0

def clean_json_text(raw_text: str) -> str:
    """Strips markdown code blocks, reasoning tags, and whitespace from LLM output."""
    if not raw_text:
        return ""
    cleaned = raw_text.strip()
    # Strip <think> reasoning tags
    cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()
    # Strip ```json ... ``` blocks
    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

def _extract_keys(keys_raw: str, single_key: str = "", prefix_filter: str = "") -> List[str]:
    """Helper to merge and parse comma/newline separated API keys."""
    combined = f"{keys_raw or ''},{single_key or ''}"
    raw_list = [k.strip() for k in re.split(r'[,\s\n]+', combined) if k.strip()]
    invalid_placeholders = {"mock_key_for_testing", "your_gemini_api_key_here", "your_xai_api_key", "key1", "key2", "csk_key1", "gsk_key1", "sk-or-v1-your_openrouter_key"}
    valid_keys = [k for k in raw_list if k not in invalid_placeholders and len(k) > 10]
    
    if prefix_filter:
        if prefix_filter == "AIzaSy":
            filtered = [k for k in valid_keys if k.startswith("AIzaSy") or k.startswith("AQ.")]
        elif prefix_filter == "xai-":
            filtered = [k for k in valid_keys if k.startswith("xai-") or k.startswith("xai_")]
        else:
            filtered = [k for k in valid_keys if k.startswith(prefix_filter)]
        if filtered:
            return list(dict.fromkeys(filtered))

    return list(dict.fromkeys(valid_keys))


class AIRotatorEngine:
    """
    Cascading Multi-Provider AI Engine with key rotation, automatic rate-limit failover,
    and support for Gemini, xAI Grok, Groq, SambaNova, Cerebras, and OpenRouter.
    """

    def __init__(self):
        pass

    def get_configured_providers(self) -> List[Dict[str, Any]]:
        """
        Dynamically constructs the active cascade of AI providers based on available keys.
        """
        providers = []

        # 1. Google AI Studio (Gemini REST) - Primary
        gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
        if gemini_keys:
            gem_model = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
            candidate_gemini = [gem_model, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
            providers.append({
                "name": "Gemini_REST",
                "base_url": "REST",
                "keys": gemini_keys,
                "models": list(dict.fromkeys([m for m in candidate_gemini if m])),
                "headers": lambda k: {}
            })

        # 2. OpenRouter Free Tier (Secondary Backup)
        openrouter_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""), prefix_filter="sk-or-")
        if not openrouter_keys:
            openrouter_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""))
        if openrouter_keys:
            or_model = getattr(settings, "OPENROUTER_MODEL", "qwen/qwen-2.5-7b-instruct").replace(":free", "")
            providers.append({
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1/chat/completions",
                "keys": openrouter_keys,
                "models": list(dict.fromkeys([m for m in [or_model, "qwen/qwen-2.5-7b-instruct", "meta-llama/llama-3.3-70b-instruct"] if m and m != "qwen/qwen-2.5-7b-instruct:free"])),
                "headers": lambda k: {
                    "Authorization": f"Bearer {k}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://leadradar.win",
                    "X-Title": "LeadRadar CDP"
                }
            })

        # 3. xAI Grok API
        xai_keys = _extract_keys(getattr(settings, "XAI_API_KEYS", ""), getattr(settings, "XAI_API_KEY", ""))
        if xai_keys:
            xai_model = getattr(settings, "XAI_GROK_MODEL", "grok-2-latest")
            providers.append({
                "name": "xAI_Grok",
                "base_url": "https://api.x.ai/v1/chat/completions",
                "keys": xai_keys,
                "models": list(dict.fromkeys([xai_model, "grok-2-latest", "grok-2-1212"])),
                "headers": lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            })

        # 4. Groq Cloud Pool (Moved to Fallback)
        groq_keys = _extract_keys(getattr(settings, "GROQ_API_KEYS", ""), getattr(settings, "GROQ_API_KEY", ""), prefix_filter="gsk_")
        if groq_keys:
            g_model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
            if "qwen" in g_model or "groq/compound" in g_model:
                g_model = "llama-3.3-70b-versatile"
            candidate_groq = [g_model, "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
            filtered_groq = [m for m in candidate_groq if m]
            providers.append({
                "name": "Groq",
                "base_url": "https://api.groq.com/openai/v1/chat/completions",
                "keys": groq_keys,
                "models": list(dict.fromkeys(filtered_groq)),
                "headers": lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            })

        # 5. SambaNova Cloud (Fallback)
        sambanova_keys = _extract_keys(getattr(settings, "SAMBANOVA_API_KEYS", ""), getattr(settings, "SAMBANOVA_API_KEY", ""))
        if sambanova_keys:
            model = getattr(settings, "SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct")
            providers.append({
                "name": "SambaNova",
                "base_url": "https://api.sambanova.ai/v1/chat/completions",
                "keys": sambanova_keys,
                "models": [model, "Meta-Llama-3.1-8B-Instruct"],
                "headers": lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            })

        # 6. Cerebras Cloud (Fallback)
        cerebras_keys = _extract_keys(getattr(settings, "CEREBRAS_API_KEYS", ""), getattr(settings, "CEREBRAS_API_KEY", ""), prefix_filter="csk-")
        if cerebras_keys:
            model = getattr(settings, "CEREBRAS_MODEL", "llama3.3-70b")
            providers.append({
                "name": "Cerebras",
                "base_url": "https://api.cerebras.ai/v1/chat/completions",
                "keys": cerebras_keys,
                "models": list(dict.fromkeys([model, "llama3.3-70b", "llama3.1-8b"])),
                "headers": lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            })

        return providers

    async def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 1000,
        response_format_json: bool = False,
        timeout: float = 12.0
    ) -> Optional[str]:
        """
        Executes text generation request with cascading multi-provider failover.
        Returns raw or JSON string response on success, None on total failure.
        """
        providers = self.get_configured_providers()
        if not providers:
            logger.warning("🚨 AIRotatorEngine: No active AI providers configured! Check API keys in .env.")
            return None

        now = time.time()

        for provider in providers:
            p_name = provider["name"]
            base_url = provider["base_url"]
            keys = provider["keys"]
            models = provider["models"]

            # Filter keys not currently on cooldown
            ready_keys = [k for k in keys if _key_cooldowns.get(k, 0) <= now]
            if not ready_keys:
                logger.debug(f"⏳ Provider {p_name} keys are on 5-minute cooldown. Skipping...")
                continue

            # Round-Robin Key Rotation: rotate ready keys so load is spread 100% evenly!
            start_idx = _key_indices.get(p_name, 0) % len(ready_keys)
            rotated_keys = ready_keys[start_idx:] + ready_keys[:start_idx]
            _key_indices[p_name] = start_idx + 1

            for api_key in rotated_keys:
                key_suffix = api_key[-4:] if len(api_key) >= 4 else api_key
                key_num = (keys.index(api_key) + 1) if api_key in keys else 1
                key_info = f"Ключ #{key_num} из {len(keys)} (...{key_suffix})"

                if p_name == "Gemini_REST":
                    gemini_key_failed = False
                    for model_name in models:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                        prompt_sys = f"{system_prompt}\n\n{user_prompt}"
                        body = {
                            "contents": [{"parts": [{"text": prompt_sys}]}],
                            "generationConfig": {"temperature": temperature}
                        }
                        if response_format_json:
                            body["generationConfig"]["response_mime_type"] = "application/json"

                        try:
                            async with httpx.AsyncClient(timeout=timeout) as client:
                                res = await client.post(url, json=body)
                                if res.status_code == 200:
                                    data = res.json()
                                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                                    if text:
                                        logger.info(f"✅ AIRotator Engine Success: Provider={p_name} | Key=...{key_suffix} | Model={model_name}")
                                        _key_cooldowns.pop(api_key, None)
                                        return text
                                elif res.status_code in (402, 403, 429):
                                    logger.info(f"⏳ Gemini Key ...{key_suffix} hit rate limit (HTTP {res.status_code}). Setting 5-minute cooldown (300s)...")
                                    _key_cooldowns[api_key] = time.time() + 300.0
                                    try:
                                        from src.bot.alert_bot import notify_superadmins_llm_error
                                        asyncio.create_task(notify_superadmins_llm_error(p_name, model_name, f"HTTP {res.status_code} Rate Limit or Blocked", key_info=key_info))
                                    except Exception:
                                        pass
                                    gemini_key_failed = True
                                    break
                                else:
                                    logger.debug(f"Gemini REST notice: HTTP {res.status_code} ({model_name}): {res.text[:120]}")
                        except Exception as gem_err:
                            logger.debug(f"Gemini REST exception ({model_name}): {gem_err}")
                    if not gemini_key_failed and api_key not in _key_cooldowns:
                        _key_cooldowns[api_key] = time.time() + 30.0
                    continue

                headers = provider["headers"](api_key)

                for model_name in models:
                    payload = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens
                    }

                    if response_format_json:
                        payload["response_format"] = {"type": "json_object"}

                    try:
                        async with httpx.AsyncClient(timeout=timeout) as client:
                            res = await client.post(base_url, headers=headers, json=payload)
                            
                            if res.status_code == 200:
                                data = res.json()
                                content = data["choices"][0]["message"]["content"]
                                if content:
                                    logger.info(f"✅ AIRotator Engine Success: Provider={p_name} | Key=...{key_suffix} | Model={model_name}")
                                    _key_cooldowns.pop(api_key, None)
                                    return content
                            elif res.status_code in (401, 402):
                                # Payment required / Unauthorized -> Set long 1-hour cooldown
                                logger.info(f"Notice: HTTP {res.status_code} on {p_name} Key (...{key_suffix}). Setting 1h cooldown...")
                                _key_cooldowns[api_key] = time.time() + 3600.0
                                try:
                                    from src.bot.alert_bot import notify_superadmins_llm_error
                                    asyncio.create_task(notify_superadmins_llm_error(p_name, model_name, f"HTTP {res.status_code} Payment Required / Unauthorized", key_info=key_info))
                                except Exception:
                                    pass
                                break
                            elif res.status_code in (403, 429):
                                logger.info(f"⏳ {p_name} Key ...{key_suffix} hit rate limit (HTTP {res.status_code}). Setting 5-minute cooldown (300s)...")
                                _key_cooldowns[api_key] = time.time() + 300.0
                                try:
                                    from src.bot.alert_bot import notify_superadmins_llm_error
                                    asyncio.create_task(notify_superadmins_llm_error(p_name, model_name, f"HTTP {res.status_code} Rate Limit or Permission Denied", key_info=key_info))
                                except Exception:
                                    pass
                                break
                            else:
                                logger.debug(f"AIRotator notice: {p_name} HTTP {res.status_code} ({model_name}): {res.text[:120]}")

                    except Exception as err:
                        err_str = str(err)
                        if "429" in err_str or "rate limit" in err_str.lower():
                            logger.info(f"⏳ AIRotator Rate Limit Exception on {p_name} Key (...{key_suffix}). Setting 5-minute cooldown (300s)...")
                            _key_cooldowns[api_key] = time.time() + 300.0
                            try:
                                from src.bot.alert_bot import notify_superadmins_llm_error
                                asyncio.create_task(notify_superadmins_llm_error(p_name, model_name, f"Exception: {err_str}", key_info=key_info))
                            except Exception:
                                pass
                            break
                        else:
                            logger.debug(f"AIRotator exception on {p_name} ({model_name}): {err_str[:120]}")

        # Fallback to direct Gemini REST API if OpenAI-compatible cascade didn't return output
        fallback_gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
        for gemini_key in fallback_gemini_keys:
            key_sfx = gemini_key[-4:] if len(gemini_key) >= 4 else gemini_key
            try:
                g_model = getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{g_model}:generateContent?key={gemini_key}"
                prompt_sys = f"{system_prompt}\n\n{user_prompt}"
                body = {
                    "contents": [{"parts": [{"text": prompt_sys}]}],
                    "generationConfig": {"temperature": temperature}
                }
                if response_format_json:
                    body["generationConfig"]["response_mime_type"] = "application/json"

                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.post(url, json=body)
                    if res.status_code == 200:
                        data = res.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                        if text:
                            logger.info(f"✅ AIRotator Fallback Success: Direct Gemini REST ({g_model}) Key=...{key_sfx}")
                            return text
            except Exception as gem_err:
                logger.error(f"Direct Gemini REST fallback error (Key ...{key_sfx}): {gem_err}")

        logger.error("🚨 AIRotatorEngine: All configured AI providers & fallbacks failed or exhausted rate limits.")
        return None

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        timeout: float = 12.0
    ) -> Optional[Dict[str, Any]]:
        """
        Executes completion request enforcing JSON format and returning parsed Python dictionary.
        """
        raw_text = await self.generate_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format_json=True,
            timeout=timeout
        )
        if not raw_text:
            return None

        cleaned = clean_json_text(raw_text)
        try:
            return json.loads(cleaned)
        except Exception as e:
            logger.error(f"Failed to parse JSON output from AIRotator: {e}\nRaw output snippet: {raw_text[:200]}")
            return None

    def get_rotator_status(self) -> Dict[str, Any]:
        """
        Returns real-time telemetry status of all configured AI providers, keys, and cooldown timers.
        """
        now = time.time()
        providers = self.get_configured_providers()
        
        status_list = []
        total_keys = 0
        active_keys = 0
        cooldown_keys = 0

        for provider in providers:
            p_name = provider["name"]
            models = provider["models"]
            keys = provider["keys"]

            for k in keys:
                total_keys += 1
                key_suffix = f"...{k[-4:]}" if len(k) >= 4 else k
                cooldown_exp = _key_cooldowns.get(k, 0)
                
                if cooldown_exp <= now:
                    active_keys += 1
                    status_list.append({
                        "provider": p_name,
                        "key_suffix": key_suffix,
                        "status": "READY",
                        "cooldown_remaining_sec": 0,
                        "unblocks_at_utc": None,
                        "models": models
                    })
                else:
                    cooldown_keys += 1
                    remaining = int(cooldown_exp - now)
                    unblock_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(cooldown_exp))
                    status_list.append({
                        "provider": p_name,
                        "key_suffix": key_suffix,
                        "status": "COOLDOWN",
                        "cooldown_remaining_sec": remaining,
                        "unblocks_at_utc": unblock_str,
                        "models": models
                    })

        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "total_keys": total_keys,
            "active_ready_keys": active_keys,
            "cooldown_keys": cooldown_keys,
            "keys_status": status_list
        }

rotator_engine_instance = AIRotatorEngine()


# Global Singleton Instance
ai_rotator = AIRotatorEngine()
