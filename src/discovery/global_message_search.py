import logging
import asyncio
from typing import Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("intent_hunter.glde")

class GlobalMessageSearcher:
    """
    Global Lead Discovery Engine (GLDE).
    Executes proactive search_global queries via Pyrogram Userbots to find messages globally.
    """

    @staticmethod
    async def execute_global_search(query: str, limit: int = 40) -> int:
        """
        Executes a global message search using the first available active Userbot node.
        Returns the number of messages successfully ingested.
        """
        import sys
        app_module = sys.modules.get("src.api.app")
        ingestor = getattr(app_module, "ingestor", None) if app_module else None
        
        if not ingestor or getattr(ingestor, "_is_running", True) == False:
            logger.warning("GLDE: Cannot execute global search - TelegramIngestor is not running.")
            return 0
            
        available_node = None
        is_night = ingestor._is_night_mode()
        
        for node in ingestor.scrapers:
            can_join, _ = node.can_perform_mtproto_join(is_night, ingestor.swarm_circuit_breaker_until)
            if can_join and getattr(node, "app", None) and getattr(node.app, "is_connected", False):
                available_node = node
                break
                
        if not available_node:
            logger.info("🛡️ GLDE Anti-Ban: No free nodes available for global search.")
            return 0
            
        logger.info(f"🔎 GLDE: Executing search_global for '{query}' using node #{available_node.db_id}...")
        
        try:
            # Consume 1 from daily_join_count to rate limit global searches the same way we limit group joins
            available_node.daily_join_count += 1
            available_node.last_join_at = datetime.now(timezone.utc)
            
            # Persist DB join count update to keep node limits synced
            if available_node.db_id > 0:
                try:
                    from src.db.models import ScraperAccount
                    from src.db.session import AsyncSessionLocal
                    from sqlalchemy import update
                    async with AsyncSessionLocal() as session:
                        await session.execute(
                            update(ScraperAccount)
                            .where(ScraperAccount.id == available_node.db_id)
                            .values(daily_join_count=available_node.daily_join_count, last_join_at=available_node.last_join_at)
                        )
                        await session.commit()
                except Exception as db_err:
                    logger.warning(f"Notice updating ScraperAccount DB count in GLDE: {db_err}")

            messages_processed = 0
            # pyrogram.client.Client.search_global yields messages
            async for message in available_node.app.search_global(query, limit=limit):
                if not message or not message.text:
                    continue
                
                # We only want messages from users or anonymous group admins
                user = message.from_user
                if not user:
                    user = getattr(message, "sender_chat", None)
                if not user:
                    continue
                    
                user_id = getattr(user, "id", 0)
                username = getattr(user, "username", None)
                first_name = getattr(user, "first_name", None) or getattr(user, "title", None)
                last_name = getattr(user, "last_name", None)
                
                chat = message.chat
                if not chat:
                    continue
                chat_id = getattr(chat, "id", 0)
                chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "Global Telegram Chat"
                
                # Forward to ingestor for processing, AI scoring, and DB storage
                await ingestor.process_incoming_message(
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    message_id=message.id,
                    text=message.text
                )
                messages_processed += 1
                
            logger.info(f"✅ GLDE: Search for '{query}' complete. Found & ingested {messages_processed} messages.")
            return messages_processed
            
        except Exception as e:
            logger.error(f"❌ GLDE Search Error on node {available_node.db_id} for '{query}': {e}")
            err_str = str(e).lower()
            if "flood" in err_str:
                available_node.status = "FLOOD_WAIT"
                available_node.flood_until = datetime.now(timezone.utc) + timedelta(minutes=15)
            return 0
