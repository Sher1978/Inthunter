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
                model=getattr(settings, "SAFE_GROQ_MODEL", "openai/gpt-oss-120b"),
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
        return {"unique_authors_ratio": 0.0, "link_density": 1.0, "avg_length": 0.0, "buyer_signals_count": 0, "trash_ratio": 0.0, "ad_or_user_activity_ratio": 0.0}

    total_count = len(messages)
    authors = set()
    link_count = 0
    total_chars = 0
    buyer_signals_count = 0
    trash_count = 0
    real_activity_count = 0

    buyer_keywords = (
        "сниму", "ищу", "нужен", "нужна", "нужны", "посоветуйте", "кто сдает", "кто сдаёт",
        "кто делает", "сколько стоит", "подскажите", "купим", "требуется", "интересует",
        "ищем", "какая цена", "где найти", "кто поможет", "подскажите пожал",
        "ищу варианты", "нужна консультация", "хочу заказать", "аренда", "подберите",
        "порекомендуйте", "почем", "кто может", "где можно", "кто знает", "нужен риелтор",
        "нужен трансфер", "нужен гид", "кто меняет", "обмен"
    )
    
    trash_keywords = (
        "порно", "porn", "18+", "казино", "casino", "onlyfans", "webcam",
        "эскорт", "escort", "шлюхи", "проститутки", "интим", "нарко", "мефедрон", "соли", "закладки", "рулетка", "ставка"
    )
    
    ad_keywords = (
        "сдаю", "продам", "предлагаю", "оказываю", "услуги", "цена", "скидка", "акция", "в наличии", "обращайтесь",
        "реклама", "сотрудничество", "b2b", "партнерство", "опт", "поставщик", "доставка", "производитель", "каталог", "бизнес", "прайс"
    )

    author_counts = {}
    for m in messages:
        txt = (m.get("message_text") or "").strip()
        txt_low = txt.lower()
        total_chars += len(txt)
        
        # Author tracking
        user_key = m.get("username") or m.get("first_name") or m.get("user_id") or "anon"
        authors.add(user_key)
        author_counts[user_key] = author_counts.get(user_key, 0) + 1

        has_link = "http://" in txt or "https://" in txt or "t.me/" in txt
        is_hashtag_spam = txt.count("#") >= 5

        # Link/Ad density check
        if has_link or is_hashtag_spam:
            link_count += 1

        # Trash check
        if any(kw in txt_low for kw in trash_keywords):
            trash_count += 1

        # Real activity check (buyer signals, conversational, or legitimate ads without too many links)
        is_buyer = any(kw in txt_low for kw in buyer_keywords)
        is_ad = any(kw in txt_low for kw in ad_keywords)
        
        if is_buyer:
            buyer_signals_count += 1
            real_activity_count += 1
        elif is_ad and not is_hashtag_spam:
            real_activity_count += 1
        elif len(txt) > 20 and not has_link: # Conversational
            real_activity_count += 1

    max_single_author_count = max(author_counts.values()) if author_counts else 0
    max_author_share = round(max_single_author_count / max(1, total_count), 2)

    return {
        "unique_authors_ratio": round(len(authors) / max(1, total_count), 2),
        "max_author_share": max_author_share,
        "link_density": round(link_count / max(1, total_count), 2),
        "avg_length": round(total_chars / max(1, total_count), 1),
        "buyer_signals_count": buyer_signals_count,
        "trash_ratio": round(trash_count / max(1, total_count), 2),
        "ad_or_user_activity_ratio": round(real_activity_count / max(1, total_count), 2)
    }


async def evaluate_chat_quality(username_or_link: str, platform: str = "telegram") -> Dict[str, Any]:
    """
    Evaluates a candidate chat/group from Telegram, VK, OK, or MAX Messenger.
    Uses pre-metrics to filter out bot dumps without wasting free LLM tokens.
    For ambiguous/promising chats, calls LLM (Groq/Gemini cascade) with strict JSON output.
    Approve live communities with high author diversity for real-time monitoring.
    Now supports Channels via API if they meet the 40% ad/user activity requirement.
    """
    from src.ingestion.platform_detector import clean_telegram_target
    clean_u = clean_telegram_target(username_or_link).lstrip("@").lower()

    trash_keywords = (
        "порно", "porn", "18+", "казино", "casino", "onlyfans", "webcam",
        "эскорт", "escort", "шлюхи", "проститутки", "интим", "нарко", "мефедрон", "соли", "закладки", "рулетка", "ставка"
    )

    if any(kw in clean_u for kw in trash_keywords):
        return {
            "score": 0,
            "status": "REJECTED",
            "chat_type": "TRASH",
            "detected_niches": [],
            "reason": "Запрещенная тематика (спам/порно/казино) в названии."
        }

    # Pre-reject personal profile handles by suffix for Telegram
    if platform == "telegram":
        profile_suffixes = ('_bot', '_support', '_contact', '_owner', '_ceo')
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
        if ingestor and ingestor.scrapers and getattr(ingestor, "_is_running", False):
            for node in ingestor.scrapers:
                if getattr(node, "status", None) == "BANNED" or not node.app:
                    continue
                if not getattr(node.app, "is_connected", False):
                    continue
                try:
                    pyro_msgs = []
                    async for m in node.app.get_chat_history(clean_u, limit=30):
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
                        break
                except Exception as pyro_err:
                    logger.debug(f"Pyrogram chat history notice for @{clean_u} on node #{getattr(node, 'db_id', '?')}: {pyro_err}")

        # 2. Fallback to Zero-Auth Public Scraper
        if not posts:
            scraper = PublicTelegramScraper()
            posts = await scraper.fetch_latest_messages(username_or_link)

    # Handling non-existent / 404 channels
    if posts is None:
        return {
            "score": 0,
            "status": "REJECTED",
            "chat_type": "NON_EXISTENT",
            "detected_niches": [],
            "reason": "Канала не существует в Telegram (UsernameNotOccupied / 404 Not Found)."
        }

    # Handling groups with < 2 public web preview posts (Telegram group chats redirect with 302)
    target_community_kw = (
        "dubai", "дубай", "нячанг", "пхукет", "бали", "аренда", "обмен", "чат", "expat", 
        "community", "жилье", "виза", "визы", "работа", "вакансии", "недвиж", "вилла", "авто", "байк",
        "мамы", "мамочки", "родители", "детсад", "садик", "школа", "дети", "домохозяйки",
        "moms", "parents", "housewives", "nursery", "school", "kindergarten", "kids", "family",
        "gems", "nordanglia", "kingsschool", "britishschool", "repton", "raffles"
    )
    is_target_community = any(kw in clean_u for kw in target_community_kw)

    if not posts or len(posts) < 2:
        if is_target_community:
            return {
                "score": 70,
                "status": "APPROVED",
                "chat_type": "LIVE_COMMUNITY",
                "detected_niches": ["community"],
                "reason": f"Кандидат Telegram-группы (ожидает MTProto вступления юзербота)."
            }
        return {
            "score": 20,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": f"Недостаточно сообщений для аудита (найдено {len(posts) if posts else 0} из 2 необходимых)."
        }

    # 1. Pre-metrics filtering (Zero Token Cost Optimization)
    metrics = calculate_pre_metrics(posts)
    logger.info(f"📊 Pre-metrics for {username_or_link}: authors_ratio={metrics['unique_authors_ratio']}, trash={metrics['trash_ratio']}, ad_or_user={metrics['ad_or_user_activity_ratio']}")

    if metrics["trash_ratio"] > 0.05:
        return {
            "score": 10,
            "status": "REJECTED",
            "chat_type": "TRASH",
            "detected_niches": [],
            "reason": f"Слишком много запрещенного контента (порно/казино): {int(metrics['trash_ratio']*100)}%."
        }

    is_monopolized = metrics.get("max_author_share", 0.0) >= 0.50
    # Снижаем порог продуктивности до 20%, так как рекламные/B2B каналы нам теперь нужны
    is_productive = metrics["ad_or_user_activity_ratio"] >= 0.20

    if not is_productive and not is_target_community:
        # If it's monopolized and not productive, it's a bot feed.
        # But we now allow channels if they have at least 10% unique authors or ANY ad activity
        if metrics["unique_authors_ratio"] < 0.05 and is_monopolized and metrics["ad_or_user_activity_ratio"] < 0.10:
            return {
                "score": 15,
                "status": "REJECTED",
                "chat_type": "SPAM_DUMP",
                "detected_niches": [],
                "reason": f"Ботовская ферма или мусорный канал: продуктивность {int(metrics['ad_or_user_activity_ratio']*100)}%, топ-автор {int(metrics.get('max_author_share', 0)*100)}%."
            }
            
        # High link density pure ad feed rejection (> 90% links/hashtags) - raised from 75% for B2B lists
        if metrics["link_density"] > 0.90:
            return {
                "score": 20,
                "status": "REJECTED",
                "chat_type": "SPAM_DUMP",
                "detected_niches": [],
                "reason": f"Исключительно спам-ссылки без текста ({int(metrics['link_density']*100)}% ссылок)."
            }

    # EXPANDED LIVE COMMUNITY / CHANNEL APPROVAL:
    # Accept if productive (>= 40% real ads/user activity) OR if it's a good community (not monopolized + low spam)
    if is_productive or (metrics["unique_authors_ratio"] >= 0.15 and not is_monopolized and metrics["link_density"] <= 0.70) or (is_target_community and not is_monopolized):
        score = 85 if is_productive else (75 if not is_target_community else 85)
        chat_type = "B2B_CHANNEL" if is_monopolized else "LIVE_COMMUNITY"
        return {
            "score": score,
            "status": "APPROVED",
            "chat_type": chat_type,
            "detected_niches": ["community"],
            "reason": f"Результативный чат/канал: продуктивность {int(metrics['ad_or_user_activity_ratio']*100)}%, спам {int(metrics['link_density']*100)}%."
        }

    # 2. Free LLM Quality Audit (Groq/Gemini cascade)
    sample_snippets = []
    for i, p in enumerate(posts[:30], 1):
        txt = (p.get("message_text") or "").replace("\n", " ").strip()[:180]
        user_label = p.get("username") or p.get("first_name") or "Пользователь"
        sample_snippets.append(f"{i}. [{user_label}]: \"{txt}\"")

    formatted_timeline = "\n".join(sample_snippets)

    system_instruction = (
        "ROLE: Traffic Quality Auditor for LeadRadar.win.\n"
        "TASK: Analyze recent messages from a Telegram group/channel. We are looking for REAL COMMUNITIES/GROUPS, OR HIGH QUALITY CHANNELS containing real ads/services, business directories, and B2B partners.\n\n"
        "CRITICAL RULES:\n"
        "- APPROVE if there is user communication, requests, services, community activity, OR FAMILY/MOMS/SCHOOLS discussions -> status='APPROVED', score=60-90.\n"
        "- APPROVE ADVERTISING CHANNELS, B2B PARTNER LISTS, and business directories. Even if it's 100% ads, if it contains real businesses/services, we want it! -> status='APPROVED', chat_type='B2B_CHANNEL', score=80-100.\n"
        "- REJECT IMMEDIATELY ONLY if it contains PORN, crypto scams, or 100% automated gibberish spam without business context -> status='REJECTED', score=10.\n\n"
        "OUTPUT FORMAT (Strict JSON ONLY):\n"
        "{\n"
        '  "buyer_leads_count": 1,\n'
        '  "score": 85,\n'
        '  "status": "APPROVED",\n'
        '  "chat_type": "B2B_CHANNEL",\n'
        '  "detected_niches": ["B2B", "ADVERTISING"],\n'
        '  "reason": "Рекламный канал или база B2B партнеров."\n'
        "}"
    )

    prompt = (
        f"Group/Channel: {username_or_link}\n"
        f"Pre-metrics: authors_ratio={metrics['unique_authors_ratio']}, link_density={metrics['link_density']}, ad_user_activity={metrics['ad_or_user_activity_ratio']}\n\n"
        f"Messages sample (30 msgs max):\n{formatted_timeline}\n\n"
        f"Return strict JSON verdict:"
    )

    # Respect Free LLM Rate Limit
    await asyncio.sleep(AUDIT_RATE_LIMIT_DELAY)

    # Call LLM via Groq primary, Gemini fallback
    raw_res = await _call_llm_json(prompt, system_instruction)

    if raw_res and "score" in raw_res:
        score_val = int(raw_res.get("score", 50))
        # Approve if score >= 40 AND (has ad activity OR has diversity)
        if score_val >= 40 and (metrics["ad_or_user_activity_ratio"] >= 0.15 or (metrics["unique_authors_ratio"] >= 0.05 and metrics["link_density"] <= 0.90)):
            return {
                "score": score_val,
                "status": "APPROVED",
                "chat_type": raw_res.get("chat_type", "LIVE_COMMUNITY"),
                "detected_niches": raw_res.get("detected_niches", ["community"]),
                "reason": raw_res.get("reason", "Одобрено ИИ-аудитором.")
            }

        return {
            "score": score_val,
            "status": "REJECTED",
            "chat_type": "SPAM_DUMP",
            "detected_niches": [],
            "reason": raw_res.get("reason", "Не соответствует стандартам качества (спам, боты, порно).")
        }

    # 3. Fallback Heuristic Audit if LLM API is unavailable / rate-limited
    is_live = metrics["ad_or_user_activity_ratio"] >= 0.20 or (metrics["unique_authors_ratio"] >= 0.05 and metrics["link_density"] <= 0.90)
    heuristic_score = 75 if is_live else 30
    return {
        "score": heuristic_score,
        "status": "APPROVED" if is_live else "REJECTED",
        "chat_type": "LIVE_COMMUNITY" if is_live else "SPAM_DUMP",
        "detected_niches": ["community"] if is_live else [],
        "reason": "Эвристический аудит: " + ("Результативный чат/канал." if is_live else "Низкое качество/Спам.")
    }

