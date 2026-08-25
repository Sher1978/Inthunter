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
    invalid_placeholders = {"mock_key_for_testing", "your_gemini_api_key_here", "key1", "key2", "csk_key1", "gsk_key1", "sk-or-v1-your_openrouter_key"}
    valid_keys = [k for k in raw_list if k not in invalid_placeholders and len(k) > 10]
    
    if prefix_filter:
        if prefix_filter == "AIzaSy":
            filtered = [k for k in valid_keys if k.startswith("AIzaSy") or k.startswith("AQ.")]
        else:
            filtered = [k for k in valid_keys if k.startswith(prefix_filter)]
        if filtered:
            return list(dict.fromkeys(filtered))

    return list(dict.fromkeys(valid_keys))


class AIRotatorEngine:
    """
    Cascading Multi-Provider AI Engine with key rotation, automatic rate-limit failover,
    and support for Gemini (3.6 Flash / 3.5 Flash), Groq, SambaNova, Cerebras, and OpenRouter.
    """

    def __init__(self):
        pass

    def get_configured_providers(self) -> List[Dict[str, Any]]:
        """
        Dynamically constructs the active cascade of AI providers based on available keys.
        Default priority order:
        1. Google AI Studio / Gemini (gemini-3.6-flash, gemini-3.5-flash-lite, gemini-flash-latest)
        2. Groq Cloud Pool (qwen/qwen3.6-27b, openai/gpt-oss-120b, groq/compound-mini)
        3. OpenRouter Free Tier
        4. SambaNova Cloud
        5. Cerebras Cloud
        """
        providers = []

        # 1. Google AI Studio (Gemini 3.6 Flash / 3.5 Flash-Lite / Flash-Latest)
        gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
        if gemini_keys:
            gem_model = getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
            candidate_gemini = [gem_model, "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-flash-latest"]
            providers.append({
                "name": "Gemini_REST",
                "base_url": "REST",
                "keys": gemini_keys,
                "models": list(dict.fromkeys([m for m in candidate_gemini if m])),
                "headers": lambda k: {}
            })

        # 2. Groq Cloud Pool
        groq_keys = _extract_keys(getattr(settings, "GROQ_API_KEYS", ""), getattr(settings, "GROQ_API_KEY", ""), prefix_filter="gsk_")
        if groq_keys:
            g_model = getattr(settings, "GROQ_MODEL", "qwen/qwen3.6-27b")
            if g_model == "groq/compound":
                g_model = "qwen/qwen3.6-27b"
            candidate_groq = [g_model, "qwen/qwen3.6-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "groq/compound-mini"]
            filtered_groq = [m for m in candidate_groq if m and m != "groq/compound"]
            providers.append({
                "name": "Groq",
                "base_url": "https://api.groq.com/openai/v1/chat/completions",
                "keys": groq_keys,
                "models": list(dict.fromkeys(filtered_groq)),
                "headers": lambda k: {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            })

        # 3. OpenRouter Free Tier
        openrouter_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""), prefix_filter="sk-or-")
        if not openrouter_keys:
            openrouter_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""))
        if openrouter_keys:
            or_model = getattr(settings, "OPENROUTER_MODEL", "qwen/qwen-2.5-7b-instruct")
            providers.append({
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1/chat/completions",
                "keys": openrouter_keys,
                "models": list(dict.fromkeys([or_model, "qwen/qwen-2.5-7b-instruct", "meta-llama/llama-3.3-70b-instruct", "google/gemma-2-9b-it"])),
                "headers": lambda k: {
                    "Authorization": f"Bearer {k}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://leadradar.win",
                    "X-Title": "LeadRadar CDP"
                }
            })

        # 4. SambaNova Cloud (Fallback)
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

        # 5. Cerebras Cloud (Fallback)
        cerebras_keys = _extract_keys(getattr(settings, "CEREBRAS_API_KEYS", ""), getattr(settings, "CEREBRAS_API_KEY", ""), prefix_filter="csk-")
        if cerebras_keys:
            model = getattr(settings, "CEREBRAS_MODEL", "gpt-oss-120b")
            providers.append({
                "name": "Cerebras",
                "base_url": "https://api.cerebras.ai/v1/chat/completions",
                "keys": cerebras_keys,
                "models": list(dict.fromkeys([model, "gpt-oss-120b", "gemma-4-31b"])),
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

        # Check if ALL keys across all providers are on cooldown. If so, reset cooldowns to allow retry.
        all_keys = [k for p in providers for k in p["keys"]]
        if all_keys and all(_key_cooldowns.get(k, 0) > now for k in all_keys):
            logger.debug("🔄 All provider keys were on cooldown. Resetting key cooldowns for fresh retry cycle...")
            _key_cooldowns.clear()
            now = time.time()

        for provider in providers:
            p_name = provider["name"]
            base_url = provider["base_url"]
            keys = provider["keys"]
            models = provider["models"]

            # Filter keys not currently on cooldown
            ready_keys = [k for k in keys if _key_cooldowns.get(k, 0) <= now]
            if not ready_keys:
                logger.debug(f"⏳ Provider {p_name} keys are on cooldown. Skipping to next provider...")
                continue

            for api_key in ready_keys:
                key_suffix = api_key[-4:] if len(api_key) >= 4 else api_key

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
                                    logger.debug(f"AIRotator Rate Limit/Quota ({res.status_code}) on Gemini Key ...{key_suffix}. Setting 30s cooldown and trying next key...")
                                    _key_cooldowns[api_key] = time.time() + 30.0
                                    gemini_key_failed = True
                                    break
                                else:
                                    logger.debug(f"Gemini REST notice: HTTP {res.status_code} ({model_name}): {res.text[:120]}")
                        except Exception as gem_err:
                            logger.debug(f"Gemini REST exception ({model_name}): {gem_err}")
                    if not gemini_key_failed and api_key not in _key_cooldowns:
                        _key_cooldowns[api_key] = time.time() + 15.0
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
                                # Payment required / Unauthorized -> Set long 1-hour cooldown so we instantly bypass to working providers
                                logger.debug(f"Notice: HTTP {res.status_code} on {p_name} Key (...{key_suffix}). Setting 1h cooldown...")
                                _key_cooldowns[api_key] = time.time() + 3600.0
                                break
                            elif res.status_code in (403, 429):
                                logger.debug(f"Notice: HTTP {res.status_code} on {p_name} Key (...{key_suffix}). Setting 30s cooldown...")
                                _key_cooldowns[api_key] = time.time() + 30.0
                                break
                            else:
                                logger.debug(f"AIRotator notice: {p_name} HTTP {res.status_code} ({model_name}): {res.text[:120]}")

                    except Exception as err:
                        err_str = str(err)
                        if "429" in err_str or "rate limit" in err_str.lower():
                            logger.debug(f"AIRotator Rate Limit Exception on {p_name} Key (...{key_suffix}). Setting 30s cooldown...")
                            _key_cooldowns[api_key] = time.time() + 30.0
                            break
                        else:
                            logger.debug(f"AIRotator exception on {p_name} ({model_name}): {err_str[:120]}")

        # Fallback to direct Gemini REST API if OpenAI-compatible cascade didn't return output
        gemini_key = getattr(settings, "GEMINI_API_KEY", "")
        if gemini_key and (gemini_key.startswith("AIzaSy") or gemini_key.startswith("AQ.")):
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
                            logger.info(f"✅ AIRotator Fallback Success: Direct Gemini REST ({g_model})")
                            return text
            except Exception as gem_err:
                logger.error(f"Direct Gemini REST fallback error: {gem_err}")

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


# Global Singleton Instance
ai_rotator = AIRotatorEngine()
