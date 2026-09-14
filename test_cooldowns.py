import asyncio
import time
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from src.config import settings
from src.ai.rotator_engine import ai_rotator, _key_cooldowns
from src.ai.batch_scorer import _eval_batch_with_provider

async def test_rotator_engine():
    print("--- Testing rotator_engine.py ---")
    
    # Fake dead key
    fake_key = "gsk_FAKE_GROQ_KEY_THAT_WILL_401"
    
    # Replace settings in memory
    original_groq_keys = getattr(settings, "GROQ_API_KEYS", "")
    settings.GROQ_API_KEYS = fake_key
    
    print(f"Simulating call with key: {fake_key}")
    
    # 2. Call generate_completion
    result = await ai_rotator.generate_completion("You are a test", "Test", timeout=5.0)
    
    # 3. Check cooldown
    cooldown = _key_cooldowns.get(fake_key, 0)
    now = time.time()
    
    if cooldown > now:
        rem = cooldown - now
        print(f"[SUCCESS] Key placed on cooldown for {rem:.1f}s!")
    else:
        print("[ERROR] Cooldown not applied!")

    # Restore
    settings.GROQ_API_KEYS = original_groq_keys
    print("")

async def test_batch_scorer():
    print("--- Testing batch_scorer.py ---")
    
    fake_key = "AIzaSy_FAKE_GEMINI_KEY"
    
    print(f"Simulating batch_scorer call with key: {fake_key}")
    
    payload = {
        "contents": [{"parts": [{"text": "test"}]}]
    }
    
    # Call _eval_batch_with_provider
    await _eval_batch_with_provider(
        provider="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-3.6-flash",
        headers_func=lambda k: {"Content-Type": "application/json"},
        payload=payload,
        keys=[fake_key],
        cooldown_sec=4.5
    )
    
    cooldown = _key_cooldowns.get(fake_key, 0)
    now = time.time()
    
    if cooldown > now:
        rem = cooldown - now
        print(f"[SUCCESS] Key placed on cooldown for {rem:.1f}s in batch_scorer!")
    else:
        print("[ERROR] Cooldown not applied in batch_scorer!")


async def main():
    await test_rotator_engine()
    await test_batch_scorer()

if __name__ == "__main__":
    asyncio.run(main())
