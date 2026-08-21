import re
import html
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("intent_hunter.public_scraper")

class PublicTelegramScraper:
    """
    Zero-auth scraper that fetches public Telegram channel feeds via web preview:
    https://t.me/s/<channel_username>
    No Telegram API keys, userbots, or accounts required.
    """

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }

    def _clean_username(self, raw_target: str) -> str:
        clean = raw_target.strip().replace("https://t.me/s/", "").replace("https://t.me/", "").replace("http://t.me/", "")
        clean = clean.replace("@", "").split("/")[0].strip()
        return clean

    def _strip_html(self, raw_html: str) -> str:
        if not raw_html:
            return ""
        text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        return html.unescape(text).strip()

    async def fetch_latest_messages(self, channel_target: str, client: Optional[httpx.AsyncClient] = None) -> List[Dict]:
        clean_user = self._clean_username(channel_target)
        if not clean_user:
            return []

        url = f"https://t.me/s/{clean_user}"
        messages = []

        import random
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
        ]
        headers = {
            "User-Agent": random.choice(user_agents),
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        try:
            if client is not None:
                res = await client.get(url, headers=headers, timeout=10.0)
            else:
                async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10.0) as local_client:
                    res = await local_client.get(url)

            if res.status_code != 200:
                logger.warning(f"⚠️ Telegram Web Scraper HTTP {res.status_code} for @{clean_user}")
                return []

            raw_body = res.text
            soup = BeautifulSoup(raw_body, "html.parser")

            # Extract channel title from header or meta og:title if available
            chat_title = f"@{clean_user}"
            header_el = soup.select_one(".tgme_header_title span, .tgme_page_title span")
            if header_el:
                extracted = header_el.get_text().strip()
                if extracted and extracted != f"@{clean_user}":
                    chat_title = extracted
            else:
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    extracted = og_title["content"].replace("Telegram: Contact", "").replace("Telegram: View", "").strip()
                    if extracted and extracted != f"@{clean_user}":
                        chat_title = extracted

            # Parse message nodes
            post_nodes = soup.select(".tgme_widget_message")
            for post in post_nodes:
                try:
                    data_post = post.get("data-post", "")
                    if not data_post or "/" not in data_post:
                        continue
                    try:
                        msg_id = int(data_post.split("/")[-1])
                    except ValueError:
                        continue

                    # Extract Message Text using BeautifulSoup get_text
                    text_el = post.select_one(".tgme_widget_message_text")
                    if not text_el:
                        continue
                    msg_text = text_el.get_text(separator="\n").strip()
                    if not msg_text:
                        continue

                    # Extract Timestamp
                    time_el = post.select_one("time")
                    ts = datetime.now(timezone.utc)
                    if time_el and time_el.get("datetime"):
                        try:
                            ts = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
                        except Exception:
                            pass

                    # Extract Author Name
                    author_el = post.select_one(".tgme_widget_message_owner_name")
                    author_name = author_el.get_text().strip() if author_el else f"Пользователь {msg_id}"

                    # Deterministic hash for user_id (never uses Python's non-deterministic hash())
                    import zlib
                    user_key = f"{clean_user}_{author_name}" if author_el else f"{clean_user}_post_{msg_id}"
                    det_user_id = (zlib.crc32(user_key.encode("utf-8")) & 0x7FFFFFFF)

                    messages.append({
                        "message_id": msg_id,
                        "text": msg_text,
                        "chat_title": chat_title,
                        "user_id": det_user_id,
                        "username": None,
                        "first_name": author_name,
                        "last_name": None,
                        "timestamp": ts
                    })
                except Exception as e:
                    logger.debug(f"Error parsing post node in @{clean_user}: {e}")
                    continue

            logger.info(f"Successfully scraped {len(messages)} public messages from @{clean_user}")
            return messages

        except Exception as e:
            logger.error(f"Error fetching public preview for @{clean_user}: {e}")
            return []

