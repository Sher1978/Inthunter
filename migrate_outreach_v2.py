import sqlite3
import os
import sys

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

def migrate():
    print("Подключение к БД...")
    
    # 1. Создаем новые таблицы синхронным движком
    try:
        from sqlalchemy import create_engine
        from src.db.models import Base
        db_url = "sqlite:///intent_hunter.db"
        sync_engine = create_engine(db_url)
        Base.metadata.create_all(sync_engine)
        print("Новые таблицы (outreach_projects, outreach_tasks, outreach_task_accounts) успешно созданы или уже существуют.")
    except Exception as e:
        print(f"Ошибка при создании таблиц через SQLAlchemy: {e}")

    # 2. Добавляем колонки в существующие таблицы
    db_path = "intent_hunter.db"
    if not os.path.exists(db_path):
        print(f"Файл БД не найден: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE b2b_prospects ADD COLUMN task_id INTEGER REFERENCES outreach_tasks(id) ON DELETE SET NULL;")
        print("Добавлена колонка 'task_id' в таблицу 'b2b_prospects'.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("Колонка 'task_id' уже существует в 'b2b_prospects'. Пропускаем.")
        else:
            print(f"Ошибка ALTER TABLE b2b_prospects: {e}")

    try:
        cursor.execute("ALTER TABLE outreach_accounts ADD COLUMN project_id INTEGER REFERENCES outreach_projects(id) ON DELETE SET NULL;")
        print("Добавлена колонка 'project_id' в таблицу 'outreach_accounts'.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("Колонка 'project_id' уже существует в 'outreach_accounts'. Пропускаем.")
        else:
            print(f"Ошибка ALTER TABLE outreach_accounts: {e}")

    conn.commit()
    conn.close()
    print("Миграция БД успешно завершена!")

if __name__ == "__main__":
    migrate()
