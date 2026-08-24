import re
import logging
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
}

class VKPublicScraper:
    """Scrapes public posts and comments from VK groups and walls."""
    @staticmethod
    async def fetch_latest_messages(target_identifier: str, limit: int = 10) -> List[Dict[str, Any]]:
        clean_target = target_identifier.replace("https://vk.com/", "").replace("https://vk.ru/", "").strip('/')
        url = f"https://vk.com/{clean_target}"
        results = []

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=HEADERS) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(f"VK fetch notice for {clean_target}: HTTP {resp.status_code}")
                    return []

                html = resp.text
                title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                chat_title = title_match.group(1).replace(" | ВКонтакте", "").strip() if title_match else f"VK: {clean_target}"

                # Extract post text snippets from wall HTML
                post_blocks = re.findall(r'class="wall_post_text[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
                if not post_blocks:
                    post_blocks = re.findall(r'class="pi_text[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)

                for idx, block in enumerate(post_blocks[:limit]):
                    clean_text = re.sub(r'<[^>]+>', ' ', block).strip()
                    if clean_text and len(clean_text) >= 15:
                        results.append({
                            "message_text": clean_text,
                            "chat_title": chat_title,
                            "user_id": f"vk_post_{clean_target}_{idx}",
                            "username": clean_target,
                            "first_name": "VK Автор",
                            "timestamp": datetime.now(timezone.utc)
                        })
        except Exception as e:
            logger.warning(f"Error scraping VK target {clean_target}: {e}")

        return results


class OKPublicScraper:
    """Scrapes public topics and comments from Odnoklassniki groups."""
    @staticmethod
    async def fetch_latest_messages(target_identifier: str, limit: int = 10) -> List[Dict[str, Any]]:
        clean_target = target_identifier.replace("https://ok.ru/", "").strip('/')
        url = f"https://ok.ru/{clean_target}"
        results = []

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=HEADERS) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(f"OK fetch notice for {clean_target}: HTTP {resp.status_code}")
                    return []

                html = resp.text
                title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                chat_title = title_match.group(1).replace(" | ОК", "").strip() if title_match else f"OK: {clean_target}"

                # Extract topic texts from OK feeds
                text_blocks = re.findall(r'class="media-text_cnt[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
                if not text_blocks:
                    text_blocks = re.findall(r'class="feed_b[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)

                for idx, block in enumerate(text_blocks[:limit]):
                    clean_text = re.sub(r'<[^>]+>', ' ', block).strip()
                    if clean_text and len(clean_text) >= 15:
                        results.append({
                            "message_text": clean_text,
                            "chat_title": chat_title,
                            "user_id": f"ok_topic_{clean_target}_{idx}",
                            "username": clean_target,
                            "first_name": "OK Автор",
                            "timestamp": datetime.now(timezone.utc)
                        })
        except Exception as e:
            logger.warning(f"Error scraping OK target {clean_target}: {e}")

        return results


class MAXPublicScraper:
    """Scrapes public message streams from MAX Messenger groups."""
    @staticmethod
    async def fetch_latest_messages(target_identifier: str, limit: int = 10) -> List[Dict[str, Any]]:
        clean_target = target_identifier.replace("https://max.ru/s/", "").replace("https://max.ru/", "").strip('/')
        url = f"https://max.ru/s/{clean_target}"
        results = []

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=HEADERS) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(f"MAX fetch notice for {clean_target}: HTTP {resp.status_code}")
                    return []

                html = resp.text
                title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                chat_title = title_match.group(1).replace(" | MAX", "").strip() if title_match else f"MAX: {clean_target}"

                text_blocks = re.findall(r'class="max_message_text[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
                for idx, block in enumerate(text_blocks[:limit]):
                    clean_text = re.sub(r'<[^>]+>', ' ', block).strip()
                    if clean_text and len(clean_text) >= 15:
                        results.append({
                            "message_text": clean_text,
                            "chat_title": chat_title,
                            "user_id": f"max_msg_{clean_target}_{idx}",
                            "username": clean_target,
                            "first_name": "MAX Пользователь",
                            "timestamp": datetime.now(timezone.utc)
                        })
        except Exception as e:
            logger.warning(f"Error scraping MAX target {clean_target}: {e}")

        return results
