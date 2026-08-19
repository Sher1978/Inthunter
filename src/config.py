import os
from typing import List, Union
from pydantic import Field
from pydantic_settings import BaseSettings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "intent_hunter.db").replace("\\", "/")

class Settings(BaseSettings):
    PROJECT_NAME: str = "Intent Hunter CDP"
    VERSION: str = "1.0.0-lean"
    
    # Database
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DB_PATH}"
    
    # AI Provider ('groq', 'gemini', or 'auto')
    AI_PROVIDER: str = "auto"
    
    # Gemini AI
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    
    # Groq AI (Free tier at https://console.groq.com)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "qwen/qwen3.6-27b"


    
    # Telegram API Credentials
    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    USERBOT_SESSION_STRING: str = ""
    
    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    ALERT_CHANNEL_ID: Union[int, str] = 0
    
    # Target Listening Chats (Comma separated)
    TARGET_CHATS_RAW: str = Field(default="", alias="TARGET_CHATS")
    
    # System settings
    LOG_LEVEL: str = "INFO"
    MIN_MESSAGES_FOR_SCORING: int = 1
    
    @property
    def target_chats(self) -> List[str]:
        if not self.TARGET_CHATS_RAW:
            return []
        return [chat.strip() for chat in self.TARGET_CHATS_RAW.split(",") if chat.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
