"""
Telegram Link Generator Helper
Generates direct permalinks for Telegram messages in public chats or private supergroups/channels.
"""
from typing import Optional

def generate_message_permalink(chat_id: int, message_id: int, chat_username: Optional[str] = None) -> str:
    """
    Generates a direct clickable link to a Telegram message.
    
    Examples:
    - Public chat with username: https://t.me/auto_chat/12345
    - Private supergroup/channel (id starts with -100): https://t.me/c/1234567890/12345
    """
    if chat_username and chat_username.strip():
        clean_user = chat_username.strip().lstrip("@")
        return f"https://t.me/{clean_user}/{message_id}"
    
    cid_str = str(chat_id)
    if cid_str.startswith("-100"):
        clean_id = cid_str[4:]
    elif cid_str.startswith("-"):
        clean_id = cid_str[1:]
    else:
        clean_id = cid_str
        
    return f"https://t.me/c/{clean_id}/{message_id}"
