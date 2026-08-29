import asyncio
import httpx
import time
import sys
from src.config import settings
from src.ai.rotator_engine import _extract_keys

GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'

async def test_key(client, provider_name, api_key, url, headers, json_body):
    start = time.time()
    try:
        if "gemini" in provider_name.lower():
            res = await client.post(url, json=json_body, timeout=10.0)
        else:
            res = await client.post(url, headers=headers, json=json_body, timeout=10.0)
            
        elapsed = time.time() - start
        key_masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else api_key
        
        if res.status_code == 200:
            return f"{GREEN}[OK]{RESET} {provider_name:<12} | {key_masked:<15} | {elapsed:.2f}s"
        elif res.status_code == 401:
            return f"{RED}[401 Unauthorized]{RESET} {provider_name:<12} | {key_masked:<15} | Check key or credits"
        elif res.status_code == 402:
            return f"{RED}[402 Payment]{RESET} {provider_name:<12} | {key_masked:<15} | Out of credits"
        elif res.status_code == 403:
            return f"{RED}[403 Forbidden]{RESET} {provider_name:<12} | {key_masked:<15} | Blocked/Permission Denied"
        elif res.status_code == 429:
            return f"{YELLOW}[429 RateLimit]{RESET} {provider_name:<12} | {key_masked:<15} | Limit exceeded"
        else:
            try:
                err_text = str(res.json())[:50]
            except:
                err_text = res.text[:50]
            return f"{RED}[{res.status_code} Error]{RESET} {provider_name:<12} | {key_masked:<15} | {err_text}"
    except Exception as e:
        key_masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else api_key
        return f"{RED}[Exception]{RESET} {provider_name:<12} | {key_masked:<15} | {str(e)[:50]}"

async def main():
    print(f"{CYAN}=== LLM API Key Health Check ==={RESET}")
    
    # 1. Gemini
    gemini_keys = _extract_keys(getattr(settings, "GEMINI_API_KEYS", ""), getattr(settings, "GEMINI_API_KEY", ""), prefix_filter="AIzaSy")
    
    # 2. OpenRouter
    or_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""), prefix_filter="sk-or-")
    if not or_keys:
        or_keys = _extract_keys(getattr(settings, "OPENROUTER_API_KEYS", ""), getattr(settings, "OPENROUTER_API_KEY", ""))
        
    # 3. Groq
    groq_keys = _extract_keys(getattr(settings, "GROQ_API_KEYS", ""), getattr(settings, "GROQ_API_KEY", ""), prefix_filter="gsk_")
    
    # 4. SambaNova
    samba_keys = _extract_keys(getattr(settings, "SAMBANOVA_API_KEYS", ""), getattr(settings, "SAMBANOVA_API_KEY", ""))
    
    # 5. Cerebras
    cer_keys = _extract_keys(getattr(settings, "CEREBRAS_API_KEYS", ""), getattr(settings, "CEREBRAS_API_KEY", ""), prefix_filter="csk-")
    
    # 6. xAI
    xai_keys = _extract_keys(getattr(settings, "XAI_API_KEYS", ""), getattr(settings, "XAI_API_KEY", ""))

    tasks = []
    
    async with httpx.AsyncClient() as client:
        base_payload = {
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 5
        }
        gemini_payload = {
            "contents": [{"parts": [{"text": "Hello"}]}],
            "generationConfig": {"maxOutputTokens": 5}
        }
        
        for k in gemini_keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={k}"
            tasks.append(test_key(client, "Gemini", k, url, {}, gemini_payload))
            
        for k in or_keys:
            url = "https://openrouter.ai/api/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "meta-llama/llama-3.1-8b-instruct:free"}
            tasks.append(test_key(client, "OpenRouter", k, url, h, p))
            
        for k in groq_keys:
            url = "https://api.groq.com/openai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "llama3-8b-8192"}
            tasks.append(test_key(client, "Groq", k, url, h, p))
            
        for k in samba_keys:
            url = "https://api.sambanova.ai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "Meta-Llama-3.1-8B-Instruct"}
            tasks.append(test_key(client, "SambaNova", k, url, h, p))
            
        for k in cer_keys:
            url = "https://api.cerebras.ai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "llama3.1-8b"}
            tasks.append(test_key(client, "Cerebras", k, url, h, p))
            
        for k in xai_keys:
            url = "https://api.x.ai/v1/chat/completions"
            h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
            p = {**base_payload, "model": "grok-2-latest"}
            tasks.append(test_key(client, "xAI", k, url, h, p))

        if not tasks:
            print("No keys found in .env!")
            return

        print(f"Testing {len(tasks)} keys...")
        results = await asyncio.gather(*tasks)
        
        # Sort results for cleaner output
        for r in sorted(results):
            print(r)

if __name__ == "__main__":
    asyncio.run(main())
