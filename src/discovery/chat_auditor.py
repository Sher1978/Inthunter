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
    Evaluates prompt using AIRotatorEngine cascade (SambaNova -> Cerebras -> Groq -> Gemini -> OpenRouter)
    returning JSON dict. Respects rate limits and handles fallbacks cleanly.
    """
    from src.config import settings
    import re

    # 1. Primary: AIRotatorEngine Multi-Provider Cascade
    try:
        from src.ai.rotator_engine import ai_rotator
        json_res = await ai_rotator.generate_json(
            system_prompt=system_instruction,
            user_prompt=prompt,
            temperature=0.1,
            timeout=10.0
        )
        if json_res:
            return json_res
    except Exception as rot_err:
        logger.warning(f"AIRotator audit call notice: {rot_err}")

    # 2. Try Groq API Fallback
    raw_keys = (getattr(settings, "GROQ_API_KEYS", "") or "") + "," + (settings.GROQ_API_KEY or "")
    key_pool = [k.strip() for k in re.split(r'[,\s\n]+', raw_keys) if k.strip().startswith("gsk_")]

    if key_pool:
        try:
            from groq import AsyncGroq
            api_key = key_pool[0]
            client = AsyncGroq(api_key=api_key, max_retries=0, timeout=10.0)
            
            completion = await client.chat.completions.create(
                model=getattr(settings, "GROQ_MODEL", "groq/compound") or "groq/compound",
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

    # 3. Try Gemini API Fallback
    gemini_key = getattr(settings, "GEMINI_API_KEY", None)
    if gemini_key and (gemini_key.startswith("AIzaSy") or gemini_key.startswith("AQ.")):
        try:
            import httpx
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{getattr(settings, 'GEMINI_MODEL', 'gemini-3.6-flash')}:generateContent?key={gemini_key}"
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
        return {"unique_authors_ratio": 0.0, "link_density": 1.0, "avg_length": 0.0, "buyer_signals_count": 0}

    total_count = len(messages)
    authors = set()
    link_count = 0
    total_chars = 0
    buyer_signals_count = 0

    buyer_keywords = (
        "сниму", "ищу", "нужен", "нужна", "нужны", "посоветуйте", "кто сдает", "кто сдаёт",
        "кто делает", "сколько стоит", "подскажите", "купим", "требуется", "интересует",
        "ищем", "какая цена", "где найти", "посоветуйте", "кто поможет", "подскажите пожал",
        "ищу варианты", "нужна консультация", "хочу заказать", "аренда", "подберите",
        "порекомендуйте", "почем", "кто может", "где можно", "кто знает", "нужен риелтор",
        "нужен трансфер", "нужен гид", "кто меняет", "обмен"
    )

    for m in messages:
        txt = (m.get("message_text") or "").strip()
        txt_low = txt.lower()
        total_chars += len(txt)
        
        # Author tracking
        user_key = m.get("username") or m.get("first_name") or m.get("user_id") or "anon"
        authors.add(user_key)

        # Link/Ad density check
        if "http://" in txt or "https://" in txt or "t.me/" in txt or txt.count("#") >= 5:
            link_count += 1

        # Buyer lead signals
        if any(kw in txt_low for kw in buyer_keywords):
            buyer_signals_count += 1

    return {
        "unique_authors_ratio": round(len(authors) / max(1, total_count), 2),
        "link_density": round(link_count / max(1, total_count), 2),
        "avg_length": round(total_chars / max(1, total_count), 1),
        "buyer_signals_count": buyer_signals_count
    }


async def evaluate_chat_quality(username_or_link: str, platform: str = "telegram") -> Dict[str, Any]:
    """
    Evaluates a candidate chat/group from Telegram, VK, OK, or MAX Messenger.
    Uses pre-metrics to filter out bot dumps without wasting free LLM tokens.
    For ambiguous/promising chats, calls LLM (Groq/Gemini cascade) with strict JSON output.
    Approve live communities with high author diversity for real-time monitoring.
    """
    clean_u = username_or_link.strip().replace("https://t.me/", "").replace("http://t.me/", "").lstrip("@").lower()

    # Pre-reject personal profile handles by suffix for Telegram
    if platform == "telegram":
        profile_suffixes = (
            '_hr', '_recruiter', '_manager', '_admin', '_moderator', '_owner', '_ceo', 
            '_contact', '_agent', '_realtor', '_broker', '_seller', '_boss', '_dev', 
            '_vip', '_lead', '_buyer', '_bot', '_official', '_channel', '_support'
        )
        if any(clean_u.endswith(sfx) for sfx in profile_suffixes):
            return {
                "score": 0,
                "status": "REJECTED",
                "chat_type": "PERSONAL_PROFILE",
                "detected_niches": [],
                "reason": "Личный профиль или бот (не является сообществом)."
            }

    # Fetch posts using Pyrogram Userbot or platform scrapers
    from src.ingestion.vk_ok_scrapers import VKPublicScraper, OKPublicScraper, MAXPublicScraper
    posts = []

    if platform == "vk":
        posts = await VKPublicScraper.fetch_latest_messages(username_or_link)
    elif platform == "ok":
        posts = await OKPublicScraper.fetch_latest_messages(username_or_link)
    elif platform == "max":
        posts = await MAXPublicScraper.fetch_latest_messages(username_or_link)
    else:
        # 1. Try Pyrogram Userbot if client is active
        import sys
        app_module = sys.modules.get("src.api.app")
        ingestor = getattr(app_module, "ingestor", None) if app_module else None
        if ingestor and getattr(ingestor, "app", None) and getattr(ingestor, "_is_running", False):
            try:
                pyro_msgs = []
                async for m in ingestor.app.get_chat_history(clean_u, limit=30):
                    if m.text or m.caption:
                        pyro_msgs.append({
                            "message_id": m.id,
                            "message_text": m.text or m.caption or "",
                            "username": m.from_user.username if m.from_user else None,
                            "first_name": m.from_user.first_name if m.from_user else "User",
                            "timestamp": m.date
                        })
                if pyro_msgs:
                    posts = pyro_msgs
            except Exception as pyro_err:
                logger.debug(f"Pyrogram chat history notice for @{clean_u}: {pyro_err}")

        # 2. Fallback to Zero-Auth Public Scraper
        if not posts:
            scraper = PublicTelegramScraper()
            posts = await scraper.fetch_latest_messages(username_or_link)

    # REQUIRE MINIMUM 5 POSTS: If fewer than 5 posts returned by scrapers/userbot, reject as unverified/inactive.
    if not posts or len(posts) < 5:
        return {
            "score": 20,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": f"Недостаточно активных сообщений для аудита (найдено {len(posts) if posts else 0} из 5 необходимых)."
        }

    # 1. Pre-metrics filtering (Zero Token Cost Optimization)
    metrics = calculate_pre_metrics(posts)
    logger.info(f"📊 Pre-metrics for {username_or_link}: authors_ratio={metrics['unique_authors_ratio']}, link_density={metrics['link_density']}, buyer_signals={metrics['buyer_signals_count']}")

    # Single/Few-author bot feed rejection (< 20% unique authors)
    if metrics["unique_authors_ratio"] < 0.20:
        return {
            "score": 15,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": f"Ботовский рекламный канал/дамп: слишком мало уникальных авторов ({int(metrics['unique_authors_ratio']*100)}%)."
        }

    # High link density pure ad feed rejection (> 40% links/hashtags)
    if metrics["link_density"] > 0.40:
        return {
            "score": 20,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": f"Рекламная доска с высокой плотностью спама ({int(metrics['link_density']*100)}% ссылок)."
        }

    # GOLDEN RATIO LIVE COMMUNITY APPROVAL:
    # Requires high author diversity (>= 35%), low link density (<= 40%), and active message length.
    if metrics["unique_authors_ratio"] >= 0.35 and metrics["link_density"] <= 0.40 and metrics["avg_length"] >= 15:
        return {
            "score": 85,
            "status": "APPROVED",
            "chat_type": "LIVE_COMMUNITY",
            "detected_niches": ["community"],
            "reason": f"Качественное живое сообщество: вариативность авторов {int(metrics['unique_authors_ratio']*100)}%, спам {int(metrics['link_density']*100)}%."
        }

    # 2. Free LLM Quality Audit (Groq/Gemini cascade)
    sample_snippets = []
    for i, p in enumerate(posts[:30], 1):
        txt = (p.get("message_text") or "").replace("\n", " ").strip()[:180]
        user_label = p.get("username") or p.get("first_name") or "Пользователь"
        sample_snippets.append(f"{i}. [{user_label}]: \"{txt}\"")

    formatted_timeline = "\n".join(sample_snippets)

    system_instruction = (
        "ROLE: Strict Traffic Quality Auditor for LeadRadar.win.\n"
        "TASK: Analyze up to 30 recent messages from a Telegram group to verify if it is a REAL LIVE COMMUNITY with user discussions or a SPAM DUMP.\n\n"
        "CRITICAL RULES:\n"
        "- ONLY APPROVE if there is active, genuine conversation between multiple real users -> status='APPROVED', score=80-95.\n"
        "- REJECT if it is a commercial ad dump, single-seller channel, or spam feed -> status='REJECTED', score=20.\n\n"
        "OUTPUT FORMAT (Strict JSON ONLY):\n"
        "{\n"
        '  "buyer_leads_count": 1,\n'
        '  "score": 85,\n'
        '  "status": "APPROVED",\n'
        '  "chat_type": "LIVE_COMMUNITY",\n'
        '  "detected_niches": ["REAL_ESTATE", "COMMUNITY"],\n'
        '  "reason": "Живое сообщество с активными дискуссиями пользователей."\n'
        "}"
    )

    prompt = (
        f"Group: {username_or_link}\n"
        f"Pre-metrics: authors_ratio={metrics['unique_authors_ratio']}, link_density={metrics['link_density']}, buyer_signals={metrics['buyer_signals_count']}\n\n"
        f"Messages sample (30 msgs max):\n{formatted_timeline}\n\n"
        f"Return strict JSON verdict:"
    )

    # Respect Free LLM Rate Limit
    await asyncio.sleep(AUDIT_RATE_LIMIT_DELAY)

    # Call LLM via Groq primary, Gemini fallback
    raw_res = await _call_llm_json(prompt, system_instruction)

    if raw_res and "score" in raw_res:
        buyer_cnt = int(raw_res.get("buyer_leads_count", metrics["buyer_signals_count"]))
        score_val = int(raw_res.get("score", 50))

        if score_val >= 80 and metrics["unique_authors_ratio"] >= 0.30 and metrics["link_density"] <= 0.40:
            return {
                "score": score_val,
                "status": "APPROVED",
                "chat_type": raw_res.get("chat_type", "LIVE_COMMUNITY"),
                "detected_niches": raw_res.get("detected_niches", ["community"]),
                "reason": raw_res.get("reason", "Одобрено ИИ-аудитором по критериям Золотого Сечения.")
            }

        return {
            "score": score_val,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": raw_res.get("reason", "Не соответствует строгим стандартам качества групп.")
        }

    # 3. Fallback Heuristic Audit if LLM API is unavailable / rate-limited
    is_live = metrics["unique_authors_ratio"] >= 0.20 and metrics["link_density"] < 0.60
    heuristic_score = 75 if is_live else 30
    return {
        "score": heuristic_score,
        "status": "APPROVED" if is_live else "REJECTED",
        "chat_type": "LIVE_COMMUNITY" if is_live else "SPAM_DUMP",
        "detected_niches": ["community"] if is_live else [],
        "reason": "Эвристический аудит: " + ("Живое сообщество пользователей." if is_live else "Высокая плотность спама.")
    }

