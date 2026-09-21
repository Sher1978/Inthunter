import asyncio
import httpx
import time
from src.config import settings
from src.ai.rotator_engine import _extract_keys

async def test_key_tg(client, provider_name, api_key, url, headers, json_body, delay: float = 0.0):
    if delay > 0:
        await asyncio.sleep(delay)
    start = time.time()
    key_masked = f"...{api_key[-4:]}" if len(api_key) > 4 else api_key
    try:
        if "gemini" in provider_name.lower():
            res = await client.post(url, json=json_body, timeout=10.0)
        else:
            res = await client.post(url, headers=headers, json=json_body, timeout=10.0)
            
        elapsed = time.time() - start
        
        if res.status_code == 200:
            return f"🟢 <b>{provider_name}</b> | <code>{key_masked}</code> | OK ({elapsed:.1f}s)"
        elif res.status_code == 401:
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | 401 Invalid Key"
        elif res.status_code == 402:
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | 402 Out of Credits"
        elif res.status_code == 403:
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | 403 Blocked (IP/Geo)"
        elif res.status_code == 429:
            return f"🟡 <b>{provider_name}</b> | <code>{key_masked}</code> | 429 RateLimit"
        else:
            try:
                err_data = res.json()
                msg = err_data.get("error", {}).get("message", res.text[:25])
            except Exception:
                msg = res.text[:25]
            return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | HTTP {res.status_code} ({msg})"
    except Exception as e:
        err_msg = str(e).strip() or type(e).__name__
        err_msg = err_msg[:30]
        return f"🔴 <b>{provider_name}</b> | <code>{key_masked}</code> | Error: {err_msg}"

async def run_api_key_check() -> str:
    gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
    groq_keys = _extract_keys(getattr(settings, "GROQ_API_KEYS", ""), getattr(settings, "GROQ_API_KEY", ""), prefix_filter="gsk_")
    xai_keys = _extract_keys(getattr(settings, "XAI_API_KEYS", ""), getattr(settings, "XAI_API_KEY", ""))

    tasks = []
    
    async with httpx.AsyncClient() as client:
        base_payload = {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
        gemini_payload = {"contents": [{"parts": [{"text": "Hi"}]}], "generationConfig": {"maxOutputTokens": 5}}
        
        for idx, k in enumerate(gemini_keys):
            gem_m = getattr(settings, "SAFE_GEMINI_MODEL", "gemini-3.6-flash")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gem_m}:generateContent?key={k}"
            # Stagger gemini checks slightly to prevent instant RPM spikes
            tasks.append(test_key_tg(client, "Gemini", k, url, {}, gemini_payload, delay=idx * 0.15))
            
        for idx, k in enumerate(groq_keys):
            url = "https://api.groq.com/openai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            gr_m = getattr(settings, "SAFE_GROQ_MODEL", "openai/gpt-oss-120b")
            p = {**base_payload, "model": gr_m}
            tasks.append(test_key_tg(client, "Groq", k, url, h, p, delay=idx * 0.1))
            
        for idx, k in enumerate(xai_keys):
            url = "https://api.x.ai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            xai_m = getattr(settings, "XAI_GROK_MODEL", "grok-2-latest")
            p = {**base_payload, "model": xai_m}
            tasks.append(test_key_tg(client, "xAI", k, url, h, p, delay=idx * 0.1))

        if not tasks:
            return "⚠️ Не найдено настроенных API ключей в .env!"

        results = await asyncio.gather(*tasks)
        
        report_lines = ["🔑 <b>Отчет о здоровье API Ключей ИИ</b>\n"]
        for r in sorted(results):
            report_lines.append(r)
            
        return "\n".join(report_lines)
