import logging
import asyncio

class TelegramErrorHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            # Prevent infinite loops if the error comes from the alert system itself
            if record.name == "intent_hunter.alert_bot" or "notify_superadmins" in record.funcName:
                return
            
            # Skip some spammy internal Uvicorn or asyncio errors if needed
            if "uvicorn.error" in record.name and "Accept failed on a socket" in record.getMessage():
                return

            msg_text = record.getMessage()

            # Skip transient Telegram polling conflict errors during container rolling deployments
            if "TelegramConflictError" in msg_text or "terminated by other getUpdates request" in msg_text or "Conflict: terminated by other" in msg_text:
                return

            # Skip handled userbot search ban and evacuation notices (dedicated alerts already sent)
            if "SEARCH BANNED" in msg_text or "EMERGENCY: Userbot" in msg_text:
                return
                
            try:
                log_entry = self.format(record)
                
                msg = (
                    f"🚨 <b>СИСТЕМНАЯ ОШИБКА (ЛОГГЕР)</b>\n"
                    f"<b>Модуль:</b> <code>{record.name}</code>\n"
                    f"<b>Функция:</b> <code>{record.funcName}</code>\n\n"
                    f"<code>{log_entry[:3000]}</code>"
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
