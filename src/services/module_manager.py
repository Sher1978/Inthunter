import logging
from typing import Dict, Any

logger = logging.getLogger("intent_hunter.module_manager")

class ModuleManager:
    """
    Central state manager for system module execution states.
    Allows instant runtime toggling of background subsystems (Reader, Scout, AI Scorer, Outreach, Auto-Pruning).
    """
    def __init__(self):
        self._modules: Dict[str, bool] = {
            "reader": True,        # Telegram message ingestor & listener
            "scout": True,         # Discovery engine & chat auditor
            "ai_scorer": True,     # AI Lead qualification & batch worker
            "outreach": True,      # B2B auto-outreach worker
            "auto_pruning": False  # Aggressive channel auto-pruning (OFF by default for manual test stability)
        }

    def is_enabled(self, module_name: str) -> bool:
        """Returns True if target module is currently enabled."""
        return self._modules.get(module_name.lower(), True)

    def set_status(self, module_name: str, enabled: bool) -> bool:
        """Sets target module enabled status and logs change."""
        key = module_name.lower()
        if key in self._modules:
            old_val = self._modules[key]
            self._modules[key] = bool(enabled)
            logger.info(f"🎛 Module Control: [{key.upper()}] status changed from {old_val} to {enabled}")
            return True
        return False

    def toggle(self, module_name: str) -> bool:
        """Toggles status of target module and returns new boolean state."""
        key = module_name.lower()
        if key in self._modules:
            self._modules[key] = not self._modules[key]
            logger.info(f"🎛 Module Control: [{key.upper()}] toggled to {self._modules[key]}")
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
                    "name": "🔎 ИИ-Скаут (Discovery Engine)",
                    "enabled": self._modules["scout"],
                    "description": "Автоматический поиск и аудит новых каналов"
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
