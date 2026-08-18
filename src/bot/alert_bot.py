import logging
from typing import List
from aiogram import Bot, Dispatcher, html
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.config import settings
from src.ai.schemas import LeadScoringResult
from src.db.models import UserActivityLog
from src.bot.handlers import router

logger = logging.getLogger("intent_hunter.bot")

bot: Bot = None
dp: Dispatcher = None

def init_bot():
    global bot, dp
    raw_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    clean_token = raw_token.strip().strip('"').strip("'")

    if clean_token and clean_token != "mock_bot_token":
        try:
            bot = Bot(token=clean_token)
            dp = Dispatcher()
            dp.include_router(router)
            logger.info(f"Aiogram 3 Bot initialized for token ending in ...{clean_token[-6:]}")
        except Exception as e:
            logger.error(f"Failed to initialize Aiogram Bot: {e}")
    else:
        logger.warning(f"TELEGRAM_BOT_TOKEN empty or mock ('{raw_token}'). Bot initialization skipped.")


async def broadcast_lead_alert(
    user_id: int,
    lead_result: LeadScoringResult,
    messages: List[UserActivityLog]
):
    """
    Formats and broadcasts lead alert card to partner alert channel.
    """
    niche_labels = {
        "auto_kasko": "Автострахование (КАСКО / ОСАГО)",
        "real_estate": "Недвижимость / Аренда / Покупка",
        "auto_broker": "Автоброкер / Подбор автомобилей",
    }
    niche_title = niche_labels.get(lead_result.niche_code, lead_result.niche_code.upper())
    
    # Format message timeline history (Masking full Telegram handles until purchase)
    timeline_lines = []
    for msg in messages[-3:]: # Show last 3 relevant messages
        timestamp_fmt = msg.timestamp.strftime("%d %b %H:%M")
        chat_fmt = msg.chat_title or "Групповой чат"
        timeline_lines.append(f"• <b>{timestamp_fmt}</b> [{chat_fmt}]: <i>\"{html.quote(msg.message_text)}\"</i>")
    
    timeline_text = "\n".join(timeline_lines)
    
    alert_text = (
        f"🔥 <b>ПОСТУПИЛ НОВЫЙ ГОРЯЧИЙ ЛИД!</b>\n\n"
        f"<b>Категория:</b> {niche_title}\n"
        f"<b>Температура:</b> {lead_result.temperature} (Готовность: {int(lead_result.confidence_score * 100)}%)\n"
        f"<b>Свежесть:</b> Только что\n\n"
        f"📜 <b>История действий пользователя:</b>\n"
        f"{timeline_text}\n\n"
        f"💡 <b>Рекомендация ИИ по продажам (Sales Hook):</b>\n"
        f"«{html.quote(lead_result.sales_hook)}»\n\n"
        f"💰 <b>Стоимость контакта:</b> {800 if lead_result.temperature == 'HOT' else 500} ₽\n"
        f"───────────────────────────"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Выкупить лид и получить контакт",
                    callback_data=f"buy_lead:{user_id}"
                )
            ]
        ]
    )

    logger.info(f"\n=================== LEAD ALERT CARD ===================\n{alert_text}\n=======================================================")

    if bot and settings.ALERT_CHANNEL_ID and str(settings.ALERT_CHANNEL_ID) != "0":
        try:
            await bot.send_message(
                chat_id=settings.ALERT_CHANNEL_ID,
                text=alert_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            logger.info(f"Broadcasted lead alert to channel {settings.ALERT_CHANNEL_ID}")
        except Exception as e:
            logger.error(f"Error broadcasting lead alert via Telegram Bot: {e}")
