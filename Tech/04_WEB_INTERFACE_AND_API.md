# Веб-Интерфейс и REST API

Веб-приложение и REST API построены на FastAPI (`src/api/routes.py`) и обслуживают как Telegram Mini App (TMA), так и основной дашборд администратора.

## 1. Режим работы без мокапных данных (Zero Mock Policy)
Все REST API эндпоинты в `routes.py` возвращают аутентичные данные напрямую из БД:
*   `GET /api/channels`: Выводит целевые каналы с фильтрацией по локации (`dubai`) и нише (`real_estate`, `community`).
*   `GET /api/stats`: Реальная статистика сканирования, количества профилей и лидов.
*   `GET /api/hr/vacancies`: Реальный список опубликованных вакансий по категорию трудоустройства.
*   `GET /api/hr/stats`: Реальные агрегированные данные по подпискам и кандидатам.

## 2. Административный эндпоинт очистки (Emergency Clean)
Добавлен специальный эндпоинт быстрого обслуживания базы данных:
*   `GET /api/admin/emergency-clean`
*   **Функции**: Моментально выполняет `TRUNCATE TABLE user_activity_logs, ai_evaluation_logs, collector_logs;`, а затем вызывает `VACUUM FULL;` и `CHECKPOINT;` в PostgreSQL.
*   **Возвращаемый ответ**:
    ```json
    {
      "status": "ok",
      "results": {
        "truncate": "SUCCESS",
        "vacuum": "SUCCESS",
        "checkpoint": "SUCCESS"
      }
    }
    ```

## 3. Авторизация (TMA & Passcode)
*   `POST /api/tma/auth`: Валидация подписи `initData` от Telegram WebApp через HMAC-SHA256 с токеном бота. Выдает JWT Access Token.
*   `POST /api/auth/verify-passcode`: Резервная авторизация по админ-паролю (`260669` / `ADMIN_PASSCODE`).
