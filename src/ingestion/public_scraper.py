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
                res = await client.get(url, headers=headers, timeout=10.0, follow_redirects=False)
            else:
                async with httpx.AsyncClient(headers=headers, follow_redirects=False, timeout=10.0) as local_client:
                    res = await local_client.get(url)

            if res.status_code in (301, 302, 307, 308):
                logger.info(f"ℹ️ Telegram @{clean_user} является ГРУППОВЫМ ЧАТОМ (HTTP {res.status_code} Redirect: веб-превью поддерживается только для КАНАЛОВ, нужен Юзербот).")
                try:
                    import src.api.app as app_module
                    if hasattr(app_module, "ingestor") and app_module.ingestor:
                        app_module.ingestor.group_chat_302_count += 1
                except Exception:
                    pass

                now_ts = time.time()
                if not hasattr(self, "_302_logged_timestamps"):
                    self._302_logged_timestamps = {}
                
                # Only push to live terminal log once per 30 minutes per group chat
                if now_ts - self._302_logged_timestamps.get(clean_user, 0) > 1800:
                    self._302_logged_timestamps[clean_user] = now_ts
                    try:
                        from src.services.process_logger import process_logger
                        process_logger.add_log(
                            category="SCRAPER",
                            level="warning",
                            title=f"💬 @{clean_user} — ГРУППОВОЙ ЧАТ (HTTP {res.status_code} Redirect)",
                            details="Веб-сканер отдает 302 для групп. Для чтения сообщений из чатов необходим подключенный Юзербот MTProto."
                        )
                    except Exception:
                        pass
                return []

            if res.status_code == 404:
                logger.warning(f"❌ Telegram channel @{clean_user} DOES NOT EXIST (HTTP 404 Not Found)")
                asyncio.create_task(purge_dead_channel(clean_user, reason="Telegram HTTP 404 Not Found"))
                return None

            if res.status_code != 200:
                logger.warning(f"⚠️ Telegram Web Scraper HTTP {res.status_code} for @{clean_user}")
                return None

            raw_body = res.text
            if "tgme_page_error_title" in raw_body and ("If you have Telegram" in raw_body or "If you have" in raw_body or "not found" in raw_body.lower()):
                logger.warning(f"❌ Telegram channel @{clean_user} DOES NOT EXIST (Username not found)")
                asyncio.create_task(purge_dead_channel(clean_user, reason="Telegram Username Not Found"))
                return None

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

                    # Extract Author Name & Contact Handle
                    author_el = post.select_one(".tgme_widget_message_owner_name")
                    raw_author_name = author_el.get_text().strip() if author_el else ""

                    # Extract username / phone / email contact inside text
                    contact_match = re.search(r'(@[a-zA-Z0-9_]{5,32})|(?:\+?\d{9,14})|([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', msg_text)
                    extracted_username = None
                    
                    if contact_match:
                        matched_str = contact_match.group(0).strip()
                        if matched_str.startswith("@"):
                            extracted_username = matched_str.replace("@", "")
                            author_name = matched_str
                            user_key = f"contact_{matched_str.lower()}"
                        else:
                            author_name = f"Контакт {matched_str}"
                            user_key = f"contact_{matched_str.lower()}"
                    elif raw_author_name and raw_author_name.lower() != chat_title.lower() and raw_author_name.lower() != f"@{clean_user}".lower():
                        author_name = raw_author_name
                        user_key = f"{clean_user}_{raw_author_name}"
                    else:
                        author_name = f"Автор сообщения #{msg_id}"
                        user_key = f"{clean_user}_post_author_{msg_id}"

                    # Deterministic hash for user_id (never uses Python's non-deterministic hash())
                    import zlib
                    det_user_id = (zlib.crc32(user_key.encode("utf-8")) & 0x7FFFFFFF)

                    messages.append({
                        "message_id": msg_id,
                        "text": msg_text,
                        "chat_title": chat_title,
                        "user_id": det_user_id,
                        "username": extracted_username,
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


async def purge_dead_channel(username_or_link: str, reason: str = "Канал не существует в Telegram (404 Not Found)"):
    """Auto-deletes a dead/non-existent channel from MonitoredChannel and adds it to BlacklistedChat."""
    try:
        from src.db.session import AsyncSessionLocal
        from src.db.models import MonitoredChannel, BlacklistedChat
        from sqlalchemy import select, delete

        clean = username_or_link.strip()
        if not clean.startswith("@") and not "t.me/" in clean:
            clean = f"@{clean}"

        async with AsyncSessionLocal() as session:
            # 1. Delete from MonitoredChannel
            stmt_del = delete(MonitoredChannel).where(
                (MonitoredChannel.username_or_link == clean) |
                (MonitoredChannel.username_or_link == clean.replace("@", ""))
            )
            await session.execute(stmt_del)

            # 2. Add to BlacklistedChat to prevent re-adding
            ex_blk = (await session.execute(select(BlacklistedChat).where(BlacklistedChat.chat_username == clean))).scalar_one_or_none()
            if not ex_blk:
                session.add(BlacklistedChat(
                    chat_username=clean,
                    reason=f"Авто-очистка: {reason}"
                ))
            await session.commit()

        try:
            from src.services.process_logger import process_logger
            process_logger.add_log(
                category="SCRAPER",
                level="warning",
                title=f"🧹 АВТО-ОЧИСТКА: Удален недействительный канал {clean}",
                details=reason
            )
        except Exception:
            pass

        logger.info(f"🧹 Auto-Purged dead channel {clean}: {reason}")
    except Exception as e:
        logger.warning(f"Notice purging dead channel {username_or_link}: {e}")

