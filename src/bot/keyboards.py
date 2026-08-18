from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

NICHE_NAMES = {
    "auto_kasko": "🚗 Автострахование (КАСКО/ОСАГО)",
    "real_estate": "🏠 Недвижимость (Покупка/Аренда)",
    "auto_broker": "🏎️ Автоброкер / Подбор авто"
}

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Мой Профиль"), KeyboardButton(text="💳 Баланс")],
            [KeyboardButton(text="🎯 Мои Ниши"), KeyboardButton(text="ℹ️ Инструкция")]
        ],
        resize_keyboard=True
    )

def get_niche_inline_keyboard(user_niches: list) -> InlineKeyboardMarkup:
    buttons = []
    for code, name in NICHE_NAMES.items():
        is_subbed = code in user_niches
        status_icon = "✅" if is_subbed else "❌"
        text = f"{status_icon} {name}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"toggle_niche:{code}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_buy_lead_keyboard(lead_id: str, price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Выкупить контакт за {int(price)} ₽",
                    callback_data=f"buy_lead:{lead_id}"
                )
            ]
        ]
    )

def get_topup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💵 +1 000 ₽", callback_data="topup:1000"),
                InlineKeyboardButton(text="💵 +5 000 ₽", callback_data="topup:5000"),
                InlineKeyboardButton(text="💵 +10 000 ₽", callback_data="topup:10000")
            ]
        ]
    )
