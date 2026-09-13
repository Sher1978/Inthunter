import re
import html
import logging
from typing import List, Dict, Optional, Set
import httpx

from src.config import settings

logger = logging.getLogger("intent_hunter.google_cse")

class GoogleCSEFinder:
    """
    Google Custom Search API (and Dorks web search fallback) engine for discovering 
    Telegram public groups, channels, and private invite links (t.me/joinchat / t.me/+...).
    """

    def __init__(self):
        self.api_key = getattr(settings, "GOOGLE_CSE_API_KEY", "") or ""
        self.cse_id = getattr(settings, "GOOGLE_CSE_ID", "") or ""
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }

    async def search_dorks(
        self,
        keywords: str,
        location_code: str = "global",
        limit: int = 30
    ) -> List[Dict]:
        """
        Executes Google Search Dorks for target keywords to find Telegram chats & invite links.
        Uses Google CSE API if keys configured, otherwise uses public search parsing fallback.
        """
        logger.info(f"🔎 Google Dorks Discovery for keywords: '{keywords}' (GEO: {location_code})...")

        if self.api_key and self.cse_id:
            return await self._search_via_cse_api(keywords, location_code, limit)
        else:
            return await self._search_via_web_dorks(keywords, location_code, limit)

    async def _search_via_cse_api(
        self,
        keywords: str,
        location_code: str,
        limit: int = 30
    ) -> List[Dict]:
        """Queries official Google Custom Search API."""
        dork_queries = [
            f'site:t.me/joinchat "{keywords}"',
            f'site:t.me/+ "{keywords}"',
            f'site:t.me "{keywords}" "чат" OR "group" OR "community"'
        ]

        found_candidates: List[Dict] = []
        seen_handles: Set[str] = set()

        async with httpx.AsyncClient(timeout=10.0, headers=self.headers) as client:
            for query in dork_queries:
                try:
                    url = "https://www.googleapis.com/customsearch/v1"
                    params = {
                        "key": self.api_key,
                        "cx": self.cse_id,
                        "q": query,
                        "num": 10
                    }
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("items", [])
                        for item in items:
                            link = item.get("link", "")
                            title = item.get("title", "")
                            snippet = item.get("snippet", "")
                            
                            extracted = self._extract_telegram_links(f"{link} {title} {snippet}")
                            for target in extracted:
                                clean_low = target.lower().strip()
                                if clean_low not in seen_handles:
                                    seen_handles.add(clean_low)
                                    found_candidates.append({
                                        "username": target,
                                        "title": html.unescape(title) or target,
                                        "chat_type": "group" if "+" in target or "joinchat" in target or "chat" in title.lower() else "channel",
                                        "description": f"🔍 Google Dork Match: {query}",
                                        "estimated_members": "Indexed Web Group",
                                        "niche_code": location_code,
                                        "source": "GOOGLE_CSE_DORK"
                                    })
                    else:
                        logger.warning(f"Google CSE HTTP {resp.status_code}: {resp.text[:150]}")
                except Exception as e:
                    logger.debug(f"Google CSE query notice for '{query}': {e}")

        logger.info(f"✅ Google CSE API found {len(found_candidates)} Telegram targets for '{keywords}'")
        return found_candidates[:limit]

    async def _search_via_web_dorks(
        self,
        keywords: str,
        location_code: str,
        limit: int = 30
    ) -> List[Dict]:
        """Fallback lightweight web dork search via public endpoints."""
        found_candidates: List[Dict] = []
        seen_handles: Set[str] = set()

        # Query public web search fallback endpoint (e.g. DuckDuckGo HTML or SearXNG public)
        dork_query = f'site:t.me "{keywords}" chat'
        url = f"https://html.duckduckgo.com/html/?q={httpx.QueryParams({'q': dork_query}).get('q')}"

        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self.headers, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    extracted = self._extract_telegram_links(resp.text)
                    for target in extracted:
                        clean_low = target.lower().strip()
                        if clean_low not in seen_handles:
                            seen_handles.add(clean_low)
                            found_candidates.append({
                                "username": target,
                                "title": f"Web Match: {target}",
                                "chat_type": "group" if "+" in target or "joinchat" in target else "channel",
                                "description": f"🔍 Public Web Search Dork for '{keywords}'",
                                "estimated_members": "Indexed Web Target",
                                "niche_code": location_code,
                                "source": "GOOGLE_WEB_DORK"
                            })
        except Exception as e:
            logger.debug(f"Notice during Web Dorks search for '{keywords}': {e}")

        logger.info(f"✅ Web Dorks fallback found {len(found_candidates)} Telegram targets for '{keywords}'")
        return found_candidates[:limit]

    def _extract_telegram_links(self, text: str) -> List[str]:
        """
        Extracts Telegram public handles (@username) and private invite links 
        (t.me/joinchat/... or t.me/+...) from raw text/HTML snippet.
        """
        if not text:
            return []

        # Match @usernames and t.me/ links
        pattern = r'(?:https?://)?(?:t\.me/|@)([a-zA-Z0-9_+\-]{5,64})'
        matches = re.findall(pattern, text)

        extracted = []
        ignored = {'joinchat', 'share', 'contact', 'addstickers', 'proxy', 'c', 's', 'login', 'bot'}

        for raw in matches:
            clean = raw.strip()
            if not clean or clean.lower() in ignored:
                continue

            if clean.startswith("+") or clean.startswith("joinchat/"):
                full_link = f"https://t.me/{clean}"
                if full_link not in extracted:
                    extracted.append(full_link)
            else:
                formatted = f"@{clean.lstrip('@')}"
                if len(formatted) >= 5 and formatted not in extracted:
                    extracted.append(formatted)

        return extracted
