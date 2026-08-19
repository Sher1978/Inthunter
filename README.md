# 🚀 Intent Hunter CDP (Outreach)

**Intent Hunter CDP** — B2B Customer Data Platform с модулем ИИ-скоринга поведенческих интентов, мониторингом Telegram-каналов/чатов и системой автоматической продажи лидов B2B-партнерам.

---

## 🛠 Технологический стек

* **Язык & Рантайм:** Python 3.11+, Asyncio
* **API & Web Panel:** FastAPI, Uvicorn, HTML5/CSS3/Vanilla JS Dashboard
* **Бот & Маркетплейс:** Aiogram 3.x
* **ИИ-Модели:** Groq Cloud (`llama-3.3-70b-versatile`), xAI Grok (`grok-2-latest`), Google Gemini (`gemini-2.5-flash`)
* **База данных:** SQLite / PostgreSQL (`SQLAlchemy 2.0 Async`, `asyncpg`, `aiosqlite`)
* **Сборщики:** Pyrogram Userbot + Zero-Auth Telegram Web Scraper + VK Scraper

---

## 🚂 Деплой на Railway (Официальная Платформа)

Проект полностью оптимизирован для автоматического развертывания на **[Railway](https://railway.com)**.

### Файлы конфигурации Railway:
1. **`Procfile`**: `web: uvicorn src.api.app:app --host 0.0.0.0 --port $PORT`
2. **`railway.json`**: настраивает NIXPACKS сборку и `/api/health` healthcheck.
3. **`Dockerfile`**: альтернативная контейнеризация для Railway Docker Deploy.

### Пошаговый запуск на Railway:

1. **Создание проекта:**
   * Зайдите на [Railway Dashboard](https://railway.com/dashboard).
   * Нажмите **New Project** -> **Deploy from GitHub repo**.
   * Выберите репозиторий `SherShadow/Inthunter`.

2. **Переменные окружения (Environment Variables):**
   В настройках сервиса на Railway добавьте следующие переменные:

   | Переменная | Описание / Значение |
   | :--- | :--- |
   | `PORT` | `8000` (Railway задает автоматически) |
   | `DATABASE_URL` | Ссылка на PostgreSQL (автоматически при добавлении Postgres в Railway) |
   | `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота из `@BotFather` |
   | `GROQ_API_KEY` | API-ключ Groq Cloud |
   | `GROQ_MODEL` | `llama-3.3-70b-versatile` |
   | `XAI_API_KEY` | API-ключ xAI Grok (опционально) |
   | `GEMINI_API_KEY` | API-ключ Google Gemini (опционально) |
   | `TELEGRAM_API_ID` | Telegram API ID от my.telegram.org (для юзербота) |
   | `TELEGRAM_API_HASH` | Telegram API Hash от my.telegram.org (для юзербота) |
   | `USERBOT_SESSION_STRING` | Pyrogram Session String (для юзербота) |
   | `LOG_LEVEL` | `INFO` |

3. **Публичный домен:**
   * В разделе **Settings** на странице сервиса в Railway нажмите **Generate Domain** (получите ссылку вида `*.up.railway.app`).

---

## ⚡ Локальный запуск

```bash
# 1. Клонирование репозитория
git clone https.github.com/SherShadow/Inthunter.git
cd Outreach

# 2. Установка зависимостей
pip install -r requirements.txt

# 3. Запуск веб-панели и бота
python -m src.main
```

Веб-интерфейс будет доступен по адресу: `http://localhost:8000`

---

## 📄 Спецификация Архитектуры

Полное архитектурное руководство приведено в файле [ARCHITECTURE.md](file:///c:/Sher_AI_Studio/projects/Outreach/ARCHITECTURE.md).
