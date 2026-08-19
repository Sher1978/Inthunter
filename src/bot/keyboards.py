from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

NICHE_NAMES = {
    "real_estate": "🏠 Недвижимость (Покупка/Аренда)",
    "bike_rent": "🛵 Аренда байков & Трансфер",
    "currency_exchange": "💱 Обмен валюты (RUB/USDT)",
    "services_visa": "🛂 Визаран & Услуги",
    "auto_kasko": "🚗 Страхование (КАСКО/ОСАГО)",
    "community": "💬 Сообщество / Общий чат"
}

def get_main_reply_keyboard(is_monitoring_active: bool = True) -> ReplyKeyboardMarkup:
    monitoring_label = "🔕 Выключить мониторинг" if is_monitoring_active else "🔔 Включить мониторинг"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=monitoring_label)],
            [KeyboardButton(text="📡 Каналы прослушки"), KeyboardButton(text="📊 Статистика (Admin)")],
            [KeyboardButton(text="👤 Мой Профиль"), KeyboardButton(text="💳 Баланс"), KeyboardButton(text="🎯 Мои Ниши")]
        ],
        resize_keyboard=True
    )

def get_channels_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить чат / канал", callback_data="add_channel")],
            [InlineKeyboardButton(text="🔄 Обновить список", callback_data="refresh_channels")]
        ]
    )

def get_niche_inline_keyboard(user_niches: list) -> InlineKeyboardMarkup:
    buttons = []
    for code, name in NICHE_NAMES.items():
        is_subbed = code in user_niches
        status_icon = "✅" if is_subbed else "❌"
        text = f"{status_icon} {name}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"toggle_niche:{code}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_buy_lead_keyboard(lead_id: str, price_usd: float = 1.00) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Выкупить контакт за ${price_usd:.2f} USD",
                    callback_data=f"buy_lead:{lead_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Анализ активности лида (ИИ)",
                    callback_data=f"analyze_lead:{lead_id}"
                )
            ]
        ]
    )

def get_topup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌟 $100 USD (5,000 Stars)", callback_data="stars_invoice:100:5000"),
                InlineKeyboardButton(text="🌟 $250 USD (12,500 Stars)", callback_data="stars_invoice:250:12500")
            ],
            [
                InlineKeyboardButton(text="🌟 $500 USD (25,000 Stars)", callback_data="stars_invoice:500:25000"),
                InlineKeyboardButton(text="🌟 $1,000 USD (50,000 Stars)", callback_data="stars_invoice:1000:50000")
            ]
        ]
    )

def get_moderation_inline_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🆕 DEMO", callback_data=f"mod:{target_user_id}:DEMO"),
                InlineKeyboardButton(text="🔵 REGULAR", callback_data=f"mod:{target_user_id}:REGULAR"),
                InlineKeyboardButton(text="⭐ VIP", callback_data=f"mod:{target_user_id}:VIP")
            ],
            [
                InlineKeyboardButton(text="🔑 ADMIN", callback_data=f"mod:{target_user_id}:ADMIN"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:{target_user_id}:REJECT")
            ]
        ]
    )
