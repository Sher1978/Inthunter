import os
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

NICHE_NAMES = {
    "real_estate": "🏠 Недвижимость (Покупка/Аренда)",
    "bike_rent": "🛵 Аренда байков & Трансфер",
    "currency_exchange": "💱 Обмен валюты (RUB/USDT)",
    "services_visa": "🛂 Визаран & Услуги",
    "auto_kasko": "🚗 Страхование (КАСКО/ОСАГО)",
    "community": "💬 Сообщество / Общий чат"
}

def register_dynamic_rubric(code: str, name: str):
    """Registers new AI-discovered rubrics dynamically in memory."""
    if code and name:
        NICHE_NAMES[code] = name

def get_main_reply_keyboard(is_monitoring_active: bool = True, role: str = "DEMO", is_debug_monitoring: bool = False) -> ReplyKeyboardMarkup:
    monitoring_label = "🔕 Выключить мониторинг" if is_monitoring_active else "🔔 Включить мониторинг"
    web_url = os.getenv("WEB_APP_URL", "https://inthunter-production.up.railway.app/dashboard")
    
    rows = [
        [KeyboardButton(text="🤖 Поиск чатов с Grok AI")],
        [KeyboardButton(text=monitoring_label)],
        [KeyboardButton(text="📡 Каналы прослушки"), KeyboardButton(text="📊 Статистика (Admin)")]
    ]
    
    if role in ["SUPERADMIN", "ADMIN"]:
        rows.append([
            KeyboardButton(text="🌐 Веб-Панель", web_app=WebAppInfo(url=web_url)),
            KeyboardButton(text="👑 Управление ролями")
        ])
        rows.append([
            KeyboardButton(text="🩺 Здоровье сканера"),
            KeyboardButton(text="➕ Запросить новую нишу")
        ])
        rows.append([
            KeyboardButton(text="👤 Мой Профиль"),
            KeyboardButton(text="💳 Баланс")
        ])
    else:
        rows.append([
            KeyboardButton(text="👤 Мой Профиль"),
            KeyboardButton(text="💳 Баланс"),
            KeyboardButton(text="➕ Запросить новую нишу")
        ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True
    )

def get_grok_niche_preset_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏠 Недвижимость (Нячанг)", callback_data="grok_preset:нячанг аренда жилье"),
                InlineKeyboardButton(text="🛵 Байки & Аренда", callback_data="grok_preset:нячанг байк аренда")
            ],
            [
                InlineKeyboardButton(text="🚗 Автострахование (КАСКО)", callback_data="grok_preset:автострахование каско"),
                InlineKeyboardButton(text="🛂 Визаран & Услуги", callback_data="grok_preset:нячанг визаран")
            ],
            [
                InlineKeyboardButton(text="💱 Обмен валюты", callback_data="grok_preset:нячанг обмен валюты")
            ]
        ]
    )

def get_channels_inline_keyboard(is_admin: bool = True) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🔍 Поиск чатов с Grok AI", callback_data="grok_search_prompt")],
        [InlineKeyboardButton(text="➕ Добавить вручную", callback_data="add_channel")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="🗑️ Удалить канал из прослушки", callback_data="open_delete_channels_menu")])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="refresh_channels")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_delete_channels_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        ch_name = ch.title or ch.username_or_link
        btn_text = f"🗑️ Удалить: {ch_name[:30]}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_ch:{ch.id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к каналам", callback_data="refresh_channels")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_screenshot_candidate_keyboard(index: int, total: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Утвердить и добавить в прослушку", callback_data=f"ocr_appr:{index}:{total}")
            ],
            [
                InlineKeyboardButton(text="❌ Пропустить", callback_data=f"ocr_skip:{index}:{total}"),
                InlineKeyboardButton(text="🚀 Добавить все оставшиеся", callback_data=f"ocr_appr_all:{index}:{total}")
            ]
        ]
    )

def get_grok_candidate_keyboard(username: str, index: int, total: int) -> InlineKeyboardMarkup:
    clean_u = username.replace("@", "")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Утвердить и добавить", callback_data=f"grok_appr:{clean_u}:{index}:{total}")
            ],
            [
                InlineKeyboardButton(text="💬 Уточнить поиск", callback_data="grok_refine_prompt"),
                InlineKeyboardButton(text="❌ Пропустить", callback_data=f"grok_skip:{clean_u}:{index}:{total}")
            ]
        ]
    )

def get_grok_proactive_chat_keyboard(suggested_questions: list = None) -> InlineKeyboardMarkup:
    buttons = []
    if suggested_questions:
        for idx, q in enumerate(suggested_questions[:3]):
            buttons.append([InlineKeyboardButton(text=f"💡 {q}", callback_data=f"grok_q:{idx}")])
    
    buttons.append([InlineKeyboardButton(text="💬 Уточнить поиск", callback_data="grok_refine_prompt")])
    buttons.append([InlineKeyboardButton(text="🛑 Завершить диалог с Grok", callback_data="grok_exit_dialog")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_grok_next_batch_keyboard(batch_count: int = 3, remaining_count: int = 0, total_pool_count: int = 0) -> InlineKeyboardMarkup:
    buttons = []
    if batch_count > 0:
        buttons.append([InlineKeyboardButton(text=f"⚡ ✅ Добавить эти {batch_count} канала", callback_data="grok_approve_batch")])

    if total_pool_count > 0:
        buttons.append([InlineKeyboardButton(text=f"🚀 ⚡ Добавить ВСЕ {total_pool_count} каналов из пула", callback_data="grok_approve_all_pool")])

    btn_label = f"➡️ Показать еще 3 канала от Grok ({remaining_count} в буфере)" if remaining_count > 0 else "➡️ Загрузить еще 3 новых канала от Grok ♾️"
    buttons.append([InlineKeyboardButton(text=btn_label, callback_data="grok_next_batch")])
    buttons.append([InlineKeyboardButton(text="💬 Задать новый запрос Grok", callback_data="grok_search_prompt")])
    buttons.append([InlineKeyboardButton(text="🛑 Завершить диалог с Grok", callback_data="grok_exit_dialog")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_niche_inline_keyboard(user_niches: list) -> InlineKeyboardMarkup:
    buttons = []
    for code, name in NICHE_NAMES.items():
        is_subbed = code in user_niches
        status_icon = "✅" if is_subbed else "❌"
        text = f"{status_icon} {name}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"toggle_niche:{code}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_buy_lead_keyboard(lead_id: str, price_usd: float = 1.00, exclusive_price: float = 10.00) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🛒 Купить лид (${price_usd:.2f} USD)",
                    callback_data=f"buy_lead:{lead_id}:std"
                ),
                InlineKeyboardButton(
                    text=f"👑 Выкупить эксклюзивно (${exclusive_price:.2f} USD)",
                    callback_data=f"buy_lead:{lead_id}:excl"
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
                InlineKeyboardButton(text="👑 SUPERADMIN", callback_data=f"mod:{target_user_id}:SUPERADMIN")
            ],
            [
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:{target_user_id}:REJECT")
            ]
        ]
    )

def get_superadmin_role_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="role_search_start")],
            [InlineKeyboardButton(text="📱 QR-код персонала", callback_data="get_staff_qr")],
            [InlineKeyboardButton(text="👥 Пользователи и ники", callback_data="role_list_all:0")],
            [InlineKeyboardButton(text="⛔ Заблокированные", callback_data="list_blocked_users")]
        ]
    )

def get_user_role_edit_keyboard(target_user_id: int, is_blocked: bool = False) -> InlineKeyboardMarkup:
    block_button_text = "🟢 Разблокировать юзера" if is_blocked else "⛔ Заблокировать юзера"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ VIP", callback_data=f"set_role_btn:{target_user_id}:VIP"),
                InlineKeyboardButton(text="🔑 ADMIN", callback_data=f"set_role_btn:{target_user_id}:ADMIN"),
                InlineKeyboardButton(text="👑 SUPERADMIN", callback_data=f"set_role_btn:{target_user_id}:SUPERADMIN")
            ],
            [
                InlineKeyboardButton(text="🔵 REGULAR", callback_data=f"set_role_btn:{target_user_id}:REGULAR"),
                InlineKeyboardButton(text="🆕 DEMO", callback_data=f"set_role_btn:{target_user_id}:DEMO")
            ],
            [
                InlineKeyboardButton(text=block_button_text, callback_data=f"toggle_block_user:{target_user_id}")
            ],
            [
                InlineKeyboardButton(text="🔙 К управлению ролями", callback_data="open_role_menu")
            ]
        ]
    )

def get_staff_request_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Назначить VIP", callback_data=f"staff_approve:{target_user_id}:VIP"),
                InlineKeyboardButton(text="🔑 Назначить ADMIN", callback_data=f"staff_approve:{target_user_id}:ADMIN")
            ],
            [
                InlineKeyboardButton(text="👑 Назначить SUPERADMIN", callback_data=f"staff_approve:{target_user_id}:SUPERADMIN"),
                InlineKeyboardButton(text="🔵 Назначить REGULAR", callback_data=f"staff_approve:{target_user_id}:REGULAR")
            ],
            [
                InlineKeyboardButton(text="❌ Отклонить заявку", callback_data=f"staff_approve:{target_user_id}:REJECT")
            ]
        ]
    )
