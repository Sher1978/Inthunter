import time
import logging
import asyncio
from typing import Dict, List, Tuple, Any, Optional
from src.config import settings

logger = logging.getLogger("intent_hunter.ai.budget_guard")

class AIBudgetGuard:
    """
    AI Safety Governor & Circuit Breaker.
    Monitors input/output tokens, tracks 429 rate limit errors per hour/day,
    and trips circuit breakers to prevent runaway retry loops.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._reset_date = time.strftime("%Y-%m-%d", time.gmtime())
        
        # Daily Telemetry Counters
        self.daily_input_tokens: int = 0
        self.daily_output_tokens: int = 0
        self.daily_total_requests: int = 0
        self.daily_429_errors: int = 0
        
        # Recent 429 error timestamps (for 1-hour window rate limiting)
        self._429_timestamps: List[float] = []
        
        # Provider Circuit Breaker Map: provider_name -> expiration timestamp
        self._circuit_breakers: Dict[str, float] = {}
        
        # Global Emergency Kill-Switch (manual or automated)
        self.is_paused: bool = False
        self._last_alert_time: float = 0.0

    def _check_daily_reset(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self._reset_date != today:
            logger.info(f"🔄 AIBudgetGuard: New UTC Day ({today}). Resetting daily token and error counters.")
            self._reset_date = today
            self.daily_input_tokens = 0
            self.daily_output_tokens = 0
            self.daily_total_requests = 0
            self.daily_429_errors = 0
            self._429_timestamps.clear()

    async def can_make_request(self, provider_name: str = "all") -> Tuple[bool, str]:
        """
        Checks if AI requests can be safely executed or if Circuit Breaker is active.
        """
        async with self._lock:
            self._check_daily_reset()
            now = time.time()

            if self.is_paused:
                return False, "🛑 AI Requests manually paused by Superadmin."

            # Check provider-specific circuit breaker
            cb_until = self._circuit_breakers.get(provider_name, 0)
            if cb_until > now:
                rem_sec = int(cb_until - now)
                return False, f"⏳ Provider {provider_name} Circuit Breaker active ({rem_sec}s remaining due to 429 RateLimit)."

            # Check system-wide circuit breaker
            global_cb = self._circuit_breakers.get("GLOBAL", 0)
            if global_cb > now:
                rem_sec = int(global_cb - now)
                return False, f"⏳ Global AI Circuit Breaker active ({rem_sec}s remaining due to high 429 error frequency)."

            return True, "OK"

    async def record_usage(self, provider_name: str, input_tokens: int = 0, output_tokens: int = 0):
        """
        Records successful request token usage.
        """
        async with self._lock:
            self._check_daily_reset()
            self.daily_total_requests += 1
            self.daily_input_tokens += max(0, input_tokens)
            self.daily_output_tokens += max(0, output_tokens)

    async def record_429_error(self, provider_name: str, key_suffix: str = ""):
        """
        Records a 429 TooManyRequests error.
        If 429 count in past hour exceeds threshold, trips Circuit Breaker for 30 minutes.
        """
        now = time.time()
        async with self._lock:
            self._check_daily_reset()
            self.daily_429_errors += 1
            self._429_timestamps.append(now)
            
            # Keep only timestamps within past 1 hour (3600 seconds)
            cutoff = now - 3600.0
            self._429_timestamps = [t for t in self._429_timestamps if t >= cutoff]
            
            hourly_429_count = len(self._429_timestamps)
            logger.warning(f"⚠️ AIBudgetGuard: 429 RateLimit error on {provider_name} (...{key_suffix}). Hourly 429 count: {hourly_429_count}/{settings.AI_CIRCUIT_BREAKER_429_THRESHOLD}")

            # Trip GLOBAL circuit breaker for 30 minutes only if hourly threshold reached across all attempts
            if hourly_429_count >= settings.AI_CIRCUIT_BREAKER_429_THRESHOLD:
                logger.error(f"🚨 AIBudgetGuard: High 429 error frequency ({hourly_429_count} in 1 hour). TRIPPING GLOBAL CIRCUIT BREAKER FOR 30 MINUTES!")
                self._circuit_breakers["GLOBAL"] = now + 1800.0 # 30 minutes
                
                # Notify superadmins via Telegram Alert Bot (throttled once per 10 min)
                if now - self._last_alert_time > 600.0:
                    self._last_alert_time = now
                    asyncio.create_task(self._send_circuit_breaker_alert(provider_name, hourly_429_count))

    async def _send_circuit_breaker_alert(self, provider_name: str, count: int):
        """Sends emergency Telegram alert to Superadmins when Circuit Breaker trips."""
        try:
            from src.bot.alert_bot import notify_superadmins_system_alert
            msg = (
                f"🚨 <b>АВАРИЙНЫЙ ПРЕРЫВАТЕЛЬ ЦЕПИ ИИ (CIRCUIT BREAKER) СРАБОТАЛ!</b>\n\n"
                f"⚠️ <b>Провайдер:</b> <code>{provider_name}</code>\n"
                f"📊 <b>Ошибок 429 за час:</b> <code>{count}</code> (Порог: {settings.AI_CIRCUIT_BREAKER_429_THRESHOLD})\n"
                f"⏳ <b>Действие:</b> Запросы к ИИ заморожены на 30 минут для защиты от зависаний и бана ключей.\n"
                f"💡 Ротатор автоматически возобновит работу после спада нагрузки."
            )
            await notify_superadmins_system_alert(msg)
        except Exception as e:
            logger.debug(f"Notice sending circuit breaker alert: {e}")

    def get_telemetry_status(self) -> Dict[str, Any]:
        """
        Returns telemetry statistics for admin status reports.
        """
        now = time.time()
        cb_status = {}
        for p, exp in self._circuit_breakers.items():
            if exp > now:
                cb_status[p] = f"ACTIVE ({int(exp - now)}s remaining)"

        return {
            "date_utc": self._reset_date,
            "daily_requests": self.daily_total_requests,
            "daily_input_tokens": self.daily_input_tokens,
            "daily_output_tokens": self.daily_output_tokens,
            "daily_429_errors": self.daily_429_errors,
            "hourly_429_errors": len([t for t in self._429_timestamps if t >= now - 3600.0]),
            "is_paused": self.is_paused,
            "active_circuit_breakers": cb_status
        }

ai_budget_guard = AIBudgetGuard()
