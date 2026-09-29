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
            messages_processed = 0
            found_messages = []
            
            # Fetch messages quickly to avoid Pyrogram MTProto cursor timeouts
            try:
                import asyncio
                # Give the search a maximum of 30 seconds to fetch results
                async def fetch_results():
                    async for message in available_node.app.search_global(query, limit=limit):
                        if message and message.text:
                            found_messages.append(message)
                await asyncio.wait_for(fetch_results(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ GLDE Search for '{query}' partially timed out, but fetched {len(found_messages)} messages.")
            except Exception as search_e:
                err_str = str(search_e).lower()
                if "timeout" in err_str:
                    logger.warning(f"⚠️ GLDE Search for '{query}' timed out via Pyrogram, fetched {len(found_messages)} messages.")
                else:
                    raise search_e

            # Process the fetched messages
            for message in found_messages:
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
                # Use create_task to not block this loop for too long if needed, but awaiting is fine here since cursor is closed
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
            err_str = str(e).lower()
            if "timeout" in err_str:
                logger.warning(f"⚠️ GLDE Search Warning on node {available_node.db_id} for '{query}': {e}")
            else:
                logger.error(f"❌ GLDE Search Error on node {available_node.db_id} for '{query}': {e}")
            if "flood" in err_str:
                available_node.status = "FLOOD_WAIT"
                available_node.flood_until = datetime.now(timezone.utc) + timedelta(minutes=15)
            return 0
