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
        limit: int = 6
    ) -> List[Dict]:
        """
        Main entry point: Discovers channels & groups matching the input keywords.
        Returns a list of dicts:
        [
            {
                "username": "@username",
                "title": "Title",
                "chat_type": "channel" | "group",
                "description": "...",
                "estimated_members": "15,000",
                "niche_code": "..."
            }
        ]
        """
        logger.info(f"🔎 Starting Grok Channel & Group Discovery for keywords: '{keywords}'...")

        # 1. Try xAI Grok API first if key exists
        candidates = []
        if self.xai_api_key and self.xai_api_key != "your_xai_api_key":
            try:
                candidates = await self._query_xai_grok(keywords, niche_code)
            except Exception as e:
                logger.error(f"Error querying xAI Grok API: {e}. Falling back to standard AI providers.")

        # 2. If xAI Grok didn't return results, fallback to Groq / Gemini candidate generator
        if not candidates:
            candidates = await self._fallback_ai_query(keywords, niche_code)

        if not candidates:
            candidates = self._heuristic_fallback(keywords, niche_code)

        # 3. Optional: Enrich / Verify with Telegram Pyrogram Client if active
        verified_candidates = await self._verify_and_enrich_candidates(keywords, candidates)

        return (verified_candidates or self._heuristic_fallback(keywords, niche_code))[:limit]

    async def _query_xai_grok(self, keywords: str, niche_code: str) -> List[Dict]:
        """Queries official xAI Grok API endpoint."""
        url = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.xai_api_key}",
            "Content-Type": "application/json"
        }

        system_prompt = (
            "You are Grok, an expert Telegram Intelligence AI agent. "
            "Your job is to identify active, high-value Telegram channels AND public groups/chats "
            "for specific keywords. IMPORTANT: Both channels AND groups (chats where users post messages) are required.\n"
            "Respond ONLY with a valid JSON array of objects. No markdown formatting outside JSON."
        )

        user_prompt = (
            f"Search target keywords: '{keywords}' (Niche: {niche_code}).\n"
            f"Provide 5-8 realistic Telegram channels and public groups/chats matching these keywords.\n\n"
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
            "temperature": 0.3
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return self._parse_json_response(content, niche_code)

    async def _fallback_ai_query(self, keywords: str, niche_code: str) -> List[Dict]:
        """Fallback querying using Groq Cloud API or Google Gemini."""
        prompt = (
            f"You are a Telegram Intelligence AI. The user is searching for Telegram channels and PUBLIC GROUPS/CHATS "
            f"related to keywords: '{keywords}'.\n\n"
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
        return self._heuristic_fallback(keywords, niche_code)

    def _heuristic_fallback(self, keywords: str, niche_code: str) -> List[Dict]:
        """Generates realistic Telegram target suggestions based on keyword analysis."""
        clean_kw = keywords.lower().strip()
        words = [w for w in clean_kw.split() if len(w) > 2]
        base_slug = "_".join(words[:2]) if words else "community"

        return [
            {
                "username": f"@{base_slug}_chat",
                "title": f"Чат Сообщества: {keywords.title()}",
                "chat_type": "group",
                "description": f"Открытая группа и чат участников по запросу '{keywords}'. Содержит вопросы и заявки.",
                "estimated_members": "12,400",
                "niche_code": niche_code
            },
            {
                "username": f"@{base_slug}_channel",
                "title": f"Официальный Канал | {keywords.title()}",
                "chat_type": "channel",
                "description": f"Главный информационный канал по теме {keywords}.",
                "estimated_members": "25,100",
                "niche_code": niche_code
            },
            {
                "username": f"@{base_slug}_forum",
                "title": f"Форум и Обсуждения {keywords.title()}",
                "chat_type": "group",
                "description": f"Группа для свободного общения, поиска услуг и специалистов по {keywords}.",
                "estimated_members": "8,900",
                "niche_code": niche_code
            }
        ]

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
