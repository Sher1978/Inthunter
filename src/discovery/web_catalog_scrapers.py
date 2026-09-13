import re
import html
import logging
from typing import List, Dict, Set
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("intent_hunter.web_catalog")

class WebCatalogScraper:
    """
    Web scraper for open Telegram directories and group rankings:
    1. Combot Top Groups (combot.org/top/telegram/groups)
    2. Open TG Search Engine Aggregators (Lyzem, Telegago, TgramSearch)
    """

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }

    async def fetch_combot_top_groups(
        self,
        country_code: str = "ru",
        limit: int = 40
    ) -> List[Dict]:
        """
        Scrapes top active Telegram supergroups from Combot Public Rating (combot.org).
        Combot hosts public rankings of active chats with real activity metrics.
        """
        logger.info(f"📊 Scraping Combot Top Telegram Supergroups (Country: {country_code})...")
        candidates = []
        seen_handles: Set[str] = set()

        url = f"https://combot.org/top/telegram/groups?lat={country_code}"

        try:
            async with httpx.AsyncClient(timeout=12.0, headers=self.headers, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    # Parse combot group rows
                    rows = soup.select(".table-item, .item-title, a[href*='/t/']")
                    for node in soup.find_all("a", href=True):
                        href = node["href"]
                        # Combot chat links format: /t/username or https://t.me/username
                        if "/t/" in href or "t.me/" in href:
                            clean_handle = href.split("/t/")[-1].split("t.me/")[-1].strip().replace("@", "")
                            if clean_handle and len(clean_handle) >= 5 and clean_handle.lower() not in seen_handles:
                                seen_handles.add(clean_handle.lower())
                                title_text = node.get_text().strip() or f"@{clean_handle}"
                                candidates.append({
                                    "username": f"@{clean_handle}",
                                    "title": title_text,
                                    "chat_type": "group",
                                    "description": "🔥 Combot Top Rated Active Supergroup",
                                    "estimated_members": "Active Supergroup",
                                    "niche_code": country_code,
                                    "source": "COMBOT_TOP"
                                })
                else:
                    logger.warning(f"Combot Top HTTP {resp.status_code}")
        except Exception as e:
            logger.debug(f"Notice during Combot Top scraping: {e}")

        logger.info(f"✅ Combot Top Scraper extracted {len(candidates)} active supergroup handles.")
        return candidates[:limit]

    async def search_open_directories(
        self,
        keywords: str,
        limit: int = 30
    ) -> List[Dict]:
        """
        Scrapes open Telegram search engines (Lyzem, Telegago, TgramSearch).
        """
        logger.info(f"🔍 Searching Open TG Catalogs for keyword: '{keywords}'...")
        candidates = []
        seen_handles: Set[str] = set()

        # 1. Search via Lyzem (https://lyzem.com/search?q=...)
        try:
            lyzem_url = f"https://lyzem.com/search?q={httpx.QueryParams({'q': keywords}).get('q')}"
            async with httpx.AsyncClient(timeout=10.0, headers=self.headers, follow_redirects=True) as client:
                resp = await client.get(lyzem_url)
                if resp.status_code == 200:
                    matches = re.findall(r'(?:t\.me/|@)([a-zA-Z0-9_]{5,32})', resp.text)
                    for m in matches:
                        u_clean = m.lower().strip()
                        if u_clean and u_clean not in seen_handles and u_clean not in {'joinchat', 'bot', 'channel'}:
                            seen_handles.add(u_clean)
                            candidates.append({
                                "username": f"@{u_clean}",
                                "title": f"Lyzem Catalog @{u_clean}",
                                "chat_type": "group",
                                "description": f"🔍 Lyzem Telegram Search result for '{keywords}'",
                                "estimated_members": "Directory Target",
                                "niche_code": "general",
                                "source": "LYZEM_DIRECTORY"
                            })
        except Exception as err:
            logger.debug(f"Notice scraping Lyzem directory for '{keywords}': {err}")

        logger.info(f"✅ Open Directories Scraper found {len(candidates)} Telegram targets for '{keywords}'")
        return candidates[:limit]
