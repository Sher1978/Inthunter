# LeadRADAR: Project Bible & Навигатор

Добро пожаловать в проект **LeadRADAR** (также известный во внутреннем коде как *Intent Hunter* или *Outreach*).
Этот документ — главный манифест и точка входа для всех ИИ-агентов и разработчиков, работающих над проектом.

## Суть проекта

**LeadRADAR** — это автономный ИИ-перехватчик лидов и вакансий из Telegram. 
Система сканирует сообщения в целевых Telegram-группах и каналах (с фокусировкой на **Дубай: Недвижимость и Работа/Вакансии**), использует двухэтапную фильтрацию (Gatekeeper / VQS) и мощную каскадную LLM (Gemini 3.6 Flash / Groq / OpenRouter) для выявления сообщений с высоким покупательским намерением (Buyer Intent). Найденные лиды мгновенно доставляются конечным клиентам (B2B/B2C) через Telegram-бота и Web-Маркетплейс (TMA).

### Ключевые ценности (Core Values)
1. **Экономия токенов (Cost Efficiency):** Мы не отправляем в LLM каждое сообщение. 95% мусора отсекается локальными эвристическими фильтрами (Gatekeeper / VQS).
2. **Анти-Бан защита (Stealth & Safety):** Поддерживается ротация пула из 15 юзерботов (3 активных аккаунта в режиме `ACTIVE`, 12 в горячем резерве `DISABLED`). Каждому юзерботу установлен лимит не более 20 вступлений в день с джиттер-задержками (12-15 минут). Также используется HTTP-скрапинг (Zero-Auth) для публичных каналов.
3. **Автономность & Самоочистка (Self-Healing & DB Guard):** Система автоматически контролирует размер базы данных (PostgreSQL / SQLite). При достижении размера > 30 МБ или > 3000 логов запускается автоматический `TRUNCATE` логов + `VACUUM FULL` + `CHECKPOINT` для моментального освобождения дискового объема Railway.
4. **Аутентичность данных (Zero Mock Policy):** Платформа не использует мокапные данные или заглушки в продакшене. Все метрики и список вакансий отдаются строго из базы данных.

---

## Глоссарий терминов

*   **Ingestor (Сборщик):** Модуль (`telegram.py`), отвечающий за чтение сообщений из Telegram (через ротационный пул Pyrogram Swarm или Public Scraper).
*   **Pyrogram Swarm:** Пул юзербот-сессий (преобразованных из `.session` в формат base64 struct Pyrogram v2), автоматически сидируемый при запуске базы.
*   **Gatekeeper:** Быстрый, легковесный Python-фильтр, отсекающий длинную рекламу и явный мусор до обращения к ИИ.
*   **VQS (Vendor Quality Score):** Алгоритм оценки качества сообщения, отделяющий запросы клиентов (`LEAD_REQUEST`) от предложений подрядчиков (`VENDOR_OFFER`).
*   **Splitter (Двойная воронка):** Логика, которая направляет `LEAD_REQUEST` в ИИ-Анализатор, а `VENDOR_OFFER` — в базу для B2B-аутрича (`OutreachLead`).
*   **DB Guard:** Автономный демон контроля диска, очищающий логи и выполняющий `VACUUM FULL` & `CHECKPOINT`.
*   **Emergency Clean API:** Административный эндпоинт `/api/admin/emergency-clean` для моментальной 1-клик очистки базы.
*   **CoT (Chain-of-Thought):** Логика, по которой ИИ в `scorer.py` рассуждает перед вынесением вердикта. Записывается в `AIEvaluationLog`.
*   **TMA (Telegram Mini App):** Веб-приложение (HTML/JS), открываемое внутри Telegram для отображения дашборда и покупки лидов.

---

## Структура репозитория

```text
Outreach/
├── src/
│   ├── ai/               # Интеграция с LLM (scorer.py, rotator_engine.py, budget_guard.py)
│   ├── api/              # FastAPI веб-сервер (app.py, routes.py, tma_auth.py, auth.py)
│   ├── bot/              # Telegram-боты (alert_bot.py, hr_bot.py, keyboards.py)
│   ├── db/               # SQLAlchemy модели и сессии (models.py, session.py)
│   ├── ingestion/        # Скраперы, юзерботы и фильтры (telegram.py, public_scraper.py, vendor_quality.py)
│   ├── services/         # Фоновые процессы и утилиты (db_guard.py, process_logger.py)
│   └── discovery/        # Поиск новых групп и авто-добавление (chat_manager.py)
├── static/               # Фронтенд для TMA и Dashboard (index.html, app.js, style.css)
├── scratch/              # Вспомогательные скрипты загрузки/конвертации/тестирования
└── Tech/                 # Текущая документация
```

## Как использовать эту документацию
1. Изучаете структуру БД и модели? Читайте [01_ARCHITECTURE_AND_DB.md](01_ARCHITECTURE_AND_DB.md).
2. Меняете логику фильтров или промпты ИИ? Читайте [02_DATA_INGESTION_AND_AI.md](02_DATA_INGESTION_AND_AI.md).
3. Добавляете новые команды боту или HR-модулю? Читайте [03_USER_FUNNELS_AND_BOT.md](03_USER_FUNNELS_AND_BOT.md).
4. Обновляете веб-интерфейс или REST API? Читайте [04_WEB_INTERFACE_AND_API.md](04_WEB_INTERFACE_AND_API.md).
5. Ищете причину переполнения памяти или настроек Railway? Читайте [05_DEVOPS_AND_MAINTENANCE.md](05_DEVOPS_AND_MAINTENANCE.md).
