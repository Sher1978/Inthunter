# 🚀 Intent Hunter CDP (Outreach)

**Intent Hunter CDP** — B2B Customer Data Platform с модулем ИИ-скоринга поведенческих интентов, мониторингом Telegram-каналов/чатов и системой автоматической продажи лидов B2B-партнерам.

## 🌐 Среда исполнения: ОНЛАЙН СЕРВЕР RAILWAY (Production)

> 🟢 **Проект запущен и работает ОНЛАЙН на сервере Railway (`railway.app` / `railway.com`).**
> Все системные процессы, сканеры Telegram, B2B-бот и мульти-провайдерный ротатор ИИ работают 24/7 в облачной инфраструктуре Railway.

---

## 🛠 Технологический стек

* **Язык & Рантайм:** Python 3.11+, Asyncio
* **API & Web Panel:** FastAPI, Uvicorn, HTML5/CSS3/Vanilla JS Dashboard
* **Бот & Маркетплейс:** Aiogram 3.x
* **ИИ-Модели & AIRotator:** SambaNova Cloud, Cerebras Cloud, Groq Cloud Pool, Google AI Studio (Gemini 2.5 Flash-Lite / 2.5 Flash), OpenRouter (:free)
* **База данных:** SQLite / PostgreSQL (`SQLAlchemy 2.0 Async`, `asyncpg`, `aiosqlite`)
* **Сборщики:** Pyrogram Userbot + Zero-Auth Telegram Web Scraper + VK Scraper

---

## 🚂 Переменные окружения на Railway (Railway Dashboard -> Variables)

Для работы ротатора ИИ и платформы ОНЛАЙН на сервере Railway заданы следующие ключевые переменные:

| Переменная | Описание / Значение |
| :--- | :--- |
| `AI_PROVIDER` | `auto` (Каскадный ротатор ИИ) |
| `SAMBANOVA_API_KEY` | API-ключ SambaNova Cloud (`cloud.sambanova.ai`) |
| `CEREBRAS_API_KEY` | API-ключ Cerebras Cloud (`cloud.cerebras.ai`) |
| `GROQ_API_KEYS` | Пул API-ключей Groq Cloud (через запятую) |
| `GEMINI_API_KEYS` | Пул API-ключей Google AI Studio (Gemini 2.5 Flash-Lite) |
| `OPENROUTER_API_KEY` | API-ключ OpenRouter (`openrouter.ai`) |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота из `@BotFather` |
| `TELEGRAM_API_ID` | Telegram API ID от my.telegram.org (для юзербота) |
| `TELEGRAM_API_HASH` | Telegram API Hash от my.telegram.org (для юзербота) |
| `USERBOT_SESSION_STRING` | Pyrogram Session String (для юзербота) |
| `DATABASE_URL` | Ссылка на PostgreSQL на Railway |

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
