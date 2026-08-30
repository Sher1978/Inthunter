# Архитектура и База Данных

Система построена на асинхронном стеке Python (FastAPI + Aiogram 3 + SQLAlchemy 2.0 Async) с поддержкой PostgreSQL (Railway) и SQLite (локальная разработка).

## Высокоуровневая Архитектура (Mermaid)

```mermaid
graph TD
    subgraph Источники Данных
        TG_G[Закрытые Группы TG] -->|Pyrogram Swarm (3 Active / 12 Standby)| Ingestor
        TG_P[Публичные Каналы TG] -->|HTTP Web Scraper| Ingestor
    end

    subgraph Ядро Ingestion & Защита
        Ingestor -->|Сырой текст| Gatekeeper[Локальный Пре-фильтр]
        Gatekeeper -->|Одобрено| Splitter[VQS Splitter]
        Gatekeeper -->|Спам| DB_Logs[(AIEvaluationLog)]
    end

    subgraph ИИ и Аналитика
        Splitter -->|VENDOR_OFFER| DB_Outreach[(OutreachLead)]
        Splitter -->|LEAD_REQUEST| AI_Rotator[Multi-LLM Rotator: Gemini 3.6 / Groq / OpenRouter]
        AI_Rotator -->|Не лид| DB_Logs
        AI_Rotator -->|ГОРЯЧИЙ ЛИД| DB_Leads[(Lead)]
    end

    subgraph Авто-Очистка и Безопасность
        DB_Guard[DB Guard Daemon] -->|TRUNCATE & VACUUM & CHECKPOINT| DB_Storage[(PostgreSQL / SQLite Storage)]
        API_Clean[/api/admin/emergency-clean] --> DB_Guard
    end

    subgraph Пользовательские Интерфейсы
        DB_Leads --> API[FastAPI Server]
        DB_Logs --> API
        API --> TMA[Web Маркетплейс TMA]
        API --> Bot[Telegram Alert Bot & HR Bot]
    end
```

## Структура Базы Данных (SQLAlchemy)

Модели определены в `src/db/models.py`. Мы используем `AsyncSession` для всех операций.

### 1. Ядро пользователей и биллинга
*   **`Partner` (Партнеры/Пользователи бота):** Хранит `telegram_id`, `balance`, `role` (DEMO, REGULAR, VIP, SUPERADMIN), `subscribed_niches` и настройки уведомлений.
*   **`LeadPurchase` (Покупки):** Транзакционная таблица покупок лидов пользователями.

### 2. Ядро лидов и аутрича
*   **`Lead` (Квалифицированные B2C Лиды):** Сообщения, признанные ИИ лидами. Содержит `intent_summary`, `sales_hook`, `confidence_score`, `niche_code` и `status` (AVAILABLE, SOLD, EXPIRED).
*   **`OutreachLead` (B2B Подрядчики):** Пользователи, предлагающие услуги (VQS >= 40). Используются для автоматического B2B аутрича Екатерины.
*   **`HRVacancy` (Вакансии):** База реальных вакансий по категории трудоустройства в Дубае.

### 3. Инфраструктура сбора и юзерботов
*   **`ScraperAccount`:** Управление пулом из 15 аккаунтов юзерботов (`session_string`, `phone_number`, `status` [ACTIVE/DISABLED], `max_daily_joins`, `flood_until`). Первые 3 аккаунта автоматически сидируются при старте базы.
*   **`OutreachAccount`:** Аккаунты для отправки личных сообщений B2B подрядчикам.
*   **`MonitoredChannel`:** Целевые каналы сканирования (отфильтрованные строго по Дубаю: Недвижимость и Работа).
*   **`ChannelCandidate`:** Авто-обнаруженные Telegram-чаты (Discovery Engine).
*   **`BlacklistedChat` & `BlacklistedUser`:** Черные списки каналов и спамеров.

### 4. Телеметрия и Логи (Контролируются DB Guard)
*   **`UserActivityLog`:** Сырые сообщения из Telegram для построения контекста сообщений.
*   **`AIEvaluationLog`:** Логи Chain-of-Thought рассуждений ИИ и отсеянного спама.
*   **`CollectorLog`:** Телеметрия опроса каналов.

> [!IMPORTANT]
> При превышении 30 МБ логов или 3000 записей `DB Guard` выполняет безопасный `TRUNCATE` логов без затрогивания таблиц `Lead`, `Partner`, `HRVacancy` или `ScraperAccount`.
