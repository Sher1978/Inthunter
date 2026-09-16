import os
import json
import logging
from typing import Dict, Any

from src.config import PERSISTENT_DIR

logger = logging.getLogger("intent_hunter.module_manager")

class ModuleManager:
    """
    Central state manager for system module execution states.
    Allows instant runtime toggling of background subsystems (Reader, Scout, AI Scorer, Outreach, Auto-Pruning).
    Persists module states to disk so preferences remain preserved across system restarts.
    """
    def __init__(self):
        self._config_file = os.path.join(PERSISTENT_DIR, "module_states.json")
        self._modules: Dict[str, bool] = {
            "reader": True,               # Telegram message ingestor & listener
            "scout": True,                # Discovery engine & chat auditor
            "scout_global_search": False, # Global search via Groq/keywords
            "scout_regex_extract": False, # Regex extraction from messages (default OFF to prevent unwanted candidate flooding)
            "scout_common_chats": False,  # Sync common chats with userbot swarm
            "ai_scorer": True,            # AI Lead qualification & batch worker
            "outreach": True,             # B2B auto-outreach worker
            "auto_pruning": False         # Aggressive channel auto-pruning
        }
        self._load_states()

    def _load_states(self):
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        for k, v in saved.items():
                            if k in self._modules:
                                self._modules[k] = bool(v)
                logger.info(f"🎛 Module Control: Restored saved module states from {self._config_file}: {self._modules}")
            except Exception as e:
                logger.warning(f"Failed loading module states from {self._config_file}: {e}")

    def _save_states(self):
        try:
            os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._modules, f, indent=2, ensure_ascii=False)
            logger.info(f"🎛 Module Control: Saved module states to {self._config_file}")
        except Exception as e:
            logger.error(f"Failed saving module states to {self._config_file}: {e}")

    def is_enabled(self, module_name: str) -> bool:
        """Returns True if target module is currently enabled."""
        key = module_name.lower()
        return self._modules.get(key, False if key.startswith("scout_") else True)

    def set_status(self, module_name: str, enabled: bool) -> bool:
        """Sets target module enabled status, logs change, and persists to disk."""
        key = module_name.lower()
        if key in self._modules:
            old_val = self._modules[key]
            self._modules[key] = bool(enabled)
            logger.info(f"🎛 Module Control: [{key.upper()}] status changed from {old_val} to {enabled}")
            self._save_states()
            return True
        return False

    def toggle(self, module_name: str) -> bool:
        """Toggles status of target module, persists to disk, and returns new boolean state."""
        key = module_name.lower()
        if key in self._modules:
            self._modules[key] = not self._modules[key]
            logger.info(f"🎛 Module Control: [{key.upper()}] toggled to {self._modules[key]}")
            self._save_states()
            return self._modules[key]
        return False

    def get_all_states(self) -> Dict[str, Any]:
        """Returns full state dictionary for API & Web UI status board."""
        return {
            "status": "ok",
            "modules": {
                "reader": {
                    "key": "reader",
                    "name": "💬 Читатель (Ingestor)",
                    "enabled": self._modules["reader"],
                    "description": "Захват и сохранение входящих сообщений из Telegram"
                },
                "scout": {
                    "key": "scout",
                    "name": "🔎 ИИ-Скаут (Мастер-выключатель)",
                    "enabled": self._modules["scout"],
                    "description": "Общий рубильник для всех функций скаутинга"
                },
                "scout_global_search": {
                    "key": "scout_global_search",
                    "name": "🔎 Искатель (Global Search)",
                    "enabled": self._modules["scout_global_search"],
                    "description": "Автоматический поиск новых чатов по ключевым словам"
                },
                "scout_regex_extract": {
                    "key": "scout_regex_extract",
                    "name": "🔎 Регулярки (Regex Extract)",
                    "enabled": self._modules["scout_regex_extract"],
                    "description": "Перехват пригласительных ссылок из сообщений"
                },
                "scout_common_chats": {
                    "key": "scout_common_chats",
                    "name": "🔎 Общие чаты (Common Chats)",
                    "enabled": self._modules["scout_common_chats"],
                    "description": "Поиск общих групп через рой юзерботов"
                },
                "ai_scorer": {
                    "key": "ai_scorer",
                    "name": "🧠 ИИ-Анализатор (Lead Scorer)",
                    "enabled": self._modules["ai_scorer"],
                    "description": "Квалификация интентов сообщений через Gemini/Groq LLM"
                },
                "outreach": {
                    "key": "outreach",
                    "name": "🚀 B2B Аутрич (Outreach Worker)",
                    "enabled": self._modules["outreach"],
                    "description": "Автоматическое приглашение поставщиков услуг"
                },
                "auto_pruning": {
                    "key": "auto_pruning",
                    "name": "🧹 Авто-очистка каналов",
                    "enabled": self._modules["auto_pruning"],
                    "description": "Автоматическое удаление молчащих чатов (Пауза для тестов)"
                }
            }
        }

# Global singleton instance
module_manager = ModuleManager()

