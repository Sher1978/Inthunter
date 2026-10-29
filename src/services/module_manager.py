import os
import json
import logging
from typing import Dict, Any, Optional

from src.config import PERSISTENT_DIR

logger = logging.getLogger("intent_hunter.module_manager")

DEFAULT_GEO_MATRIX: Dict[str, Dict[str, Any]] = {
    "goa": {
        "code": "goa",
        "name": "🇮🇳 Северное Гоа",
        "reader": True,
        "ai_scorer": True,
        "outreach": True,
        "subregions": ["goa", "north_goa", "arambol", "morjim", "mandrem", "siolim", "vagator", "anjuna"]
    },
    "dubai": {
        "code": "dubai",
        "name": "🇦🇪 Дубай / ОАЭ",
        "reader": True,
        "ai_scorer": True,
        "outreach": True,
        "subregions": ["dubai", "ae", "uae", "sharjah", "abu_dhabi"]
    },
    "vietnam": {
        "code": "vietnam",
        "name": "🇻🇳 Вьетнам",
        "reader": False,
        "ai_scorer": False,
        "outreach": False,
        "subregions": ["vietnam", "nhatrang", "danang", "phuquoc", "muine", "camranh"]
    },
    "thailand": {
        "code": "thailand",
        "name": "🇹🇭 Таиланд",
        "reader": False,
        "ai_scorer": False,
        "outreach": False,
        "subregions": ["thailand", "phuket", "bangkok", "pattaya", "samui"]
    },
    "bali": {
        "code": "bali",
        "name": "🇮🇩 Бали / Индонезия",
        "reader": False,
        "ai_scorer": False,
        "outreach": False,
        "subregions": ["bali", "indonesia", "ubud", "canggu", "seminyak"]
    },
    "moscow": {
        "code": "moscow",
        "name": "🇷🇺 Москва / РФ",
        "reader": False,
        "ai_scorer": False,
        "outreach": False,
        "subregions": ["moscow", "russia", "spb"]
    },
    "global": {
        "code": "global",
        "name": "🌍 Global / Прочие",
        "reader": True,
        "ai_scorer": True,
        "outreach": True,
        "subregions": ["global", "international"]
    }
}

GEO_ALIASES: Dict[str, str] = {
    # Goa / India
    "goa": "goa", "north_goa": "goa", "india": "goa", "индия": "goa", "гоа": "goa",
    # Dubai / UAE
    "dubai": "dubai", "ae": "dubai", "uae": "dubai", "оаэ": "dubai", "дубай": "dubai",
    # Vietnam
    "vietnam": "vietnam", "вьетнам": "vietnam", "nhatrang": "vietnam", "нячанг": "vietnam",
    "danang": "vietnam", "дананг": "vietnam", "phuquoc": "vietnam", "фукуок": "vietnam",
    # Thailand
    "thailand": "thailand", "таиланд": "thailand", "тайланд": "thailand",
    "phuket": "thailand", "пхукет": "thailand", "bangkok": "thailand", "бангкок": "thailand",
    "pattaya": "thailand", "паттайя": "thailand", "samui": "thailand", "самуи": "thailand",
    # Bali / Indonesia
    "bali": "bali", "бали": "bali", "indonesia": "bali", "индонезия": "bali",
    # Moscow / Russia
    "moscow": "moscow", "москва": "moscow", "spb": "moscow", "питер": "moscow", "russia": "moscow",
    # Global
    "global": "global", "all": "global", "any": "global", "": "global"
}


class ModuleManager:
    """
    Central state manager for system execution states and GEO isolation matrix.
    Allows instant runtime toggling of global background subsystems and per-GEO execution flows:
    - Ingestion/Reader (per GEO & project)
    - AI Intent Scorer (per GEO & project)
    - Outreach Engine (per GEO & project)
    Persists preferences to disk so states remain preserved across server restarts.
    """
    def __init__(self):
        self._config_file = os.path.join(PERSISTENT_DIR, "module_states.json")
        self._modules: Dict[str, bool] = {
            "reader": True,               # Telegram message ingestor & listener
            "scout": True,                # Discovery engine & chat auditor
            "scout_global_search": False, # Global search via Groq/keywords
            "scout_regex_extract": False, # Regex extraction from messages
            "scout_common_chats": False,  # Sync common chats with userbot swarm
            "ai_scorer": True,            # AI Lead qualification & batch worker
            "outreach": True,             # B2B auto-outreach worker
            "auto_pruning": False,        # Aggressive channel auto-pruning
            "userbot_joiner": True,       # Swarm Userbot Auto-Joiner worker
            "global_killswitch": False    # MAIN KILLSWITCH: Completely stops all system processes when True
        }
        # Deep copy default geo matrix
        self._geo_matrix: Dict[str, Dict[str, Any]] = {
            k: {**v} for k, v in DEFAULT_GEO_MATRIX.items()
        }
        self._load_states()

    def _load_states(self):
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        # Support legacy flat format or new nested format
                        mod_dict = saved.get("modules", saved)
                        if isinstance(mod_dict, dict):
                            for k, v in mod_dict.items():
                                if k in self._modules:
                                    self._modules[k] = bool(v)

                        # Load GEO matrix states
                        geo_saved = saved.get("geo_matrix")
                        if isinstance(geo_saved, dict):
                            for g_key, g_data in geo_saved.items():
                                if g_key in self._geo_matrix and isinstance(g_data, dict):
                                    for m_key in ("reader", "ai_scorer", "outreach"):
                                        if m_key in g_data:
                                            self._geo_matrix[g_key][m_key] = bool(g_data[m_key])

                logger.info(f"🎛 Module Control: Restored saved states from {self._config_file}")
            except Exception as e:
                logger.warning(f"Failed loading module states from {self._config_file}: {e}")

    def _save_states(self):
        try:
            os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
            to_save = {
                "modules": self._modules,
                "geo_matrix": self._geo_matrix
            }
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2, ensure_ascii=False)
            logger.info(f"🎛 Module Control: Saved system and GEO matrix states to {self._config_file}")
        except Exception as e:
            logger.error(f"Failed saving module states to {self._config_file}: {e}")

    # =========================================================================
    # GLOBAL MODULE METHODS
    # =========================================================================
    def is_enabled(self, module_name: str) -> bool:
        """Returns True if global target module is currently enabled."""
        if self._modules.get("global_killswitch", False) and module_name.lower() != "global_killswitch":
            return False

        key = module_name.lower()
        return self._modules.get(key, False if key.startswith("scout_") else True)

    def set_status(self, module_name: str, enabled: bool) -> bool:
        """Sets global target module enabled status, logs change, and persists to disk."""
        key = module_name.lower()
        if key in self._modules:
            old_val = self._modules[key]
            self._modules[key] = bool(enabled)
            logger.info(f"🎛 Module Control: [{key.upper()}] status changed from {old_val} to {enabled}")
            self._save_states()
            return True
        return False

    def toggle(self, module_name: str) -> bool:
        """Toggles global status of target module, persists to disk, and returns new state."""
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
                },
                "userbot_joiner": {
                    "key": "userbot_joiner",
                    "name": "🤖 Авто-Вступления Юзерботов",
                    "enabled": self._modules.get("userbot_joiner", True),
                    "description": "Автоматическое распределение и вступление роя в целевые группы"
                }
            },
            "geo_matrix": self.get_geo_matrix()["geo_matrix"]
        }

    # =========================================================================
    # GEO ISOLATION MATRIX METHODS
    # =========================================================================
    def canonicalize_geo(self, raw_geo: Optional[str]) -> str:
        """Resolves subregion codes, aliases, or raw inputs to canonical GEO key."""
        if not raw_geo:
            return "global"
        clean = str(raw_geo).strip().lower().replace("-", "_").replace(" ", "_")
        return GEO_ALIASES.get(clean, "global")

    def is_geo_enabled(self, raw_geo: Optional[str], module_name: str) -> bool:
        """
        Multi-tenant Gatekeeper: checks if a specific subsystem is active for the target GEO.
        First checks global master switch, then evaluates the granular GEO switch.
        """
        # Master switch check
        if not self.is_enabled(module_name):
            return False

        canon_geo = self.canonicalize_geo(raw_geo)
        geo_entry = self._geo_matrix.get(canon_geo)
        if not geo_entry:
            geo_entry = self._geo_matrix.get("global", {})

        return bool(geo_entry.get(module_name, True))

    def set_geo_status(self, raw_geo: str, module_name: str, enabled: bool) -> bool:
        """Sets active status for a specific module in target GEO."""
        canon_geo = self.canonicalize_geo(raw_geo)
        if canon_geo in self._geo_matrix:
            if module_name in ("reader", "ai_scorer", "outreach"):
                old_val = self._geo_matrix[canon_geo].get(module_name, True)
                self._geo_matrix[canon_geo][module_name] = bool(enabled)
                logger.info(f"🌐 GEO Control: [{canon_geo.upper()}] module [{module_name}] changed from {old_val} to {enabled}")
                self._save_states()
                return True
        return False

    def toggle_geo_status(self, raw_geo: str, module_name: str) -> bool:
        """Toggles active status for a specific module in target GEO."""
        canon_geo = self.canonicalize_geo(raw_geo)
        if canon_geo in self._geo_matrix and module_name in ("reader", "ai_scorer", "outreach"):
            cur = self._geo_matrix[canon_geo].get(module_name, True)
            return self.set_geo_status(canon_geo, module_name, not cur)
        return False

    def set_geo_all(self, raw_geo: str, enabled: bool) -> bool:
        """Master toggle for target GEO: sets all 3 subsystems (reader, ai_scorer, outreach) at once."""
        canon_geo = self.canonicalize_geo(raw_geo)
        if canon_geo in self._geo_matrix:
            for m in ("reader", "ai_scorer", "outreach"):
                self._geo_matrix[canon_geo][m] = bool(enabled)
            logger.info(f"🌐 GEO Control: [{canon_geo.upper()}] ALL modules set to {enabled}")
            self._save_states()
            return True
        return False

    def get_geo_matrix(self) -> Dict[str, Any]:
        """Returns deep copy of current GEO control matrix."""
        return {
            "status": "ok",
            "geo_matrix": {k: {**v} for k, v in self._geo_matrix.items()}
        }


# Global singleton instance
module_manager = ModuleManager()
