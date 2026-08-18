import re
import html
import logging
from datetime import datetime, timezone
from typing import List, Dict
import httpx

logger = logging.getLogger("intent_hunter.vk_scraper")

class VkPublicScraper:
    """
    Zero-auth public VK discussion scraper.
    Parses open VK group discussion topics (https://vk.com/topic-XXXX_YYYY) without API tokens.
    """

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }

    def _strip_html(self, raw_html: str) -> str:
        if not raw_html:
            return ""
        text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        return html.unescape(text).strip()

    async def fetch_topic_messages(self, topic_url: str) -> List[Dict]:
        """Fetches posts from a public VK topic thread."""
        if "vk.com" not in topic_url:
            return []

        messages = []
        try:
            async with httpx.AsyncClient(headers=self.headers, follow_redirects=True, timeout=12.0) as client:
                res = await client.get(topic_url)
                if res.status_code != 200:
                    logger.warning(f"Failed to fetch VK topic {topic_url} (HTTP {res.status_code})")
                    return []

                raw_body = res.text

                # Extract topic title
                title_match = re.search(r'<title>(.*?)</title>', raw_body)
                chat_title = self._strip_html(title_match.group(1)).replace(" | ВКонтакте", "") if title_match else "ВКонтакте Обсуждение"

                # Match post blocks in VK topic
                posts_blocks = raw_body.split('class="bp_post')
                for block in posts_blocks[1:]:
                    try:
                        # Extract post ID
                        id_match = re.search(r'id="post(-\d+_\d+)"', block)
                        if not id_match:
                            continue
                        post_id_str = id_match.group(1)
                        post_num_id = abs(hash(post_id_str)) % (10**9)

                        # Extract Message Text
                        text_match = re.search(r'<div class="bp_text"[^>]*>(.*?)</div>', block, re.DOTALL)
                        msg_text = self._strip_html(text_match.group(1)) if text_match else ""

                        if not msg_text:
                            continue

                        # Extract Author
                        author_match = re.search(r'<a class="bp_author"[^>]*>(.*?)</a>', block, re.DOTALL)
                        author_name = self._strip_html(author_match.group(1)) if author_match else "Пользователь VK"

                        messages.append({
                            "message_id": post_num_id,
                            "text": msg_text,
                            "chat_title": f"VK: {chat_title}",
                            "user_id": abs(hash(author_name)) % (10**9),
                            "username": None,
                            "first_name": author_name,
                            "last_name": None,
                            "timestamp": datetime.now(timezone.utc)
                        })
                    except Exception as e:
                        logger.debug(f"Error parsing VK post block: {e}")
                        continue

            logger.info(f"Successfully scraped {len(messages)} posts from VK topic: {chat_title}")
            return messages

        except Exception as e:
            logger.error(f"Error fetching VK topic {topic_url}: {e}")
            return []
