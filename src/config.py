import os
from typing import List, Union
from pydantic import Field
from pydantic_settings import BaseSettings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSISTENT_DIR = os.getenv("PERSISTENT_DATA_DIR", BASE_DIR)
DB_PATH = os.path.join(PERSISTENT_DIR, "intent_hunter.db").replace("\\", "/")

class Settings(BaseSettings):
    PROJECT_NAME: str = "Intent Hunter CDP"
    VERSION: str = "1.0.0-lean"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DB_PATH}")
    
    # AI Provider ('auto', 'sambanova', 'cerebras', 'groq', 'gemini', 'openrouter')
    AI_PROVIDER: str = "auto"
    AI_CASCADE_PROVIDERS: str = "sambanova,cerebras,groq,gemini,openrouter"

    # SambaNova Systems AI (Free tier at https://cloud.sambanova.ai)
    SAMBANOVA_API_KEY: str = Field(default_factory=lambda: os.getenv("SAMBANOVA_API_KEY", ""), alias="SAMBANOVA_API_KEY")
    SAMBANOVA_API_KEYS: str = Field(default_factory=lambda: os.getenv("SAMBANOVA_API_KEYS", ""), alias="SAMBANOVA_API_KEYS")
    SAMBANOVA_MODEL: str = Field(default_factory=lambda: os.getenv("SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct"), alias="SAMBANOVA_MODEL")

    # Cerebras Cloud AI (Free tier at https://cerebras.ai)
    CEREBRAS_API_KEY: str = Field(default_factory=lambda: os.getenv("CEREBRAS_API_KEY", ""), alias="CEREBRAS_API_KEY")
    CEREBRAS_API_KEYS: str = Field(default_factory=lambda: os.getenv("CEREBRAS_API_KEYS", ""), alias="CEREBRAS_API_KEYS")
    CEREBRAS_MODEL: str = Field(default_factory=lambda: os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"), alias="CEREBRAS_MODEL")
    
    # Gemini AI (Google AI Studio)
    GEMINI_API_KEY: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""), alias="GEMINI_API_KEY")
    GEMINI_API_KEYS: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEYS", ""), alias="GEMINI_API_KEYS")
    GEMINI_MODEL: str = Field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.0-flash"), alias="GEMINI_MODEL")
    
    # Groq AI (Free tier at https://console.groq.com)
    GROQ_API_KEY: str = Field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""), alias="GROQ_API_KEY")
    GROQ_API_KEYS: str = Field(default_factory=lambda: os.getenv("GROQ_API_KEYS", ""), alias="GROQ_API_KEYS")
    GROQ_MODEL: str = Field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"), alias="GROQ_MODEL")

    # OpenRouter AI (https://openrouter.ai)
    OPENROUTER_API_KEY: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""), alias="OPENROUTER_API_KEY")
    OPENROUTER_API_KEYS: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEYS", ""), alias="OPENROUTER_API_KEYS")
    OPENROUTER_MODEL: str = Field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-7b-instruct:free"), alias="OPENROUTER_MODEL")

    # xAI Grok API
    XAI_API_KEY: str = Field(default_factory=lambda: os.getenv("XAI_API_KEY", ""), alias="XAI_API_KEY")
    XAI_GROK_MODEL: str = Field(default_factory=lambda: os.getenv("XAI_GROK_MODEL", "grok-2-latest"), alias="XAI_GROK_MODEL")

    # Telegram API Credentials
    TELEGRAM_API_ID: int = Field(default=33842717, alias="TELEGRAM_API_ID")
    TELEGRAM_API_HASH: str = Field(default="370212aabacfec01a554788aeda7cf0e", alias="TELEGRAM_API_HASH")
    USERBOT_SESSION_STRING: str = Field(
        default="BAIEZh0AKnTSAGUpr_v8B09pTM_djnUCCtUl0Epv8MwtAJmZe_xQqQhhX8xuCb6yqLvC9_AUQJ_-uZR2bGV0LCdIcCBhQB5OwwtNCJDc5hML5kIp9clf4FEvlF2OVP13XvkFAFMngf5O9trHo0RiDyA8X9yGnsFuvnGDOF5Cr_xz9W18CiUyUFQcq5XLL-pERxEdSAb0mJyHpuCnl-ZJoo4cdzMd_KmVsXdYiXml4ZdI3qd760hJ3XRKOeSLoZRuy_nQLFpAagMkQT8fI0KXFGZzkHpVMiE5JhUJInFMLVFaGxl4Efg-WKjH2vAopSytQadpZVqNaQ006w2CktQKJVKwp8FxawAAAAFNMW3RAA",
        alias="USERBOT_SESSION_STRING"
    )
    
    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = Field(default="8866001783:AAECIV1s5bEm4TqKnySLHA4f-vRz10vR90s", alias="TELEGRAM_BOT_TOKEN")
    ALERT_CHANNEL_ID: Union[int, str] = 0
    SUPERADMIN_USERNAME: str = "sherlockdxb"
    ADMIN_PASSCODE: str = Field(default="260669", alias="ADMIN_PASSCODE")
    
    # Target Listening Chats (Comma separated)
    TARGET_CHATS_RAW: str = Field(default="", alias="TARGET_CHATS")
    
    # System settings & DB Guard Size Limits
    LOG_LEVEL: str = "INFO"
    MIN_MESSAGES_FOR_SCORING: int = 1
    SECRET_KEY: str = Field(default="radar-jwt-secret-change-me-in-prod", alias="SECRET_KEY")
    MAX_DB_SIZE_MB: float = Field(default=350.0, alias="MAX_DB_SIZE_MB")
    MAX_ACTIVITY_LOG_ROWS: int = Field(default=15000, alias="MAX_ACTIVITY_LOG_ROWS")
    MAX_AI_LOG_ROWS: int = Field(default=10000, alias="MAX_AI_LOG_ROWS")
    RETENTION_DAYS: int = Field(default=3, alias="RETENTION_DAYS")
    LEAD_TTL_HOURS: int = Field(default=3, alias="LEAD_TTL_HOURS")
    
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
