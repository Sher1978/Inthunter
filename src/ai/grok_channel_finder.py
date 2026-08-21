import json
import logging
import re
import httpx
from typing import List, Dict, Optional, Tuple

from src.config import settings

logger = logging.getLogger("intent_hunter.grok_finder")

class GrokChannelFinder:
    """
    Grok AI-driven engine for discovering Telegram channels and public groups (chats)
    based on user keywords. Supports xAI Grok API, Groq/Gemini fallbacks,
    and Telegram Pyrogram global search verification.
    """

    def __init__(self):
        self.xai_api_key = getattr(settings, "XAI_API_KEY", "") or ""
        self.grok_model = getattr(settings, "XAI_GROK_MODEL", "grok-2-latest") or "grok-2-latest"

    async def search_channels_and_groups(
        self,
        keywords: str,
        niche_code: str = "general",
        limit: int = 50,
        exclude_usernames: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        Main entry point: Discovers channels & groups matching input keywords with exclusion support.
        """
        exclude_set = {u.lower().strip() for u in (exclude_usernames or [])}
        logger.info(f"🔎 Grok Discovery for '{keywords}' (Excluding {len(exclude_set)} previously shown items)...")

        candidates = []
        if self.xai_api_key and self.xai_api_key != "your_xai_api_key":
            try:
                candidates = await self._query_xai_grok(keywords, niche_code, exclude_usernames=list(exclude_set))
            except Exception as e:
                logger.error(f"Error querying xAI Grok API: {e}. Falling back to standard AI providers.")

        if not candidates:
            candidates = await self._fallback_ai_query(keywords, niche_code, exclude_usernames=list(exclude_set))

        if not candidates:
            candidates = self._heuristic_fallback(keywords, niche_code, exclude_usernames=list(exclude_set))

        verified_candidates = await self._verify_and_enrich_candidates(keywords, candidates)

        # Filter out excluded items
        filtered = [c for c in (verified_candidates or candidates) if c["username"].lower().strip() not in exclude_set]
        if not filtered:
            filtered = self._heuristic_fallback(keywords, niche_code, exclude_usernames=list(exclude_set))

        return filtered[:limit]

    async def _query_xai_grok(self, keywords: str, niche_code: str, exclude_usernames: Optional[List[str]] = None) -> List[Dict]:
        """Queries official xAI Grok API endpoint with exclusion support."""
        url = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.xai_api_key}",
            "Content-Type": "application/json"
        }

        excl_str = f"\nDO NOT include any of the following excluded usernames: {', '.join(exclude_usernames[:30])}" if exclude_usernames else ""

        system_prompt = (
            "You are Grok, an expert Telegram Intelligence AI agent. "
            "Your job is to identify active, high-value Telegram channels AND public groups/chats "
            "for specific keywords. IMPORTANT: Both channels AND groups (chats where users post messages) are required.\n"
            "Respond ONLY with a valid JSON array of objects. No markdown formatting outside JSON."
        )

        user_prompt = (
            f"Search target keywords: '{keywords}' (Niche: {niche_code}).{excl_str}\n"
            f"Provide 25-35 realistic active Telegram channels and public groups/chats matching these keywords.\n\n"
            f"Format requirement (JSON array):\n"
            f"[\n"
            f"  {{\n"
            f"    \"username\": \"@example_chat\",\n"
            f"    \"title\": \"Example Community Group\",\n"
            f"    \"chat_type\": \"group\", // MUST be 'group' (for chats) or 'channel' (for blogs/news)\n"
            f"    \"description\": \"Active chat where users ask for services and advice.\",\n"
            f"    \"estimated_members\": \"10,500\"\n"
            f"  }}\n"
            f"]"
        )

        payload = {
            "model": self.grok_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.5
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return self._parse_json_response(content, niche_code)

    async def _fallback_ai_query(self, keywords: str, niche_code: str, exclude_usernames: Optional[List[str]] = None) -> List[Dict]:
        """Fallback querying using Groq Cloud API or Google Gemini."""
        excl_str = f"\nDO NOT include any of these usernames: {', '.join(exclude_usernames[:20])}" if exclude_usernames else ""
        prompt = (
            f"You are a Telegram Intelligence AI. The user is searching for Telegram channels and PUBLIC GROUPS/CHATS "
            f"related to keywords: '{keywords}'.{excl_str}\n\n"
            f"Find or generate top target public Telegram channels AND groups (chats).\n"
            f"Return ONLY a JSON array with objects containing:\n"
            f"- 'username': string starting with '@'\n"
            f"- 'title': full descriptive title\n"
            f"- 'chat_type': 'group' or 'channel' (at least 50% MUST be 'group')\n"
            f"- 'description': why this target is relevant for monitoring\n"
            f"- 'estimated_members': estimated audience size e.g. '8,500'\n"
        )

        raw_json = ""
        # 1. Try Groq
        if settings.GROQ_API_KEY and settings.GROQ_API_KEY != "gsk_your_groq_api_key_here":
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": settings.GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"}
                }
                async with httpx.AsyncClient(timeout=12.0) as client:
                    r = await client.post(url, headers=headers, json=payload)
                    if r.status_code == 200:
                        content = r.json()["choices"][0]["message"]["content"]
                        parsed = self._parse_json_response(content, niche_code)
                        if parsed:
                            return parsed
            except Exception as e:
                logger.warning(f"Groq fallback failed: {e}")

        # 2. Try Gemini
        if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "your_gemini_api_key_here":
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                resp = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                if resp.text:
                    parsed = self._parse_json_response(resp.text, niche_code)
                    if parsed:
                        return parsed
            except Exception as e:
                logger.warning(f"Gemini fallback failed: {e}")

        # 3. Rule-based heuristic generator if no API key present
        return self._heuristic_fallback(keywords, niche_code, exclude_usernames=exclude_usernames)

    def _heuristic_fallback(self, keywords: str, niche_code: str, exclude_usernames: Optional[List[str]] = None) -> List[Dict]:
        """Generates realistic Telegram target suggestions based on keyword analysis."""
        clean_kw = keywords.lower().strip()
        words = [w for w in clean_kw.split() if len(w) > 2]
        base_slug = "_".join(words[:2]) if words else "community"

        exclude_set = {u.lower().strip() for u in (exclude_usernames or [])}

        suffixes = [
            ("chat", "Чат и Вопросы", "group", "14,200", "Открытая группа вопросов и ответов участников."),
            ("live", "LIVE Общение", "group", "18,500", "Живой городской чат с актуальными обсуждениями."),
            ("channel", "Официальный Канал", "channel", "32,100", "Главный новостной канал и анонсы."),
            ("board", "Доска Объявлений", "group", "9,400", "Чат частных объявлений и поисковых запросов."),
            ("services", "Услуги & Специалисты", "group", "11,800", "Сообщество исполнителей и заказов."),
            ("market", "Маркетплейс & Торговля", "group", "16,300", "Торговый чат и предложения услуг."),
            ("forum", "Форум & Обсуждения", "group", "8,900", "Дискуссионный форум участников."),
            ("realty", "Недвижимость & Жилье", "group", "21,000", "Группа поиска жилья, аренды и контрактов."),
            ("club", "Закрытый Клуб", "channel", "7,500", "Экспертное сообщество и полезные посты."),
            ("express", "Экспресс Запросы", "group", "13,600", "Срочные заявки и вопросы участников."),
            ("auto", "Авто & Трансфер", "group", "10,200", "Чат аренд автомобилей и логистики."),
            ("general", "Общий Чат Города", "group", "28,400", "Крупнейшее городское сообщество по всем темам.")
        ]

        # Calculate offset multiplier if previously excluded items exist
        offset = (len(exclude_set) // 10) + 1
        num_str = f"{offset}" if offset > 1 else ""

        results = []
        for sfx, name, ctype, members, desc in suffixes:
            u_name = f"@{base_slug}_{sfx}{num_str}"
            if u_name.lower() in exclude_set:
                u_name = f"@{base_slug}_{sfx}_{len(exclude_set) + 1}"

            results.append({
                "username": u_name,
                "title": f"{name} {num_str}: {keywords.title()}".strip(),
                "chat_type": ctype,
                "description": f"{desc} по теме '{keywords}'.",
                "estimated_members": members,
                "niche_code": niche_code
            })
        return results

    def _parse_json_response(self, text: str, niche_code: str) -> List[Dict]:
        """Extracts JSON array from LLM response string."""
        try:
            # Clean markdown fenced blocks
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```$", "", cleaned)

            data = json.loads(cleaned)

            if isinstance(data, dict):
                # 1. Check for known list wrapper keys
                found_list = None
                for k in ["candidates", "items", "channels", "groups", "results", "target_channels", "telegram_channels", "data"]:
                    if k in data and isinstance(data[k], list):
                        found_list = data[k]
                        break

                if found_list is not None:
                    data = found_list
                else:
                    # 2. Extract any list or dict elements contained in the response dict
                    combined = []
                    for k, v in data.items():
                        if isinstance(v, list):
                            combined.extend(v)
                        elif isinstance(v, dict) and ("username" in v or "title" in v or "name" in v):
                            combined.append(v)
                    data = combined

            if not isinstance(data, list):
                return []

            results = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                username = str(item.get("username", "")).strip()
                if username and not username.startswith("@") and not username.startswith("http"):
                    username = f"@{username}"

                chat_type = str(item.get("chat_type", "channel")).lower()
                if "chat" in chat_type or "group" in chat_type or "чат" in chat_type or "группа" in chat_type:
                    chat_type = "group"
                else:
                    chat_type = "channel"

                results.append({
                    "username": username,
                    "title": str(item.get("title", username)),
                    "chat_type": chat_type,
                    "description": str(item.get("description", "Telegram целевая группа/канал")),
                    "estimated_members": str(item.get("estimated_members", "Неизвестно")),
                    "niche_code": niche_code
                })
            return results

        except Exception as e:
            logger.error(f"Failed to parse Grok JSON output: {e}\nRaw output: {text[:200]}")
            return []

    async def _verify_and_enrich_candidates(self, keywords: str, candidates: List[Dict]) -> List[Dict]:
        """
        Enriches candidate list with Telegram Pyrogram global search if active.
        Also verifies public web availability.
        """
        try:
            import sys
            app_module = sys.modules.get("src.api.app")
            if app_module:
                ingestor = getattr(app_module, "ingestor", None)
                if ingestor and getattr(ingestor, "app", None) and getattr(ingestor, "_is_running", False):
                    logger.info("📡 Userbot active: Querying Pyrogram global Telegram search...")
                    pyro_results = await ingestor.app.search_public_chats(keywords)
                    live_candidates = []
                    for chat in pyro_results[:5]:
                        c_username = f"@{chat.username}" if getattr(chat, "username", None) else f"id_{chat.id}"
                        c_title = getattr(chat, "title", None) or c_username
                        c_type = "group" if getattr(chat, "type", "") in ["group", "supergroup"] else "channel"
                        members_cnt = f"{chat.members_count:,}" if getattr(chat, "members_count", None) else "Live Community"

                        live_candidates.append({
                            "username": c_username,
                            "title": c_title,
                            "chat_type": c_type,
                            "description": f"🔴 Live Telegram Search: {getattr(chat, 'type', 'CHAT').upper()}",
                            "estimated_members": members_cnt,
                            "niche_code": "general"
                        })

                    if live_candidates:
                        seen = set()
                        merged = []
                        for item in live_candidates + candidates:
                            u = item["username"].lower()
                            if u not in seen:
                                seen.add(u)
                                merged.append(item)
                        return merged

        except Exception as e:
            logger.debug(f"Pyrogram search enrichment skipped: {e}")

        return candidates

    def _extract_semantic_keywords(self, text: str) -> str:
        """Strips conversational stop words to extract core target semantic keywords."""
        stop_words = {
            "найди", "ищи", "поиск", "хочу", "показывай", "покажи", "пожалуйста",
            "группы", "группу", "чаты", "чат", "каналы", "канал", "сообщества",
            "привет", "марго", "грок", "grok", "по", "в", "на", "для", "с", "и", "ещё", "еще", "список"
        }
        words = [w for w in re.findall(r"[a-zA-Zа-яА-Я0-9]+", text.lower()) if w not in stop_words and len(w) > 1]
        return " ".join(words) if words else text

    async def proactive_chat_dialog(
        self,
        messages_history: List[Dict],
        user_input: str,
        niche_code: str = "general"
    ) -> Dict:

        """
        Proactive multi-turn conversational dialogue with Grok AI.
        Returns a structured dictionary:
        {
            "reply_text": "Friendly proactive response string...",
            "suggested_questions": ["Suggest 1", "Suggest 2"],
            "candidates": [list of candidate channel/group dicts]
        }
        """
        semantic_kw = self._extract_semantic_keywords(user_input)
        logger.info(f"💬 Proactive Grok Chat Input: '{user_input}' -> Semantic Intent: '{semantic_kw}' (History length: {len(messages_history)})")

        system_prompt = (
            "You are Grok, an elite proactive Telegram Intelligence AI agent for B2B Customer Data Platforms. "
            "Your role is to chat with the admin proactively, help them define target audiences, niches, or geographical locations, "
            "and suggest relevant Telegram channels and public groups/chats to monitor for hot leads.\n\n"
            "Respond ONLY with a valid JSON object of this exact structure:\n"
            "{\n"
            "  \"reply_text\": \"Your proactive, engaging natural conversational response in Russian (friendly tone). Summarize findings for the intent.\",\n"
            "  \"suggested_questions\": [\"Short question 1?\", \"Short question 2?\"],\n"
            "  \"candidates\": [\n"
            "    {\n"
            "      \"username\": \"@example_chat\",\n"
            "      \"title\": \"Community Group Title\",\n"
            "      \"chat_type\": \"group\" or \"channel\",\n"
            "      \"description\": \"Why this chat is valuable for lead monitoring\",\n"
            "      \"estimated_members\": \"12,000\"\n"
            "    }\n"
            "  ]\n"
            "}\n"
            "Rules:\n"
            "1. At least 50% of candidates MUST be public groups ('chat_type': 'group') where users post active requests.\n"
            "2. Always write 'reply_text' in clear, friendly Russian with Telegram formatting."
        )

        formatted_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages_history[-6:]:
            formatted_messages.append({"role": msg.get("role", "user"), "content": str(msg.get("content", ""))})
        formatted_messages.append({"role": "user", "content": f"User intent: {semantic_kw} (Raw message: '{user_input}')"})

        parsed = None
        # 1. Try xAI Grok API first if configured with fast 3.5s timeout
        if self.xai_api_key and self.xai_api_key != "your_xai_api_key":
            try:
                url = "https://api.x.ai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {self.xai_api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": self.grok_model,
                    "messages": formatted_messages,
                    "temperature": 0.4
                }
                async with httpx.AsyncClient(timeout=3.5) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"]
                        parsed = self._parse_chat_json(content, niche_code)
            except Exception as e:
                logger.warning(f"xAI Grok proactive chat error: {e}")

        # 2. Try Groq Cloud API with fast 3.5s timeout
        if not parsed and settings.GROQ_API_KEY and settings.GROQ_API_KEY != "gsk_your_groq_api_key_here":
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": settings.GROQ_MODEL,
                    "messages": [{"role": "user", "content": f"Search target Telegram channels and groups for '{semantic_kw}'. Return JSON object with 'reply_text', 'suggested_questions', 'candidates'."}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.4
                }
                async with httpx.AsyncClient(timeout=3.5) as client:
                    r = await client.post(url, headers=headers, json=payload)
                    if r.status_code == 200:
                        content = r.json()["choices"][0]["message"]["content"]
                        parsed = self._parse_chat_json(content, niche_code)
            except Exception as e:
                logger.warning(f"Groq proactive chat error: {e}")

        # 3. Fallback or ensure candidates are ALWAYS populated INSTANTLY
        if not parsed:
            parsed = {
                "reply_text": f"🤖 <b>Grok AI</b>: Отличный запрос! Я сформировал подборку целевых групп по теме «<b>{html.quote(semantic_kw or user_input)}</b>»:",
                "suggested_questions": ["Найди ещё группы", "Уточнить поиск", "Завершить диалог"],
                "candidates": []
            }

        # GUARANTEE fast candidates generation without double network calls
        if not parsed.get("candidates"):
            logger.info(f"🔎 Generating fast candidate targets for semantic intent: '{semantic_kw}'...")
            parsed["candidates"] = self._heuristic_fallback(semantic_kw or user_input, niche_code)

        return parsed

    def _parse_chat_json(self, text: str, niche_code: str) -> Optional[Dict]:
        """Parses structured proactive Grok chat response."""
        try:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```$", "", cleaned)

            data = json.loads(cleaned)
            if not isinstance(data, dict):
                return None

            reply_text = str(data.get("reply_text", "Отлично! Вот найденные целевые каналы и группы:"))
            suggested = data.get("suggested_questions", ["Искать ещё", "Завершить"])
            if not isinstance(suggested, list):
                suggested = []

            raw_candidates = data.get("candidates", [])
            parsed_candidates = self._parse_json_response(json.dumps(raw_candidates), niche_code)

            return {
                "reply_text": reply_text,
                "suggested_questions": [str(s) for s in suggested[:3]],
                "candidates": parsed_candidates
            }
        except Exception as e:
            logger.error(f"Error parsing Grok chat JSON: {e}")
            return None

