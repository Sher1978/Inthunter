import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from src.ingestion.public_scraper import PublicTelegramScraper

logger = logging.getLogger(__name__)
AUDIT_RATE_LIMIT_DELAY = 2.5
async def _call_llm_json(prompt: str, system_instruction: str) -> Optional[Dict[str, Any]]:
    """
    Evaluates prompt using Groq (qwen/qwen3.6-27b) or Gemini (gemini-2.5-flash) returning JSON dict.
    Respects rate limits and handles fallbacks cleanly.
    """
    from src.config import settings
    import re

    # 1. Try Groq API
    raw_keys = (getattr(settings, "GROQ_API_KEYS", "") or "") + "," + (settings.GROQ_API_KEY or "")
    key_pool = [k.strip() for k in re.split(r'[,\s\n]+', raw_keys) if k.strip().startswith("gsk_")]

    if key_pool:
        try:
            from groq import AsyncGroq
            api_key = key_pool[0]
            client = AsyncGroq(api_key=api_key, max_retries=0, timeout=10.0)
            
            completion = await client.chat.completions.create(
                model=getattr(settings, "GROQ_MODEL", "qwen/qwen3.6-27b") or "qwen/qwen3.6-27b",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            content = completion.choices[0].message.content
            if content:
                cleaned = content.strip().replace("```json", "").replace("```", "").strip()
                return json.loads(cleaned)
        except Exception as e:
            logger.warning(f"Notice: Groq audit call notice: {e}")

    # 2. Try Gemini API
    gemini_key = getattr(settings, "GEMINI_API_KEY", None)
    if gemini_key and gemini_key.startswith("AIzaSy"):
        try:
            import httpx
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash')}:generateContent?key={gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": f"{system_instruction}\n\n{prompt}"}]}],
                "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    txt = data["candidates"][0]["content"]["parts"][0]["text"]
                    cleaned = txt.strip().replace("```json", "").replace("```", "").strip()
                    return json.loads(cleaned)
        except Exception as e:
            logger.warning(f"Notice: Gemini audit call notice: {e}")

    return None


def calculate_pre_metrics(messages: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Computes preliminary heuristic quality metrics from recent chat messages.
    Zero LLM token cost.
    """
    if not messages:
        return {"unique_authors_ratio": 0.0, "link_density": 1.0, "avg_length": 0.0}

    total_count = len(messages)
    authors = set()
    link_count = 0
    total_chars = 0

    for m in messages:
        txt = (m.get("message_text") or "").strip()
        total_chars += len(txt)
        
        # Author tracking
        user_key = m.get("username") or m.get("first_name") or m.get("user_id") or "anon"
        authors.add(user_key)

        # Link/Ad density check
        if "http://" in txt or "https://" in txt or "t.me/" in txt or txt.count("#") >= 5:
            link_count += 1

    return {
        "unique_authors_ratio": round(len(authors) / max(1, total_count), 2),
        "link_density": round(link_count / max(1, total_count), 2),
        "avg_length": round(total_chars / max(1, total_count), 1)
    }


async def evaluate_chat_quality(username_or_link: str) -> Dict[str, Any]:
    """
    Evaluates a candidate Telegram chat.
    Uses pre-metrics to filter out bot dumps without wasting free LLM tokens.
    For ambiguous/promising chats, calls LLM (Groq/Gemini cascade) with strict JSON output.
    """
    scraper = PublicTelegramScraper()
    posts = await scraper.fetch_latest_messages(username_or_link)

    if not posts or len(posts) < 3:
        return {
            "score": 10,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": "Мертвый чат или менее 3 публичных сообщений."
        }

    # 1. Pre-metrics filtering (Zero Token Cost Optimization)
    metrics = calculate_pre_metrics(posts)
    logger.info(f"📊 Pre-metrics for {username_or_link}: authors_ratio={metrics['unique_authors_ratio']}, link_density={metrics['link_density']}")

    # Single-author bot feed rejection
    if len(posts) >= 10 and metrics["unique_authors_ratio"] < 0.12:
        return {
            "score": 20,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": f"Ботовский рекламный дамп: 1-2 авторов на {len(posts)} сообщений."
        }

    # High link density pure ad feed rejection
    if metrics["link_density"] > 0.85:
        return {
            "score": 25,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": f"Рекламная доска с высокой плотностью ссылок ({int(metrics['link_density']*100)}%)."
        }

    # 2. Free LLM Quality Audit (Groq/Gemini cascade)
    sample_snippets = []
    for i, p in enumerate(posts[:25], 1):
        txt = (p.get("message_text") or "").replace("\n", " ").strip()[:180]
        user_label = p.get("username") or p.get("first_name") or "Пользователь"
        sample_snippets.append(f"{i}. [{user_label}]: \"{txt}\"")

    formatted_timeline = "\n".join(sample_snippets)

    system_instruction = (
        "ROLE: Traffic Quality Auditor for LeadRadar.win.\n"
        "TASK: Analyze recent 25 messages from a Telegram group to decide if it is a LIVE COMMUNITY with real commercial B2B leads or a DEAD SPAM DUMP.\n\n"
        "EVALUATION CRITERIA (Score 0-100):\n"
        "1. Live Dialogue Density (40%): Human discussions, short questions vs. long automated ad posts.\n"
        "2. Author Diversity (30%): Many unique users vs. auto-posting bots.\n"
        "3. Commercial Intent (30%): B2B services, rental, auto, legal, real estate, currency exchange.\n\n"
        "THRESHOLDS:\n"
        "- 65 - 100: APPROVED (Live community or valuable commercial board).\n"
        "- 0 - 64: REJECTED (Spam dump, dead chat, pure bot feed).\n\n"
        "OUTPUT FORMAT (Strict JSON ONLY):\n"
        "{\n"
        '  "score": 75,\n'
        '  "status": "APPROVED",\n'
        '  "chat_type": "LIVE_COMMUNITY",\n'
        '  "detected_niches": ["REAL_ESTATE", "AUTO_RENTAL"],\n'
        '  "reason": "1 short sentence explaining verdict in Russian."\n'
        "}"
    )

    prompt = (
        f"Group: {username_or_link}\n"
        f"Pre-metrics: authors_ratio={metrics['unique_authors_ratio']}, link_density={metrics['link_density']}\n\n"
        f"Messages sample:\n{formatted_timeline}\n\n"
        f"Return strict JSON verdict:"
    )

    # Respect Free LLM Rate Limit
    await asyncio.sleep(AUDIT_RATE_LIMIT_DELAY)

    # Call LLM via Groq primary, Gemini fallback
    raw_res = await _call_llm_json(prompt, system_instruction)

    if raw_res and "score" in raw_res:
        score_val = int(raw_res.get("score", 50))
        status_val = "APPROVED" if score_val >= 65 else "REJECTED"
        return {
            "score": score_val,
            "status": status_val,
            "chat_type": raw_res.get("chat_type", "LIVE_COMMUNITY" if score_val >= 65 else "SPAM_DUMP"),
            "detected_niches": raw_res.get("detected_niches", ["community"]),
            "reason": raw_res.get("reason", "Оценка ИИ-аудитора.")
        }

    # 3. Fallback Heuristic Audit if LLM API is unavailable / rate-limited
    heuristic_score = 70 if metrics["unique_authors_ratio"] >= 0.40 and metrics["link_density"] < 0.50 else 45
    return {
        "score": heuristic_score,
        "status": "APPROVED" if heuristic_score >= 65 else "REJECTED",
        "chat_type": "LIVE_COMMUNITY" if heuristic_score >= 65 else "SPAM_DUMP",
        "detected_niches": ["community"],
        "reason": "Резервная эвристическая оценка (диалог и уникальность авторов)."
    }

