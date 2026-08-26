import time
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

class ProcessLogItem:
    def __init__(self, log_id: int, category: str, level: str, title: str, details: str):
        self.id = log_id
        self.category = category  # 'SCRAPER', 'USERBOT', 'WATCHDOG', 'AI_SCORER', 'SYSTEM'
        self.level = level        # 'info', 'success', 'warning', 'lead', 'noise', 'error'
        self.title = title
        self.details = details
        self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict:
        vn_time = self.created_at + timedelta(hours=7)
        return {
            "id": self.id,
            "category": self.category,
            "level": self.level,
            "title": self.title,
            "details": self.details,
            "timestamp_fmt": vn_time.strftime("%H:%M:%S"),
            "timestamp_full": vn_time.strftime("%d.%m.%Y %H:%M:%S")
        }


class ProcessLogBuffer:
    def __init__(self, max_capacity: int = 500):
        self.max_capacity = max_capacity
        self._logs: List[ProcessLogItem] = []
        self._counter = 0
        self._lock = threading.Lock()
        self.last_activity_at = datetime.now(timezone.utc)

    def add_log(self, category: str, level: str, title: str, details: str = "") -> Dict:
        with self._lock:
            self._counter += 1
            item = ProcessLogItem(self._counter, category, level, title, details)
            self._logs.append(item)
            if len(self._logs) > self.max_capacity:
                self._logs.pop(0)
            self.last_activity_at = item.created_at
            return item.to_dict()

    def get_logs(self, since_id: int = 0, limit: int = 100, category_filter: Optional[str] = None) -> List[Dict]:
        with self._lock:
            filtered = self._logs
            if since_id > 0:
                filtered = [l for l in filtered if l.id > since_id]
            if category_filter and category_filter.lower() != 'all':
                filtered = [l for l in filtered if l.category.lower() == category_filter.lower()]
            
            sliced = filtered[-limit:] if limit > 0 else filtered
            return [l.to_dict() for l in sliced]

    def hydrate_from_db_logs(self, db_collector_logs: list, db_ai_eval_logs: list = None):
        with self._lock:
            has_scraper = len([l for l in self._logs if l.category == "SCRAPER"]) >= 5
            has_ai = len([l for l in self._logs if l.category == "AI_SCORER"]) >= 5
            
            if db_ai_eval_logs and not has_ai:
                for a in db_ai_eval_logs:
                    is_l = getattr(a, "is_lead", False)
                    chat = getattr(a, "chat_title", "Chat") or "Telegram Chat"
                    reason = getattr(a, "reasoning", "") or "ИИ проверил покупательский интент"
                    niche = getattr(a, "niche_code", "")
                    created = getattr(a, "created_at", None)

                    self._counter += 1
                    item = ProcessLogItem(
                        self._counter,
                        "AI_SCORER",
                        "lead" if is_l else "noise",
                        f"🔥 ГОРЯЧИЙ ЛИД ОБНАРУЖЕН в [{chat}] ({niche or 'общий'})" if is_l else f"🧠 ИИ-Анализатор: Проверка интента в [{chat}] — НЕ ЛИД",
                        reason[:150]
                    )
                    if created:
                        item.created_at = created
                    self._logs.append(item)

            if db_collector_logs and not has_scraper:
                for c in db_collector_logs:
                    new_m = getattr(c, "new_messages_count", 0)
                    status = getattr(c, "status", "OK")
                    chat = getattr(c, "chat_title", "Chat") or "Telegram Chat"
                    uname = getattr(c, "username_or_link", "")
                    created = getattr(c, "created_at", None)
                    
                    lvl = "success" if new_m > 0 else ("error" if status == "FAILED" else "info")
                    det = getattr(c, "details", None) or f"Опрос выполнен ({new_m} новых сообщений)"
                    
                    self._counter += 1
                    item = ProcessLogItem(
                        self._counter,
                        "SCRAPER",
                        lvl,
                        f"📡 Опрос чата {chat} ({uname or 'канал'}) — {new_m} новых сообщений",
                        det
                    )
                    if created:
                        item.created_at = created
                    self._logs.append(item)

            self._logs.sort(key=lambda x: x.created_at)
            if len(self._logs) > self.max_capacity:
                self._logs = self._logs[-self.max_capacity:]
            if self._logs:
                self.last_activity_at = self._logs[-1].created_at

    def get_last_activity_seconds(self) -> float:
        with self._lock:
            if not self.last_activity_at:
                return 0.0
            return (datetime.now(timezone.utc) - self.last_activity_at).total_seconds()


# Global singleton instance
process_logger = ProcessLogBuffer(max_capacity=500)
