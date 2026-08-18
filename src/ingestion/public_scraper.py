import re
import html
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
import httpx

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
        # Replace <br> and <br/> with newlines
        text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r'<[^>]+>', '', text)
        # Unescape HTML entities (&quot;, &amp;, etc.)
        return html.unescape(text).strip()

    async def fetch_latest_messages(self, channel_target: str) -> List[Dict]:
        clean_user = self._clean_username(channel_target)
        if not clean_user:
            return []

        url = f"https://t.me/s/{clean_user}"
        messages = []

        try:
            async with httpx.AsyncClient(headers=self.headers, follow_redirects=True, timeout=12.0) as client:
                res = await client.get(url)
                if res.status_code != 200:
                    logger.warning(f"Failed to fetch public channel preview for @{clean_user} (HTTP {res.status_code})")
                    return []

                raw_body = res.text

                # Extract channel title from header if available
                title_match = re.search(r'<div class="tgme_header_title"[^>]*>\s*<span[^>]*>(.*?)</span>', raw_body, re.DOTALL)
                chat_title = self._strip_html(title_match.group(1)) if title_match else f"@{clean_user}"

                # Split body by message widget wrappers
                raw_posts = raw_body.split('<div class="tgme_widget_message_wrap')
                for block in raw_posts[1:]:
                    try:
                        # Extract data-post ("durov/123")
                        post_match = re.search(r'data-post="([^"]+)"', block)
                        if not post_match:
                            continue
                        data_post = post_match.group(1)
                        msg_id = int(data_post.split("/")[-1]) if "/" in data_post else 0

                        # Extract Message Text
                        text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
                        msg_text = self._strip_html(text_match.group(1)) if text_match else ""

                        if not msg_text or not msg_id:
                            continue

                        # Extract Timestamp
                        time_match = re.search(r'<time[^>]*datetime="([^"]+)"', block)
                        ts = datetime.now(timezone.utc)
                        if time_match:
                            try:
                                ts = datetime.fromisoformat(time_match.group(1).replace("Z", "+00:00"))
                            except Exception:
                                pass

                        # Extract Author Name
                        author_match = re.search(r'<div class="tgme_widget_message_owner_name"[^>]*>(.*?)</div>', block, re.DOTALL)
                        author_name = self._strip_html(author_match.group(1)) if author_match else "Пользователь Telegram"

                        messages.append({
                            "message_id": msg_id,
                            "text": msg_text,
                            "chat_title": chat_title,
                            "user_id": abs(hash(author_name)) % (10**9),
                            "username": None,
                            "first_name": author_name,
                            "last_name": None,
                            "timestamp": ts
                        })
                    except Exception as e:
                        logger.debug(f"Error parsing post block in @{clean_user}: {e}")
                        continue

            logger.info(f"Successfully scraped {len(messages)} public messages from @{clean_user}")
            return messages

        except Exception as e:
            logger.error(f"Error fetching public preview for @{clean_user}: {e}")
            return []
