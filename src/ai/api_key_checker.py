import asyncio
import httpx
import time
from src.config import settings
from src.ai.rotator_engine import _extract_keys

async def test_key_tg(client, provider_name, api_key, url, headers, json_body):
    start = time.time()
    try:
        if "gemini" in provider_name.lower():
            res = await client.post(url, json=json_body, timeout=10.0)
        else:
            res = await client.post(url, headers=headers, json=json_body, timeout=10.0)
            
        elapsed = time.time() - start
        key_masked = f"...{api_key[-4:]}" if len(api_key) > 4 else api_key
        
        if res.status_code == 200:
            return f"🟢 <b>{provider_name}</b> | <code>{key_masked}</code> | OK ({elapsed:.1f}s)"
        elif res.status_code == 401:
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | 401 Auth/Credits"
        elif res.status_code == 402:
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | 402 No Credits"
        elif res.status_code == 403:
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | 403 Blocked"
        elif res.status_code == 429:
            return f"🟡 <b>{provider_name}</b> | <code>{key_masked}</code> | 429 RateLimit"
        else:
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | HTTP {res.status_code}"
    except Exception as e:
        key_masked = f"...{api_key[-4:]}" if len(api_key) > 4 else api_key
        err_msg = str(e)[:30]
        return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | Error: {err_msg}"

async def run_api_key_check() -> str:
    gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
    or_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""), prefix_filter="sk-or-")
    if not or_keys:
        or_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""))
    groq_keys = _extract_keys(getattr(settings, "GROQ_API_KEYS", ""), getattr(settings, "GROQ_API_KEY", ""), prefix_filter="gsk_")
    samba_keys = _extract_keys(getattr(settings, "SAMBANOVA_API_KEYS", ""), getattr(settings, "SAMBANOVA_API_KEY", ""))
    cer_keys = _extract_keys(getattr(settings, "CEREBRAS_API_KEYS", ""), getattr(settings, "CEREBRAS_API_KEY", ""), prefix_filter="csk-")
    xai_keys = _extract_keys(getattr(settings, "XAI_API_KEYS", ""), getattr(settings, "XAI_API_KEY", ""))

    tasks = []
    
    async with httpx.AsyncClient() as client:
        base_payload = {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
        gemini_payload = {"contents": [{"parts": [{"text": "Hi"}]}], "generationConfig": {"maxOutputTokens": 5}}
        
        for k in gemini_keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={k}"
            tasks.append(test_key_tg(client, "Gemini", k, url, {}, gemini_payload))
            
        for k in or_keys:
            url = "https://openrouter.ai/api/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "qwen/qwen-2.5-7b-instruct:free"}
            tasks.append(test_key_tg(client, "OpenRouter", k, url, h, p))
            
        for k in groq_keys:
            url = "https://api.groq.com/openai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "llama3-8b-8192"}
            tasks.append(test_key_tg(client, "Groq", k, url, h, p))
            
        for k in samba_keys:
            url = "https://api.sambanova.ai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "Meta-Llama-3.1-8B-Instruct"}
            tasks.append(test_key_tg(client, "SambaNova", k, url, h, p))
            
        for k in cer_keys:
            url = "https://api.cerebras.ai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "llama3.1-8b"}
            tasks.append(test_key_tg(client, "Cerebras", k, url, h, p))
            
        for k in xai_keys:
            url = "https://api.x.ai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "grok-2-latest"}
            tasks.append(test_key_tg(client, "xAI", k, url, h, p))

        if not tasks:
            return "⚠️ Не найдено настроенных API ключей в .env!"

        results = await asyncio.gather(*tasks)
        
        report_lines = ["🔑 <b>Отчет о здоровье API Ключей ИИ</b>\n"]
        for r in sorted(results):
            report_lines.append(r)
            
        return "\n".join(report_lines)
