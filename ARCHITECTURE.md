# 📘 SYSTEM ARCHITECTURE BIBLE: "INTENT HUNTER CDP"

**Версия спецификации:** 1.1 (Zero-Cost Lean Build)

**Целевая среда:** Google Antigravity IDE

**Целевой стек:** Python 3.11+, Asyncio, Pyrogram/Telethon, FastAPI, SQLite / PostgreSQL (SQLAlchemy Async), Redis / In-Memory Queue, Aiogram 3.x, Groq Cloud SDK (`qwen/qwen3.6-27b` / `llama-3.3-70b-versatile` - 100% Free Tier) + Google GenAI SDK (`gemini-2.5-flash`).


---

## 1. НАЗНАЧЕНИЕ И КОНЦЕПЦИЯ СИСТЕМЫ

**Intent Hunter CDP** — это B2B Customer Data Platform с модулем ИИ-скоринга поведенческих интентов.

### Принцип работы:

1. **Слушает** публичные и профильные комьюнити/чаты 24/7 в фоновом режиме через пассивных юзерботов.
2. **Агрегирует** сообщения, комментарии и действия пользователей в единый цифровой профиль (User Profile Timeline).
3. **Анализирует** накопившуюся историю сообщений через **Gemini 2.5 Flash**, оценивает степень «прогрева» (Lead Temperature), категорию услуги и генерирует рекомендацию по продажам (Sales Hook).
4. **Продает** карточки готовых горячих лидов с их историей и контактом B2B-партнерам через специализированный Telegram-бот/кабинет.

---

## 2. АРХИТЕКТУРА ДАННЫХ И СТЕК ТЕХНОЛОГИЙ

```
 [ Telegram Chats / Public Groups ]
                 │
                 ▼
 ┌───────────────────────────────┐
 │ Module 1: Ingestion Engine    │ (Telethon / Pyrogram Userbots)
 └───────────────┬───────────────┘
                 │ (Raw Messages)
                 ▼
 ┌───────────────────────────────┐
 │ PostgreSQL (CDP Storage)      │ ── (User Profiles + Activity Logs)
 └───────────────┬───────────────┘
                 │ (Trigger: Activity Threshold / Cron)
                 ▼
 ┌───────────────────────────────┐
 │ Module 2: AI Scoring Engine   │ (Gemini 2.5 Flash Structured JSON)
 └───────────────┬───────────────┘
                 │ (Qualified Lead Object)
                 ▼
 ┌───────────────────────────────┐
 │ Module 3: Marketplace & Bot   │ (Aiogram 3 / FastAPI Lead Bidding)
 └───────────────┬───────────────┘
                 │
                 ▼
      [ B2B Partner / Client ]
```

---

## 3. СХЕМА БАЗЫ ДАННЫХ (PostgreSQL Schema)

```sql
-- 1. Таблица профилей пользователей
CREATE TABLE user_profiles (
    user_id BIGINT PRIMARY KEY, -- Telegram ID
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    behavior_summary TEXT, -- Обобщенный цифровой портрет от ИИ
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Таблица логов активности (Сообщения из чатов)
CREATE TABLE user_activity_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    chat_id BIGINT NOT NULL,
    chat_title VARCHAR(255),
    message_id BIGINT NOT NULL,
    message_text TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Таблица квалифицированных лидов
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES user_profiles(user_id),
    niche_code VARCHAR(100) NOT NULL, -- e.g., 'auto_kasko', 'real_estate'
    temperature VARCHAR(20) NOT NULL, -- 'WARM', 'HOT'
    confidence_score NUMERIC(3, 2), -- 0.00 to 1.00
    intent_summary TEXT NOT NULL, -- Краткая суть потребности
    sales_hook TEXT NOT NULL, -- Рекомендация для менеджера
    status VARCHAR(50) DEFAULT 'AVAILABLE', -- 'AVAILABLE', 'SOLD', 'EXPIRED'
    price NUMERIC(10, 2) NOT NULL DEFAULT 500.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Таблица B2B-Партнеров
CREATE TABLE partners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id BIGINT UNIQUE NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    balance NUMERIC(10, 2) DEFAULT 0.00,
    subscribed_niches TEXT[] DEFAULT '{}', -- Массив кодов ниш
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Транзакции и Покупки
CREATE TABLE lead_purchases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES leads(id),
    partner_id UUID NOT NULL REFERENCES partners(id),
    price_paid NUMERIC(10, 2) NOT NULL,
    purchased_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. МОДУЛИ И АЛГОРИТМЫ РАБОТЫ

### МОДУЛЬ 1: Ingestion Engine (Сборщик сообщений)

* **Технология:** Python, Asyncio, Pyrogram / Telethon.
* **Функционал:**
1. Подключается к пулу целевых чатов через $N$ пассивных аккаунтов.
2. Перехватывает каждое новое входящее сообщение (`NewMessage` event).
3. Делает `UPSERT` в таблицу `user_profiles` (создает или обновляет метаданные юзера).
4. Записывает текст и метаданные сообщения в таблицу `user_activity_logs`.
5. Отправляет сигнал в Redis Pub/Sub или Celery-очередь для проверки лид-триггеров.

---

### МОДУЛЬ 2: AI Scoring Engine (ИИ-Аналитик и Квалификатор)

* **Технология:** `google-genai` SDK, `gemini-2.5-flash`.
* **Триггер вызова:** При появлении в истории юзера от 2+ сообщений ИЛИ при обнаружении ключевых стоп-слов в очередном сообщении.
* **Алгоритм:**
1. Извлекает последние $K$ сообщений данного `user_id` за последние 14 дней из `user_activity_logs`.
2. Формирует JSON-нагрузку и отправляет в **Gemini 2.5 Flash** со структурированным выводом (Structured JSON Output).

#### Промпт ИИ-Аналитика:

```text
You are a lead qualification intelligence engine for B2B marketplaces.
Analyze the user's chat activity timeline and identify if the user is demonstrating a real intention to purchase products/services in one of the active niches.

Target Niches:
- 'auto_kasko': Insurance, KASKO, OSAGO inquiries.
- 'real_estate': Buying/renting property, searching agents.
- 'auto_broker': Buying vehicles, car inspections.

Rules:
1. Mark 'is_lead: true' ONLY if the user asks for prices, recommendations, services, or shows clear purchasing signals.
2. If 'is_lead: true', assign temperature: 'WARM' or 'HOT'.
3. Generate 'sales_hook' - actionable advice for the salesperson on how to approach this exact lead based on their timeline.
```

#### JSON Output Schema (Pydantic / GenAI Specs):

```json
{
  "is_lead": true,
  "niche_code": "auto_kasko",
  "temperature": "HOT",
  "confidence_score": 0.92,
  "intent_summary": "Пользователь ищет где оформить КАСКО на новый Chery Tiggo в течение недели.",
  "sales_hook": "Спешит с оформлением. В первом сообщении предложите мгновенный расчет без лишних звонков и укажите на возможность брокерского дисконта."
}
```

---

### МОДУЛЬ 3: Lead Marketplace & B2B Bot (Продавец лидов)

* **Технология:** Python, `aiogram` (v3.x), FastAPI.
* **Функционал:**
1. При сохранении нового лида со статусом `is_lead: true` в таблицу `leads`, бот выбирает всех партнеров из таблицы `partners`, у которых в `subscribed_niches` есть соответствующий `niche_code`.
2. Бот рассылает анонс карточки лида в Telegram без контактов пользователя.

#### Формат вывода карточки лида партнерскому боту:

```
🔥 ПОСТУПИЛ НОВЫЙ ГОРЯЧИЙ ЛИД!

Категория: Автострахование (КАСКО)
Температура: HOT (Готовность: 92%)
Свежесть: 2 минуты назад

📜 История действий пользователя:
• 14 Авг [Чат Автомобилистов]: "Ребят, как сейчас с выплатами по КАСКО у Ингоса?"
• Сегодня [Чат Сообщества]: "Посоветуйте проверенного брокера, нужно быстро сделать КАСКО"

💡 Рекомендация ИИ по продажам:
«Спешит с оформлением. В первом сообщении предложите мгновенный расчет без лишних звонков и укажите на возможность брокерского дисконта.»

💰 Стоимость контакта: 800 ₽
───────────────────────────
[ 💳 Выкупить лид и получить Telegram-контакт ]
```

3. При нажатии на кнопку **«Выкупить лид»**:
* Проверяется баланс партнера (`partners.balance >= lead.price`).
* Списываются средства с баланса.
* Статус лида меняется на `SOLD`.
* Партнеру мгновенно выдаются прямая ссылка на профиль (`t.me/username`), `user_id` и прямая ссылка на диалог с пользователем.
