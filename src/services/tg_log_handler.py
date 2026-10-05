import logging
import asyncio
import time

_last_ai_failure_alert: float = 0.0

class TelegramErrorHandler(logging.Handler):
    def emit(self, record):
        global _last_ai_failure_alert
        if record.levelno >= logging.ERROR:
            # Prevent infinite loops if the error comes from the alert system itself
            if record.name in ("intent_hunter.alert_bot", "intent_hunter.bot") or "notify_superadmins" in record.funcName or "_send_to" in record.funcName:
                return
            
            # Skip some spammy internal Uvicorn or asyncio errors if needed
            if "uvicorn.error" in record.name and "Accept failed on a socket" in record.getMessage():
                return

            # Skip SQLAlchemy connection pool CancelledError on shutdown.
            # This fires when Railway sends SIGTERM and asyncio cancels tasks while the
            # pool is mid-cleanup. It is cosmetic — no data is lost.
            if "sqlalchemy.pool" in record.name and "_close_connection" in record.funcName:
                return
            if "asyncio.exceptions.CancelledError" in record.getMessage() and "sqlalchemy" in record.getMessage():
                return

            msg_text = record.getMessage()

            # Throttle repetitive AI rate-limit / provider exhaustion alerts to max once per 15 minutes
            if any(term in msg_text for term in ["ALL BATCH SCORING TIERS FAILED", "AIRotatorEngine: All configured AI providers", "All configured AI providers", "Dead/Unauthorized", "Rate Limit"]):
                now = time.time()
                if now - _last_ai_failure_alert < 900.0: # 15 minutes
                    return
                _last_ai_failure_alert = now

            # Skip transient Telegram polling conflict errors during container rolling deployments
            if "TelegramConflictError" in msg_text or "terminated by other getUpdates request" in msg_text or "Conflict: terminated by other" in msg_text:
                return

            # Skip handled userbot search ban and evacuation notices (dedicated alerts already sent)
            if "SEARCH BANNED" in msg_text or "EMERGENCY: Userbot" in msg_text:
                return
                
            # Skip harmless Pyrogram background task errors on shutdown/disconnect
            if "Cannot operate on a closed database" in msg_text and "pyrogram" in msg_text:
                return
                
            try:
                log_entry = self.format(record)
                
                import html
                escaped_name = html.escape(record.name)
                escaped_func = html.escape(record.funcName)
                escaped_log = html.escape(log_entry[:3000])
                
                msg = (
                    f"🚨 <b>СИСТЕМНАЯ ОШИБКА (ЛОГГЕР)</b>\n"
                    f"<b>Модуль:</b> <code>{escaped_name}</code>\n"
                    f"<b>Функция:</b> <code>{escaped_func}</code>\n\n"
                    f"<code>{escaped_log}</code>"
                )
                
                try:
                    loop = asyncio.get_running_loop()
                    from src.bot.alert_bot import notify_superadmins_system_alert
                    loop.create_task(notify_superadmins_system_alert(msg))
                except RuntimeError:
                    # Not inside an active event loop
                    pass
            except Exception:
                pass
