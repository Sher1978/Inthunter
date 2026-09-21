import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv("c:/Sher_AI_Studio/projects/Outreach/.env")

groq_keys_raw = os.getenv("GROQ_API_KEYS", "")
groq_key_single = os.getenv("GROQ_API_KEY", "")

combined = f"{groq_keys_raw},{groq_key_single}"
import re
raw_list = [k.strip() for k in re.split(r'[,\s\n]+', combined) if k.strip()]
valid_keys = [k for k in raw_list if len(k) > 10 and k.startswith("gsk_")]
valid_keys = list(dict.fromkeys(valid_keys))

print(f"Found {len(valid_keys)} Groq keys")

async def test_groq():
    if not valid_keys:
        print("No keys found")
        return

    key = valid_keys[0]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = "https://api.groq.com/openai/v1/chat/completions"

    models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "groq/compound"]
    
    async with httpx.AsyncClient() as client:
        for model in models:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 10
            }
            res = await client.post(url, headers=headers, json=payload)
            print(f"Model: {model} -> Status: {res.status_code}")
            if res.status_code != 200:
                print(res.text)

asyncio.run(test_groq())
