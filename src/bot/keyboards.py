import os
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

NICHE_NAMES = {
    "real_estate": "🏠 Недвижимость (Покупка/Аренда)",
    "bike_rent": "🛵 Аренда байков & Трансфер",
    "currency_exchange": "💱 Обмен валюты (RUB/USDT)",
    "services_visa": "🛂 Визаран & Услуги",
    "auto_kasko": "🚗 Страхование (КАСКО/ОСАГО)",
    "hr_hiring": "👔 HR & Найм персонала",
    "community": "💬 Сообщество / Общий чат",
    "other_b2b": "💼 B2B Услуги & Прочее"
}

def register_dynamic_rubric(code: str, name: str):
    """Registers new AI-discovered rubrics dynamically in memory."""
    if code and name:
        NICHE_NAMES[code] = name

def get_main_reply_keyboard(is_monitoring_active: bool = True, role: str = "DEMO", is_debug_monitoring: bool = False) -> ReplyKeyboardMarkup:
    monitoring_label = "🔕 Выключить мониторинг" if is_monitoring_active else "🔔 Включить мониторинг"
    web_url = os.getenv("WEB_APP_URL", "https://inthunter-production.up.railway.app/dashboard")
    marketplace_url = os.getenv("MARKETPLACE_APP_URL", "https://inthunter-production.up.railway.app/marketplace")
    
    if role in ["SUPERADMIN", "ADMIN"]:
        rows = [
            [KeyboardButton(text="🎯 Маркетплейс лидов", web_app=WebAppInfo(url=marketplace_url)), KeyboardButton(text="🌐 Веб-Панель Дашборд", web_app=WebAppInfo(url=web_url))],
            [KeyboardButton(text="⚙️ Управление проектом"), KeyboardButton(text="📊 Аналитика")],
            [KeyboardButton(text="📡 Каналы прослушки"), KeyboardButton(text="🤖 Поиск чатов с Grok AI")],
            [KeyboardButton(text="👤 Мой Профиль"), KeyboardButton(text="💳 Баланс")],
            [KeyboardButton(text="🤝 Партнерка (20% RevShare)"), KeyboardButton(text="📦 Архив лидов")]
        ]
    else:
        rows = [
            [KeyboardButton(text="🎯 Маркетплейс лидов", web_app=WebAppInfo(url=marketplace_url)), KeyboardButton(text="🌐 Веб-Панель Дашборд", web_app=WebAppInfo(url=web_url))],
            [KeyboardButton(text="🤖 Поиск чатов с Grok AI"), KeyboardButton(text=monitoring_label)],
            [KeyboardButton(text="📡 Каналы прослушки"), KeyboardButton(text="👤 Мой Профиль")],
            [KeyboardButton(text="💳 Баланс"), KeyboardButton(text="📦 Архив лидов")],
            [KeyboardButton(text="🤝 Партнерка (20% RevShare)")]
        ]

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True
    )


def get_superadmin_management_keyboard() -> InlineKeyboardMarkup:
    web_url = os.getenv("WEB_APP_URL", "https://inthunter-production.up.railway.app/dashboard")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎯 Автопоиск чатов & ГЕО", callback_data="admin_open_discovery"),
                InlineKeyboardButton(text="👑 Роли & Блокировки", callback_data="open_roles_menu")
            ],
            [
                InlineKeyboardButton(text="📡 Каналы прослушки", callback_data="refresh_channels"),
                InlineKeyboardButton(text="🤖 Скаутинг с Grok AI", callback_data="grok_search_prompt")
            ],
            [
                InlineKeyboardButton(text="📊 Метрики & Здоровье", callback_data="open_analytics_menu"),
                InlineKeyboardButton(text="🧠 Обучение ИИ (/study)", callback_data="admin_open_study")
            ],
            [
                InlineKeyboardButton(text="💸 Заявки на модерацию", callback_data="admin_open_pending"),
                InlineKeyboardButton(text="🌐 Веб-Панель Управления", web_app=WebAppInfo(url=web_url))
            ]
        ]
    )

def get_analytics_inline_keyboard() -> InlineKeyboardMarkup:
    web_url = os.getenv("WEB_APP_URL", "https://inthunter-production.up.railway.app/dashboard")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏱ Ежечасный отчёт", callback_data="analytics_hourly"),
                InlineKeyboardButton(text="📅 Архив отчётов за день", callback_data="analytics_daily_archive")
            ],
            [
                InlineKeyboardButton(text="📈 Эффективность каналов (Heatmap)", callback_data="analytics_channels_heat"),
                InlineKeyboardButton(text="⚙️ Здоровье сканера", callback_data="analytics_scanner_health")
            ],
            [
                InlineKeyboardButton(text="👑 Управление ролями", callback_data="open_roles_menu"),
                InlineKeyboardButton(text="🌐 Аналитика в Веб-Панели", web_app=WebAppInfo(url=web_url))
            ]
        ]
    )

def get_referral_inline_keyboard(referral_link: str, can_withdraw: bool = False) -> InlineKeyboardMarkup:
    import urllib.parse
    share_text = urllib.parse.quote("🚀 Перехватывай горячих лидов раньше конкурентов с ИИ-сканером RADAR!")
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(referral_link)}&text={share_text}"

    buttons = [
        [InlineKeyboardButton(text="📱 Получить QR-код для приглашений", callback_data="ref_qr_code")],
        [InlineKeyboardButton(text="🚀 Поделиться реферальной ссылкой", url=share_url)]
    ]
    if can_withdraw:
        buttons.insert(0, [InlineKeyboardButton(text="💸 Запросить вывод от $50 USD", callback_data="ref_withdraw_start")])
    else:
        buttons.append([InlineKeyboardButton(text="ℹ️ Вывод доступен от $50.00 USD", callback_data="ref_info_withdraw")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_grok_niche_preset_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏠 Недвижимость (Нячанг)", callback_data="grok_preset:нячанг аренда жилье"),
            ],
            [
                InlineKeyboardButton(text="💱 Обмен валюты", callback_data="grok_preset:нячанг обмен валюты")
            ]
        ]
    )

def get_channels_inline_keyboard(is_admin: bool = True, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    web_url = os.getenv("WEB_APP_URL", "https://inthunter-production.up.railway.app/dashboard")
    buttons = []
    
    # Pagination controls if more than 1 page
    if total_pages > 1:
        prev_p = (page - 1) % total_pages
        next_p = (page + 1) % total_pages
        buttons.append([
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"channels_page:{prev_p}"),
            InlineKeyboardButton(text=f"📄 {page + 1} / {total_pages}", callback_data="noop"),
            InlineKeyboardButton(text="Вперед ▶️", callback_data=f"channels_page:{next_p}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🌐 Все чаты в Веб-Панели", web_app=WebAppInfo(url=web_url)),
        InlineKeyboardButton(text="🔍 Поиск с Grok ИИ", callback_data="grok_search_prompt")
    ])
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить вручную", callback_data="add_channel"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"channels_page:{page}")
    ])
    if is_admin:
        buttons.append([InlineKeyboardButton(text="🗑️ Удалить канал из прослушки", callback_data="open_delete_channels_menu")])

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

LOCATION_NAMES = {
    "nhatrang": "🇻🇳 Нячанг (Вьетнам)",
    "dubai": "🇦🇪 Дубай (ОАЭ)",
    "phuket": "🇹🇭 Пхукет (Таиланд)",
    "bali": "🇮🇩 Бали (Индонезия)",
    "global": "🌐 Глобал / РФ"
}

def get_niche_inline_keyboard(user_niches: list, is_onboarding: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    all_selected = not user_niches or "all" in user_niches or len(user_niches) >= len(NICHE_NAMES)
    all_icon = "✅" if all_selected else "❌"
    buttons.append([InlineKeyboardButton(text=f"{all_icon} 🔥 ВСЕ НИШИ И РУБРИКИ", callback_data="toggle_niche:all")])

    for code, name in NICHE_NAMES.items():
        is_subbed = all_selected or code in user_niches
        status_icon = "✅" if is_subbed else "❌"
        text = f"{status_icon} {name}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"toggle_niche:{code}")])
    
    if is_onboarding:
        buttons.append([InlineKeyboardButton(text="➡️ Далее: Выбор локаций (Шаг 2 из 2) ➔", callback_data="onb_step:locations")])
    else:
        buttons.append([InlineKeyboardButton(text="💾 Сохранить и вернуться в профиль", callback_data="profile_view")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_location_inline_keyboard(user_locations: list, is_onboarding: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    all_selected = not user_locations or "all" in user_locations or len(user_locations) >= len(LOCATION_NAMES)
    all_icon = "✅" if all_selected else "❌"
    buttons.append([InlineKeyboardButton(text=f"{all_icon} 📍 ВСЕ ГЕО И ЛОКАЦИИ", callback_data="toggle_loc:all")])

    for code, name in LOCATION_NAMES.items():
        is_subbed = all_selected or code in user_locations
        status_icon = "✅" if is_subbed else "❌"
        text = f"{status_icon} {name}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"toggle_loc:{code}")])
    
    if is_onboarding:
        buttons.append([InlineKeyboardButton(text="🚀 Завершить и запустить ИИ-сканер! ⚡", callback_data="onb_finish")])
    else:
        buttons.append([InlineKeyboardButton(text="💾 Сохранить и вернуться в профиль", callback_data="profile_view")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_profile_inline_keyboard() -> InlineKeyboardMarkup:
    mp_url = os.getenv("MARKETPLACE_APP_URL", "https://inthunter-production.up.railway.app/marketplace")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏷️ Настроить Ниши / Рубрики", callback_data="edit_user_niches"),
                InlineKeyboardButton(text="📍 Настроить Гео / Локации", callback_data="edit_user_locations")
            ],
            [
                InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="open_deposit_menu"),
                InlineKeyboardButton(text="🚀 Веб-Маркетплейс (TMA)", web_app=WebAppInfo(url=mp_url))
            ]
        ]
    )

def get_buy_lead_keyboard(lead_id: str, price_usd: float = 1.00, exclusive_price: float = 10.00, user_id: int = None) -> InlineKeyboardMarkup:
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
            [InlineKeyboardButton(text="🥶 Морозильная Камера Чатов ($2/мес)", callback_data="superadmin_frozen_chats:0")],
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
